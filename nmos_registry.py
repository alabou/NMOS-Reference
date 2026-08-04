#!/usr/bin/env python3
# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NMOS IS-04 Registry application.

A standalone registry serving the IS-04 v1.3 Registration and Query APIs, so
that trying ``nmos-reference`` needs nothing installed beyond this checkout.

Three listeners, on ports chosen to match the defaults already baked into the
Node's client flags — ``--rdsRegistrationPort``, ``--rdsQueryPort`` — so a
Node needs no reconfiguration to talk to it:

  * Registration API  (default 8447)  Nodes POST their resources here.
  * Query API         (default 8446)  Controllers read them back.
  * Query WebSocket   (default 8448)  live updates; the target of ``ws_href``.

Security
--------
The two APIs are deliberately not configured the same way, and the asymmetry
is normative. ``specs/NMOS With Control Plane Security.md:105`` (
TR-10-SEC) requires that the Registration API "MUST not require the NMOS Nodes
to use OAuth 2.0 authorizations" and that it "MUST be secured using TLS with
server authentication or mutual client-server authentication". So:

  * Registration accepts no TLS, server-TLS, or mTLS — the three Registry
    Access Policy values of that document — and never OAuth 2.0.
  * Query additionally supports OAuth 2.0, in both its server-TLS and mTLS
    forms, giving the same five modes a Node's own API supports.

Usage:
    python3 nmos_registry.py --registryDisableTLS
    python3 nmos_registry.py --registryCertificate cert.pem --registryKey key.pem
    python3 nmos_registry.py --registrationTrustedRootCA ca.pem  # mTLS registration
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import socket
import ssl
import sys
import uuid
from logging.handlers import RotatingFileHandler
from typing import Any

from aiohttp import web

from nmos.api.tr10_tls import apply_tr10_tls_restrictions
from nmos.node.security_tags import NAP, RAAM, RAP

# Same access-log format as the Node: aiohttp's default plus ``%Tf``, the
# per-request duration, so a slow query is visible in the log without external
# tooling.
_ACCESS_LOG_FORMAT = '%a %t "%r" %s %b "%{Referer}i" "%{User-Agent}i" %Tf'


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def _host_arg(value: str) -> str:
    """argparse ``type`` for host / address options.

    Strips surrounding whitespace, so a stray space from a shell default like
    ``"${1:- 127.0.0.1}"`` cannot turn a valid address into an unresolvable
    hostname. Same helper, same reason, as ``nmos_node.py``.
    """
    return value.strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Flag names and grouping deliberately mirror ``nmos_node.py``: one shared
    server certificate for the process with a separate trust anchor per
    interface, exactly as the Node has one ``--nodeCertificate`` with
    ``--nodeTrustedRootCA`` and ``--controlTrustedRootCA``. An operator who
    knows the Node's flags already knows these.
    """
    p = argparse.ArgumentParser(
        description="NMOS IS-04 Registry (Registration + Query APIs)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # --- Registry server (shared across both interfaces) ---
    g = p.add_argument_group("Registry Server")
    g.add_argument("--registryAddr", type=_host_arg, default="127.0.0.1",
                   help="Bind address for all registry listeners")
    g.add_argument("--registryCertificate", default="",
                   help="Server certificate (*.chain.pem), shared by both "
                        "interfaces")
    g.add_argument("--registryKey", default="", help="Server private key")
    g.add_argument("--registryDisableTLS", action="store_true",
                   help="Disable TLS on all registry listeners. Registration "
                        "then runs under TR-10-SEC RAP=0 (Unrestricted "
                        "Registration over HTTP).")
    g.add_argument("--registrySerialNumber", default="SNR12345",
                   help="Registry serial number. Used as the BCP-002-02 "
                        "instance identifier when validating the 'aud' claim "
                        "of OAuth 2.0 tokens on the Query API.")

    # --- Registration interface ---
    g = p.add_argument_group("Registration API")
    g.add_argument("--registrationPort", type=int, default=8447,
                   help="Registration API port (Node's --rdsRegistrationPort)")
    g.add_argument("--registrationTrustedRootCA", action="append", default=None,
                   help="Trusted root CA for Registration client-certificate "
                        "auth (PEM path; may be repeated). Empty = TR-10-SEC "
                        "RAP=1 (server-authenticated TLS). Non-empty = RAP=2 "
                        "(Restricted Registration, mutual TLS).")
    g.add_argument("--registrationOptionalClientAuth", action="store_true",
                   help="Accept unauthenticated clients at the TLS layer "
                        "(CERT_OPTIONAL) and enforce client certificates in "
                        "the application instead. Registration has no "
                        "read-only verbs, so this mainly aids diagnostics.")

    # --- Query interface ---
    g = p.add_argument_group("Query API")
    g.add_argument("--queryPort", type=int, default=8446,
                   help="Query API port (Node's --rdsQueryPort)")
    g.add_argument("--queryWebSocketPort", type=int, default=8448,
                   help="Query API WebSocket port, advertised in ws_href")
    g.add_argument("--queryTrustedRootCA", action="append", default=None,
                   help="Trusted root CA for Query client-certificate auth "
                        "(PEM path; may be repeated). Empty = server-TLS only.")
    g.add_argument("--queryOptionalClientAuth", action="store_true",
                   help="Allow unauthenticated clients read-only access to "
                        "the Query API (CERT_OPTIONAL); state-changing verbs "
                        "still require a client certificate.")

    # --- Behaviour ---
    g = p.add_argument_group("Registry Behaviour")
    g.add_argument("--garbageCollectionInterval", type=float, default=12.0,
                   help="Seconds of heartbeat silence after which a Node and "
                        "all its sub-resources are removed. Default per "
                        "Behaviour - Registration.md:47; also nmos-cpp's "
                        "registration_expiry_interval.")
    g.add_argument("--forgetInterval", type=float, default=60.0,
                   help="Seconds a removed resource is retained as non-extant "
                        "before being dropped entirely (nmos-cpp's "
                        "forget_erased_resources stage).")
    g.add_argument("--pagingLimit", type=int, default=10,
                   help="Default Query API page size (nmos-cpp "
                        "query_paging_default)")
    g.add_argument("--pagingLimitMax", type=int, default=100,
                   help="Largest page size honoured (nmos-cpp "
                        "query_paging_limit)")
    g.add_argument("--statusInterval", type=float, default=5.0,
                   help="Seconds between registry status log lines "
                        "(0 disables)")

    # --- OAuth2 (Query API only) ---
    # Flag names are identical to the Node's so the same launch scripts, CA
    # files and authorization server configuration apply unchanged.
    g = p.add_argument_group("OAuth2 (Query API only)")
    g.add_argument("--oauth2", action="store_true",
                   help="Enable OAuth 2.0 authorization on the Query API. "
                        "Has no effect on the Registration API, which "
                        "TR-10-SEC:105 forbids from requiring OAuth 2.0.")
    g.add_argument("--oauth2Host", type=_host_arg, default="",
                   help="OAuth2 authorization server host")
    g.add_argument("--oauth2Port", type=int, default=4444,
                   help="OAuth2 authorization server port")
    g.add_argument("--oauth2TrustedRootCA", action="append", default=None,
                   help="OAuth2 trusted root CA (PEM path; may be repeated)")
    g.add_argument("--oauth2DisableTLS", action="store_true",
                   help="Disable TLS towards the OAuth2 server")
    g.add_argument("--oauth2ApiSelector", default="realms/TR-10-SEC",
                   help="IS-10 / RFC 8414 §3.1 'api_selector' — the path "
                        "component of the issuer identifier. Empty for ORY "
                        "Hydra; 'realms/<realm>' for Keycloak.")
    g.add_argument("--oauth2AudienceMode", default="serial",
                   choices=["serial", "cert", "either"],
                   help="OAuth 2.0 Audience Identification Mode "
                        "(TR-10-SEC §12.4).")

    # --- Logging ---
    g = p.add_argument_group("Logging")
    g.add_argument("--logFile", default="/tmp/nmos-registry.log",
                   help="Log file path (empty=disable file logging)")

    # --- Global TLS ---
    p.add_argument("--trustedRootCA", action="append", default=None,
                   help="Global trusted root CA (PEM path; may be repeated). "
                        "Fallback for OUTGOING-TLS contexts whose per-role "
                        "flag is unset (currently the OAuth2 client). The "
                        "INCOMING-mTLS listeners do NOT fall back to it: an "
                        "interface with no per-role CA has no trust store, "
                        "which is what keeps 'no CA configured' from silently "
                        "meaning 'trust the global set'.")
    p.add_argument("--gcrl", type=str, default=None,
                   help="Path to a Global CRL PEM bundle (TR-10-SEC §12.14). "
                        "When set, applied to every TLS verify store and "
                        "VERIFY_CRL_CHECK_LEAF is enabled.")

    ns = p.parse_args(argv)

    # ``action="append"`` leaves the attribute None when the flag is omitted;
    # normalise so every CA option is uniformly a list[str].
    for attr in (
        "registrationTrustedRootCA",
        "queryTrustedRootCA",
        "oauth2TrustedRootCA",
        "trustedRootCA",
    ):
        if getattr(ns, attr) is None:
            setattr(ns, attr, [])

    return ns


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(args: argparse.Namespace) -> None:
    """Configure logging with an optional rotating file handler."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console)

    if args.logFile:
        try:
            handler = RotatingFileHandler(
                args.logFile, maxBytes=1_000_000, backupCount=3,
            )
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            root.addHandler(handler)
        except OSError as exc:
            print(
                f"Warning: cannot open log file {args.logFile}: {exc}",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# TLS helpers
# ---------------------------------------------------------------------------

def _ca_list(specific: list[str], fallback: list[str]) -> list[str]:
    """Resolve the effective CA list for one service.

    A non-empty per-service list fully overrides the global one; the two are
    never merged. Same semantics as ``nmos_node.py::_ca_list`` — merging would
    silently widen a deliberately narrow trust store.
    """
    return specific if specific else fallback


def _server_context(
    args: argparse.Namespace,
    trusted_root_ca: list[str],
    optional_client_auth: bool,
) -> ssl.SSLContext | None:
    """Build a server SSL context for one listener.

    Returns None when TLS is disabled or no certificate was supplied, which
    the caller renders as a plain HTTP listener.

    The client-certificate trust anchor is per-interface, which is what allows
    Registration to require mTLS while Query does not, or vice versa.
    """
    if args.registryDisableTLS:
        return None
    if not args.registryCertificate or not args.registryKey:
        logging.warning(
            "TLS requested but no --registryCertificate/--registryKey "
            "supplied — running without TLS",
        )
        return None

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    apply_tr10_tls_restrictions(ctx, gcrl_path=getattr(args, "gcrl", None))
    ctx.load_cert_chain(args.registryCertificate, args.registryKey)

    if trusted_root_ca:
        for ca in trusted_root_ca:
            ctx.load_verify_locations(ca)
        ctx.verify_mode = (
            ssl.CERT_OPTIONAL if optional_client_auth else ssl.CERT_REQUIRED
        )
    return ctx


def build_registration_ssl_context(
    args: argparse.Namespace,
) -> ssl.SSLContext | None:
    """SSL context for the Registration listener.

    Anchor is ``--registrationTrustedRootCA``. With it empty the listener is
    server-authenticated TLS (TR-10-SEC RAP=1); with it set, mutual TLS
    (RAP=2). With ``--registryDisableTLS`` it is plain HTTP (RAP=0).
    """
    return _server_context(
        args,
        args.registrationTrustedRootCA,
        args.registrationOptionalClientAuth,
    )


def build_query_ssl_context(args: argparse.Namespace) -> ssl.SSLContext | None:
    """SSL context for the Query and Query-WebSocket listeners.

    Anchor is ``--queryTrustedRootCA``. Both listeners share it: a
    subscription's ``secure`` attribute describes one negotiated mode for the
    pair, so splitting their TLS configuration would make that attribute
    unrepresentable.
    """
    return _server_context(
        args, args.queryTrustedRootCA, args.queryOptionalClientAuth,
    )


# ---------------------------------------------------------------------------
# Server tasks
# ---------------------------------------------------------------------------

async def go_registry_servers(
    dg: Any,
    registration_app: web.Application,
    query_app: web.Application,
    query_ws_app: web.Application,
    args: argparse.Namespace,
) -> None:
    """Run all three listeners until the dispatch group is cancelled."""
    registration_ssl = build_registration_ssl_context(args)
    query_ssl = build_query_ssl_context(args)

    runners = [
        web.AppRunner(
            registration_app, shutdown_timeout=2.0,
            access_log_format=_ACCESS_LOG_FORMAT,
        ),
        web.AppRunner(
            query_app, shutdown_timeout=2.0,
            access_log_format=_ACCESS_LOG_FORMAT,
        ),
        # The WebSocket runner gets a longer shutdown grace than the HTTP
        # ones: its connections are long-lived by design, and cutting them at
        # the same 2 s as an idle REST socket would routinely truncate a grain
        # mid-write on shutdown.
        web.AppRunner(query_ws_app, shutdown_timeout=5.0),
    ]
    for runner in runners:
        await runner.setup()

    host = args.registryAddr or "0.0.0.0"
    try:
        sites = [
            web.TCPSite(
                runners[0], host, args.registrationPort,
                ssl_context=registration_ssl,
            ),
            web.TCPSite(
                runners[1], host, args.queryPort, ssl_context=query_ssl,
            ),
            web.TCPSite(
                runners[2], host, args.queryWebSocketPort,
                ssl_context=query_ssl,
            ),
        ]
        for site in sites:
            await site.start()

        _print_banner(args, registration_ssl, query_ssl)
        await dg.done()
    finally:
        for runner in runners:
            await runner.cleanup()


def classify_registration_rap(args: argparse.Namespace) -> RAP:
    """The Registration interface's TR-10-SEC Registry Access Policy.

    §"Registry Access Policy" enumerates exactly three, and the flags select
    them directly: no TLS is Unrestricted Registration over HTTP (0), TLS with
    no client-certificate anchor is Unrestricted Registration with server
    authentication (1), and an anchor makes it Restricted Registration over
    mutual TLS (2).
    """
    if args.registryDisableTLS or not (
        args.registryCertificate and args.registryKey
    ):
        return RAP.UNRESTRICTED_HTTP
    if args.registrationTrustedRootCA:
        return RAP.RESTRICTED_MTLS
    return RAP.UNRESTRICTED_HTTPS


def classify_query_nap(args: argparse.Namespace) -> NAP:
    """The Query interface's TR-10-SEC Node Access Policy.

    The Query API is configured against the same matrix a Node's own API is,
    so it is classified with the same rules as
    ``nmos/node/security_tags.py::_compute_nap``:

    * No TLS -> Unrestricted Read Write (0). The specification is blunt about
      this one: a device so configured "MUST not claim compliance".
    * ``--queryOptionalClientAuth`` with OAuth 2.0 off -> Unrestricted Read
      Only (1). Reads are open to every client, writes are enforced per RAAM.
    * Otherwise -> Restricted Read Write (2).

    OAuth 2.0 deliberately forces 2 even alongside
    ``--queryOptionalClientAuth``: §"Unrestricted Read Only" states the policy
    "is not allowed when OAuth 2.0 authorizations are used, in which case even
    read access MUST be explicitly provided by the OAuth 2.0 authorizations".
    Every read route is wrapped in ``check_oauth2``, so the deployment really
    is 2, and reporting 1 would misdescribe it.
    """
    if args.registryDisableTLS or not (
        args.registryCertificate and args.registryKey
    ):
        return NAP.UNRESTRICTED_RW
    if args.queryOptionalClientAuth and not args.oauth2:
        return NAP.UNRESTRICTED_RO
    return NAP.RESTRICTED_RW


def classify_query_raam(args: argparse.Namespace) -> RAAM:
    """The Query interface's Restricted Access Authorization Mode.

    Which mechanism enforces the restrictions NAP calls for: mutual TLS,
    OAuth 2.0, or both. Mirrors
    ``nmos/node/security_tags.py::_compute_raam``.

    Only meaningful when one of them is actually configured — see
    ``query_has_authorization``.
    """
    has_mtls = bool(args.queryTrustedRootCA)
    if has_mtls and args.oauth2:
        return RAAM.MTLS_PLUS_OAUTH2
    if args.oauth2:
        return RAAM.OAUTH2
    return RAAM.MTLS


def query_has_authorization(args: argparse.Namespace) -> bool:
    """Is any RAAM mechanism actually configured on the Query interface?

    RAAM enumerates *how* restricted access is enforced: mutual TLS, OAuth
    2.0, or both. With neither a client-certificate trust anchor nor OAuth 2.0
    there is no mechanism at all, so nothing is enforced — every verb,
    including the state-changing ones, is open to any client that completes
    the TLS handshake.

    That configuration is not one of the three NAP policies. It is TLS-encrypted
    Unrestricted Read Write, which the specification only enumerates for plain
    HTTP. The banner reports it rather than letting ``NAP=2 RESTRICTED_RW``
    imply a restriction that does not exist.
    """
    return bool(args.queryTrustedRootCA) or bool(args.oauth2)


def _print_banner(
    args: argparse.Namespace,
    registration_ssl: ssl.SSLContext | None,
    query_ssl: ssl.SSLContext | None,
) -> None:
    """Print the endpoint summary and the effective security policy.

    The policy is reported in TR-10-SEC's own vocabulary rather than as a list
    of the flags that produced it. An operator can then read the running
    compliance mode off the console instead of deriving it — which matters
    most for the combinations that are not obvious, such as OAuth 2.0
    overriding ``--queryOptionalClientAuth`` from NAP=1 to NAP=2.
    """
    display_host = args.registryAddr or socket.gethostbyname(
        socket.gethostname(),
    )
    registration_scheme = "https" if registration_ssl else "http"
    query_scheme = "https" if query_ssl else "http"
    ws_scheme = "wss" if query_ssl else "ws"

    rap = classify_registration_rap(args)
    nap = classify_query_nap(args)
    raam = classify_query_raam(args)

    # RAAM describes how restrictions are enforced, so it is reported only
    # when a mechanism actually exists. Printing "RAAM=0 MTLS" beside a
    # listener with no trust anchor and no OAuth 2.0 would name a protection
    # that is not in force.
    query_authorized = query_has_authorization(args)
    query_policy = f"NAP={int(nap)} {nap.name}"
    if nap is not NAP.UNRESTRICTED_RW:
        query_policy += (
            f", RAAM={int(raam)} {raam.name}"
            if query_authorized
            else ", RAAM=none configured"
        )

    print(f"\nNMOS Registry running on {display_host}")
    print(
        f"  Registration: {registration_scheme}://{display_host}:"
        f"{args.registrationPort}/x-nmos/registration/v1.3/",
    )
    print(f"                  [RAP={int(rap)} {rap.name}]")
    print(
        f"  Query:        {query_scheme}://{display_host}:{args.queryPort}"
        f"/x-nmos/query/v1.3/",
    )
    print(f"                  [{query_policy}]")
    print(
        f"  Query WS:     {ws_scheme}://{display_host}:"
        f"{args.queryWebSocketPort}/x-nmos/query/v1.3/subscriptions/{{id}}",
    )

    if nap is NAP.UNRESTRICTED_RW:
        # §"Unrestricted Read Write": "the device MUST not claim compliance
        # with this specification while so configured". Worth saying out loud
        # rather than leaving the operator to infer it from NAP=0.
        print(
            "\n  WARNING: no TLS — this configuration is NOT compliant with "
            "NMOS With Control Plane Security\n"
            "           (TR-10-SEC). Use it for development only.",
        )
    elif not query_authorized:
        # TLS is on, so the transport is protected and NAP reads as
        # Restricted Read Write -- but no RAAM mechanism is configured, so
        # nothing actually restricts anything. Creating a subscription is a
        # write, and it is open to any client that completes the handshake.
        print(
            "\n  WARNING: the Query API has no authorization mechanism — "
            "neither --queryTrustedRootCA\n"
            "           nor --oauth2. Traffic is encrypted, but writes "
            "(subscription creation)\n"
            "           are open to any client. Configure one to obtain "
            "Restricted Read Write.",
        )

    print(
        f"\n  Point a Node at it with: --rdsHost {display_host} "
        f"--rdsRegistrationPort {args.registrationPort} "
        f"--rdsQueryPort {args.queryPort}"
        + ("  --rdsDisableTLS" if registration_ssl is None else ""),
    )
    print()


async def go_registry_authorizations(
    dg: Any, security: Any, args: argparse.Namespace,
) -> None:
    """Maintain the JWKS cache used to validate Query API bearer tokens.

    The same TR-10-SEC §14.3.2 lifecycle the Node runs — 23 h + jitter
    refresh, 36 h hard invalidation, exponential backoff, fail-closed until
    the first fetch succeeds — via the shared ``JWKSCache``.

    Only the Query API consumes these keys. The Registration API must not
    require OAuth 2.0 at all (TR-10-SEC:105), so its InterfaceSecurity has
    ``oauth2=False`` and never consults them.
    """
    import aiohttp

    from nmos.oauth2 import JWKS, discover_jwks
    from nmos.oauth2.jwks_cache import JWKSCache

    if not args.oauth2Host:
        logging.warning(
            "registry: --oauth2 set but no --oauth2Host; Query API bearer "
            "validation will fail closed",
        )
        await dg.done()
        return

    scheme = "http" if args.oauth2DisableTLS else "https"

    ssl_ctx: ssl.SSLContext | None = None
    if not args.oauth2DisableTLS:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        apply_tr10_tls_restrictions(ssl_ctx, gcrl_path=getattr(args, "gcrl", None))
        cas = _ca_list(args.oauth2TrustedRootCA, args.trustedRootCA)
        if cas:
            for ca in cas:
                ssl_ctx.load_verify_locations(ca)
        else:
            ssl_ctx.load_default_certs()

    connector_ssl: bool | ssl.SSLContext = (
        ssl_ctx if ssl_ctx is not None else False
    )
    connector = aiohttp.TCPConnector(ssl=connector_ssl)
    async with aiohttp.ClientSession(connector=connector) as session:
        async def fetch_jwks() -> JWKS:
            return await discover_jwks(
                scheme=scheme,
                host=args.oauth2Host,
                port=args.oauth2Port,
                api_selector=args.oauth2ApiSelector or "",
                client=session,
            )

        def on_update(jwks: JWKS | None) -> None:
            # None means invalidate: the middleware refuses every
            # authenticated request while the keyset is None.
            security.oauth2_keys = jwks

        cache = JWKSCache(fetch=fetch_jwks, on_update=on_update)
        try:
            await cache.run(is_done=lambda: dg.is_done)
        except asyncio.CancelledError:
            return


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    """Build the registry and dispatch every background task."""
    from nmos.cert_check import cert_dns_identities
    from nmos.errors import Done
    from nmos.registry import (
        InterfaceSecurity,
        Registry,
        create_query_app,
        create_query_ws_app,
        create_registration_app,
    )
    from nmos.registry.gc import run_garbage_collection, run_status_reporting
    from nmos.registry.store import RegistryStore
    from nmos.registry.subscriptions import SubscriptionManager
    from nmos.tasks import DispatchGroup

    store = RegistryStore(
        gc_interval=args.garbageCollectionInterval,
        forget_interval=args.forgetInterval,
    )
    # ``Behaviour - Querying.md:37`` -- source_id identifies the Query API
    # instance. A fresh id per process is what lets a client notice that the
    # registry restarted and its subscription state is gone.
    registry = Registry(store, query_id=str(uuid.uuid4()))
    registry.attach_subscriptions(SubscriptionManager(registry))

    tls_enabled = not args.registryDisableTLS and bool(
        args.registryCertificate and args.registryKey,
    )

    # Identities from our own server certificate, used for the OAuth 2.0
    # audience check. Read once here rather than per request.
    cert_names: list[str] = []
    if tls_enabled:
        try:
            cert_names = list(cert_dns_identities(args.registryCertificate))
        except Exception as exc:
            logging.warning(
                "registry: cannot read identities from %s: %s",
                args.registryCertificate, exc,
            )

    # TR-10-SEC:105 -- Registration never uses OAuth 2.0. This is the line
    # that enforces it: with oauth2=False the check_oauth2 decorator is a
    # pass-through, and no Registration route is wrapped in one regardless.
    registration_security = InterfaceSecurity(
        client_auth_required=bool(args.registrationTrustedRootCA),
        oauth2=False,
        serial_number=args.registrySerialNumber,
        tls_server_cert_names=cert_names,
    )
    query_security = InterfaceSecurity(
        client_auth_required=bool(args.queryTrustedRootCA),
        oauth2=bool(args.oauth2),
        serial_number=args.registrySerialNumber,
        tls_server_cert_names=cert_names,
        use_serial_number_in_aud=args.oauth2AudienceMode != "cert",
    )

    registration_app = create_registration_app(registry, registration_security)
    query_app = create_query_app(
        registry,
        query_security,
        tls=tls_enabled,
        ws_port=args.queryWebSocketPort,
        paging_limit=args.pagingLimit,
        paging_limit_max=args.pagingLimitMax,
    )
    query_ws_app = create_query_ws_app(registry, query_security)

    dg = await DispatchGroup.create()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, dg.cancel)
        except NotImplementedError:
            # Windows exposes no asyncio signal-handler API; fall back to the
            # C-level handler and hop back onto the loop thread so Ctrl-C
            # still cancels cleanly instead of raising through the loop.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(dg.cancel))

    await dg.dispatch(
        go_registry_servers(
            dg, registration_app, query_app, query_ws_app, args,
        ),
    )
    await dg.dispatch(run_garbage_collection(dg, registry))
    await dg.dispatch(run_status_reporting(dg, registry, args.statusInterval))

    if args.oauth2:
        await dg.dispatch(
            go_registry_authorizations(dg, query_security, args),
        )

    # A failure in any dispatched task -- most often a listener that could not
    # bind because its port is already taken -- must be reported loudly and
    # must make the process exit non-zero.
    #
    # Logging it at debug level (as the Node's equivalent still does) hides the
    # single most common startup failure: the operator sees only "NMOS Registry
    # stopped", exit status 0, and no listening sockets, with nothing anywhere
    # saying which port was busy. That is a genuinely hard thing to diagnose
    # from the outside, so it is worth the extra handling here.
    try:
        await dg.wait()
    except asyncio.CancelledError:
        # Clean shutdown: SIGINT/SIGTERM called dg.cancel().
        logging.info("registry: shutting down")
    except Done:
        logging.info("registry: shutting down")
    except OSError as exc:
        logging.error(
            "registry: a listener failed to start: %s\n"
            "           Registration=%d Query=%d WebSocket=%d on %s\n"
            "           Check whether another process already holds one of "
            "those ports.",
            exc,
            args.registrationPort, args.queryPort, args.queryWebSocketPort,
            args.registryAddr or "0.0.0.0",
        )
        raise SystemExit(1) from exc
    except Exception as exc:
        logging.error("registry: stopped after an error: %s", exc, exc_info=True)
        raise SystemExit(1) from exc


def validate_startup_certs(args: argparse.Namespace) -> None:
    """Fail fast on a mis-configured certificate set.

    Run after argparse and before the event loop, so the operator gets a clear
    diagnostic instead of a TLS handshake failure at the first connection.
    Mirrors ``nmos_node.py::validate_startup_certs``, including its rule that
    each per-interface trust anchor must itself chain to the global
    ``--trustedRootCA`` set — a mis-issued anchor would otherwise be
    discovered only when a legitimate client was rejected.
    """
    import os

    from nmos.cert_check import CertCheckError, check_certificate, check_trusted_ca

    if args.registryDisableTLS:
        return

    if not args.registryCertificate or not args.registryKey:
        raise SystemExit(
            "CONFIG: TLS is enabled but --registryCertificate / --registryKey "
            "were not supplied. Pass both, or run with --registryDisableTLS.",
        )

    for role, path in (
        ("--registryCertificate", args.registryCertificate),
        ("--registryKey", args.registryKey),
    ):
        if not os.path.isfile(path):
            raise SystemExit(f"CONFIG: {role} is not accessible: {path!r}")

    interface_cas = [
        ("--registrationTrustedRootCA", args.registrationTrustedRootCA),
        ("--queryTrustedRootCA", args.queryTrustedRootCA),
    ]

    if any(cas for _role, cas in interface_cas):
        if not args.trustedRootCA:
            raise SystemExit(
                "CONFIG: --trustedRootCA is required to validate the "
                "per-interface trusted root CAs",
            )
        for path in args.trustedRootCA:
            if not os.path.isfile(path):
                raise SystemExit(
                    f"CONFIG: --trustedRootCA is not accessible: {path!r}",
                )
        for role, cas in interface_cas:
            for path in cas:
                if not os.path.isfile(path):
                    raise SystemExit(
                        f"CONFIG: {role} is not accessible: {path!r}",
                    )
                try:
                    check_trusted_ca(args.trustedRootCA, path)
                except CertCheckError as exc:
                    raise SystemExit(
                        f"CONFIG: {role} {path!r} is not valid based on "
                        f"global --trustedRootCA: {exc}",
                    ) from exc

    if args.trustedRootCA:
        try:
            check_certificate(
                args.trustedRootCA,
                args.registryCertificate,
                args.registryKey,
                args.registrySerialNumber,
            )
        except CertCheckError as exc:
            raise SystemExit(
                f"CONFIG: --registryCertificate is not valid: {exc}",
            ) from exc


def run() -> None:
    args = parse_args()
    setup_logging(args)
    validate_startup_certs(args)
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
    logging.info("NMOS Registry stopped")


if __name__ == "__main__":
    run()
