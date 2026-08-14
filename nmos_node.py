#!/usr/bin/env python3
# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NMOS Node application.

Production-grade NMOS Node that:
- Parses CLI flags
- Initializes a Node with resources from JSON config files
- Runs an HTTP/HTTPS server via aiohttp
- Registers with an NMOS registry (heartbeat loop)
- Handles graceful shutdown via DispatchGroup

Usage:
    python3 nmos_node.py --nodeSerialNumber SNX12345 --nodeConfig config1
    python3 nmos_node.py --rdsHost 192.168.1.50 --rdsRegistrationPort 8444 --rdsDisableTLS
    python3 nmos_node.py --nodeDisableTLS --nodeAddr 0.0.0.0 --nodePort 5050
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import os
import signal
import socket
import ssl
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from typing import Any

from aiohttp import web

from nmos.api.tr10_tls import apply_tr10_tls_restrictions
from nmos.ip import Addr, new_addr_from_string

# Access-log format for the node API: aiohttp's default plus ``%Tf`` — the
# time taken to serve each request, in seconds (floating fraction). aiohttp's
# default format omits request duration, so the node access log could only be
# read for completion timestamps; appending ``%Tf`` lets per-request handler
# latency (e.g. a slow PATCH) be read straight from nmos-node.log without any
# external tooling.
_NODE_ACCESS_LOG_FORMAT = (
    '%a %t "%r" %s %b "%{Referer}i" "%{User-Agent}i" %Tf'
)

# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def _host_arg(value: str) -> str:
    """argparse ``type`` for host / address options.

    Strips surrounding whitespace so a stray space in a launch script or
    env var — e.g. ``--rdsHost ' 192.168.1.1'`` from a default like
    ``"${3:- 192.168.1.1}"`` — cannot turn a valid IP into an unresolvable
    hostname (aiohttp would otherwise fail with "Name or service not
    known"). Applied to the network host/address args; ports use
    ``type=int``, which already tolerates surrounding whitespace.
    """
    return value.strip()


def _resolve_leg_address(host: str) -> str:
    """Return ``host`` as a numeric address, resolving it if it is a name.

    A leg carries an *address*, not a name: it becomes the ``SourceIp`` /
    ``InterfaceIp`` of IS-05 transport parameters, and
    ``activation_engine._get_unused_multicast_address_ipv4`` parses it as a
    dotted quad to derive each sender's multicast group
    (``239.<index+1>.<octet3>.<octet4>``).

    Under TLS ``--nodeAddr`` is a *certificate name* (``XYZ-SNX00001``),
    because the advertised hrefs have to match a DNS SAN. Stored unresolved,
    that name parsed as zero octets, so every Node on the rig silently derived
    the same group (``239.<index+1>.0.0``) and two Nodes streamed into each
    other's group. Resolving here keeps the name for the hrefs — the caller
    passes ``host`` on untouched — while the leg gets a real address.
    """
    try:
        ipaddress.ip_address(host)
        return host  # already numeric (v4 or v6)
    except ValueError:
        pass

    try:
        resolved = socket.gethostbyname(host)
    except OSError as exc:
        print(
            f"nmos_node.py: WARNING: '{host}' does not resolve to an IPv4 "
            f"address ({exc}). IS-05 transport-parameter auto-resolution will "
            f"fall back to 0.0.0.0, so every Node derives the same multicast "
            f"group. Pass a resolvable name or a numeric --nodeAddr.",
        )
        return host

    return resolved


def _leg_addr(value: str) -> Addr | None:
    """``value`` as a typed address, or None when it is not one.

    ``IPv4Settings.address`` is declared ``Addr | None`` and every consumer
    reads it as ``str(leg.ipv4.address) if leg.ipv4.address else "0.0.0.0"``, so
    a raw string satisfied them by accident while contradicting the type.

    None rather than an exception on the unparseable path, because
    ``_resolve_leg_address`` deliberately returns the *unresolved name* when DNS
    fails rather than stopping the Node -- so this can be handed a hostname, and
    turning a warned-about degraded start into a crash would be worse than the
    thing being fixed. It also makes the code do what that warning already
    says: the address falls back to ``0.0.0.0`` rather than a name reaching an
    IS-05 ``source_ip``, where it would never have been valid.
    """
    try:
        return new_addr_from_string(value)
    except Exception:
        return None


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(
        description="NMOS Node Server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # --- Registry (single, simplified from rds0/rds1/rds2) ---
    g = p.add_argument_group("Registry (RDS)")
    g.add_argument("--rdsHost", type=_host_arg, default="",
                   help="RDS server host or IP (empty=no registry)")
    g.add_argument("--rdsRegistrationPort", type=int, default=8447, help="RDS registration port")
    g.add_argument("--rdsQueryPort", type=int, default=8446,
                   help="RDS query API port (controller UI bootstrap)")
    g.add_argument("--rdsWebSocketPort", type=int, default=8448,
                   help="RDS WebSocket port (controller UI live updates)")
    g.add_argument("--rdsCertificateName", default="Example.Company.Device.Server.example.com",
                   help="RDS server TLS certificate name")
    g.add_argument("--rdsTrustedRootCA", action="append", default=None,
                   help="Trusted root CA for RDS server (PEM path; "
                        "may be repeated to trust multiple roots)")
    g.add_argument("--rdsClientCertificate", default="", help="Client certificate (*.chain.pem)")
    g.add_argument("--rdsClientKey", default="", help="Client private key")
    g.add_argument("--rdsDisableTLS", action="store_true", help="Disable TLS for registry")
    g.add_argument(
        "--rdsDistributed", action="store_true",
        help="The --rds entries are members of ONE distributed registry "
             "sharing state (the registry's own --distributed mode), rather "
             "than independent registries. Only the Controller needs telling: "
             "the Node discovers it at runtime from the heartbeat response "
             "(Behaviour - Registration.md:124). Clustered, a failover changes "
             "nothing: the new member holds the same state, so there is "
             "nothing to invalidate. Independent, the Controller drops every "
             "subscription, empties ALL SIX resource kinds and refetches them "
             "from the new registry -- otherwise the cache becomes the union "
             "of two registries' contents and shows resources that exist "
             "nowhere. Defaults off, which is the recoverable direction: a "
             "needless refetch rather than phantom resources.",
    )
    g.add_argument(
        "--rds", action="append", default=None, metavar="SPEC",
        help="Additional registry, repeatable up to 5 times, for failover "
             "against a distributed registry. Comma-separated key=value "
             "fields: host, registrationPort, queryPort, wsPort, certName, "
             "ca (repeatable), cert, key, disableTLS. Anything omitted is "
             "inherited from the --rds* flags above, so "
             "'--rds host=10.0.0.2' is a complete entry when members share "
             "ports and trust material. Clients move to the next entry when "
             "the current registry fails and stay there; they do not fail "
             "back. Without --rds the --rds* flags describe one registry, "
             "exactly as before.",
    )

    # --- Node server ---
    g = p.add_argument_group("Node Server")
    g.add_argument("--nodeAddr", type=_host_arg, default="127.0.0.1",
                   help="Node server bind address")
    g.add_argument("--nodePort", type=int, default=5050, help="Node server port")
    g.add_argument("--nodeCertificate", default="", help="Server certificate (*.chain.pem)")
    g.add_argument("--nodeKey", default="", help="Server private key")
    g.add_argument("--nodeTrustedRootCA", action="append", default=None,
                   help="Trusted root CA for client auth (PEM path; "
                        "may be repeated to trust multiple roots)")
    g.add_argument("--controlTrustedRootCA", action="append", default=None,
                   help="Trusted root CA for IS-05/IS-11 control-endpoint "
                        "client auth (PEM path; may be repeated). When "
                        "non-empty: IS-05/IS-11 are split onto --controlPort "
                        "with their own SSL context using this CA set, AND "
                        "the embedded controller validates remote IS-05/IS-11 "
                        "server certs against this CA set. When empty: "
                        "IS-05/IS-11 share --nodePort and --nodeTrustedRootCA.")
    g.add_argument("--controlPort", type=int, default=0,
                   help="Port for the split IS-05/IS-11 listener; only used "
                        "when --controlTrustedRootCA is set. Defaults to "
                        "--nodePort + 1 when 0 and --controlTrustedRootCA "
                        "is set.")
    g.add_argument("--nodeClientCertificate", default="",
                   help="Client certificate the embedded controller presents "
                        "to remote Node-level endpoints (Node API, Node "
                        "Reservation acquire/renew/release/keepalive) for "
                        "mTLS. Empty = no client cert presented (no mTLS on "
                        "this path). Distinct from --rdsClientCertificate, "
                        "which is RDS-only.")
    g.add_argument("--nodeClientKey", default="",
                   help="Private key for --nodeClientCertificate.")
    g.add_argument("--controlClientCertificate", default="",
                   help="Client certificate the embedded controller presents "
                        "to remote IS-05/IS-11 endpoints for mTLS. Falls "
                        "back to --nodeClientCertificate when empty (matches "
                        "the --controlTrustedRootCA fallback semantics). "
                        "Empty everywhere = no client cert presented.")
    g.add_argument("--controlClientKey", default="",
                   help="Private key for --controlClientCertificate. Falls "
                        "back to --nodeClientKey when empty.")
    g.add_argument("--nodeDisableTLS", action="store_true", help="Disable TLS on node server")
    g.add_argument("--nodeOptionalClientAuth", action="store_true",
                   help="Allow unauthenticated clients read-only access "
                        "(method-aware mTLS: SSL context comes up with "
                        "verify_mode=CERT_OPTIONAL; the application-level "
                        "client_auth_middleware then rejects state-changing "
                        "methods unless a peer cert was presented).")
    g.add_argument("--nodeControlPort", type=int, default=0,
                   help="Controller UI port (0=disabled); uses --rdsQueryPort / "
                        "--rdsWebSocketPort for registry data")
    g.add_argument("--controllerAdminPassword", default="",
                   help="Admin password for the controller UI login form "
                        "(password only, no user name). "
                        "REQUIRED when --nodeControlPort > 0.")
    g.add_argument("--debug-in-depth", dest="debug_in_depth",
                   action="store_true",
                   help="Enable deep debug tracing on the controller UI: "
                        "per-request trace ids, client-event browser hook, "
                        "snapshot endpoint, and a rotating log file in the "
                        "system temporary directory. "
                        "No-op when --nodeControlPort is 0.")

    # --- Node configuration ---
    g = p.add_argument_group("Node Configuration")
    g.add_argument("--nodeSerialNumber", default="SNX12345", help="Node serial number")
    g.add_argument("--nodeConfig", default="config1", help="Config name or JSON file path")
    g.add_argument("--wallGroup", type=int, default=0, help="Display Wall base group")
    g.add_argument("--ipmx", action="store_true", help="Enable IPMX mode")
    g.add_argument("--privacy", action=argparse.BooleanOptionalAction, default=True,
                   help="Transport privacy encryption (PEP)")

    # --- Capability control ---
    g = p.add_argument_group("Capabilities")
    g.add_argument("--noSenderCaps", action="store_true",
                   help="Strip all sender capabilities (emulates pre-BCP-004-01 nodes)")
    g.add_argument("--noSenderVideoRaw", action="store_true",
                   help="Disable video/raw constraint sets (meta:enabled=false)")
    g.add_argument("--noSenderAudioRaw", action="store_true",
                   help="Disable audio/L* constraint sets (meta:enabled=false)")

    # --- OAuth2 ---
    g = p.add_argument_group("OAuth2")
    g.add_argument("--oauth2", action="store_true", help="Enable OAuth2.0 authorization")
    g.add_argument("--oauth2Host", type=_host_arg, default="",
                   help="OAuth2 server host")
    g.add_argument("--oauth2Port", type=int, default=4444, help="OAuth2 server port")
    g.add_argument("--oauth2CertificateName",
                   default="Example.Company.Device.Server.example.com",
                   help="OAuth2 TLS certificate name")
    g.add_argument("--oauth2TrustedRootCA", action="append", default=None,
                   help="OAuth2 trusted root CA (PEM path; "
                        "may be repeated to trust multiple roots)")
    g.add_argument("--oauth2DisableTLS", action="store_true", help="Disable TLS for OAuth2")
    # Controller-side auth_code flow config. Exposes ``oauth2ClientId`` /
    # ``oauth2ClientSecret``. ``oauth2ApiSelector`` is the IS-10
    # / RFC 8414 §3.1 ``api_selector`` — the path component of the
    # issuer identifier (Hydra leaves it empty; Keycloak uses
    # ``realms/<realm>``). When --oauth2ClientId is left empty the
    # controller derives ``controller-<nodeSerialNumber>`` so a
    # single ``--nodeSerialNumber SNX00001`` is enough config.
    g.add_argument("--oauth2ClientId", default="",
                   help="OAuth2 client_id used by the embedded controller "
                        "to initiate the authorization_code flow. "
                        "Default: 'controller-<nodeSerialNumber>'.")
    g.add_argument("--oauth2ClientSecret", default="",
                   help="OAuth2 client_secret used alongside "
                        "--oauth2ClientId for the auth_code exchange.")
    g.add_argument("--oauth2ApiSelector", default="realms/TR-10-SEC",
                   help="IS-10 / RFC 8414 §3.1 'api_selector' — the "
                        "path component of the issuer identifier. "
                        "Empty for ORY Hydra; 'realms/<realm>' for "
                        "Keycloak (default: 'realms/TR-10-SEC').")
    # TR-10-SEC §12.4: OAuth 2.0 Audience Identification Mode (OAIM).
    # Selects how the Node validates the ``aud`` claim of incoming
    # Bearer tokens. Reference-node already implements all three modes
    # in ``nmos/oauth2/__init__.py:498-565`` (with RFC 4592 wildcards);
    # this flag picks which one applies at runtime and drives the
    # ``urn:x-vsf:tag:tr-10-sec:oaim-config/v1.0`` tag emitted on
    # ``GET /x-nmos/node/v1.3/self``.
    g.add_argument("--oauth2AudienceMode", default="serial",
                   choices=["serial", "cert", "either"],
                   help="OAuth 2.0 Audience Identification Mode "
                        "(TR-10-SEC §12.4). 'serial' = aud entries "
                        "match the BCP-002-02 instance identifier AND "
                        "the TLS server cert SAN (default). "
                        "'cert' = aud entries match the TLS server "
                        "cert CN/SAN with RFC 4592 wildcards. "
                        "'either' = try both per entry.")

    # --- Logging ---
    g = p.add_argument_group("Logging")
    g.add_argument("--logFile", default="/tmp/nmos-node.log",
                   help="Log file path (empty=disable file logging)")

    # --- Global TLS ---
    p.add_argument("--trustedRootCA", action="append", default=None,
                   help="Global trusted root CA (PEM path; "
                        "may be repeated to trust multiple roots). "
                        "Used as the fallback for OUTGOING-TLS contexts "
                        "when their per-role flag is unset: "
                        "--rdsTrustedRootCA (registry client) and "
                        "--oauth2TrustedRootCA (OAuth AS client). "
                        "Also referenced by the config-validation step "
                        "that verifies each --nodeTrustedRootCA entry. "
                        "The INCOMING-mTLS listeners (--nodeTrustedRootCA, "
                        "--controlTrustedRootCA) do NOT fall back to it "
                        "at runtime — those listeners have no trust store "
                        "when their per-role flag is empty.")
    # --- TR-10-SEC §12.14 Global CRL ---
    # Default: no CRL — Node performs cert verification WITHOUT
    # revocation checks. When set, the PEM file at this path is
    # concatenated into every SSL context's verify store and
    # VERIFY_CRL_CHECK_LEAF is enabled, so any leaf cert whose
    # serial number appears in the bundle is rejected at TLS-verify
    # time. Per §12.14 the GCRL may be a concatenation of multiple
    # per-CA CRLs; OpenSSL matches each CRL block to its issuer CA
    # from the loaded trust store.
    p.add_argument("--gcrl", type=str, default=None,
                   help="Path to a Global CRL PEM bundle (one or more "
                        "X509 CRL blocks, each signed by a configured "
                        "CA). When set, applied to every TLS verify "
                        "store; VERIFY_CRL_CHECK_LEAF is enabled. "
                        "When unset (default), no CRL checking.")

    ns = p.parse_args()

    # ``action="append"`` leaves the attribute as ``None`` when the
    # flag is omitted; normalise to an empty list so callers can treat
    # every CA option uniformly as ``list[str]``.
    for attr in (
        "rdsTrustedRootCA",
        "nodeTrustedRootCA",
        "controlTrustedRootCA",
        "oauth2TrustedRootCA",
        "trustedRootCA",
    ):
        if getattr(ns, attr) is None:
            setattr(ns, attr, [])

    # ``--controlPort`` is only meaningful in split-listener mode
    # (i.e. when ``--controlTrustedRootCA`` is non-empty). Default it
    # to ``nodePort + 1`` so operators get a working split-port setup
    # by setting the CA flag alone. When ``--controlTrustedRootCA``
    # is empty the value is unused and left untouched.
    if ns.controlTrustedRootCA and ns.controlPort == 0:
        ns.controlPort = ns.nodePort + 1

    return ns


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(args: argparse.Namespace) -> None:
    """Configure logging with optional rotating file handler."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler (always active)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console)

    # File handler (if logFile specified)
    if args.logFile:
        try:
            fh = RotatingFileHandler(
                args.logFile, maxBytes=1_000_000, backupCount=3,
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            root.addHandler(fh)
        except OSError as exc:
            print(f"Warning: cannot open log file {args.logFile}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# TLS helpers
# ---------------------------------------------------------------------------

def registry_selector(args: argparse.Namespace) -> Any:
    """The ordered registries this process should use, or None for standalone.

    Each caller gets its **own** selector, deliberately. The Node's
    registration loop and the Controller are independent clients that happen to
    share a process, and every member of a distributed registry serves the same
    shared state -- so the two of them sitting on different members is normal
    operation, not a fault to be corrected.

    Sharing one selector would merge their failure domains: six Controller-side
    WebSocket failures would move the Node's registration even though its own
    connection was healthy, and vice versa. Keeping them separate means each
    fails over on evidence about the registry *it* is talking to.

    Returns None when no registry is configured at all (``--rdsHost ""``),
    which is the standalone mode the Controller seeds from the local Node.
    """
    from nmos.rds_targets import (
        RegistrySelector, build_targets, target_from_scalars,
    )

    default = target_from_scalars(
        host=args.rdsHost,
        registration_port=args.rdsRegistrationPort,
        query_port=args.rdsQueryPort,
        ws_port=args.rdsWebSocketPort,
        tls=not args.rdsDisableTLS,
        certificate_name=args.rdsCertificateName,
        trusted_root_ca=_ca_list(args.rdsTrustedRootCA, args.trustedRootCA),
        client_certificate=args.rdsClientCertificate,
        client_key=args.rdsClientKey,
    )
    targets = build_targets(getattr(args, "rds", None), default)
    selector = RegistrySelector(targets) if targets else None
    if selector is not None and len(selector) > 1:
        logging.info(
            "registry: %d registries configured, starting with %s "
            "(failover moves forward only, no failback)",
            len(selector), selector.current.label,
        )
    return selector


def _ca_list(specific: list[str], fallback: list[str]) -> list[str]:
    """Resolve the effective list of trusted-root-CA paths for one service.

    Preserves the pre-multi-CA semantics of ``args.X or args.trustedRootCA``:
    a non-empty per-service list fully **overrides** the global; only when
    the per-service list is empty does the global list apply. The two are
    never merged — that would change today's behaviour.
    """
    return specific if specific else fallback


def build_server_ssl_context(args: argparse.Namespace) -> ssl.SSLContext | None:
    """Build SSL context for the Node-API aiohttp server (the listener
    on ``--nodePort``). Returns None if TLS disabled.

    Client-cert trust anchor is ``--nodeTrustedRootCA``. Paired with
    ``build_control_server_ssl_context`` which builds the analogous
    context for the optional IS-05/IS-11 split listener.
    """
    if args.nodeDisableTLS:
        return None

    if not args.nodeCertificate or not args.nodeKey:
        logging.warning("TLS enabled but no certificate/key provided — running without TLS")
        return None

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    apply_tr10_tls_restrictions(ctx, gcrl_path=getattr(args, "gcrl", None))
    ctx.load_cert_chain(args.nodeCertificate, args.nodeKey)

    if args.nodeTrustedRootCA:
        for ca in args.nodeTrustedRootCA:
            ctx.load_verify_locations(ca)
        if args.nodeOptionalClientAuth:
            ctx.verify_mode = ssl.CERT_OPTIONAL
        else:
            ctx.verify_mode = ssl.CERT_REQUIRED

    return ctx


def build_controller_ui_ssl_context(
    args: argparse.Namespace,
) -> ssl.SSLContext | None:
    """Build SSL context for the embedded Controller UI listener
    (``--nodeControlPort``).

    **Rule: mTLS → TLS for the Controller UI.** The Controller UI is
    a browser-facing admin endpoint, not part of the protocol
    surface (Node API, IS-05/IS-08/IS-11) that TR-10-SEC's mTLS
    requirement covers. Under Configuration C the Node listener runs
    mTLS; this listener takes that same TLS context and **downgrades
    it to plain server-TLS** by forcing ``verify_mode=CERT_NONE``,
    so a browser without a client cert can still reach
    ``https://<host>:<nodeControlPort>/controller/``. Admin
    authentication on the UI is handled at the application layer via
    OAuth 2.0 (Config B/C) or the local-admin password.

    Returns ``None`` when TLS is disabled.
    """
    ctx = build_server_ssl_context(args)
    if ctx is None:
        return None
    # Apply the mTLS-to-TLS conversion rule. The Node listener's
    # ``verify_mode`` may be CERT_REQUIRED (Config A/C) or
    # CERT_OPTIONAL (NAP=1 under Config A); both downgrade to
    # CERT_NONE on the Controller UI.
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def build_control_server_ssl_context(
    args: argparse.Namespace,
) -> ssl.SSLContext | None:
    """Build SSL context for the IS-05/IS-11 split listener on
    ``--controlPort``. Returns None when no split is in effect (i.e.
    when ``--controlTrustedRootCA`` is empty) or TLS is disabled.

    Uses the same server cert/key as the Node listener; the only
    difference is the client-cert trust anchor — ``--controlTrustedRootCA``
    here rather than ``--nodeTrustedRootCA``. This is what lets the
    operator authorise a different population of client certs (typically
    controllers issued by a separate CA hierarchy) to drive IS-05/IS-11.
    """
    if not args.controlTrustedRootCA:
        return None
    if args.nodeDisableTLS:
        return None
    if not args.nodeCertificate or not args.nodeKey:
        return None

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    apply_tr10_tls_restrictions(ctx, gcrl_path=getattr(args, "gcrl", None))
    ctx.load_cert_chain(args.nodeCertificate, args.nodeKey)
    for ca in args.controlTrustedRootCA:
        ctx.load_verify_locations(ca)
    if args.nodeOptionalClientAuth:
        ctx.verify_mode = ssl.CERT_OPTIONAL
    else:
        ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def build_registry_ssl_context(args: argparse.Namespace) -> ssl.SSLContext | None:
    """Build SSL context for the registry client. Returns None if TLS disabled."""
    if args.rdsDisableTLS:
        return None

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    apply_tr10_tls_restrictions(ctx, gcrl_path=getattr(args, "gcrl", None))

    if args.rdsClientCertificate and args.rdsClientKey:
        ctx.load_cert_chain(args.rdsClientCertificate, args.rdsClientKey)

    cas = _ca_list(args.rdsTrustedRootCA, args.trustedRootCA)
    if cas:
        for ca in cas:
            ctx.load_verify_locations(ca)
    else:
        ctx.load_default_certs()

    return ctx


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

def resolve_config_path(config_name: str) -> str:
    """Map config name to JSON file path.

    Examples:
        "config1"           → nmos/node/config/builtin/config1.json
        "/path/custom.json" → /path/custom.json (absolute path passthrough)
        "my.json"           → my.json (relative path with .json extension)
    """
    if os.path.isabs(config_name) or config_name.endswith(".json"):
        return config_name
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "nmos", "node", "config", "builtin", f"{config_name}.json")


# ---------------------------------------------------------------------------
# Capability post-processing
# ---------------------------------------------------------------------------

def apply_capability_flags(node: Any, args: argparse.Namespace) -> None:
    """Apply --noSenderCaps, --noSenderVideoRaw, --noSenderAudioRaw flags.

    These modify sender capabilities after ConfigBuilder has loaded them:
    - noSenderCaps: strip all capabilities (emulates pre-BCP-004-01 nodes)
    - noSenderVideoRaw: set meta:enabled=false on video/raw constraint sets
    - noSenderAudioRaw: set meta:enabled=false on audio/L* constraint sets
    """
    if not (args.noSenderCaps or args.noSenderVideoRaw or args.noSenderAudioRaw):
        return

    for _, sender in node.senders:
        if args.noSenderCaps:
            # Strip all capabilities — set Caps to zero (undefined)
            if hasattr(sender, 'Caps'):
                sender.Caps.set_to_zero()
            continue

        # For VideoRaw/AudioRaw: disable matching constraint sets via meta:enabled
        if not hasattr(sender, 'Caps') or not sender.Caps.defined:
            continue

        caps_val = sender.Caps.value
        if not hasattr(caps_val, 'ConstraintSets') or not caps_val.ConstraintSets.defined:
            continue

        for cs in caps_val.ConstraintSets.value:
            cs_val = cs
            if not hasattr(cs_val, 'MetaFormat'):
                continue
            if not cs_val.MetaFormat.defined:
                continue

            fmt = str(cs_val.MetaFormat.value)

            if args.noSenderVideoRaw and fmt == "urn:x-nmos:format:video":
                # Check if this is a video/raw constraint set
                if cs_val.Constraints.defined:
                    for key, constraint in cs_val.Constraints._inner.items():
                        if str(key) == "urn:x-nmos:cap:format:media_type":
                            # This is a media_type constraint — check if video/raw
                            # Disable the entire constraint set
                            cs_val.MetaEnabled.value = False
                            break

            if args.noSenderAudioRaw and fmt == "urn:x-nmos:format:audio":
                # Check if this is an audio/L* constraint set
                if cs_val.Constraints.defined:
                    for key, constraint in cs_val.Constraints._inner.items():
                        if str(key) == "urn:x-nmos:cap:format:media_type":
                            cs_val.MetaEnabled.value = False
                            break


# ---------------------------------------------------------------------------
# Server tasks
# ---------------------------------------------------------------------------

async def go_node_server(
    dg: Any,
    app: web.Application,
    args: argparse.Namespace,
    *,
    control_app: web.Application | None = None,
) -> None:
    """Run the HTTP/HTTPS server(s).

    Blocks until the DispatchGroup is cancelled (signal or error),
    then cleanly shuts down both server(s).

    When ``control_app`` is ``None`` (default): a single listener on
    ``--nodePort`` serves all routes — the pre-``--controlTrustedRootCA``
    topology. When ``control_app`` is provided: ``app`` is the Node-API
    app on ``--nodePort`` (with ``--nodeTrustedRootCA``) and
    ``control_app`` is the IS-05/IS-11 app on ``--controlPort`` (with
    ``--controlTrustedRootCA``).
    """
    ssl_ctx = build_server_ssl_context(args)

    # ``shutdown_timeout`` defaults to 60 s — too long for a dev /
    # test box. Lowering to 2 s means a single Ctrl-C ends the process
    # promptly even when the browser holds long-lived connections
    # (SSE on /controller/api/status-events, primarily). In-flight
    # NMOS API requests are sub-second and won't be impacted; the
    # SSE stream gets closed abruptly and the browser auto-reconnects
    # on the next page load.
    runner = web.AppRunner(
        app, shutdown_timeout=2.0, access_log_format=_NODE_ACCESS_LOG_FORMAT,
    )
    await runner.setup()
    control_runner: web.AppRunner | None = None
    if control_app is not None:
        control_runner = web.AppRunner(control_app, shutdown_timeout=2.0)
        await control_runner.setup()
    try:
        host = args.nodeAddr or "0.0.0.0"
        site = web.TCPSite(runner, host, args.nodePort, ssl_context=ssl_ctx)
        await site.start()

        scheme = "https" if ssl_ctx else "http"
        display_host = args.nodeAddr or socket.gethostbyname(socket.gethostname())
        port = args.nodePort

        if control_runner is not None:
            control_ssl_ctx = build_control_server_ssl_context(args)
            control_site = web.TCPSite(
                control_runner, host, args.controlPort,
                ssl_context=control_ssl_ctx,
            )
            await control_site.start()
            control_scheme = "https" if control_ssl_ctx else "http"
            print(
                f"\nNMOS Node server running on {scheme}://{display_host}:{port}",
            )
            print(
                f"  IS-04:           {scheme}://{display_host}:{port}/x-nmos/node/v1.3/",
            )
            print(
                f"  IS-05 (control): {control_scheme}://{display_host}:{args.controlPort}/x-nmos/connection/v1.1/",
            )
            print(
                f"  IS-11 (control): {control_scheme}://{display_host}:{args.controlPort}/x-nmos/streamcompatibility/v1.0/",
            )
            print()
        else:
            print(f"\nNMOS Node server running on {scheme}://{display_host}:{port}")
            print(f"  IS-04: {scheme}://{display_host}:{port}/x-nmos/node/v1.3/")
            print(f"  IS-05: {scheme}://{display_host}:{port}/x-nmos/connection/v1.1/")
            print()

        # Block until DispatchGroup is cancelled
        await dg.done()
    finally:
        # Disarm any activation scheduled for a moment that will never come.
        # These timers are not owned by the dispatch group, so nothing else
        # stops them, and one firing into a half-dismantled Node is worse than
        # one that simply never happens.
        from nmos.node.activation_engine import cancel_pending_activations
        cancel_pending_activations(app["node"])

        await runner.cleanup()
        if control_runner is not None:
            await control_runner.cleanup()


async def go_controller_server(
    dg: Any, node: Any, args: argparse.Namespace,
) -> None:
    """Run the embedded NMOS Controller UI on --nodeControlPort.

    Assembles the resource cache, remote client (mTLS towards other
    Nodes), RDS query bootstrap client, and RDS WebSocket subscriber,
    then serves the controller aiohttp app on args.nodeControlPort.
    """
    from nmos.controller import create_controller_app
    from nmos.controller.api_client import RemoteNodeClient
    from nmos.controller.cache import ResourceCache
    from nmos.controller.rds_query import RdsQueryClient, RdsQueryConfig
    from nmos.controller.rds_websocket import (
        RdsWebSocketClient, RdsWebSocketConfig,
    )

    # Build the outbound (controller → remote Node) TLS context(s).
    #
    # Trust anchors:
    #   * ``node_outbound_ssl``    validates remote Node-API + Node
    #                              Reservation server certs (anchor:
    #                              --nodeTrustedRootCA).
    #   * ``control_outbound_ssl`` validates remote IS-05/IS-11 server
    #                              certs (anchor: --controlTrustedRootCA,
    #                              with --nodeTrustedRootCA as fallback).
    #
    # Client certs (mTLS, when the remote requires one):
    #   * Node-level outbound presents --nodeClientCertificate /
    #     --nodeClientKey when supplied; nothing otherwise.
    #   * Control-level outbound presents --controlClientCertificate /
    #     --controlClientKey, falling back to the node pair when empty
    #     (mirrors the CA fallback). Empty everywhere = no client cert.
    #
    # ``--rdsClientCertificate`` is intentionally NOT loaded here — it
    # identifies us to the RDS registry only. Reusing it for outbound
    # to remote Nodes was a pre-existing bug; the per-purpose flags
    # above are the correct surface.
    def _build_outbound(
        ca_list: list[str],
        cert: str,
        key: str,
    ) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        apply_tr10_tls_restrictions(ctx, gcrl_path=getattr(args, "gcrl", None))
        if ca_list:
            for ca in ca_list:
                ctx.load_verify_locations(ca)
        else:
            ctx.load_default_certs()
        if cert and key:
            ctx.load_cert_chain(cert, key)
        return ctx

    node_outbound_ssl: ssl.SSLContext | None = None
    control_outbound_ssl: ssl.SSLContext | None = None
    if not args.nodeDisableTLS:
        node_outbound_ssl = _build_outbound(
            _ca_list(args.nodeTrustedRootCA, args.trustedRootCA),
            args.nodeClientCertificate,
            args.nodeClientKey,
        )
        if args.controlTrustedRootCA:
            control_cert = (
                args.controlClientCertificate or args.nodeClientCertificate
            )
            control_key = args.controlClientKey or args.nodeClientKey
            control_outbound_ssl = _build_outbound(
                _ca_list(args.controlTrustedRootCA, args.trustedRootCA),
                control_cert,
                control_key,
            )

    cache = ResourceCache()
    remote_client = RemoteNodeClient(
        ssl_context=node_outbound_ssl,
        control_ssl_context=control_outbound_ssl,
    )

    if not args.controllerAdminPassword:
        logging.warning(
            "controller: --controllerAdminPassword not set; controller UI "
            "will NOT start",
        )
        await dg.done()
        return

    # Debug tracing — when ``--debug-in-depth`` is set, the controller
    # app also mounts the debug endpoints and routes a verbose event
    # trail to a per-(addr,port) log file. Absent the flag, nothing
    # extra is registered.
    debug_log_path: str | None = None
    if args.debug_in_depth:
        host_safe = (args.nodeAddr or "0.0.0.0").replace(":", "-")
        debug_log_path = os.path.join(
            tempfile.gettempdir(),
            f"nmos-controller-{host_safe}-{args.nodeControlPort}.log",
        )

    # Build the OAuth2 config when --oauth2 is enabled. The client_id
    # defaults to ``controller-<nodeSerialNumber>`` so a single
    # ``--nodeSerialNumber`` suffices when the operator follows the
    # nmos_keycloak.py provisioning convention. ``--oauth2ClientId``
    # explicitly overrides.
    oauth2_config = None
    if args.oauth2:
        from nmos.controller.oauth2 import OAuth2Config
        client_id = (
            args.oauth2ClientId
            or f"controller-{args.nodeSerialNumber}"
        )
        oauth2_scheme = "http" if args.oauth2DisableTLS else "https"
        # Issuer URL = host:port + the api_selector path. With Hydra
        # api_selector is empty; with Keycloak it's 'realms/<realm>'.
        # Strip leading/trailing '/' so concatenation always yields
        # a single '/' between host and path.
        api_selector = (args.oauth2ApiSelector or "").strip("/")
        issuer_path = f"/{api_selector}" if api_selector else ""
        oauth2_config = OAuth2Config(
            issuer=(
                f"{oauth2_scheme}://{args.oauth2Host}:{args.oauth2Port}"
                f"{issuer_path}"
            ),
            client_id=client_id,
            client_secret=args.oauth2ClientSecret,
            api_selector=api_selector,
            ca_bundle=tuple(
                _ca_list(args.oauth2TrustedRootCA, args.trustedRootCA),
            ),
        )

    app = create_controller_app(
        node, cache=cache, remote_client=remote_client,
        admin_password=args.controllerAdminPassword,
        debug_log_path=debug_log_path,
        oauth2_config=oauth2_config,
    )

    # Controller UI server runs server-TLS-only (no mTLS) even under
    # Configuration C — the UI is a browser endpoint, not part of the
    # protocol surface that TR-10-SEC's mTLS requirement covers.
    # See ``build_controller_ui_ssl_context`` for the rationale.
    # ``shutdown_timeout=2.0`` keeps Ctrl-C responsive — the
    # controller's SSE stream (/controller/api/status-events) is the
    # main thing the browser holds open across page reloads, and
    # 60 s of grace would block process exit until either the SSE
    # handler returned or the operator hit Ctrl-C a second time.
    server_ssl = build_controller_ui_ssl_context(args)
    runner = web.AppRunner(
        app, shutdown_timeout=2.0, access_log_format=_NODE_ACCESS_LOG_FORMAT,
    )
    await runner.setup()

    rds_tasks: list[asyncio.Task[Any]] = []
    try:
        host = args.nodeAddr or "0.0.0.0"
        site = web.TCPSite(
            runner, host, args.nodeControlPort, ssl_context=server_ssl,
        )
        await site.start()

        scheme = "https" if server_ssl else "http"
        print(
            f"NMOS Controller UI running on "
            f"{scheme}://{args.nodeAddr}:{args.nodeControlPort}/controller/"
            " (sign in with --controllerAdminPassword; no user name)",
        )

        # Two cache-population modes — registry wins when configured.
        #
        # When --rdsHost is set, the registry is the single source of
        # truth: bootstrap fetches the snapshot, the WS subscription
        # then tracks live deltas. Seeding from the local Node would
        # only be a brief pre-RDS view that ``replace_all`` immediately
        # overwrites — confusing more than it helps.
        #
        # Without --rdsHost, the controller has nowhere to get
        # resources from, so we seed from the local Node so the
        # operator at least sees the Node it's embedded in.
        selector = registry_selector(args)
        if selector is not None:
            # Bootstrap from whichever registry is current. The WebSocket
            # subscriptions below hold this selector -- the Controller's own,
            # separate from the registration loop's -- so a member failing
            # moves all six subscribers together.
            target = selector.current
            query_config = RdsQueryConfig(
                host=target.host,
                port=target.query_port,
                tls=target.tls,
                trusted_root_ca=target.trusted_root_ca,
                client_certificate=target.client_certificate,
                client_key=target.client_key,
            )
            try:
                await RdsQueryClient(query_config).bootstrap(cache)
            except Exception as exc:
                logging.warning("controller bootstrap failed: %s", exc)

            ws_config = RdsWebSocketConfig(
                query_host=target.host,
                query_port=target.query_port,
                ws_host=target.host,
                ws_port=target.ws_port,
                tls=target.tls,
                trusted_root_ca=target.trusted_root_ca,
                client_certificate=target.client_certificate,
                client_key=target.client_key,
            )
            rds_tasks.append(asyncio.create_task(
                RdsWebSocketClient(
                    ws_config, selector, distributed=args.rdsDistributed,
                ).run(dg, cache),
            ))
        else:
            from nmos.controller.local_bootstrap import bootstrap_local_node
            try:
                await bootstrap_local_node(node, cache)
                logging.info(
                    "controller: no RDS configured — seeded cache "
                    "from local Node (%d senders, %d receivers, "
                    "%d sources, %d flows)",
                    len(node.senders), len(node.receivers),
                    len(node.sources), len(node.flows),
                )
            except Exception as exc:
                logging.warning(
                    "controller: local-Node seed failed: %s", exc,
                )

        await dg.done()
    finally:
        for t in rds_tasks:
            t.cancel()
        await runner.cleanup()


async def go_node_registration(
    dg: Any, node: Any, args: argparse.Namespace,
) -> None:
    """Run the registry registration loop."""
    from nmos.node.registry import RegistryClient

    selector = registry_selector(args)
    if selector is None:
        return
    client = RegistryClient(selector, node)
    await client.run(dg)


async def go_node_authorizations(
    dg: Any, node: Any, args: argparse.Namespace,
) -> None:
    """Periodic JWKS public-key cache for inbound OAuth 2.0 validation.

    Implements the full TR-10-SEC §14.3.2 lifecycle via
    :class:`nmos.oauth2.jwks_cache.JWKSCache`: 23h+jitter refresh,
    36h hard invalidation, exponential backoff (1→64s), and fail-closed
    until the first fetch succeeds.
    """
    import aiohttp

    from nmos.oauth2 import JWKS, discover_jwks as _discover_jwks
    from nmos.oauth2.jwks_cache import JWKSCache

    if not args.oauth2Host:
        # No OAuth2 server — just wait until done
        await dg.done()
        return

    scheme = "http" if args.oauth2DisableTLS else "https"
    # Per "NMOS With OAuth2.0" §"Authorization Server Metadata Endpoint",
    # the JWKS location is identified normatively only via the ``jwks_uri``
    # field of the AS metadata document. ``discover_jwks`` walks the three
    # URL forms required by the spec (RFC 8414 §3.1, Keycloak placement,
    # OIDC Discovery 1.0) and follows ``jwks_uri`` to fetch the keys.

    ssl_ctx: ssl.SSLContext | None = None
    if not args.oauth2DisableTLS:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        apply_tr10_tls_restrictions(ssl_ctx)
        cas = _ca_list(args.oauth2TrustedRootCA, args.trustedRootCA)
        if cas:
            for ca in cas:
                ssl_ctx.load_verify_locations(ca)
        else:
            ssl_ctx.load_default_certs()

    connector_ssl: bool | ssl.SSLContext = ssl_ctx if ssl_ctx is not None else False
    connector = aiohttp.TCPConnector(ssl=connector_ssl)
    async with aiohttp.ClientSession(connector=connector) as session:
        # The fetch coroutine waits for the Node to be published before
        # talking to the AS — otherwise the first fetch races the IS-04
        # registration handshake and the AS may not yet know about us.
        async def fetch_jwks() -> JWKS:
            while not node.publish_manager.is_published:
                await asyncio.sleep(1.0)
            return await _discover_jwks(
                scheme=scheme,
                host=args.oauth2Host,
                port=args.oauth2Port,
                api_selector=args.oauth2ApiSelector or "",
                client=session,
            )

        def on_update(jwks: JWKS | None) -> None:
            # ``None`` means "invalidate" — the Node's bearer middleware
            # refuses every authenticated request when the keyset is None.
            node.set_oauth2_public_keys(jwks)

        cache = JWKSCache(fetch=fetch_jwks, on_update=on_update)
        try:
            await cache.run(is_done=lambda: dg.is_done)
        except asyncio.CancelledError:
            return


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    """Main async entry point that dispatches all background tasks."""
    from nmos.api import create_app
    from nmos.errors import Done
    from nmos.crypto import ExclusiveSession
    from nmos.node import Node
    from nmos.node.config import ConfigBuilder
    from nmos.tasks import DispatchGroup

    # Resolve host address
    host = args.nodeAddr
    if not host:
        try:
            host = socket.gethostbyname(socket.gethostname())
        except Exception:
            host = "127.0.0.1"
        args.nodeAddr = host

    # ``host`` stays as given — the Node / Device hrefs must carry the
    # certificate name under TLS. The leg needs the address behind it.
    leg_address = _resolve_leg_address(host)
    if leg_address != host:
        print(f"  Leg address: {host} → {leg_address} (transport parameters)")

    # Resolve interface name from the leg *address*: a name matches no
    # interface address, which silently fell through to "eth0".
    iface_name = Node._resolve_interface_name(leg_address)

    # Initialize Node via node.init() with interfaces, legs, etc.
    from nmos.node.types import Leg, IPv4Settings
    node = Node()
    # Derive Node-side TLS posture from the existing
    # flags. `tls_enabled` simply mirrors what
    # ``build_server_ssl_context`` will do: TLS is on whenever it is
    # not explicitly disabled AND a cert/key pair has been supplied.
    # `client_auth_required` reuses the existing --nodeOptionalClientAuth
    # flag — which the existing ``client_auth_middleware`` already
    # interprets as "method-aware mTLS: state-changing methods require
    # a peer cert, reads do not".
    tls_enabled = (
        not args.nodeDisableTLS
        and bool(args.nodeCertificate)
        and bool(args.nodeKey)
    )
    # TR-10-SEC §8: snapshot the Node's effective security config
    # so the five urn:x-vsf:tag:tr-10-sec:*-config/v1.0 tags are
    # published in ``GET /x-nmos/node/v1.3/self``. The validator 
    # reads them to verify the operator's declared
    # ``--config`` matches what the device actually does.
    from nmos.node.security_tags import compute_security_tags
    security_tags = compute_security_tags(args).to_tags()

    # TR-10-SEC §11: NAP says which policy the device is configured for, RAAM
    # says how it is enforced. With TLS up but no RAAM mechanism at all there
    # is nothing enforcing anything, so the NAP=2 the tags advertise overstates
    # the device: both reads and writes are open to any client that completes
    # the handshake.
    #
    # The tag value is left alone deliberately. §9.1 defines Unrestricted Read
    # Write as "HTTP without TLS", so reporting NAP=0 for an encrypted listener
    # would contradict the specification as squarely as NAP=2 overstates it —
    # the configuration simply is not one of the three policies. What is worth
    # fixing is that it used to happen in silence.
    from nmos.node.security_tags import has_authorization_mechanism
    if tls_enabled and not has_authorization_mechanism(args):
        logging.warning(
            "node: TLS is enabled but no authorization mechanism is "
            "configured — none of --nodeTrustedRootCA, "
            "--nodeOptionalClientAuth or --oauth2.\n"
            "      Traffic is encrypted, but every request including "
            "state-changing ones is accepted from\n"
            "      any client. The Node advertises "
            "nap-config=2 (Restricted Read Write), which it cannot honour "
            "in this\n"
            "      configuration; a TR-10-SEC validator will fail it on "
            "SEC-9.3-2 / SEC-9.3-3.",
        )
    node.init(
        serial_number=args.nodeSerialNumber,
        host=host,
        port=args.nodePort,
        # ``args.controlPort`` is auto-defaulted in ``parse_args`` to
        # ``nodePort + 1`` whenever ``--controlTrustedRootCA`` is set,
        # and left at 0 otherwise. Passing it through unconditionally
        # lets the Node's controls[] builder publish the right
        # IS-05/IS-11 URLs without needing to re-check the CA flag.
        control_port=args.controlPort,
        wall_group=args.wallGroup,
        oauth2=args.oauth2,
        tls_enabled=tls_enabled,
        client_auth_required=args.nodeOptionalClientAuth,
        security_tags=security_tags,
        ipmx=args.ipmx,
        privacy=args.privacy,
        legs=[Leg(name=iface_name, enable=True,
                  ipv4=IPv4Settings(port=args.nodePort,
                                    address=_leg_addr(leg_address)))],
        node_label="Node",
        node_description="This is the node",
        device_label="Device",
        device_description="This is the device",
    )
    node.exclusive_session = ExclusiveSession()

    # Populate the TLS-server-cert identity list (CN + DNS SANs) from
    # the actual cert mounted on this Node. Required by both IS-10 aud
    # validation modes:
    #
    #   * serial-number mode (default) — aud entry must contain the
    #     Node's instance-id AND must equal one of the cert identities;
    #   * DNS-name mode — aud entry must DNS-wildcard-match one of the
    #     cert identities.
    #
    # An empty ``tls_server_cert_names`` list breaks BOTH modes: the
    # check in ``aud_entry_allows_current_node`` falls through to
    # ``allow_non_tls_for_testing()`` which is False in production,
    # so every aud entry is rejected and every authenticated request
    # gets a 403 "insufficient permissions" — even when the token's
    # claims are fully correct. Populating from the cert at startup
    # is the prerequisite both modes share.
    if not args.nodeDisableTLS and args.nodeCertificate:
        from nmos.cert_check import cert_dns_identities
        node.tls_server_cert_names = cert_dns_identities(
            args.nodeCertificate,
        )
        if node.tls_server_cert_names:
            logging.info(
                "OAuth2/TLS: identifying this Node via cert names: %s",
                ", ".join(node.tls_server_cert_names),
            )
        else:
            logging.warning(
                "OAuth2/TLS: failed to extract DNS identities from "
                "%s — bearer-token aud validation will fail closed",
                args.nodeCertificate,
            )
    # ``node.use_serial_number_in_aud`` defaults to True per the IS-10
    # / "NMOS With OAuth2.0" spec default. Operators wanting DNS-name
    # mode set ``node.use_serial_number_in_aud = False`` (no CLI flag
    # for it yet — could be added if a use case calls for it).

    # Load config from JSON
    config_path = resolve_config_path(args.nodeConfig)
    if not os.path.isfile(config_path):
        logging.fatal(f"Config file not found: {config_path}")
        return

    builder = ConfigBuilder(node, verbose=True)
    # Receivers first: senders may reference a receiver group by name
    # (``linked_receiver_group``) at build time — that lookup reads
    # ``ConfigBuilder._receiver_groups`` which is populated by
    # ``load_receivers``. Building senders first leaves the dict empty
    # and any sender carrying a link fails with "group not found".
    builder.load_receivers(config_path)
    builder.load_senders(config_path)

    # Apply capability flags
    apply_capability_flags(node, args)

    # Publish resources
    node.publish()

    print(f"Resources: {len(node.senders)} senders, {len(node.receivers)} receivers, "
          f"{len(node.sources)} sources, {len(node.flows)} flows")

    # Create aiohttp app(s). When --controlTrustedRootCA is set, split
    # the IS-05/IS-11 routes onto a second app served on --controlPort
    # with the control-trust SSL context; otherwise keep everything on
    # one app (today's behaviour).
    if args.controlTrustedRootCA:
        from nmos.api import create_node_app, create_control_app
        app = create_node_app(node)
        control_app: web.Application | None = create_control_app(node)
    else:
        app = create_app(node)
        control_app = None

    # Root dispatch group
    dg = await DispatchGroup.create()

    # Signal handling: SIGINT/SIGTERM → cancel dispatch group
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, dg.cancel)
        except NotImplementedError:
            # Windows: asyncio exposes no signal-handler API at all
            # (``add_signal_handler`` raises unconditionally). Fall back
            # to the C-level handler and hop back onto the loop thread,
            # so Ctrl-C still cancels the dispatch group instead of
            # tearing the process down with a traceback.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(dg.cancel))

    # Dispatch background tasks
    await dg.dispatch(go_node_server(dg, app, args, control_app=control_app))

    # Status monitor: consumes streaming engine events → BCP-008 status updates
    from nmos.node.status_monitor import run_status_monitor
    await dg.dispatch(run_status_monitor(node))

    if args.rdsHost:
        await dg.dispatch(go_node_registration(dg, node, args))

    if args.oauth2:
        await dg.dispatch(go_node_authorizations(dg, node, args))

    if args.nodeControlPort:
        await dg.dispatch(go_controller_server(dg, node, args))

    # Wait for all tasks.
    #
    # A failure in any dispatched task -- overwhelmingly the most common being
    # a listener that cannot bind because its port is already taken -- must be
    # reported loudly and must make the process exit non-zero.
    #
    # Logging it at debug level hid exactly that case: the operator saw the
    # process exit quietly with status 0, no listening sockets, and nothing
    # anywhere naming the busy port. From the outside that is indistinguishable
    # from "the Node started but my browser cannot reach it", which is a very
    # expensive thing to debug.
    try:
        await dg.wait()
    except asyncio.CancelledError:
        # Clean shutdown: SIGINT/SIGTERM called dg.cancel().
        logging.info("node: shutting down")
    except Done:
        logging.info("node: shutting down")
    except OSError as exc:
        ports = [f"node={args.nodePort}"]
        if args.controlTrustedRootCA and args.controlPort:
            ports.append(f"control={args.controlPort}")
        if args.nodeControlPort:
            ports.append(f"controller-ui={args.nodeControlPort}")
        logging.error(
            "node: a listener failed to start: %s\n"
            "      Ports in use by this Node: %s on %s\n"
            "      Check whether another process already holds one of them.",
            exc, ", ".join(ports), args.nodeAddr or "0.0.0.0",
        )
        raise SystemExit(1) from exc
    except Exception as exc:
        logging.error("node: stopped after an error: %s", exc, exc_info=True)
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def validate_startup_serial(args: argparse.Namespace) -> None:
    """Reject a serial number that cannot address a destination-port block.

    The serial's trailing number selects this Node's block of auto-resolved
    destination ports, because TR-10-9-v2 §17.1 fixes the multicast group to
    the media port's address and two Nodes on one address therefore share a
    group. Checked here so a bad serial fails at launch with an actionable
    message, rather than at the first IS-05 activation — by which point the
    Node is registered and the failure looks like an activation bug.
    """
    from nmos.errors import InvalidData
    from nmos.node.activation_engine import serial_port_index

    try:
        serial_port_index(args.nodeSerialNumber)
    except InvalidData as exc:
        raise SystemExit(f"CONFIG: --nodeSerialNumber {exc}") from exc


def validate_startup_certs(args: argparse.Namespace) -> None:
    """Startup cert verification.

    Run AFTER argparse, BEFORE entering the asyncio main loop so the
    operator gets a fast, fatal diagnostic on mis-configured cert /
    key / trusted-root-CA combinations rather than a cryptic SSL
    handshake failure mid-run.

    Verification chain (only when ``--nodeDisableTLS`` is False):

    1. ``--nodeCertificate`` and ``--nodeKey`` non-empty + accessible.
    2. If one or more ``--nodeTrustedRootCA`` paths are supplied:
       a. ``--trustedRootCA`` (global) must also carry at least one path;
       b. every path in both lists must be accessible;
       c. each ``--nodeTrustedRootCA`` entry chains to the union of
          ``--trustedRootCA`` entries (``check_trusted_ca``).
    3. If ``--trustedRootCA`` carries one or more paths:
       ``--nodeCertificate`` chains to any of those roots AND its
       SAN includes ``XYZ-<serial>`` AND ``--nodeKey`` matches
       the leaf public key (``check_certificate``).
    """
    if args.nodeDisableTLS:
        return

    from nmos.cert_check import (
        CertCheckError,
        check_certificate,
        check_trusted_ca,
    )

    # Step 1: cert + key required and accessible.
    if not args.nodeCertificate or not args.nodeKey:
        raise SystemExit(
            "CONFIG: --nodeCertificate and --nodeKey are required when "
            "TLS is enabled (use --nodeDisableTLS to opt out).",
        )
    for label, path in (
        ("--nodeCertificate", args.nodeCertificate),
        ("--nodeKey", args.nodeKey),
    ):
        if not os.path.isfile(path):
            raise SystemExit(f"CONFIG: {label} is not accessible: {path!r}")

    # Step 2: every --nodeTrustedRootCA entry must chain to the union
    # of --trustedRootCA entries. Each list may carry more than one
    # path; an entry is valid if any of the global roots anchors it.
    if args.nodeTrustedRootCA:
        if not args.trustedRootCA:
            raise SystemExit(
                "CONFIG: --trustedRootCA is required to validate "
                "--nodeTrustedRootCA",
            )
        for path in args.trustedRootCA:
            if not os.path.isfile(path):
                raise SystemExit(
                    f"CONFIG: --trustedRootCA is not accessible: {path!r}",
                )
        for path in args.nodeTrustedRootCA:
            if not os.path.isfile(path):
                raise SystemExit(
                    f"CONFIG: --nodeTrustedRootCA is not accessible: "
                    f"{path!r}",
                )
        for path in args.nodeTrustedRootCA:
            try:
                check_trusted_ca(args.trustedRootCA, path)
            except CertCheckError as exc:
                raise SystemExit(
                    f"CONFIG: --nodeTrustedRootCA {path!r} is not valid "
                    f"based on global --trustedRootCA: {exc}",
                ) from exc

    # Step 3: leaf cert must chain to the global trustedRootCA list
    # (any one of them) AND match the serial-bound SAN entry AND have
    # a matching private key.
    if args.trustedRootCA:
        for path in args.trustedRootCA:
            if not os.path.isfile(path):
                raise SystemExit(
                    f"CONFIG: --trustedRootCA is not accessible: {path!r}",
                )
        try:
            check_certificate(
                args.trustedRootCA,
                args.nodeCertificate,
                args.nodeKey,
                args.nodeSerialNumber,
            )
        except CertCheckError as exc:
            raise SystemExit(
                f"CONFIG: --nodeCertificate is not valid based on "
                f"--trustedRootCA and node serial number "
                f"{args.nodeSerialNumber!r}: {exc}",
            ) from exc


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    if sys.platform == "win32":
        # Windows uses the console API for an interactive terminal, but
        # falls back to the ANSI code page (cp1252) as soon as stdout is
        # a pipe or a file. The startup banners contain non-ASCII, so
        # redirecting output would otherwise die on UnicodeEncodeError.
        for _stream in (sys.stdout, sys.stderr):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass

    args = parse_args()
    setup_logging(args)
    validate_startup_serial(args)
    validate_startup_certs(args)

    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
