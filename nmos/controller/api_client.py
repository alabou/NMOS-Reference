# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Outbound HTTP client for remote NMOS Nodes.

Used by the controller's JSON proxy endpoints to drive IS-05 / IS-11
APIs on the Node that owns a given sender / receiver. The caller's
``Authorization`` and ``PEP-Exclusive-Authorization`` headers are
forwarded verbatim so the identity chain stays end-to-end — the token
issued to the operator's browser is the one the remote Node
authorizes against.

The remote Node's URL is resolved from the device's IS-04 ``controls``
array (``urn:x-nmos:control:connection/v1.1`` / ``…/v1.2`` for IS-05,
``urn:x-nmos:control:streamcompatibility/v1.0`` for IS-11). If the
device didn't publish a control URL the operation fails with a
descriptive error message rather than guessing.
"""

from __future__ import annotations

import json
import logging
import ssl
from dataclasses import dataclass
from typing import Any, Final

import aiohttp

from .debug_trace import DebugTrace

log = logging.getLogger(__name__)


# IS-05 connection-management control URN prefixes (support both v1.1 and v1.2).
CONN_CONTROL_URN_PREFIXES = (
    "urn:x-nmos:control:sr-ctrl/v1.",
    "urn:x-nmos:control:connection/v1.",
)

# Exact-URN preference order for the connection API. When a device
# publishes both v1.1 and v1.2 we deliberately pick v1.1 — AMWA
# introduced v1.2 only to give the JSON schemas room to grow; it adds
# no functional surface over v1.1, and some web servers (whose path
# parsers are compiled for the v1.1 routes) will 404 on the v1.2
# prefix. Picking v1.1 is therefore strictly safer with no capability
# loss. Lookup falls through to the prefix match (above) if none of
# these exact URNs are published, so future v1.3+ still work.
CONN_CONTROL_URN_PREFERRED = (
    "urn:x-nmos:control:sr-ctrl/v1.1",
    "urn:x-nmos:control:connection/v1.1",
    "urn:x-nmos:control:sr-ctrl/v1.2",
    "urn:x-nmos:control:connection/v1.2",
)

# IS-11 stream-compatibility control URN. Matches what the Python Node
# publishes in ``nmos/node/__init__.py`` (``stream-compat`` — the
# hyphenated short form, not ``streamcompatibility`` which is the URL
# path segment).
COMPAT_CONTROL_URN_PREFIX = "urn:x-nmos:control:stream-compat/v1."

# Node Reservation service URN. Published on a Node's ``services``
# array (not ``controls``) per the NMOS Node Reservation spec. The
# href points at the base of the acquire/renew/release/keepalive
# endpoints — ``{href}acquire/``, ``{href}renew/``, etc. The resolver
# looks for ``service.type == "urn:x-matrox:service:exclusive/v1.0"``.
EXCLUSIVE_SERVICE_URN_PREFIX = "urn:x-matrox:service:exclusive/v1."


@dataclass
class RemoteCallResult:
    """Result of a single remote Node API call.

    ``www_authenticate`` is captured from the response (when present)
    so the caller can distinguish auth-error realms — specifically a
    401 with ``Bearer realm="nmos-mtls"`` (client cert required) from
    a 401 with ``Bearer realm="nmos-oauth2"`` (bearer token missing
    or invalid). Empty string when the header is absent or the call
    failed at the transport layer.
    """

    status: int
    body: Any
    error: str | None = None
    www_authenticate: str = ""


# Timeout for every outbound call to a remote Node. ``total`` bounds a
# reachable-but-slow Node's whole response; ``sock_connect`` bounds the
# TCP handshake so an UNREACHABLE Node fails fast. Without the connect
# cap, a registered Node whose host silently drops SYNs (powered off /
# firewalled / stale registry entry) makes interactive pages that fetch
# live state (e.g. /receivers/configure) hang for the full ``total`` —
# observed as a multi-second UI freeze. A healthy LAN Node connects in
# well under a second, so 3 s leaves a wide margin while failing dead
# Nodes ~3x faster than the old 10 s.
NODE_REQUEST_TIMEOUT: Final[aiohttp.ClientTimeout] = aiohttp.ClientTimeout(
    total=10, sock_connect=3,
)


class RemoteNodeClient:
    """Shared-session HTTP client for forwarding ops to remote Nodes.

    One ``ClientSession`` is shared across the controller's lifetime
    for connection pooling. The caller supplies the TLS context at
    construction time (same pattern as ``RegistryClient`` in
    [nmos/node/registry.py](../node/registry.py)).
    """

    def __init__(
        self,
        ssl_context: ssl.SSLContext | None = None,
        debug_trace: DebugTrace | None = None,
        *,
        control_ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        # ``ssl_context``         — used for "Node" calls (Node Reservation
        #                           acquire/renew/release/keepalive).
        # ``control_ssl_context`` — used for IS-05 / IS-11 calls (URLs
        #                           resolved from ``device.controls[]``).
        # When the second is ``None`` (default) both kinds share the
        # first context — the pre-``--controlTrustedRootCA`` behaviour.
        # Sessions are deferred to ``start()`` because
        # ``aiohttp.TCPConnector`` requires a running event loop. We
        # always create two session slots; when both contexts are the
        # same object we still create two sessions so cleanup remains
        # symmetric and per-kind connector tuning stays available.
        self._node_ssl_context = ssl_context
        self._control_ssl_context = (
            control_ssl_context if control_ssl_context is not None
            else ssl_context
        )
        self._node_session: aiohttp.ClientSession | None = None
        self._control_session: aiohttp.ClientSession | None = None
        # When set, every outbound call emits a ``remote_out`` /
        # ``remote_in`` pair carrying the caller's ``trace_id`` so the
        # debug log can be grepped for one browser click and show the
        # full fan-out to remote Nodes. No-op when tracing is off.
        self._debug: DebugTrace | None = debug_trace

    @property
    def debug(self) -> DebugTrace | None:
        return self._debug

    def attach_debug(self, debug_trace: DebugTrace) -> None:
        """Attach (or replace) the tracer used for outbound-call logs.

        Lets the app factory inject its tracer into a caller-provided
        ``RemoteNodeClient`` so outbound HTTP correlates with the
        per-request ``trace_id`` stamped by the debug middleware.
        """
        self._debug = debug_trace

    async def start(self) -> None:
        if self._node_session is None:
            self._node_session = self._new_session(self._node_ssl_context)
        if self._control_session is None:
            self._control_session = self._new_session(self._control_ssl_context)

    @staticmethod
    def _new_session(
        ssl_context: ssl.SSLContext | None,
    ) -> aiohttp.ClientSession:
        connector_ssl: bool | ssl.SSLContext = (
            ssl_context if ssl_context is not None else False
        )
        connector = aiohttp.TCPConnector(ssl=connector_ssl)
        return aiohttp.ClientSession(connector=connector)

    async def close(self) -> None:
        if self._node_session is not None:
            await self._node_session.close()
            self._node_session = None
        if self._control_session is not None:
            await self._control_session.close()
            self._control_session = None

    # ------------------------------------------------------------------
    # Resolving remote base URLs from device controls
    # ------------------------------------------------------------------

    @staticmethod
    def connection_api_base(device: dict[str, Any]) -> str | None:
        """Return the connection-management API base URL from a device's
        IS-04 ``controls`` entry, or ``None`` if the device didn't
        publish one. v1.1 wins over v1.2 when both are published — see
        ``CONN_CONTROL_URN_PREFERRED`` for the rationale.
        """
        info = RemoteNodeClient.connection_api_info(device)
        return info[0] if info is not None else None

    @staticmethod
    def connection_api_info(
        device: dict[str, Any],
    ) -> tuple[str, bool] | None:
        """Same as ``connection_api_base`` but also returns the control
        entry's ``authorization`` flag — ``True`` when OAuth2 is
        required on this IS-05 endpoint.

        Used by the reservation-bearer injection logic (see
        ``handlers._headers_with_reservation``) to pick between
        ``Authorization`` and ``PEP-Exclusive-Authorization`` per the
        "NMOS With Node Reservation" spec §"Using Reservation along
        with OAuth2.0 authorizations".
        """
        return _control_info(
            device, CONN_CONTROL_URN_PREFIXES,
            prefer_exact=CONN_CONTROL_URN_PREFERRED,
        )

    @staticmethod
    def streamcompat_api_base(device: dict[str, Any]) -> str | None:
        """Return the streamcompatibility API base URL, or ``None``."""
        info = RemoteNodeClient.streamcompat_api_info(device)
        return info[0] if info is not None else None

    @staticmethod
    def streamcompat_api_info(
        device: dict[str, Any],
    ) -> tuple[str, bool] | None:
        """``(href, authorization)`` for IS-11 on this device, or ``None``."""
        return _control_info(device, (COMPAT_CONTROL_URN_PREFIX,))

    @staticmethod
    def exclusive_service_base(node: dict[str, Any]) -> str | None:
        """Return the Node-Reservation service base URL from the NODE's
        ``services`` array, or ``None`` if the node doesn't advertise
        the service. Endpoints append ``acquire/`` / ``renew/`` /
        ``release/`` / ``keepalive/`` to the returned href.

        Note: this reads from the NODE resource, not the Device. NMOS
        puts per-Node APIs (like the Matrox exclusive-session service
        at ``urn:x-matrox:service:exclusive/v1.0``) on ``node.services``;
        ``device.controls`` is a different, per-device set. The
        resolver walks the Node's services to find the entry.
        """
        info = RemoteNodeClient.exclusive_service_info(node)
        return info[0] if info is not None else None

    @staticmethod
    def exclusive_service_info(
        node: dict[str, Any],
    ) -> tuple[str, bool] | None:
        """``(href, authorization)`` for the Node Reservation service,
        or ``None`` if the Node doesn't publish it.

        The ``authorization`` flag is what the spec §Reservation
        RestAPI calls out as the service declaration's indication of
        "if OAuth2.0 authorizations are required to access the
        service". Callers use it to pick between ``Authorization``
        (OAuth2 off) and ``PEP-Exclusive-Authorization`` (OAuth2 on)
        for the session bearer token.
        """
        return _service_info(node, (EXCLUSIVE_SERVICE_URN_PREFIX,))

    # ------------------------------------------------------------------
    # Remote IS-05 / IS-11 operations
    # ------------------------------------------------------------------

    async def put_sender_active_constraints(
        self,
        base_url: str,
        sender_id: str,
        constraints: dict[str, Any],
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """PUT {is11_base}/senders/{id}/constraints/active/ on the remote
        Node. ``base_url`` is the streamcompatibility (IS-11) control
        href — note there is no ``single/`` segment on IS-11 (that's
        IS-05).
        """
        url = _join(base_url, f"senders/{sender_id}/constraints/active/")
        return await self._request(
            "PUT", url, forwarded_headers, json_body=constraints, trace_id=trace_id,
        )

    async def delete_sender_active_constraints(
        self,
        base_url: str,
        sender_id: str,
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """DELETE {is11_base}/senders/{id}/constraints/active/ — removes
        the active constraint set (unconstrain).
        """
        url = _join(base_url, f"senders/{sender_id}/constraints/active/")
        return await self._request("DELETE", url, forwarded_headers, trace_id=trace_id)

    async def get_sender_active_constraints(
        self,
        base_url: str,
        sender_id: str,
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """GET {is11_base}/senders/{id}/constraints/active/ — returns
        the currently-active constraint set (or an unconstrained
        response). Used by the configure page to render the
        ``Constrain`` button in its actual state at page load.
        """
        url = _join(base_url, f"senders/{sender_id}/constraints/active/")
        return await self._request("GET", url, forwarded_headers, trace_id=trace_id)

    async def get_sender_is11_status(
        self,
        base_url: str,
        sender_id: str,
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """GET {is11_base}/senders/{id}/status/ — the IS-11 sender
        compatibility status (``state``: unconstrained / constrained /
        active_constraints_violation / no_essence / awaiting_essence).
        Read-only; used by the inspector's IS-11 page."""
        url = _join(base_url, f"senders/{sender_id}/status/")
        return await self._request("GET", url, forwarded_headers, trace_id=trace_id)

    async def get_receiver_is11_status(
        self,
        base_url: str,
        receiver_id: str,
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """GET {is11_base}/receivers/{id}/status/ — the IS-11 receiver
        stream-compatibility status (``state``: unknown / compliant_stream /
        non_compliant_stream). Symmetric to ``get_sender_is11_status``."""
        url = _join(base_url, f"receivers/{receiver_id}/status/")
        return await self._request("GET", url, forwarded_headers, trace_id=trace_id)

    async def patch_sender_staged(
        self,
        base_url: str,
        sender_id: str,
        body: dict[str, Any],
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """PATCH /single/senders/{id}/staged/ — activate / deactivate.
        The trailing slash avoids relying on the remote's trailing-slash
        middleware (which on some nodes redirects PATCH and drops the
        body / re-triggers auth).
        """
        url = _join(base_url, f"single/senders/{sender_id}/staged/")
        return await self._request(
            "PATCH", url, forwarded_headers, json_body=body, trace_id=trace_id,
        )

    async def patch_receiver_staged(
        self,
        base_url: str,
        receiver_id: str,
        body: dict[str, Any],
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """PATCH /single/receivers/{id}/staged/ — activate / deactivate."""
        url = _join(base_url, f"single/receivers/{receiver_id}/staged/")
        return await self._request(
            "PATCH", url, forwarded_headers, json_body=body, trace_id=trace_id,
        )

    async def get_sender_transportfile(
        self,
        base_url: str,
        sender_id: str,
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """GET /single/senders/{id}/transportfile/ — returns the SDP
        the receiver needs for activation.
        """
        url = _join(base_url, f"single/senders/{sender_id}/transportfile/")
        return await self._request(
            "GET", url, forwarded_headers, expect_text=True, trace_id=trace_id,
        )

    # ------------------------------------------------------------------
    # IS-05 active transport parameters (for PEP public-key exchange)
    # ------------------------------------------------------------------

    async def get_sender_active(
        self,
        base_url: str,
        sender_id: str,
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """GET /single/senders/{id}/active/ — returns the sender's
        currently-active IS-05 transport params.

        Used by the receiver-activation flow to read the forwarded
        ``ext_privacy_key_generator`` / ``key_version`` / ``key_id``
        (and ``ext_privacy_ecdh_sender_public_key`` for ECDH modes).
        """
        url = _join(base_url, f"single/senders/{sender_id}/active/")
        return await self._request("GET", url, forwarded_headers, trace_id=trace_id)

    async def get_receiver_active(
        self,
        base_url: str,
        receiver_id: str,
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """GET /single/receivers/{id}/active/ — returns the receiver's
        currently-active IS-05 transport params.

        Used by the sender-activation flow (ECDH modes only) to read
        ``ext_privacy_ecdh_receiver_public_key`` and inject it into
        the sender's PATCH staged body.
        """
        url = _join(base_url, f"single/receivers/{receiver_id}/active/")
        return await self._request("GET", url, forwarded_headers, trace_id=trace_id)

    async def get_sender_constraints(
        self,
        base_url: str,
        sender_id: str,
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """GET /single/senders/{id}/constraints/ — the sender's
        IS-05 transport-parameter constraints.

        Used by the Privacy intersection logic to discover supported
        ``ext_privacy_*`` enum values per sender.
        """
        url = _join(base_url, f"single/senders/{sender_id}/constraints/")
        return await self._request("GET", url, forwarded_headers, trace_id=trace_id)

    async def get_receiver_constraints(
        self,
        base_url: str,
        receiver_id: str,
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """GET /single/receivers/{id}/constraints/ — the receiver's
        IS-05 transport-parameter constraints. Symmetric to
        ``get_sender_constraints``.
        """
        url = _join(base_url, f"single/receivers/{receiver_id}/constraints/")
        return await self._request("GET", url, forwarded_headers, trace_id=trace_id)

    async def get_sender_staged(
        self,
        base_url: str,
        sender_id: str,
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """GET /single/senders/{id}/staged/ — the sender's staged (pending)
        IS-05 transport params (mirrors ``get_sender_active``). Read-only;
        used by the transport-parameters inspector page."""
        url = _join(base_url, f"single/senders/{sender_id}/staged/")
        return await self._request("GET", url, forwarded_headers, trace_id=trace_id)

    async def get_receiver_staged(
        self,
        base_url: str,
        receiver_id: str,
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """GET /single/receivers/{id}/staged/ — the receiver's staged
        IS-05 transport params. Symmetric to ``get_sender_staged``."""
        url = _join(base_url, f"single/receivers/{receiver_id}/staged/")
        return await self._request("GET", url, forwarded_headers, trace_id=trace_id)

    # ------------------------------------------------------------------
    # Node Reservation service (urn:x-matrox:service:exclusive/v1.0)
    # ------------------------------------------------------------------

    async def acquire_exclusive(
        self,
        base_url: str,
        owner: str,
        exclusive_key_hex: str,
        forwarded_headers: dict[str, str],
        *,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """POST {exclusive_base}acquire/ — claim the exclusive session.

        Body per the Node Reservation spec: ``{owner, exclusive_key}``
        where ``exclusive_key`` is hex-encoded 16-byte random. On 200
        the response body is a string — the bearer token the caller
        attaches as ``PEP-Exclusive-Authorization: Bearer <token>`` on
        every subsequent state-changing call against that Node.

        Status mapping:
          * 200 Ok      — token returned
          * 423 Locked  — already held by another owner
        """
        url = _join(base_url, "acquire/")
        body = {"owner": owner, "exclusive_key": exclusive_key_hex}
        return await self._request(
            "POST", url, forwarded_headers, json_body=body,
            trace_id=trace_id, kind="node",
        )

    async def renew_exclusive(
        self,
        base_url: str,
        session_token: str,
        forwarded_headers: dict[str, str],
        *,
        oauth2_on_remote: bool,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """POST {exclusive_base}renew/ — extend session lifetime.

        Authenticates with the current session token in whichever
        header the remote expects (``Authorization`` when OAuth2 is
        off on the remote, ``PEP-Exclusive-Authorization`` when on).
        Response body is the new bearer token — caller must replace
        the old one.
        """
        url = _join(base_url, "renew/")
        return await self._request(
            "POST", url,
            _with_exclusive_token(
                forwarded_headers, session_token, oauth2_on_remote,
            ),
            trace_id=trace_id, kind="node",
        )

    async def release_exclusive(
        self,
        base_url: str,
        session_token: str,
        forwarded_headers: dict[str, str],
        *,
        oauth2_on_remote: bool,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """POST {exclusive_base}release/ — drop the exclusive session."""
        url = _join(base_url, "release/")
        return await self._request(
            "POST", url,
            _with_exclusive_token(
                forwarded_headers, session_token, oauth2_on_remote,
            ),
            trace_id=trace_id, kind="node",
        )

    async def keepalive_exclusive(
        self,
        base_url: str,
        session_token: str,
        forwarded_headers: dict[str, str],
        *,
        oauth2_on_remote: bool,
        trace_id: str = "",
    ) -> RemoteCallResult:
        """POST {exclusive_base}keepalive/ — reset the alivetime clock
        without extending session lifetime. Must be called at least
        every alivetime window (default 60 s) or the Node marks the
        session dormant and next renew/release returns 401.
        """
        url = _join(base_url, "keepalive/")
        return await self._request(
            "POST", url,
            _with_exclusive_token(
                forwarded_headers, session_token, oauth2_on_remote,
            ),
            trace_id=trace_id, kind="node",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        forwarded_headers: dict[str, str],
        *,
        json_body: dict[str, Any] | None = None,
        expect_text: bool = False,
        trace_id: str = "",
        kind: str = "control",
    ) -> RemoteCallResult:
        # ``kind`` picks which trust-anchor / session pair is used:
        # ``"control"`` for IS-05 / IS-11 endpoints (URLs resolved
        # from ``device.controls[]``) and ``"node"`` for Node-level
        # endpoints (currently just Node Reservation acquire/renew/
        # release/keepalive on ``node.services[]``). When the operator
        # did not split trust at startup (i.e. ``--controlTrustedRootCA``
        # was empty) both sessions share the same SSL context so the
        # routing is a no-op.
        await self.start()
        session: aiohttp.ClientSession | None = (
            self._node_session if kind == "node" else self._control_session
        )
        assert session is not None

        headers = {
            k: v for k, v in forwarded_headers.items()
            if k in ("Authorization", "PEP-Exclusive-Authorization")
        }
        data: Any = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(json_body)

        # Trace the outbound request when INFO logging is enabled — a
        # curl-equivalent line the operator can paste back to compare
        # against a manual invocation. Header values are logged as-is;
        # bearer tokens are redacted to the prefix + last four chars.
        log.info(
            "outbound %s %s | headers=%s | body=%s",
            method, url, _redact_headers(headers),
            data if data is not None else "<none>",
        )
        if self._debug is not None and self._debug.enabled:
            self._debug.emit(
                "remote_out",
                trace_id=trace_id,
                method=method,
                url=url,
                headers=_redact_headers(headers),
                body_preview=_body_preview(data),
            )
        try:
            async with session.request(
                method, url, headers=headers, data=data,
                # Disable redirect-following. The remote's
                # trailing-slash middleware would otherwise 301 a
                # ``.../staged`` to ``.../staged/`` — aiohttp preserves
                # the method on PATCH but rebuilds the request, which
                # can drop the body on some server/client combos.
                # We already post the trailing-slash form, so no
                # redirect is expected; if one comes back, we want to
                # see it in the logs rather than silently follow.
                allow_redirects=False,
                timeout=NODE_REQUEST_TIMEOUT,
            ) as resp:
                if expect_text or resp.content_type.startswith("text/"):
                    body: Any = await resp.text()
                else:
                    try:
                        body = await resp.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError, json.JSONDecodeError):
                        body = await resp.text()
                log.info(
                    "outbound -> status=%s content-type=%s body=%.200s",
                    resp.status, resp.content_type,
                    body if isinstance(body, str) else json.dumps(body),
                )
                if self._debug is not None and self._debug.enabled:
                    self._debug.emit(
                        "remote_in",
                        trace_id=trace_id,
                        method=method,
                        url=url,
                        status=resp.status,
                        content_type=resp.content_type,
                        body_preview=_body_preview(
                            body if isinstance(body, str) else json.dumps(body),
                        ),
                    )
                return RemoteCallResult(
                    status=resp.status,
                    body=body,
                    www_authenticate=resp.headers.get("WWW-Authenticate", ""),
                )
        except aiohttp.ClientError as exc:
            log.warning("outbound transport error: %s", exc)
            if self._debug is not None and self._debug.enabled:
                self._debug.emit(
                    "remote_error",
                    trace_id=trace_id,
                    method=method,
                    url=url,
                    error=repr(exc),
                )
            return RemoteCallResult(status=0, body=None, error=str(exc))
        except Exception as exc:
            log.exception("remote request failed")
            if self._debug is not None and self._debug.enabled:
                self._debug.emit(
                    "remote_error",
                    trace_id=trace_id,
                    method=method,
                    url=url,
                    error=repr(exc),
                )
            return RemoteCallResult(status=0, body=None, error=str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def exclusive_header_name(oauth2_on_remote: bool) -> str:
    """Return the HTTP header name that carries the reservation
    session bearer token on a given remote endpoint.

    Per the NMOS With Node Reservation spec
    (``specs/NMOS With Node Reservation.md`` §"Using Reservation
    along with OAuth2.0 authorizations"):

      * OAuth2 in use      → ``PEP-Exclusive-Authorization``
                             (``Authorization`` carries the OAuth2
                             bearer on the same request);
      * OAuth2 not in use  → ``Authorization``.

    The ``authorization`` flag on the service / control entry that
    advertises the endpoint IS the signal — each service / control
    entry publishes its own value.
    """
    return (
        "PEP-Exclusive-Authorization" if oauth2_on_remote else "Authorization"
    )


def _with_exclusive_token(
    forwarded_headers: dict[str, str],
    session_token: str,
    oauth2_on_remote: bool,
) -> dict[str, str]:
    """Return a copy of ``forwarded_headers`` with the reservation
    session's bearer token in the header the spec requires for the
    remote's OAuth2 configuration.

    Used by ``renew_exclusive`` / ``release_exclusive`` /
    ``keepalive_exclusive`` — each must authenticate with the token
    that ``acquire_exclusive`` returned. For the regular IS-05 /
    IS-11 calls, the reservation layer injects this header through a
    similar mechanism at the handler level (not here).
    """
    out = dict(forwarded_headers)
    out[exclusive_header_name(oauth2_on_remote)] = f"Bearer {session_token}"
    return out

def _join(base_url: str, sub_path: str) -> str:
    """Safely join a base URL with a sub-path (handles trailing slash)."""
    return base_url.rstrip("/") + "/" + sub_path.lstrip("/")


def _body_preview(body: Any) -> str:
    """Return a trimmed string preview of an outbound body / response.

    Used only by the debug-trace log — not by the production log lines.
    Caps at 500 chars so a giant SDP or transport-params blob doesn't
    blow the JSONL line size out.
    """
    if body is None:
        return ""
    s = body if isinstance(body, str) else str(body)
    return s if len(s) <= 500 else s[:500] + "…"


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with Bearer tokens masked.

    Shows ``Bearer <prefix>…<last4>`` so logs reveal whether a token
    was attached and which one, without leaking the full credential.
    Non-auth headers pass through unchanged.
    """
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k in ("Authorization", "PEP-Exclusive-Authorization") and v.startswith("Bearer "):
            tok = v[7:]
            out[k] = f"Bearer {tok[:4]}…{tok[-4:]}" if len(tok) > 8 else "Bearer <short>"
        else:
            out[k] = v
    return out


def _control_info(
    device: dict[str, Any],
    prefixes: tuple[str, ...],
    prefer_exact: tuple[str, ...] = (),
) -> tuple[str, bool] | None:
    """Resolve a control entry into ``(href, authorization)``.

    * ``prefix`` matches — any control whose ``type`` URN starts with
      one of these is a candidate.
    * ``prefer_exact`` — exact URNs listed here WIN over a generic
      prefix match, in the order they appear (used to prefer IS-05
      v1.1 over v1.2).
    * Fallback — if no exact preference hits, pick the
      highest-versioned prefix match (lexicographic-descending) so
      future v1.3+ still works without code changes.

    ``authorization`` is the per-entry boolean the NMOS IS-04
    ``controls`` schema publishes; defaults to ``False`` if the
    selected entry doesn't declare it.
    """
    return _info_from_list(
        device.get("controls"), prefixes, prefer_exact,
    )


def _service_info(
    node: dict[str, Any],
    prefixes: tuple[str, ...],
) -> tuple[str, bool] | None:
    """Resolve a service entry into ``(href, authorization)``.

    Same shape as ``_control_info`` but reads from
    ``node["services"]``. NMOS puts custom-service advertisements
    (like the Matrox exclusive-session / Node Reservation service
    at ``urn:x-matrox:service:exclusive/v1.0``) under ``services``
    rather than ``controls``.
    """
    return _info_from_list(node.get("services"), prefixes, ())


def _info_from_list(
    entries: Any,
    prefixes: tuple[str, ...],
    prefer_exact: tuple[str, ...],
) -> tuple[str, bool] | None:
    """Shared resolver for both ``controls`` and ``services`` arrays.

    Each entry is a ``{"type", "href", "authorization"}`` dict.
    ``authorization`` is optional per the IS-04 schema and defaults
    to ``False`` when missing. Preference order matches the legacy
    href-only resolver.
    """
    if not isinstance(entries, list):
        return None
    entries_list: list[Any] = entries
    candidates: list[tuple[str, str, bool]] = []
    for entry_any in entries_list:
        if not isinstance(entry_any, dict):
            continue
        entry: dict[Any, Any] = entry_any
        urn_raw = entry.get("type", "")
        href_raw = entry.get("href", "")
        auth_raw = entry.get("authorization", False)
        if not isinstance(urn_raw, str) or not isinstance(href_raw, str):
            continue
        urn: str = urn_raw
        href: str = href_raw
        authorization: bool = bool(auth_raw) if isinstance(auth_raw, bool) else False
        if any(urn.startswith(p) for p in prefixes):
            candidates.append((urn, href, authorization))
    if not candidates:
        return None
    # Exact-preference pass first.
    for preferred in prefer_exact:
        for urn, href, auth in candidates:
            if urn == preferred:
                return href, auth
    # Fallback: highest-versioned prefix match.
    candidates.sort(key=lambda p: p[0], reverse=True)
    _, href, auth = candidates[0]
    return href, auth
