# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""HTTP handlers for the embedded NMOS controller UI.

Page handlers (return Jinja2-rendered HTML):

  * ``index``                    — landing + token-entry form
  * ``senders_list``             — grouped senders
  * ``senders_caps``             — capability picker for selected senders
  * ``senders_configure``        — constrain / unconstrain / (de)activate
  * ``receivers_list``           — grouped receivers
  * ``receivers_compatible``     — compatible senders for selected receivers
  * ``receivers_caps``           — intersected caps picker
  * ``receivers_configure``      — end-to-end connect flow

JSON handlers (consumed by ``controller.js`` ``fetch()`` calls):

  * ``GET  /api/senders``
  * ``GET  /api/receivers``
  * ``GET  /api/receivers/{id}/compatible-senders``
  * ``POST /api/senders/{id}/constrain``
  * ``POST /api/senders/{id}/unconstrain``
  * ``POST /api/senders/{id}/activate``
  * ``POST /api/senders/{id}/deactivate``
  * ``POST /api/receivers/{id}/activate``

All handlers are registered in ``nmos/controller/app.py`` with the same
``check_oauth2`` + mTLS + client_auth middleware stack as the rest of
the Node API.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Final

from aiohttp import web

from nmos.enums import (
    CapMetaFormat, CapMetaLayer, CapMetaLayerCompatibilityGroups, CapMetaLayerEnabled,
    CapMetaEnabled, CapMetaPreference, CapMetaLabel, CapFormatMediaType,
    TransportUsb, ActivateImmediate,
)

from nmos.controller.api_client import (
    RemoteCallResult,
    RemoteNodeClient,
    exclusive_header_name,
)
from nmos.controller.auth import AdminSessionState
from nmos.controller.cache import (
    DeviceView,
    GroupedResource,
    NaturalGroupView,
    ResourceCache,
)
from nmos.controller.compat import (
    SupersetMatch,
    compatible_sender_groups,
    compatible_sender_groups_superset,
    compatible_senders,
    filter_sender_cs_by_receiver,
    is_compatible,
    pair_by_identity,
    resource_ccf_caps,
)
from nmos.controller.grouping import extract_group_hint
from nmos.controller.privacy import (
    EXT_PRIVACY_ECDH_CURVE,
    EXT_PRIVACY_ECDH_RECEIVER_PUBLIC_KEY,
    EXT_PRIVACY_MODE,
    EXT_PRIVACY_PROTOCOL,
    compute_privacy_options,
    is_ecdh_mode,
    receiver_to_sender_fields,
    sender_to_receiver_fields,
)
from nmos.controller.reservation import (
    ReservationError,
    ReservationLocked,
    SessionStore,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cache(request: web.Request) -> ResourceCache:
    cache = request.app.get("controller_cache")
    if not isinstance(cache, ResourceCache):
        raise web.HTTPInternalServerError(reason="controller cache not configured")
    return cache


def _remote_client(request: web.Request) -> RemoteNodeClient:
    client = request.app.get("controller_remote_client")
    if not isinstance(client, RemoteNodeClient):
        raise web.HTTPInternalServerError(reason="remote client not configured")
    return client


def _reservations(request: web.Request) -> SessionStore:
    store = request.app.get("controller_reservations")
    if not isinstance(store, SessionStore):
        raise web.HTTPInternalServerError(
            reason="reservation store not configured",
        )
    return store


def _admin_session(request: web.Request) -> AdminSessionState:
    """Return the authenticated admin's server-side state.

    The session middleware in
    [app.py::_admin_session_middleware_factory](nmos-reference/nmos/controller/app.py)
    attaches this to every authenticated request. Handlers that need
    the exclusive_key or the acquired-devices set reach for it via
    this accessor — and fail 500 if it's missing, which should never
    happen behind the middleware but guards against calling a handler
    on a request that bypassed the gate.
    """
    state = request.get("admin_session")
    if not isinstance(state, AdminSessionState):
        raise web.HTTPInternalServerError(reason="admin session missing")
    return state


def _headers_with_reservation(
    request: web.Request, device_id: str,
) -> dict[str, str]:
    """Build the forwarded-headers dict for an outbound call against a
    remote Node, injecting the reservation session bearer into the
    header the remote expects, plus the OAuth2 access token in
    ``Authorization`` when the remote Node has OAuth2 enabled.

    Per the NMOS With Node Reservation spec §"Using Reservation
    along with OAuth2.0 authorizations" (and confirmed in the
    Python Node's own test suite at
    [nmos/api/tests/test_reservation.py](../api/tests/test_reservation.py)
    and [nmos/api/tests/test_oauth2_reservation.py](../api/tests/test_oauth2_reservation.py)):

      * OAuth2 not in use on the remote → ``Authorization`` carries
        the reservation session bearer; no other auth header.
      * OAuth2 in use on the remote    → ``Authorization`` carries
        the **OAuth2 access token** from the admin's logged-in
        Keycloak session, and the reservation session bearer moves
        to ``PEP-Exclusive-Authorization``.

    The signal is the ``authorization`` flag on the Node's
    exclusive-service entry. Reservation is scoped per-Node (one
    session covers every sender/receiver/device on that Node).
    The device's ``node_id`` is resolved via the cache to find the
    right session key.
    """
    out = dict(_forwarded_auth(request))
    try:
        admin = _admin_session(request)
    except web.HTTPInternalServerError:
        return out

    cache = request.app.get("controller_cache")
    if not isinstance(cache, ResourceCache):
        return out
    node = cache.node_for_device(device_id)
    if node is None:
        return out
    node_id = node.get("id", "") or ""

    # Determine whether the remote Node has OAuth2 turned on by reading
    # the authorization flag advertised on its exclusive-service entry.
    # The Python Node sets this flag uniformly across services and
    # controls based on ``self.oauth2`` so a single read is sufficient.
    info = RemoteNodeClient.exclusive_service_info(node)
    oauth2_on_remote = bool(info[1]) if info is not None else False

    # OAuth2 bearer injection — when the remote requires it, supply
    # the admin's currently-valid access token. The proactive-refresh
    # task (see ``app.py:_oauth2_refresh_loop``) keeps this fresh; we
    # just read whatever's on the session at the moment of the call.
    # Absent (controller started without ``--oauth2``) means no bearer
    # is injected and the remote will 401 — that's the upstream
    # "controller can't act on this Node" case which Phase 5 surfaces
    # as a UI ``inaccessible_reasons`` entry.
    if oauth2_on_remote and admin.oauth2_tokens is not None:
        out["Authorization"] = f"Bearer {admin.oauth2_tokens.access_token}"

    # Reservation bearer injection — keyed per-(admin, node).
    reservations = request.app.get("controller_reservations")
    if isinstance(reservations, SessionStore) and node_id:
        token = reservations.current_token(admin, node_id)
        if token:
            out[exclusive_header_name(oauth2_on_remote)] = f"Bearer {token}"
    return out


def _device_inaccessible_reasons(
    cache: ResourceCache,
    admin: AdminSessionState,
    device_id: str,
) -> dict[str, list[str]]:
    """Per-Device read/write capability probe.

    Walks the Device's owning Node's exclusive-service entry plus the
    Device's own ``controls[]`` (where IS-04 publishes the per-API
    ``authorization`` flag) and tags each axis the controller cannot
    satisfy with a human-readable reason. The output is a dict with
    two keys:

    * ``"read"``  — reasons the controller can't even GET from this
      Device. When non-empty, every action on the Device should be
      treated as blocked (the listing template paints the Device's
      group box light red).
    * ``"write"`` — reasons that gate state-changing methods only.
      When ``read == [] and write != []`` the box stays normal-coloured
      but Activate / Deactivate / Constrain / Reset carry ``disabled``
      with a tooltip.

    Two signals fire today:

    * **OAuth2 missing** (read+write) — the Node advertises
      ``authorization=true`` on its exclusive service but the admin's
      session has no usable ``oauth2_tokens``. Populates both axes
      because every endpoint on an OAuth2 Node demands a bearer.
    * **Client cert required** (write only) — populated reactively
      from prior failed-write 401s recorded on
      ``admin.cert_required_devices``. The controller has no way to
      detect this requirement from the IS-04 ``controls[]`` array
      (the spec doesn't expose it), so we learn about it on first
      attempt and remember it for the rest of the session.
    """
    reasons: dict[str, list[str]] = {"read": [], "write": []}
    if not device_id:
        return reasons
    node = cache.node_for_device(device_id)
    if node is None:
        return reasons

    info = RemoteNodeClient.exclusive_service_info(node)
    oauth2_on_remote = bool(info[1]) if info is not None else False
    if oauth2_on_remote and admin.oauth2_tokens is None:
        msg = (
            "remote Node requires OAuth2 but the controller's admin "
            "session has no access token (re-login at "
            "/controller/oauth2/login)"
        )
        reasons["read"].append(msg)
        reasons["write"].append(msg)

    # Proactive OAuth2-audience check. The Node's
    # ``aud_entry_allows_current_node`` (nmos/oauth2/__init__.py) uses
    # serial-number-substring containment: an ``aud`` entry covers the
    # Node iff the Node's instance-id (serial) appears anywhere in
    # the entry, OR the entry is ``*``. We replicate that here so the
    # UI can predict the 401/403 BEFORE the operator clicks: a Device
    # owned by a Node whose serial doesn't appear in any ``aud`` entry
    # is unreachable for both reads and writes under this admin's
    # current token.
    if (
        oauth2_on_remote
        and admin.oauth2_tokens is not None
    ):
        device = cache.get_device(device_id)
        from nmos.controller.grouping import device_serial as _device_serial
        serial = _device_serial(device) if device is not None else None
        aud = admin.oauth2_tokens.claims.get("aud", [])
        if isinstance(aud, str):
            aud = [aud]
        if serial and not _aud_covers_serial(aud, serial):
            msg = (
                f"OAuth2 token grants do not cover device serial "
                f"{serial!r}; current aud entries: "
                f"{', '.join(repr(a) for a in aud) or '(none)'}"
            )
            reasons["read"].append(msg)
            reasons["write"].append(msg)

    if device_id in admin.cert_required_devices:
        reasons["write"].append(
            "remote Node requires a client certificate for write "
            "operations (PUT/POST/PATCH/DELETE); controller has none "
            "configured — ask the operator to start nmos_node.py with "
            "--rdsClientCertificate / --rdsClientKey",
        )

    return reasons


def _aud_covers_serial(aud: list[Any], serial: str) -> bool:
    """Mirror the Node-side aud check in serial-number mode.

    Returns True iff at least one ``aud`` entry covers a Node whose
    instance-id is ``serial``. ``"*"`` covers everything; otherwise
    the entry must contain ``serial`` as a substring (matches the
    spec-pseudocode and ``aud_entry_allows_current_node`` in
    :mod:`nmos.oauth2`). Tolerates non-string aud entries (treated
    as no-match).
    """
    if not serial:
        # Empty serial would be a substring of every string; guard
        # explicitly so an unknown serial doesn't read as "covered".
        return False
    for entry in aud:
        if entry == "*":
            return True
        if isinstance(entry, str) and serial in entry:
            return True
    return False


def _device_tls_secure(device: dict[str, Any]) -> bool:
    """True iff every entry in the Device's ``controls[]`` advertises
    an ``https://`` href.

    The lock icon next to the Device serial flips on this:
    closed-green when True, open-red when False. ``http://`` is
    treated as insecure even when the controller has a CA bundle
    configured — the URL scheme is the source of truth, not a
    guess. Devices with no controls (uncommon) default to insecure.
    """
    controls = device.get("controls") or []
    if not isinstance(controls, list) or not controls:
        return False
    for ctl in controls:
        if not isinstance(ctl, dict):
            return False
        href = str(ctl.get("href", ""))
        if not href.startswith("https://"):
            return False
    return True


def _device_capability_view(
    cache: ResourceCache,
    admin: AdminSessionState,
    device_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Build ``{device_id: {tls_secure, inaccessible_reasons}}`` for a
    listing page.

    Single source of truth feeding the device_block.html partial: the
    template reads ``device_capability[device_id]`` once per Device
    and renders the lock icon + the optional ``device-inaccessible``
    class from the result. Devices missing from the cache are skipped
    (they shouldn't appear on the page anyway).
    """
    out: dict[str, dict[str, Any]] = {}
    for did in device_ids:
        if not did:
            continue
        device = cache.get_device(did)
        if device is None:
            continue
        out[did] = {
            "tls_secure": _device_tls_secure(device),
            "inaccessible_reasons": _device_inaccessible_reasons(
                cache, admin, did,
            ),
        }
    return out


def _trace_id(request: web.Request) -> str:
    """Return the per-request trace id stamped by the debug middleware.

    Returns an empty string when tracing is off or the request wasn't
    routed through the debug middleware (e.g. test fixtures). Callers
    pass this through to ``RemoteNodeClient`` / ``SessionStore`` so a
    single browser click correlates to every outbound HTTP event it
    triggers.
    """
    value = request.get("trace_id", "")
    return str(value) if isinstance(value, str) else ""


def _forwarded_auth(request: web.Request) -> dict[str, str]:
    """Headers to attach to the outbound remote-Node request.

    Intentionally returns ``{}``. The browser authenticates to the
    controller with a session cookie (see ``auth.py``); that cookie is
    NOT a credential the remote Node would recognize, and forwarding
    it would either leak the operator's session to every upstream
    Node or, worse, forward a stale ``Authorization: Basic …`` header
    left over in the browser's credential store from an earlier
    HTTP-Basic version of this app — which would poison every
    upstream call (the remote sees a non-Bearer Authorization and
    rejects the request).

    Upstream auth (OAuth2 bearer, Reservation token, mTLS cert) is a
    server-side decision the controller's Node makes on its own; it
    is NOT derived from the browser's request headers.
    """
    return {}


def _render(
    request: web.Request, template_name: str, context: dict[str, Any],
) -> web.Response:
    env = request.app.get("jinja_env")
    if env is None:
        raise web.HTTPInternalServerError(reason="Jinja2 environment missing")
    # Expose the debug flag to every template so ``base.html`` can
    # stamp ``<html data-debug="1">`` — the JS reads that attribute
    # to decide whether to install the client-event instrumentation.
    trace: Any = request.app.get("controller_debug_trace")
    debug_enabled = bool(trace is not None and getattr(trace, "enabled", False))
    ctx = dict(context)
    ctx.setdefault("debug_enabled", debug_enabled)
    template = env.get_template(template_name)
    html = template.render(**ctx)
    return web.Response(text=html, content_type="text/html")


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in value.split(",") if s.strip()]


def _extract_remote_error_text(body: Any) -> str:
    """Best-effort extraction of a short error message from a remote
    Node's response body.

    Remote error bodies most commonly follow the NMOS ``NError`` schema
    (``{"code": N, "error": "…", "debug": "…"}``) but may also be a
    plain string (for auth middleware 401s, for instance) or an
    unexpected shape. Return the tightest human-readable form we can
    derive; fall back to the first chunk of ``str(body)`` if nothing
    structured is available.
    """
    if body is None:
        return ""
    if isinstance(body, dict):
        for key in ("error", "message", "debug", "description"):
            val = body.get(key)
            if isinstance(val, str) and val:
                return val
        return ""
    if isinstance(body, str):
        # Some servers return multi-line HTML on auth failure — keep
        # only the first 160 chars so the result cell stays readable.
        snippet = body.strip().splitlines()[0] if body.strip() else ""
        return snippet[:160]
    return str(body)[:160]


def _remote_envelope(
    result: RemoteCallResult,
    *,
    request: web.Request | None = None,
    device_id: str = "",
) -> dict[str, Any]:
    """Build the JSON envelope the browser consumes for every proxied
    remote call.

    Fields:
      * ``status``     — the remote's HTTP status (0 on connect failure)
      * ``body``       — the remote's response body (dict / str / None)
      * ``error``      — connection-level error text (non-None when
                         ``status == 0``)
      * ``message``    — short, human-readable summary ready to drop into
                         the result cell. Reads ``HTTP <status>: <detail>``
                         when the remote responded, or the raw connection
                         error otherwise.
      * ``error_kind`` — machine-readable tag the browser switches on:
                         ``"client_cert_required"`` when the remote
                         responded 401 with ``WWW-Authenticate``
                         containing the ``nmos-mtls`` realm. Empty
                         string otherwise.
      * ``device_id``  — the Device the call was directed at, echoed
                         back so the browser-side handler can mark
                         that Device's row red on the next render.

    Side effect: when the response carries the mTLS realm AND the
    caller passed ``request`` + ``device_id``, the device is added to
    the admin's :attr:`AdminSessionState.cert_required_devices` set.
    From that point on, :func:`_device_inaccessible_reasons` returns a
    write-axis reason for that device, and the listing template paints
    the Device's group box light red.
    """
    if result.error and not result.status:
        message = result.error
    elif result.status in (200, 204):
        message = f"HTTP {result.status}"
    else:
        detail = _extract_remote_error_text(result.body)
        message = (
            f"HTTP {result.status}: {detail}" if detail
            else f"HTTP {result.status}"
        )

    error_kind = ""
    if (
        result.status == 401
        and "nmos-mtls" in (result.www_authenticate or "").lower()
    ):
        error_kind = "client_cert_required"
        message = (
            "Client certificate required for write operations on this "
            "device. Reload the page to see the updated status."
        )
        if request is not None and device_id:
            try:
                admin = _admin_session(request)
            except web.HTTPInternalServerError:
                pass
            else:
                admin.cert_required_devices.add(device_id)

    return {
        "status":     result.status,
        "body":       result.body,
        "error":      result.error,
        "message":    message,
        "error_kind": error_kind,
        "device_id":  device_id,
    }


# ---------------------------------------------------------------------------
# Page handlers
# ---------------------------------------------------------------------------

async def index(request: web.Request) -> web.Response:
    return _render(request, "index.html", {"active": "home"})


async def senders_list(request: web.Request) -> web.Response:
    cache = _cache(request)
    admin = _admin_session(request)
    devices = cache.senders_grouped()
    return _render(
        request,
        "senders.html",
        {
            "active": "senders",
            "devices": devices,
            "device_capability": _device_capability_view(
                cache, admin, [d.device_id for d in devices],
            ),
        },
    )


async def receivers_list(request: web.Request) -> web.Response:
    cache = _cache(request)
    admin = _admin_session(request)
    devices = cache.receivers_grouped()
    return _render(
        request,
        "receivers.html",
        {
            "active": "receivers",
            "devices": devices,
            "device_capability": _device_capability_view(
                cache, admin, [d.device_id for d in devices],
            ),
        },
    )


async def senders_caps(request: web.Request) -> web.Response:
    cache = _cache(request)
    sender_ids = _parse_csv(request.query.get("sender_ids"))
    selected: list[dict[str, Any]] = [
        s for s in (cache.get_sender(sid) for sid in sender_ids) if s is not None
    ]
    filters = {
        "by_format":        request.query.get("byFormat", "").strip(),
        "by_layer":         request.query.get("byLayer", "").strip(),
        "by_compatibility": request.query.get("byCompatibility", "").strip(),
    }
    return _render(
        request,
        "senders_caps.html",
        {
            "active": "senders",
            "senders": selected,
            "caps_view": _build_caps_view(cache, selected, filters=filters),
            "filters": filters,
            "sender_ids_csv": ",".join(sid for sid in sender_ids),
        },
    )


async def senders_configure(request: web.Request) -> web.Response:
    cache = _cache(request)
    client = _remote_client(request)
    forwarded = _forwarded_auth(request)
    sender_ids = _parse_csv(request.query.get("sender_ids"))
    # Each sender may carry its own constraint-set choice, named
    # ``conset_<sender_id>`` in the caps-form submission. Collect them
    # into a {sender_id: index} map.
    conset_by_sender: dict[str, int] = {}
    for sid in sender_ids:
        val = request.query.get(f"conset_{sid}", "")
        if val.lstrip("-").isdigit():
            conset_by_sender[sid] = int(val)
    senders: list[dict[str, Any]] = [
        s for s in (cache.get_sender(sid) for sid in sender_ids) if s is not None
    ]
    filters = {
        "by_format":        request.query.get("byFormat", "").strip(),
        "by_layer":         request.query.get("byLayer", "").strip(),
        "by_compatibility": request.query.get("byCompatibility", "").strip(),
    }
    sender_state = await _sender_state_map(
        cache, client, forwarded, senders, trace_id=_trace_id(request),
        admin=_admin_session(request),
        request=request,
    )
    privacy_view = await _build_privacy_view(cache, client, request, senders, [])
    return _render(
        request,
        "senders_configure.html",
        {
            "active": "senders",
            "senders": senders,
            "conset_by_sender": conset_by_sender,
            "config_view": _build_configure_view(
                cache, senders, conset_by_sender, filters,
            ),
            "filters": filters,
            "sender_ids_csv": ",".join(sender_ids),
            "sender_state":   sender_state,
            # Any-wise OR so the Constrain / Activate toggles render
            # green if *at least one* selected sender is currently in
            # that state. The matching flip-off action (unconstrain /
            # deactivate) is then always meaningful — it unconditionally
            # drives every sender to the off state, regardless of which
            # were on to begin with.
            "any_constrained": any(s["constrained"] for s in sender_state.values()),
            "any_sender_active":
                any(s["active"] for s in sender_state.values()),
            # True iff every selected sender's device advertises the
            # IS-11 stream-compat control. When False the template
            # renders the master Constrain toggle ``disabled`` — PUT
            # /constraints/active on a non-IS-11 sender is a 404.
            "all_is11_supported": bool(sender_state) and all(
                s.get("is11_supported", False)
                for s in sender_state.values()
            ),
            # True iff EVERY selected sender's owning Device is writable
            # (no read-blocking OR write-blocking inaccessible reasons).
            # When False the template ``disabled``s the master
            # Constrain + Activate toggles — pointless to fire an
            # action that's guaranteed to 401/403 on at least one
            # sender. Tooltip lists the offenders.
            "all_senders_writable": bool(sender_state) and all(
                not s.get("inaccessible_reasons", {}).get("read")
                and not s.get("inaccessible_reasons", {}).get("write")
                for s in sender_state.values()
            ),
            "privacy_view": privacy_view,
        },
    )


async def receivers_compatible(request: web.Request) -> web.Response:
    """Compatible-senders page — same grouped layout as ``/senders``,
    but each device's natural groups / members are filtered by the
    receiver(s)' caps.

    ``mode=single``  — keep every sender (in its natural-group) whose
                       caps intersect *every* selected receiver.
                       Devices/groups with no surviving members are
                       dropped.
    ``mode=group``   — treat the selected receivers as one natural
                       group; keep sender natural groups with a
                       matching role shape AND per-role caps compat.
                       The template hides individual members in this
                       mode (``group_only=True``) so the operator can
                       only pick the whole group via its radio.
    ``mode=subset``  — treat the selected receivers as a SUBSET of
                       one natural group; keep sender natural groups
                       whose leaf signature is a multiset-superset of
                       the subset's signature (so a MUX sender's
                       audio legs can cover an audio-only subset).
                       Each candidate group is presented with only
                       its matched legs as member rows. Cross-group
                       subsets are rejected 400 — the UI prevents
                       them but the server must defend against
                       hand-crafted URLs.
    """
    cache = _cache(request)
    receiver_ids = _parse_csv(request.query.get("receiver_ids"))
    mode_raw = request.query.get("mode", "single")
    if mode_raw == "group":
        mode = "group"
    elif mode_raw == "subset":
        mode = "subset"
    else:
        mode = "single"

    receivers: list[dict[str, Any]] = [
        r for r in (cache.get_receiver(rid) for rid in receiver_ids)
        if r is not None
    ]

    # Server-side defence: mode=subset requires all receivers to share
    # one natural group. The UI's ``_confineSelectionToOneGroup`` in
    # controller.js already blocks cross-group selection; this guard
    # catches hand-crafted URLs. Normal empty / single-receiver cases
    # fall through and render the usual empty-result page.
    if mode == "subset" and receivers:
        from nmos.controller.grouping import extract_group_hint
        seen_keys: set[tuple[str, tuple[str, int]]] = set()
        for r in receivers:
            hint = extract_group_hint(r.get("tags"))
            if hint is None:
                raise web.HTTPBadRequest(
                    reason="subset mode: receiver lacks a group hint",
                )
            seen_keys.add((r.get("device_id", "") or "", hint.key))
        if len(seen_keys) > 1:
            raise web.HTTPBadRequest(
                reason="subset mode: receivers must all share one natural group",
            )

    devices = _build_compatible_senders_view(cache, receivers, mode)
    receiver_ids_csv = ",".join(r.get("id", "") or "" for r in receivers)
    # Subset header: summarise the subset's leaf signature (e.g.
    # "AUDIO 0, AUDIO 1") so the operator can confirm which legs the
    # matcher used. Safe to compute unconditionally — empty when not
    # in subset mode.
    subset_leaves_label = ""
    if mode == "subset" and receivers:
        from nmos.controller.grouping import extract_group_hint
        leaves: list[str] = []
        for r in receivers:
            h = extract_group_hint(r.get("tags"))
            if h is not None:
                leaves.append(f"{h.format} {h.role}")
        subset_leaves_label = ", ".join(sorted(leaves))
    return _render(
        request,
        "receivers_compatible_senders.html",
        {
            "active":              "receivers",
            "receivers":           receivers,
            "receiver_ids_csv":    receiver_ids_csv,
            "mode":                mode,
            "devices":             devices,
            "subset_leaves_label": subset_leaves_label,
            "device_capability":   _device_capability_view(
                cache, _admin_session(request),
                [d.device_id for d in devices],
            ),
        },
    )


async def receivers_view_caps(request: web.Request) -> web.Response:
    """Read-only view of the selected receivers' own capabilities —
    no sender intersection, no follow-up flow.

    Same table shape and filters as the senders caps page (reuses
    ``_build_caps_view`` which treats its input uniformly — sender or
    receiver — because each carries the same ``caps.constraint_sets``
    shape). The template ``receivers_view_caps.html`` drops the
    form/submit/radios since there's nothing to pick here.
    """
    cache = _cache(request)
    receiver_ids = _parse_csv(request.query.get("receiver_ids"))
    receivers: list[dict[str, Any]] = [
        r for r in (cache.get_receiver(rid) for rid in receiver_ids)
        if r is not None
    ]
    filters = {
        "by_format":        request.query.get("byFormat", "").strip(),
        "by_layer":         request.query.get("byLayer", "").strip(),
        "by_compatibility": request.query.get("byCompatibility", "").strip(),
    }
    return _render(
        request,
        "receivers_view_caps.html",
        {
            "active":            "receivers",
            "receivers":         receivers,
            "caps_view":         _build_caps_view(
                cache, receivers, filters=filters,
            ),
            "filters":           filters,
            "receiver_ids_csv":  ",".join(receiver_ids),
        },
    )


async def receivers_caps(request: web.Request) -> web.Response:
    """Same shape and layout as the senders caps page — one CS picker
    per (selected) sender — but with the receivers' ids threaded
    through as a hidden field so the configure page knows which
    receivers to activate when the operator finishes constraining.

    Senders whose caps don't intersect their paired receiver's caps
    are dropped here (should be empty in practice — ``/receivers/
    compatible-senders`` already filters — but we guard against URL
    tampering). If the filter leaves no senders, the template renders
    a "no overlap" empty state with a "skip constraining" escape hatch.

    Hard requirement: ``#sender_ids == #receiver_ids``. The
    compatible-senders page enforces this in the browser; direct
    links / tampered URLs with mismatched counts are rejected 400
    here.

    Pairing rule depends on ``mode`` (threaded through from the
    compatible-senders page):

      * ``mode=group`` / ``mode=subset`` — pair by
        ``(format, role_index)`` leaf identity. Cross-role pairing
        would be ambiguous across multi-leg selections, so it's
        rejected 400.
      * ``mode=single`` (or missing) — pair by URL order. Since
        single mode always has K=1 receiver, URL order is
        unambiguous and cross-role pairing is allowed (e.g.
        receiver ``AUDIO 0`` ↔ sender ``AUDIO 1``).
    """
    cache = _cache(request)
    receiver_ids = _parse_csv(request.query.get("receiver_ids"))
    sender_ids = _parse_csv(request.query.get("sender_ids"))
    mode_raw = request.query.get("mode", "single")
    if mode_raw not in ("single", "group", "subset"):
        mode_raw = "single"
    if len(sender_ids) != len(receiver_ids) or not receiver_ids:
        raise web.HTTPBadRequest(
            reason="sender_ids and receiver_ids must have matching non-empty counts",
        )
    receivers: list[dict[str, Any]] = [
        r for r in (cache.get_receiver(rid) for rid in receiver_ids) if r is not None
    ]
    senders: list[dict[str, Any]] = [
        s for s in (cache.get_sender(sid) for sid in sender_ids) if s is not None
    ]
    # Pair sender ↔ receiver — rule depends on mode.
    pairs: list[tuple[dict[str, Any], dict[str, Any]]]
    if mode_raw in ("group", "subset"):
        # Multi-leg pairing: identity-based, cross-role rejected.
        try:
            pairs = pair_by_identity(senders, receivers)
        except ValueError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
    else:
        # Single mode (K=1): URL-order zip is unambiguous and
        # permits cross-role pairing (e.g. receiver AUDIO 0 ↔ sender
        # AUDIO 1). Format + transport must still match — the
        # compatible-senders page already enforces this, so a
        # mismatch here means a stale or tampered URL. Reject 400.
        _reject_incompatible_single_pair(senders, receivers)
        pairs = list(zip(senders, receivers))
    filtered_senders: list[dict[str, Any]] = []
    for s, r in pairs:
        narrowed = filter_sender_cs_by_receiver(s, r)
        if narrowed.get("caps", {}).get("constraint_sets"):
            filtered_senders.append(narrowed)
    filters = {
        "by_format":        request.query.get("byFormat", "").strip(),
        "by_layer":         request.query.get("byLayer", "").strip(),
        "by_compatibility": request.query.get("byCompatibility", "").strip(),
    }
    return _render(
        request,
        "receivers_caps.html",
        {
            "active":            "receivers",
            "receivers":         receivers,
            "senders":           filtered_senders,
            "caps_view":         _build_caps_view(
                cache, filtered_senders, filters=filters,
            ),
            "filters":           filters,
            "receiver_ids_csv":  ",".join(rid for rid in receiver_ids),
            "sender_ids_csv":    ",".join(
                s.get("id", "") or "" for s in filtered_senders
            ),
            "mode":              mode_raw,
        },
    )


async def receivers_configure(request: web.Request) -> web.Response:
    """Receivers-path configure page. Same shape as the senders-path
    configure page — one section per selected sender with its chosen
    constraint set and editable widgets — plus a **Receivers Activate**
    toggle at the top that drives per-pair receiver activation
    (sender[i] → receiver[i]).

    Sender-side Constrain / Activate behave identically to
    ``/senders/configure`` (same JS, same layout, same per-sender
    result cells).
    """
    cache = _cache(request)
    client = _remote_client(request)
    forwarded = _forwarded_auth(request)
    receiver_ids = _parse_csv(request.query.get("receiver_ids"))
    sender_ids = _parse_csv(request.query.get("sender_ids"))
    mode_raw = request.query.get("mode", "single")
    if mode_raw not in ("single", "group", "subset"):
        mode_raw = "single"
    # Same guard as ``/receivers/caps``: counts must match. Direct
    # links / tampered URLs with mismatched counts are rejected 400.
    if len(sender_ids) != len(receiver_ids) or not receiver_ids:
        raise web.HTTPBadRequest(
            reason="sender_ids and receiver_ids must have matching non-empty counts",
        )
    # Per-sender ``conset_<sid>`` selections forwarded from the caps
    # page (same convention as the senders configure page).
    conset_by_sender: dict[str, int] = {}
    for sid in sender_ids:
        val = request.query.get(f"conset_{sid}", "")
        if val.lstrip("-").isdigit():
            conset_by_sender[sid] = int(val)
    receivers: list[dict[str, Any]] = [
        r for r in (cache.get_receiver(rid) for rid in receiver_ids) if r is not None
    ]
    senders: list[dict[str, Any]] = [
        s for s in (cache.get_sender(sid) for sid in sender_ids) if s is not None
    ]
    # Pair sender ↔ receiver — rule depends on mode (same branch as
    # ``receivers_caps``). Each sender row on this page surfaces its
    # paired receiver + a dedicated receiver-result cell. The paired
    # receiver is decorated with its device serial and ``host:port``
    # address (from the device's sr-ctrl control URL) so the
    # operator can identify the physical target at a glance — the
    # raw receiver label tends to be generic ("Net Stream Audio 0")
    # and not useful for distinguishing receivers on different
    # nodes.
    from nmos.controller.grouping import device_address as _device_address
    from nmos.controller.grouping import device_serial as _device_serial

    pairs: list[tuple[dict[str, Any], dict[str, Any]]]
    if mode_raw in ("group", "subset"):
        try:
            pairs = pair_by_identity(senders, receivers)
        except ValueError as exc:
            raise web.HTTPBadRequest(reason=str(exc))
    else:
        _reject_incompatible_single_pair(senders, receivers)
        pairs = list(zip(senders, receivers))

    pair_by_sender: dict[str, dict[str, Any]] = {}
    for s, r in pairs:
        sid = s.get("id", "") or ""
        if not sid:
            continue
        rid = r.get("id", "") or ""
        rdev = cache.get_device(r.get("device_id", "") or "") or {}
        pair_by_sender[sid] = {
            "id":             rid,
            "label":          r.get("label", "") or "",
            "device_serial":  _device_serial(rdev) or "",
            "device_address": _device_address(rdev) or "",
        }
    filters = {
        "by_format":        request.query.get("byFormat", "").strip(),
        "by_layer":         request.query.get("byLayer", "").strip(),
        "by_compatibility": request.query.get("byCompatibility", "").strip(),
    }
    # Narrow each sender's ``constraint_sets`` by its identity-paired
    # receiver (same rule as ``/receivers/caps``). A sender with zero
    # surviving CSs is kept here (rather than dropped) so the
    # operator still sees its row on the configure page — the CS
    # editor will render the "no constraint set" placeholder. Order
    # follows ``pairs`` (which follows the receivers' order).
    filtered_senders = [
        filter_sender_cs_by_receiver(s, r)
        for s, r in pairs
    ]
    sender_state = await _sender_state_map(
        cache, client, forwarded, filtered_senders,
        trace_id=_trace_id(request),
        admin=_admin_session(request),
        request=request,
    )
    receiver_state = _receiver_state_map(
        cache, receivers, admin=_admin_session(request),
    )
    privacy_view = await _build_privacy_view(
        cache, client, request, filtered_senders, receivers,
    )
    return _render(
        request,
        "receivers_configure.html",
        {
            "active":            "receivers",
            "receivers":         receivers,
            "senders":           filtered_senders,
            "pair_by_sender":    pair_by_sender,
            "conset_by_sender":  conset_by_sender,
            "config_view":       _build_configure_view(
                cache, filtered_senders, conset_by_sender, filters,
            ),
            "filters":           filters,
            "receiver_ids_csv":  ",".join(receiver_ids),
            "sender_ids_csv":    ",".join(sender_ids),
            "mode":              mode_raw,
            "sender_state":      sender_state,
            "receiver_state":    receiver_state,
            "privacy_view":      privacy_view,
            # Any-wise OR — green if at least one resource is in the
            # "on" state. Flip-off then uniformly drives everyone
            # off, so Unconstrain / Deactivate are always safe to
            # press without the operator having to reason about
            # mixed-state pages.
            "any_constrained":
                any(s["constrained"] for s in sender_state.values()),
            "any_sender_active":
                any(s["active"] for s in sender_state.values()),
            "any_receiver_active":
                any(r["active"] for r in receiver_state.values()),
            # Same gate as the senders-path: master Constrain toggle
            # disabled when any selected sender's device doesn't
            # advertise IS-11.
            "all_is11_supported": bool(sender_state) and all(
                s.get("is11_supported", False)
                for s in sender_state.values()
            ),
            # Phase 5: every selected sender + receiver must be on a
            # device the admin can read AND write. Used by the
            # template to ``disabled`` the master Constrain / Sender
            # Activate / Receiver Activate toggles — pointless to fire
            # them when at least one resource is guaranteed to 401/403.
            "all_senders_writable": bool(sender_state) and all(
                not s.get("inaccessible_reasons", {}).get("read")
                and not s.get("inaccessible_reasons", {}).get("write")
                for s in sender_state.values()
            ),
            "all_receivers_writable": bool(receiver_state) and all(
                not r.get("inaccessible_reasons", {}).get("read")
                and not r.get("inaccessible_reasons", {}).get("write")
                for r in receiver_state.values()
            ),
            # Cross-direction navigation (Track D): jump-links to the
            # reverse-flow A/V/D / USB / TB pair between the same two
            # Nodes. Resolved on the server-side cache snapshot at
            # render time; disabled with a tooltip when the
            # cardinality rule fails or the target pair is absent.
            "reverse_links": _build_reverse_links(
                cache, receivers, filtered_senders, mode_raw,
            ),
        },
    )


# ---------------------------------------------------------------------------
# JSON handlers
# ---------------------------------------------------------------------------

async def api_list_senders(request: web.Request) -> web.Response:
    cache = _cache(request)
    return web.json_response({
        "senders": [
            _grouped_summary(d) for d in cache.senders_grouped()
        ],
    })


async def api_list_receivers(request: web.Request) -> web.Response:
    cache = _cache(request)
    return web.json_response({
        "receivers": [
            _grouped_summary(d) for d in cache.receivers_grouped()
        ],
    })


async def api_compatible_senders(request: web.Request) -> web.Response:
    cache = _cache(request)
    receiver_id = request.match_info["receiver_id"]
    receiver = cache.get_receiver(receiver_id)
    if receiver is None:
        return web.json_response(
            {"error": "receiver not found"}, status=404,
        )
    senders = compatible_senders(receiver, cache.all_senders())
    return web.json_response({
        "receiver_id": receiver_id,
        "senders": [
            {"id": s.get("id"), "label": s.get("label")}
            for s in senders
        ],
    })


async def api_sender_constrain(request: web.Request) -> web.Response:
    return await _proxy_sender_constraints(request, method="PUT")


async def api_sender_unconstrain(request: web.Request) -> web.Response:
    return await _proxy_sender_constraints(request, method="DELETE")


async def api_sender_activate(request: web.Request) -> web.Response:
    """Activate a sender.

    Body (optional): ``{"privacy": {"protocol", "mode", "curve"},
    "receiver_id": "<uuid>"}``.

    * No body / no ``privacy`` key → plain activate (legacy path).
    * With ``privacy`` → build ``transport_params[0]`` carrying
      ``ext_privacy_protocol`` / ``mode`` / ``ecdh_curve`` (curve
      only when mode is ECDH).
    * ECDH modes additionally require ``receiver_id`` — the
      controller GETs the paired receiver's IS-05 ``/active/`` to
      read ``ext_privacy_ecdh_receiver_public_key`` and injects it
      into the sender's PATCH body.
    * ``PEP-Exclusive-Authorization`` is injected from the admin's
      active reservation on the sender's device (if any).
    """
    body = await _safe_json_body(request)
    privacy, receiver_id = _parse_privacy_body(body)
    if privacy is None:
        return await _proxy_sender_staged(request, master_enable=True)
    return await _activate_sender_with_privacy(
        request, privacy=privacy, paired_receiver_id=receiver_id,
    )


async def api_sender_deactivate(request: web.Request) -> web.Response:
    return await _proxy_sender_staged(request, master_enable=False)


async def api_receiver_deactivate(request: web.Request) -> web.Response:
    """Deactivate a receiver — PATCH staged with
    ``master_enable=False`` and an ``activate_immediate`` activation.
    No sender-side call / transport-file fetch is needed (the
    receiver is simply going idle).
    """
    cache = _cache(request)
    client = _remote_client(request)
    receiver_id = request.match_info["receiver_id"]

    receiver = cache.get_receiver(receiver_id)
    if receiver is None:
        return web.json_response({"error": "receiver not found"}, status=404)
    r_device = cache.get_device(receiver.get("device_id", "") or "")
    if r_device is None:
        return web.json_response(
            {"error": "owning device not in cache"}, status=404,
        )
    r_base = client.connection_api_base(r_device)
    if r_base is None:
        return web.json_response(
            {"error": "connection-management control URL missing on device"},
            status=409,
        )
    body_patch = {
        "master_enable": False,
        "activation": {"mode": ActivateImmediate.s},
    }
    forwarded = _headers_with_reservation(
        request, r_device.get("id", "") or "",
    )
    result = await client.patch_receiver_staged(
        r_base, receiver_id, body_patch, forwarded,
        trace_id=_trace_id(request),
    )
    return web.json_response(
        _remote_envelope(
            result, request=request,
            device_id=r_device.get("id", "") or "",
        ),
        status=200 if result.status == 200 else 502,
    )


async def api_receiver_activate(request: web.Request) -> web.Response:
    """Activate a receiver by pulling the SDP from the sender and
    PATCHing the receiver's staged endpoint.

    Body: ``{"sender_id": "<uuid>", "privacy": {"protocol", "mode",
    "curve"}?}``. The ``privacy`` block is optional — absent means
    plain activate (legacy path). When present, the controller:

    1. GETs the paired sender's IS-05 ``/active/`` and reads
       ``ext_privacy_key_generator`` / ``key_version`` / ``key_id``
       (and ``ecdh_sender_public_key`` for ECDH modes).
    2. Builds the receiver's ``transport_params[0]`` with
       ``ext_privacy_protocol`` / ``mode`` / ``ecdh_curve`` from the
       operator's choice + the forwarded key_* fields.
    3. PATCHes the receiver's staged endpoint with
       ``master_enable=true``, ``sender_id``, ``transport_file``
       (the SDP) and the assembled ``transport_params``.

    Forwarded ``PEP-Exclusive-Authorization`` comes from the admin's
    active reservation on the receiver's device, when one exists.
    """
    cache = _cache(request)
    client = _remote_client(request)
    receiver_id = request.match_info["receiver_id"]
    body = await _safe_json_body(request)
    sender_id = body.get("sender_id") if isinstance(body, dict) else None
    if not isinstance(sender_id, str) or not sender_id:
        return web.json_response({"error": "missing sender_id"}, status=400)
    privacy, _ = _parse_privacy_body(body)

    receiver = cache.get_receiver(receiver_id)
    sender = cache.get_sender(sender_id)
    if receiver is None or sender is None:
        return web.json_response(
            {"error": "sender or receiver not found"}, status=404,
        )
    r_device = cache.get_device(receiver.get("device_id", "") or "")
    s_device = cache.get_device(sender.get("device_id", "") or "")
    if r_device is None or s_device is None:
        return web.json_response(
            {"error": "owning device not in cache"}, status=404,
        )
    r_base = client.connection_api_base(r_device)
    s_base = client.connection_api_base(s_device)
    if r_base is None or s_base is None:
        return web.json_response(
            {"error": "connection-management control URL missing on device"},
            status=409,
        )

    r_device_id = r_device.get("id", "") or ""
    s_device_id = s_device.get("id", "") or ""
    forwarded_s = _headers_with_reservation(request, s_device_id)
    forwarded_r = _headers_with_reservation(request, r_device_id)

    trace_id = _trace_id(request)
    sdp_result = await client.get_sender_transportfile(
        s_base, sender_id, forwarded_s, trace_id=trace_id,
    )
    if sdp_result.status != 200 or not isinstance(sdp_result.body, str):
        return web.json_response(
            {
                "error": "failed to fetch sender transportfile",
                "status": sdp_result.status,
                "body": sdp_result.body,
            },
            status=502,
        )

    # Assemble the per-leg transport_params for the receiver PATCH when
    # PEP is being configured. Non-PEP path skips this block entirely.
    transport_params: list[dict[str, Any]] | None = None
    if privacy is not None:
        ecdh = is_ecdh_mode(privacy.get("mode"))
        leg: dict[str, Any] = {
            EXT_PRIVACY_PROTOCOL: privacy["protocol"],
            EXT_PRIVACY_MODE: privacy["mode"],
        }
        if ecdh and privacy.get("curve"):
            leg[EXT_PRIVACY_ECDH_CURVE] = privacy["curve"]
        # GET sender's IS-05 /active/ to read the forwarded fields.
        active_result = await client.get_sender_active(
            s_base, sender_id, forwarded_s, trace_id=trace_id,
        )
        if active_result.status != 200 or not isinstance(active_result.body, dict):
            return web.json_response(
                {
                    "error": "failed to fetch sender active params for PEP",
                    "status": active_result.status,
                    "body": active_result.body,
                },
                status=502,
            )
        forwarded_fields = sender_to_receiver_fields(
            active_result.body.get("transport_params"), ecdh=ecdh,
        )
        leg.update(forwarded_fields)
        transport_params = [leg]

    body_patch: dict[str, Any] = {
        "master_enable": True,
        "sender_id": sender_id,
        "activation": {"mode": ActivateImmediate.s},
        "transport_file": {
            "data": sdp_result.body,
            "type": "application/sdp",
        },
    }
    if transport_params is not None:
        body_patch["transport_params"] = transport_params

    result = await client.patch_receiver_staged(
        r_base, receiver_id, body_patch, forwarded_r, trace_id=trace_id,
    )
    return web.json_response(
        _remote_envelope(result, request=request, device_id=r_device_id),
        status=200 if result.status == 200 else 502,
    )


# ---------------------------------------------------------------------------
# Internals — sender proxy helpers
# ---------------------------------------------------------------------------

async def _proxy_sender_constraints(
    request: web.Request, *, method: str,
) -> web.Response:
    cache = _cache(request)
    client = _remote_client(request)
    sender_id = request.match_info["sender_id"]

    sender = cache.get_sender(sender_id)
    if sender is None:
        return web.json_response({"error": "sender not found"}, status=404)
    device = cache.get_device(sender.get("device_id", "") or "")
    if device is None:
        return web.json_response({"error": "owning device not in cache"}, status=404)
    # Active constraints live on the IS-11 streamcompatibility API —
    # PUT/DELETE ``…/senders/{id}/constraints/active``. The
    # controller resolves this by reading the device's
    # ``urn:x-nmos:control:stream-compat/v1.0`` control URL,
    # NOT the IS-05 ``sr-ctrl`` URL used for staged activations.
    base = client.streamcompat_api_base(device)
    if base is None:
        return web.json_response(
            {"error": "streamcompatibility control URL missing on device"},
            status=409,
        )
    # IS-11 PUT/DELETE is state-changing on the remote Node, so the
    # reservation bearer MUST travel on it too — same as IS-05 staged
    # activations. Route it through ``_headers_with_reservation`` so
    # the header name matches the remote's OAuth2 configuration.
    forwarded = _headers_with_reservation(
        request, device.get("id", "") or "",
    )

    trace_id = _trace_id(request)
    if method == "PUT":
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "body must be a JSON object"}, status=400)
        result = await client.put_sender_active_constraints(
            base, sender_id, body, forwarded, trace_id=trace_id,
        )
    else:
        result = await client.delete_sender_active_constraints(
            base, sender_id, forwarded, trace_id=trace_id,
        )

    return web.json_response(
        _remote_envelope(
            result, request=request,
            device_id=device.get("id", "") or "",
        ),
        status=200 if result.status in (200, 204) else 502,
    )


async def _proxy_sender_staged(
    request: web.Request, *, master_enable: bool,
) -> web.Response:
    cache = _cache(request)
    client = _remote_client(request)
    sender_id = request.match_info["sender_id"]

    sender = cache.get_sender(sender_id)
    if sender is None:
        return web.json_response({"error": "sender not found"}, status=404)
    device = cache.get_device(sender.get("device_id", "") or "")
    if device is None:
        return web.json_response({"error": "owning device not in cache"}, status=404)
    base = client.connection_api_base(device)
    if base is None:
        return web.json_response(
            {"error": "connection-management control URL missing on device"},
            status=409,
        )
    forwarded = _headers_with_reservation(request, device.get("id", "") or "")

    body = {
        "master_enable": master_enable,
        "activation": {"mode": ActivateImmediate.s},
    }
    result = await client.patch_sender_staged(
        base, sender_id, body, forwarded, trace_id=_trace_id(request),
    )
    return web.json_response(
        _remote_envelope(
            result, request=request,
            device_id=device.get("id", "") or "",
        ),
        status=200 if result.status == 200 else 502,
    )


# ---------------------------------------------------------------------------
# Privacy (PEP) activation + reservation endpoints
# ---------------------------------------------------------------------------

async def _safe_json_body(request: web.Request) -> Any:
    """Parse the JSON body if present; return ``None`` on empty body
    or parse error. Lets activate handlers stay backward-compatible
    when the browser sends no body at all (existing non-PEP callers).
    """
    raw = await request.text()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _parse_privacy_body(
    body: Any,
) -> tuple[dict[str, str] | None, str | None]:
    """Extract the ``privacy`` block and optional ``receiver_id`` from
    an activate request body.

    Returns ``(privacy, receiver_id)`` where ``privacy`` is
    ``{"protocol", "mode", "curve"?}`` with at least protocol + mode
    present, or ``None`` if no privacy block was supplied / it was
    malformed. Malformed blocks silently fall back to ``None`` — the
    activate then runs in legacy non-PEP mode. (Strict validation
    happens at activation orchestration, after the paired resource
    has been resolved.)
    """
    if not isinstance(body, dict):
        return None, None
    raw_privacy: Any = body.get("privacy")
    raw_receiver_id: Any = body.get("receiver_id")
    receiver_id = (
        raw_receiver_id if isinstance(raw_receiver_id, str) and raw_receiver_id
        else None
    )
    if not isinstance(raw_privacy, dict):
        return None, receiver_id
    protocol_any: Any = raw_privacy.get("protocol")
    mode_any: Any = raw_privacy.get("mode")
    curve_any: Any = raw_privacy.get("curve")
    if (not isinstance(protocol_any, str) or not protocol_any
            or not isinstance(mode_any, str) or not mode_any):
        return None, receiver_id
    privacy: dict[str, str] = {
        "protocol": protocol_any,
        "mode": mode_any,
    }
    if isinstance(curve_any, str) and curve_any:
        privacy["curve"] = curve_any
    return privacy, receiver_id


async def _activate_sender_with_privacy(
    request: web.Request,
    *,
    privacy: dict[str, str],
    paired_receiver_id: str | None,
) -> web.Response:
    """PATCH the sender's staged endpoint with PEP transport_params.

    For ECDH modes, first GETs the paired receiver's ``/active/`` to
    read its ``ext_privacy_ecdh_receiver_public_key`` and thread it
    into the sender's PATCH body. Non-ECDH modes only carry the
    selected protocol + mode (+ curve) — the sender generates its
    own iv / key_generator / etc. at activation time.
    """
    cache = _cache(request)
    client = _remote_client(request)
    sender_id = request.match_info["sender_id"]

    sender = cache.get_sender(sender_id)
    if sender is None:
        return web.json_response({"error": "sender not found"}, status=404)
    s_device = cache.get_device(sender.get("device_id", "") or "")
    if s_device is None:
        return web.json_response(
            {"error": "owning device not in cache"}, status=404,
        )
    s_base = client.connection_api_base(s_device)
    if s_base is None:
        return web.json_response(
            {"error": "connection-management control URL missing on device"},
            status=409,
        )
    forwarded_s = _headers_with_reservation(request, s_device.get("id", "") or "")

    ecdh = is_ecdh_mode(privacy.get("mode"))
    leg: dict[str, Any] = {
        EXT_PRIVACY_PROTOCOL: privacy["protocol"],
        EXT_PRIVACY_MODE: privacy["mode"],
    }
    if ecdh and privacy.get("curve"):
        leg[EXT_PRIVACY_ECDH_CURVE] = privacy["curve"]
    if ecdh:
        if not paired_receiver_id:
            return web.json_response(
                {
                    "error": "ECDH mode requires 'receiver_id' in the body — "
                             "controller must read the receiver's public key "
                             "before sender activation",
                },
                status=400,
            )
        receiver = cache.get_receiver(paired_receiver_id)
        if receiver is None:
            return web.json_response(
                {"error": "paired receiver not found"}, status=404,
            )
        r_device = cache.get_device(receiver.get("device_id", "") or "")
        if r_device is None:
            return web.json_response(
                {"error": "paired receiver device not in cache"}, status=404,
            )
        r_base = client.connection_api_base(r_device)
        if r_base is None:
            return web.json_response(
                {
                    "error": "paired receiver's connection control URL missing",
                },
                status=409,
            )
        forwarded_r = _headers_with_reservation(
            request, r_device.get("id", "") or "",
        )
        r_active = await client.get_receiver_active(
            r_base, paired_receiver_id, forwarded_r,
            trace_id=_trace_id(request),
        )
        if r_active.status != 200 or not isinstance(r_active.body, dict):
            return web.json_response(
                {
                    "error": "failed to fetch receiver active params for ECDH",
                    "status": r_active.status,
                    "body": r_active.body,
                },
                status=502,
            )
        ecdh_fields = receiver_to_sender_fields(
            r_active.body.get("transport_params"),
        )
        if EXT_PRIVACY_ECDH_RECEIVER_PUBLIC_KEY not in ecdh_fields:
            return web.json_response(
                {
                    "error": "receiver has no ECDH public key yet — "
                             "deactivate and reactivate the receiver to "
                             "regenerate its key, then retry",
                },
                status=409,
            )
        leg.update(ecdh_fields)

    body_patch: dict[str, Any] = {
        "master_enable": True,
        "activation": {"mode": ActivateImmediate.s},
        "transport_params": [leg],
    }
    result = await client.patch_sender_staged(
        s_base, sender_id, body_patch, forwarded_s,
        trace_id=_trace_id(request),
    )
    return web.json_response(
        _remote_envelope(
            result, request=request,
            device_id=s_device.get("id", "") or "",
        ),
        status=200 if result.status == 200 else 502,
    )


async def api_privacy_options(request: web.Request) -> web.Response:
    """``GET /api/privacy/options?ids=<csv>`` — compute the PEP
    intersection for a mixed sender/receiver selection.

    Returns ``{"protocols": [...], "modes": [...], "curves": [...],
    "exclusivity_ok": bool}``. Empty ``protocols``/``modes`` means
    PEP cannot be negotiated on this selection; the browser then
    renders the "cannot negotiate" banner.

    ``ids`` entries that aren't found in the cache are silently
    skipped (mirrors other page-render handlers).
    """
    cache = _cache(request)
    client = _remote_client(request)
    ids = _parse_csv(request.query.get("ids"))
    if not ids:
        return web.json_response({
            "protocols": [], "modes": [], "curves": [],
            "exclusivity_ok": False,
        })

    sender_constraints: list[Any] = []
    receiver_constraints: list[Any] = []
    sender_devices: list[dict[str, Any]] = []
    receiver_devices: list[dict[str, Any]] = []

    # For each selected resource, find its is05 base + fetch the
    # IS-05 transport-parameter constraints. GETs run concurrently.
    fetches: list[asyncio.Task[tuple[str, RemoteCallResult]]] = []
    for rid in ids:
        sender = cache.get_sender(rid)
        receiver = None if sender is not None else cache.get_receiver(rid)
        resource = sender if sender is not None else receiver
        if resource is None:
            continue
        device = cache.get_device(resource.get("device_id", "") or "")
        if device is None:
            continue
        base = client.connection_api_base(device)
        if base is None:
            continue
        forwarded = _headers_with_reservation(
            request, device.get("id", "") or "",
        )
        if sender is not None:
            sender_devices.append(device)
            fetches.append(asyncio.create_task(
                _get_constraints(
                    client, base, rid, "senders", forwarded,
                    trace_id=_trace_id(request),
                ),
            ))
        else:
            receiver_devices.append(device)
            fetches.append(asyncio.create_task(
                _get_constraints(
                    client, base, rid, "receivers", forwarded,
                    trace_id=_trace_id(request),
                ),
            ))

    results = await asyncio.gather(*fetches) if fetches else []
    for kind_label, res in results:
        # /constraints/ returns a raw per-leg list; /active/ returns
        # ``{transport_params: [...]}``. Accept either shape so this
        # helper works against both remote-Node variants.
        tp: Any = None
        if res.status == 200:
            if isinstance(res.body, list):
                tp = res.body
            elif isinstance(res.body, dict):
                tp = res.body.get("transport_params")
        if kind_label == "senders":
            sender_constraints.append(tp)
        else:
            receiver_constraints.append(tp)

    # Collect unique owning NODEs of the selection — the reservation
    # service lives on the Node (not the Device) per NMOS.
    selected_nodes = _nodes_for_devices(
        cache, sender_devices + receiver_devices,
    )
    opts = compute_privacy_options(
        sender_constraints,
        receiver_constraints,
        sender_devices=selected_nodes,          # passed to resolver
        receiver_devices=[],                    # folded into sender list
        device_service_resolver=client.exclusive_service_base,
    )
    return web.json_response({
        "protocols": opts.protocols,
        "modes": opts.modes,
        "curves": opts.curves,
        "exclusivity_ok": opts.exclusivity_ok,
    })


def _nodes_for_devices(
    cache: ResourceCache, devices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Dedupe a device list down to the unique owning Node resources.

    Used everywhere the Privacy / reservation flow needs to reason
    about the Node (because the reservation service and session
    scope live at the Node level, not per-device).
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for d in devices:
        nid = d.get("node_id", "") or ""
        if not nid or nid in seen:
            continue
        seen.add(nid)
        node = cache.get_node(nid)
        if node is not None:
            out.append(node)
    return out


async def _get_constraints(
    client: RemoteNodeClient,
    is05_base: str,
    resource_id: str,
    kind: str,
    forwarded: dict[str, str],
    *,
    trace_id: str = "",
) -> tuple[str, RemoteCallResult]:
    """Per-resource GET of IS-05 transport-parameter constraints.

    Returns ``(kind_label, RemoteCallResult)`` so the caller can
    slot the result into the senders-or-receivers bucket without a
    second lookup.
    """
    if kind == "senders":
        result = await client.get_sender_constraints(
            is05_base, resource_id, forwarded, trace_id=trace_id,
        )
    else:
        result = await client.get_receiver_constraints(
            is05_base, resource_id, forwarded, trace_id=trace_id,
        )
    return kind, result


async def api_privacy_acquire(request: web.Request) -> web.Response:
    """``POST /api/privacy/acquire`` — acquire an exclusive session on
    every Node listed in the body's ``node_ids`` array.

    Body: ``{"node_ids": ["<node_uuid>", ...]}``.

    Reservation is per-Node (not per-device) — one session covers
    every sender/receiver on that Node, keyed by a per-instance map.

    Returns ``{"acquired": [...], "failed": [{node_id, reason}, ...]}``.
    Prior-successful acquires are left held when a later one fails —
    the caller can retry / release as needed.
    """
    cache = _cache(request)
    client = _remote_client(request)
    reservations = _reservations(request)
    admin = _admin_session(request)

    body = await _safe_json_body(request)
    raw_ids: Any = (body or {}).get("node_ids")
    if not isinstance(raw_ids, list):
        return web.json_response(
            {"error": "missing 'node_ids' array"}, status=400,
        )
    node_ids = [n for n in raw_ids if isinstance(n, str) and n]

    acquired: list[str] = []
    failed: list[dict[str, str]] = []
    for node_id in node_ids:
        node = cache.get_node(node_id)
        if node is None:
            failed.append({"node_id": node_id, "reason": "node not in cache"})
            continue
        info = client.exclusive_service_info(node)
        if info is None:
            failed.append({
                "node_id": node_id,
                "reason": "node does not advertise the reservation service "
                          "(urn:x-matrox:service:exclusive/v1.0)",
            })
            continue
        base, oauth2_on_remote = info
        try:
            await reservations.acquire(
                admin, node_id, base,
                oauth2_on_remote=oauth2_on_remote,
                trace_id=_trace_id(request),
            )
            acquired.append(node_id)
        except ReservationLocked as exc:
            failed.append({"node_id": node_id, "reason": str(exc)})
        except ReservationError as exc:
            failed.append({"node_id": node_id, "reason": str(exc)})

    status_code = 200 if not failed else (
        207 if acquired else 409  # Multi-Status-ish on partial; 409 on all-fail
    )
    return web.json_response(
        {"acquired": acquired, "failed": failed},
        status=status_code,
    )


async def api_privacy_release(request: web.Request) -> web.Response:
    """``POST /api/privacy/release`` — release one or more exclusive
    sessions.

    Body: ``{"node_ids": [...]}`` to release a specific set, or
    ``?all=true`` in the query string to release every session this
    admin currently holds (used by the browser unload beacon).
    """
    reservations = _reservations(request)
    admin = _admin_session(request)

    trace_id = _trace_id(request)
    if request.query.get("all", "").lower() == "true":
        await reservations.release_all(admin, trace_id=trace_id)
        return web.json_response({"released": True})

    body = await _safe_json_body(request)
    raw_ids: Any = (body or {}).get("node_ids")
    if not isinstance(raw_ids, list):
        return web.json_response(
            {"error": "missing 'node_ids' array"}, status=400,
        )
    node_ids = [n for n in raw_ids if isinstance(n, str) and n]
    for node_id in node_ids:
        await reservations.release(admin, node_id, trace_id=trace_id)
    return web.json_response({"released": node_ids})


# ---------------------------------------------------------------------------
# Debug endpoints (enabled only when --debug-in-depth is on)
# ---------------------------------------------------------------------------

# Cap the structured payload the browser is allowed to push into the
# debug log. Debug traces are a developer tool — we don't want a
# runaway page sending megabyte events.
_CLIENT_EVENT_MAX_BYTES = 4096


def _debug_trace(request: web.Request) -> Any:
    """Return the ``DebugTrace`` instance wired into the app, or ``None``.

    Handlers below gate on ``trace.enabled`` so that when tracing is
    off the endpoints return 404 — matches the "routes exist only
    under ``--debug-in-depth``" promise without requiring a separate
    registration path.
    """
    return request.app.get("controller_debug_trace")


async def api_debug_client_event(request: web.Request) -> web.Response:
    """``POST /api/debug/client-event`` — persist a browser-side event.

    Body: ``{"kind": "<str>", "trace_id": "<str>"?, ...fields}``. The
    trace id is either carried in the body (when the client knows one
    — e.g. a click event tied to a pending ``fetch``) or taken from
    the request's own trace id (the middleware stamped one on this
    POST itself). Extra fields pass through unchanged.

    Returns 404 when tracing is disabled — the JS tester detects this
    and stops posting. Returns 413 on an oversized payload.
    """
    trace = _debug_trace(request)
    if trace is None or not trace.enabled:
        return web.json_response({"error": "debug tracing disabled"}, status=404)

    raw = await request.read()
    if len(raw) > _CLIENT_EVENT_MAX_BYTES:
        return web.json_response(
            {"error": "payload too large"}, status=413,
        )
    try:
        payload = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "body must be a JSON object"}, status=400)

    kind_raw = payload.pop("kind", "client_event")
    kind = str(kind_raw) if isinstance(kind_raw, (str, int)) else "client_event"
    # Let the browser override the trace id when it has a better one
    # (e.g. a click that'll spawn a later fetch). Fall back to the
    # request's own id so solo events still correlate.
    trace_id_raw = payload.pop("trace_id", None)
    trace_id: str
    if isinstance(trace_id_raw, str) and trace_id_raw:
        trace_id = trace_id_raw
    else:
        trace_id = _trace_id(request)

    # Prefix the kind so client and server events are easy to tell
    # apart when you grep the log.
    trace.emit(f"client.{kind}", trace_id=trace_id, **payload)
    return web.json_response({"logged": True, "trace_id": trace_id})


async def api_debug_snapshot(request: web.Request) -> web.Response:
    """``GET /api/debug/snapshot`` — dump the controller's live state.

    Useful when the operator reports "I see X, it seems off" — the
    assistant can pull this snapshot and diff it against what the
    browser is showing. Scope is intentionally narrow:

      * ``log_path``           — path to the rotating JSONL file;
      * ``nodes / devices / senders / receivers`` — counts + id lists
        (not full bodies — those are one grep away in the log);
      * ``admin_sessions``     — number of active admin sessions;
      * ``reservations``       — one entry per live (admin, node)
        pair with its current token-freshness state.

    Query parameters:
      * ``?resource=<uuid>`` — if supplied, also include the full
        cached body for that sender / receiver / device / node under
        the ``resource`` key. No-match returns ``resource: null``.
        Handy for "show me exactly what the cache thinks this
        sender's caps look like" without hunting through the log.

    Returns 404 when tracing is off.
    """
    trace = _debug_trace(request)
    if trace is None or not trace.enabled:
        return web.json_response({"error": "debug tracing disabled"}, status=404)

    cache = _cache(request)
    reservations = _reservations(request)

    # Reservation summary: shape mirrors ReservationSession's debug
    # fields without exposing bearer tokens.
    import time as _time
    now = _time.monotonic()
    res_summary: list[dict[str, Any]] = []
    for sess in reservations.snapshot():
        res_summary.append({
            "admin": sess.admin_session_token[:6],
            "node_id": sess.node_id,
            "has_token": bool(sess.token),
            "expires_in": round(sess.expires_at - now, 1),
            "alive_in": round(sess.alive_until - now, 1),
        })

    admin_sessions: Any = request.app.get("controller_admin_sessions")
    admin_count = (
        len(admin_sessions.all_states())
        if admin_sessions is not None else 0
    )

    payload: dict[str, Any] = {
        "log_path": trace.log_path,
        "nodes": [n.get("id", "") for n in cache.all_nodes()],
        "devices": [d.get("id", "") for d in cache.all_devices()],
        "senders": [s.get("id", "") for s in cache.all_senders()],
        "receivers": [r.get("id", "") for r in cache.all_receivers()],
        "admin_sessions": admin_count,
        "reservations": res_summary,
    }

    # Optional targeted dump of a single resource body.
    rid = request.query.get("resource", "").strip()
    if rid:
        resource: Any = (
            cache.get_sender(rid)
            or cache.get_receiver(rid)
            or cache.get_device(rid)
            or cache.get_node(rid)
        )
        payload["resource"] = resource  # may be None if not in cache
        payload["resource_kind"] = (
            "sender" if cache.get_sender(rid)
            else "receiver" if cache.get_receiver(rid)
            else "device" if cache.get_device(rid)
            else "node" if cache.get_node(rid)
            else None
        )

    return web.json_response(payload)


# ---------------------------------------------------------------------------
# View-helper: caps summary
# ---------------------------------------------------------------------------

_FORMAT_URN_PREFIX = "urn:x-nmos:format:"
# The per-partition meta keys (format, layer, layer_compatibility_groups)
# use the x-matrox URN — NMOS standard BCP-004-01 defines only label /
# enabled / preference under ``urn:x-nmos:``, and the per-partition
# extensions are Matrox-defined. MatroxCCF emits them with x-matrox
# (see ``caps/MatroxCCF.py:88-95`` — the authoritative URN registry for
# this codebase). Every config.json in ``nmos/node/config/builtin/``
# and every controller script also uses x-matrox. An earlier
# revision of this file read x-nmos, which made the filter dropdowns
# on every caps / configure page silently fall back to the natural-
# group hint for EVERY constraint set — the MUX sender that should
# show {video, audio, mux} × {0, 1, …} collapsed to {mux} × {0}.
_CAPS_META_FORMAT = CapMetaFormat.s
_CAPS_META_LAYER = CapMetaLayer.s
_CAPS_META_COMP_GROUPS = CapMetaLayerCompatibilityGroups.s
_CAPS_META_LAYER_ENABLED = CapMetaLayerEnabled.s
_CAPS_META_ENABLED = CapMetaEnabled.s
_CAPS_META_PREFERENCE = CapMetaPreference.s
_CAPS_META_LABEL = CapMetaLabel.s


def _cs_is_visible(cs: dict[str, Any]) -> bool:
    """True if the constraint set should appear on caps views.

    BCP-004-01's ``enabled`` key gates the CS at *top-level*
    intersection; Matrox adds ``layer_enabled`` to gate the CS at
    *per-layer* intersection. A MUX sender publishes its sub-layer
    CSes with ``enabled=False`` + ``layer_enabled=True`` so they do
    NOT interfere with the top-level mux negotiation but DO
    participate when narrowing a specific layer.

    For the capabilities UI we want to show a CS if either gate is
    open — hiding sub-CSes would collapse a MUX view to its two
    trunk entries and the filter dropdowns would lose every format
    / layer value that's only declared on the sub-CSes.

    Mirrors the partition walk in
    ``nmos/node/config/defaults.py`` around line 274.
    """
    enabled = cs.get(_CAPS_META_ENABLED, True)
    if enabled is not False:
        return True
    layer_enabled = cs.get(_CAPS_META_LAYER_ENABLED, False)
    return layer_enabled is True
_CAPS_FORMAT_MEDIA_TYPE = CapFormatMediaType.s


def _short_format(value: Any) -> str:
    """``urn:x-nmos:format:video`` → ``video``. Other / missing → ``""``."""
    if isinstance(value, str) and value.startswith(_FORMAT_URN_PREFIX):
        return value[len(_FORMAT_URN_PREFIX):]
    return ""


# Both URN families that carry caps/constraint keys. NMOS defines the
# core BCP-004-01 namespace; the Matrox extensions follow the same
# "<owner>:cap:<domain>:<name>" shape (e.g.
# ``urn:x-matrox:cap:transport:srt_mode``). The stripped short name
# ("format:frame_width", "transport:srt_mode") stays unambiguous
# because the leading ``<domain>:`` segment is preserved.
_CAPS_PARAM_URN_PREFIXES: tuple[str, ...] = (
    "urn:x-nmos:cap:",
    "urn:x-matrox:cap:",
)

# Meta keys — surfaced separately as columns / filters, not in the
# parameter-list expansion.
_CAPS_META_KEYS = frozenset({
    _CAPS_META_LABEL,
    _CAPS_META_ENABLED,
    _CAPS_META_PREFERENCE,
    _CAPS_META_FORMAT,
    _CAPS_META_LAYER,
    _CAPS_META_COMP_GROUPS,
})


def _short_param_name(key: str) -> str:
    """Strip a known caps URN prefix from a constraint key.

    ``urn:x-nmos:cap:format:frame_width``    → ``format:frame_width``
    ``urn:x-matrox:cap:transport:srt_mode``  → ``transport:srt_mode``

    Unknown / non-URN keys are returned unchanged so nothing surprises
    the operator.
    """
    for p in _CAPS_PARAM_URN_PREFIXES:
        if key.startswith(p):
            return key[len(p):]
    return key


def _format_constraint_value(value: Any) -> str:
    """Compact BCP-004-01 constraint-value renderer.

    BCP-004-01 §"Parameter Constraints" defines constraints as objects
    that may carry any combination of ``enum`` / ``minimum`` /
    ``maximum`` / ``pattern``. This helper produces a one-line summary.
    """
    if not isinstance(value, dict):
        return str(value)
    parts: list[str] = []
    if "enum" in value:
        items = value["enum"]
        if isinstance(items, list):
            if len(items) <= 6:
                parts.append("enum: [" + ", ".join(
                    _simple_value(v) for v in items
                ) + "]")
            else:
                parts.append(
                    f"enum: [{_simple_value(items[0])}, …, "
                    f"{_simple_value(items[-1])}] ({len(items)})"
                )
    has_min = "minimum" in value
    has_max = "maximum" in value
    if has_min and has_max:
        parts.append(
            f"[{_simple_value(value['minimum'])}"
            f"..{_simple_value(value['maximum'])}]"
        )
    elif has_min:
        parts.append(f"≥ {_simple_value(value['minimum'])}")
    elif has_max:
        parts.append(f"≤ {_simple_value(value['maximum'])}")
    if "pattern" in value:
        parts.append(f"pattern: {value['pattern']!r}")
    return " · ".join(parts) if parts else "any"


def _simple_value(v: Any) -> str:
    """Render a scalar / NMOS rational / dict for constraint display."""
    if isinstance(v, dict):
        num = v.get("numerator")
        den = v.get("denominator")
        if num is not None:
            if den in (None, 1):
                return str(num)
            return f"{num}/{den}"
        return "{…}"
    return str(v)


def _enumerate_parameter_constraints(cs: dict[str, Any]) -> list[dict[str, str]]:
    """Return the ``[{name, value}, …]`` list for the expanded row.

    Meta keys (label, enabled, preference, format, layer, compat) are
    surfaced separately as columns + filters, so we strip them from the
    expansion to avoid duplication.
    """
    out: list[dict[str, str]] = []
    for key, val in cs.items():
        if not isinstance(key, str):
            continue
        if key in _CAPS_META_KEYS:
            continue
        out.append({
            "name": _short_param_name(key),
            "value": _format_constraint_value(val),
        })
    out.sort(key=lambda p: p["name"])
    return out


def _media_type_first(cs: dict[str, Any]) -> str:
    v = cs.get(_CAPS_FORMAT_MEDIA_TYPE)
    if not isinstance(v, dict):
        return ""
    e = v.get("enum")
    if isinstance(e, list) and e and isinstance(e[0], str):
        return e[0]
    return ""


def _coerce_int(v: Any) -> int | None:
    """Accept int / numeric string; everything else → ``None``."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            return None
    return None


def _build_caps_view(
    cache: ResourceCache,
    senders: list[dict[str, Any]],
    *,
    filters: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a per-sender, filtered caps view for the capabilities page.

    Each constraint set is projected to a compact dict showing the
    BCP-004-01 meta fields (label, format, layer, preference) plus the
    media type from the format-specific caps. Disabled sets
    (``urn:x-nmos:cap:meta:enabled == False``) are silently dropped —
    the operator never sees them.

    When ``filters`` is supplied, only sets matching every active
    ``by_format`` / ``by_layer`` / ``by_compatibility`` criterion are
    returned. The full set of distinct filter values across *all*
    selected senders is returned as ``has_formats`` /
    ``has_layers`` / ``has_compatibilities`` so the template can
    populate the filter dropdowns.

    Filter rules:

      * ``by_format``         — set's meta_format must equal the value.
      * ``by_layer``          — set's meta_layer must equal (int).
      * ``by_compatibility``  — either the set declares no compat
        groups at all (matches everything, per BCP-004-01 default) or
        the selected group id is in the set's compat-groups list.
    """
    from nmos.controller.grouping import (
        device_address, device_serial, extract_group_hint,
    )

    by_format = (filters or {}).get("by_format", "")
    by_layer = (filters or {}).get("by_layer", "")
    by_compat = (filters or {}).get("by_compatibility", "")
    by_layer_int = _coerce_int(by_layer)
    by_compat_int = _coerce_int(by_compat)

    has_formats: set[str] = set()
    has_layers: set[int] = set()
    has_compat: set[int] = set()

    senders_out: list[dict[str, Any]] = []
    for s in senders:
        sid = s.get("id", "") or ""
        hint = extract_group_hint(s.get("tags"))
        role_label = (
            f"{hint.format} {hint.role}" if hint is not None else ""
        )
        dev = cache.get_device(s.get("device_id", "") or "") or {}
        entry: dict[str, Any] = {
            "id": sid,
            "label": s.get("label", "") or "",
            "role": role_label,
            "device_serial": device_serial(dev) or "",
            "device_address": device_address(dev) or "",
            "constraint_sets": [],
        }

        caps_json = s.get("caps")
        if not isinstance(caps_json, dict):
            senders_out.append(entry)
            continue
        constraint_sets = caps_json.get("constraint_sets")
        if not isinstance(constraint_sets, list):
            senders_out.append(entry)
            continue

        for i, cs in enumerate(constraint_sets):
            if not isinstance(cs, dict):
                continue
            # Skip disabled sets outright.
            if not _cs_is_visible(cs):
                continue

            # ``actual_meta_*`` carry ONLY what the published CS itself
            # declares — no fallbacks. These drive the IS-11
            # ``/constraints/active`` PUT body: a CS that doesn't carry
            # cap:meta:format / cap:meta:layer in its own JSON is a
            # trunk CS (``enabled=true``, no sub-layer markers), and
            # the body MUST reflect that. Stamping fabricated format/
            # layer values into a non-hierarchical CS body makes the
            # Node's CCF compatibility check return 422
            # "Non-hierarchical: ConSet … cannot have format=… or
            # layer=…", which is exactly the bug we hit.
            actual_meta_format = _short_format(cs.get(_CAPS_META_FORMAT))
            actual_meta_layer = _coerce_int(cs.get(_CAPS_META_LAYER))

            # ``meta_format`` / ``meta_layer`` are the DISPLAY-side
            # values — they fall back to the natural-group hint so a
            # trunk CS still renders with a meaningful "audio 0" /
            # "video 0" label, and so the by_format / by_layer
            # filter dropdowns expose every value the operator could
            # pick. Per NMOS With Natural Groups the role label is
            # uppercase ("VIDEO" / "AUDIO" / …); BCP-004-01
            # cap:meta:format is the URN short-form (lowercase), so
            # we downcase to match.
            meta_format = actual_meta_format
            meta_layer = actual_meta_layer
            if not meta_format and hint is not None:
                meta_format = hint.format.lower()
            if meta_layer is None and hint is not None:
                meta_layer = hint.role
            # Final fallback when there's no group hint at all — use the
            # sender's own IS-04 ``format`` URN.
            if not meta_format:
                meta_format = _short_format(s.get("format"))

            raw_comp = cs.get(_CAPS_META_COMP_GROUPS)
            meta_compat: list[int] = []
            if isinstance(raw_comp, list):
                for c in raw_comp:
                    ci = _coerce_int(c)
                    if ci is not None:
                        meta_compat.append(ci)

            # Seed the filter-dropdown options from the full page
            # content — every enabled constraint set (with derived
            # meta values included) contributes before the by_*
            # filters are evaluated. The dropdowns therefore always
            # expose every value the operator could pick.
            if meta_format:
                has_formats.add(meta_format)
            if meta_layer is not None:
                has_layers.add(meta_layer)
            for c in meta_compat:
                has_compat.add(c)

            # Apply the active filters.
            if by_format and meta_format != by_format:
                continue
            if by_layer_int is not None:
                if meta_layer is None or meta_layer != by_layer_int:
                    continue
            if by_compat_int is not None:
                # BCP-004-01: absence of a compat-groups list means
                # "belongs to every compatibility group".
                if meta_compat and by_compat_int not in meta_compat:
                    continue

            entry["constraint_sets"].append({
                "index": i,
                "label": cs.get(_CAPS_META_LABEL, f"set #{i}"),
                "preference": cs.get(_CAPS_META_PREFERENCE, 0),
                "meta_format": meta_format,            # display + filter
                "meta_layer": meta_layer if meta_layer is not None else "",
                # Wire-truth values — only what the CS itself declares.
                # The DOM ``data-cs-meta-*`` attributes are stamped from
                # these so the JS body builder doesn't fabricate
                # hierarchical markers on a trunk CS.
                "actual_meta_format": actual_meta_format or "",
                "actual_meta_layer": (
                    actual_meta_layer if actual_meta_layer is not None else ""
                ),
                "meta_compat": meta_compat,
                "media_type": _media_type_first(cs),
                # Parameter-constraint list, shown only when the row is
                # expanded. Keyed by the short URN suffix; rendered as a
                # compact "enum" / "[min..max]" / "min≤" / "≤max" string.
                "params": _enumerate_parameter_constraints(cs),
            })

        senders_out.append(entry)

    # Sort each sender's displayed sets by descending preference — the
    # most-preferred set gets the pre-checked radio.
    for entry in senders_out:
        entry["constraint_sets"].sort(
            key=lambda cs: (-int(cs["preference"] or 0), cs["index"]),
        )

    # Widest parameter name across every displayed constraint set.
    # Surfaced as a CSS custom property so every expanded details block
    # on the page aligns its ``name`` column on the same char count —
    # otherwise the nested-table trick sizes per-CS and rows drift.
    max_name_len = 0
    for entry in senders_out:
        for cs in entry["constraint_sets"]:
            for p in cs["params"]:
                if len(p["name"]) > max_name_len:
                    max_name_len = len(p["name"])

    return {
        "senders": senders_out,
        "has_formats":        sorted(has_formats),
        "has_layers":         sorted(has_layers),
        "has_compatibilities": sorted(has_compat),
        "max_param_name_len": max_name_len,
    }


def _is_transport_cap(urn: str) -> bool:
    """Transport-layer caps (``urn:x-nmos:cap:transport:*`` /
    ``urn:x-matrox:cap:transport:*``) are editable only on the
    capabilities of the SENDER itself, never constricted by a
    downstream controller — the widget is marked ``disabled`` via the
    ``is_transport_cap`` template helper."""
    return (
        urn.startswith("urn:x-nmos:cap:transport:")
        or urn.startswith("urn:x-matrox:cap:transport:")
    )


def _widget_for_constraint(
    urn: str, value: Any,
) -> dict[str, Any]:
    """Decide how to render a parameter-constraint edit widget.

    Mirrors the ``configs.html`` decision tree (template lines
    171-201):

    * ≥ 2 enum values → ``multiselect``  (``<select multiple>``)
    * 1 enum value    → ``readonly``     (uneditable text input)
    * min / max range with min ≠ max → ``range``  (slider + text)
    * min == max      → ``readonly``
    * anything else   → ``readonly`` with ``display='—'``

    Transport caps (`urn:x-nmos:cap:transport:*` and the Matrox
    extension) render ``disabled=True`` — they're read-only on the
    caps side per the ``is_transport_cap`` check.
    """
    disabled = _is_transport_cap(urn)
    if not isinstance(value, dict):
        return {"kind": "readonly", "display": str(value), "disabled": True}

    if "enum" in value:
        opts = value["enum"]
        if isinstance(opts, list):
            if len(opts) == 0:
                return {
                    "kind": "readonly", "display": "(empty)",
                    "disabled": True,
                }
            if len(opts) == 1:
                return {
                    "kind": "readonly",
                    "display": _simple_value(opts[0]),
                    "disabled": True,
                }
            # Each option carries both the human-readable ``display``
            # text AND a ``value`` — the JSON-encoded form of the
            # ORIGINAL typed value (bool True/False, int 1920, string
            # "video/raw", rational object, …). The template puts the
            # JSON form into ``<option value="…">`` so the browser can
            # JSON.parse each selected value back to its original
            # Python/JSON type when it builds the IS-11 PUT body.
            # Without this, a boolean cap sent back to the Node as
            # ``{"enum": ["True", "False"]}`` (strings) would be
            # rejected — IS-11 expects booleans for a boolean cap.
            display_opts = [
                {"display": _simple_value(o), "value": json.dumps(o)}
                for o in opts
            ]
            # Pre-select every option so a "Constrain" click with no
            # narrowing re-asserts the declared set unchanged.
            return {
                "kind": "multiselect",
                "options": display_opts,
                "selected": [o["display"] for o in display_opts],
                "disabled": disabled,
            }

    has_min = "minimum" in value
    has_max = "maximum" in value
    if has_min and has_max:
        mn, mx = value["minimum"], value["maximum"]
        if mn == mx:
            return {
                "kind": "readonly", "display": _simple_value(mn),
                "disabled": True,
            }
        return {
            "kind": "range",
            "min":   _simple_value(mn),
            "max":   _simple_value(mx),
            "value": _simple_value(mn),
            "display": f"{_simple_value(mn)} … {_simple_value(mx)}",
            "disabled": disabled,
        }

    # One-sided ranges and unknown shapes — render a readonly
    # placeholder rather than try to edit.
    return {"kind": "readonly", "display": "—", "disabled": True}


def _constraint_set_hash(cs: dict[str, Any]) -> str:
    """Stable short hash of a constraint set's content.

    Used as the localStorage key suffix for per-sender edit persistence
    on the configure page. Deriving the key from content (rather than
    from the set's *index*) means that when a device dynamically swaps
    or mutates a constraint set, the stored record is automatically
    orphaned and the UI falls back to defaults for the new content.

    16 hex chars of SHA-256 on a canonical JSON rendering is plenty
    for uniqueness within one operator's session; collisions across
    unrelated devices aren't a concern because the full storage key
    also includes the sender's UUID.
    """
    import hashlib
    payload = json.dumps(cs, sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _build_configure_view(
    cache: ResourceCache,
    senders: list[dict[str, Any]],
    conset_by_sender: dict[str, int],
    filters: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Per-sender view for the configure page.

    Each sender carries exactly ONE constraint set — the one the
    operator picked on the caps page (``conset_<sid>`` query param).
    Inside that set, every parameter constraint is decorated with a
    ``widget`` dict so the template can render the matching editable
    control (multi-select / range / readonly / text).

    Disabled constraint sets and filter-excluded sets are skipped.
    Filter rules match the caps-page view (format / layer /
    compatibility).
    """
    from nmos.controller.grouping import (
        device_address, device_serial, extract_group_hint,
    )

    by_format = (filters or {}).get("by_format", "")
    by_layer = (filters or {}).get("by_layer", "")
    by_compat = (filters or {}).get("by_compatibility", "")
    by_layer_int = _coerce_int(by_layer)
    by_compat_int = _coerce_int(by_compat)

    has_formats: set[str] = set()
    has_layers: set[int] = set()
    has_compat: set[int] = set()

    senders_out: list[dict[str, Any]] = []
    for s in senders:
        sid = s.get("id", "") or ""
        hint = extract_group_hint(s.get("tags"))
        role_label = (
            f"{hint.format} {hint.role}" if hint is not None else ""
        )
        dev = cache.get_device(s.get("device_id", "") or "") or {}
        status = cache.get_status(sid)
        entry: dict[str, Any] = {
            "id": sid,
            "label": s.get("label", "") or "",
            "role": role_label,
            "device_serial": device_serial(dev) or "",
            "device_address": device_address(dev) or "",
            "active": bool(status.get("active", False)),
            "constraint_set": None,
        }

        caps_json = s.get("caps")
        if not isinstance(caps_json, dict):
            senders_out.append(entry)
            continue
        constraint_sets = caps_json.get("constraint_sets")
        if not isinstance(constraint_sets, list):
            senders_out.append(entry)
            continue

        # Seed the filter-dropdown options from every enabled set.
        for i, cs in enumerate(constraint_sets):
            if not isinstance(cs, dict):
                continue
            if not _cs_is_visible(cs):
                continue
            mf = _short_format(cs.get(_CAPS_META_FORMAT))
            ml = _coerce_int(cs.get(_CAPS_META_LAYER))
            if not mf and hint is not None:
                mf = hint.format.lower()
            if ml is None and hint is not None:
                ml = hint.role
            if not mf:
                mf = _short_format(s.get("format"))
            raw_comp = cs.get(_CAPS_META_COMP_GROUPS)
            mc: list[int] = []
            if isinstance(raw_comp, list):
                for c in raw_comp:
                    ci = _coerce_int(c)
                    if ci is not None:
                        mc.append(ci)
            if mf:
                has_formats.add(mf)
            if ml is not None:
                has_layers.add(ml)
            for c in mc:
                has_compat.add(c)

        # Resolve the selected constraint set for this sender.
        chosen_index = conset_by_sender.get(sid)
        if chosen_index is None:
            senders_out.append(entry)
            continue
        if not (0 <= chosen_index < len(constraint_sets)):
            senders_out.append(entry)
            continue
        cs = constraint_sets[chosen_index]
        if not isinstance(cs, dict):
            senders_out.append(entry)
            continue
        if not _cs_is_visible(cs):
            senders_out.append(entry)
            continue

        # ``actual_meta_*`` carry ONLY what the published CS itself
        # declares — no fallbacks. These drive the IS-11
        # ``/constraints/active`` PUT body so a non-hierarchical CS
        # (no cap:meta:format / cap:meta:layer) ships a trunk body
        # (``enabled=true``, no sub-layer markers). Stamping the
        # natural-group hint onto a trunk CS body causes the Node's
        # CCF check to return 422 "Non-hierarchical: ConSet … cannot
        # have format=…/layer=…".
        #
        # The display-side ``meta_format`` / ``meta_layer`` below
        # keep the natural-group fallback so a trunk CS in a grouped
        # sender (group, MUX, etc.) still renders with a meaningful
        # role/layer label — UI grouping is independent of wire
        # truth.
        actual_meta_format = _short_format(cs.get(_CAPS_META_FORMAT))
        actual_meta_layer = _coerce_int(cs.get(_CAPS_META_LAYER))

        meta_format = actual_meta_format
        meta_layer = actual_meta_layer
        if not meta_format and hint is not None:
            meta_format = hint.format.lower()
        if meta_layer is None and hint is not None:
            meta_layer = hint.role
        if not meta_format:
            meta_format = _short_format(s.get("format"))
        raw_comp = cs.get(_CAPS_META_COMP_GROUPS)
        meta_compat: list[int] = []
        if isinstance(raw_comp, list):
            for c in raw_comp:
                ci = _coerce_int(c)
                if ci is not None:
                    meta_compat.append(ci)

        # Apply filters.
        if by_format and meta_format != by_format:
            senders_out.append(entry)
            continue
        if by_layer_int is not None:
            if meta_layer is None or meta_layer != by_layer_int:
                senders_out.append(entry)
                continue
        if by_compat_int is not None:
            if meta_compat and by_compat_int not in meta_compat:
                senders_out.append(entry)
                continue

        # Build the decorated CS.
        params: list[dict[str, Any]] = []
        for key, val in cs.items():
            if not isinstance(key, str):
                continue
            if key in _CAPS_META_KEYS:
                continue
            params.append({
                "urn":    key,
                "name":   _short_param_name(key),
                "widget": _widget_for_constraint(key, val),
            })
        params.sort(key=lambda p: p["name"])

        entry["constraint_set"] = {
            "index":        chosen_index,
            "hash":         _constraint_set_hash(cs),
            "label":        cs.get(_CAPS_META_LABEL, f"set #{chosen_index}"),
            "preference":   cs.get(_CAPS_META_PREFERENCE, 0),
            "meta_format":  meta_format,
            "meta_layer":   meta_layer if meta_layer is not None else "",
            # Wire-truth values — only what the CS itself declares.
            # The DOM ``data-cs-meta-*`` attributes are stamped from
            # these so the JS body builder doesn't fabricate
            # hierarchical markers on a trunk CS.
            "actual_meta_format": actual_meta_format or "",
            "actual_meta_layer": (
                actual_meta_layer if actual_meta_layer is not None else ""
            ),
            "meta_compat":  meta_compat,
            "media_type":   _media_type_first(cs),
            "params":       params,
        }
        senders_out.append(entry)

    # Widest parameter name across every sender's selected CS. The
    # template uses it as a CSS custom property so the ``name`` column
    # of every nested params table is the same width — rows line up
    # between the AUDIO 0 and VIDEO 0 sections (etc.) instead of each
    # section sizing to its own widest name.
    max_name_len = 0
    for entry in senders_out:
        cs = entry.get("constraint_set")
        if not cs:
            continue
        for p in cs.get("params", []):
            if len(p["name"]) > max_name_len:
                max_name_len = len(p["name"])

    return {
        "senders":            senders_out,
        "has_formats":        sorted(has_formats),
        "has_layers":         sorted(has_layers),
        "has_compatibilities": sorted(has_compat),
        "max_param_name_len": max_name_len,
    }


# ---------------------------------------------------------------------------
# View-helper: grouped summary for JSON responses
# ---------------------------------------------------------------------------

def _grouped_summary(view: Any) -> dict[str, Any]:
    """Flatten a ``DeviceView`` into a plain-dict tree for JSON."""
    return {
        "device_id": view.device_id,
        "device_serial": view.device_serial,
        "device_label": view.device_label,
        "groups": [_natural_group_summary(g) for g in view.groups],
        "ungrouped": [_grouped_resource_summary(m) for m in view.ungrouped],
    }


def _natural_group_summary(g: NaturalGroupView) -> dict[str, Any]:
    """Per spec §"Senders"/"Receivers" the group identity is just
    (transport, group_index). Format lives on each member."""
    return {
        "transport": g.hint_key[0],
        "group_index": g.hint_key[1],
        "display_name": g.display_name,
        "members": [_grouped_resource_summary(m) for m in g.members],
    }


def _grouped_resource_summary(m: GroupedResource) -> dict[str, Any]:
    return {
        "id": m.id,
        "label": m.label,
        "description": m.description,
        "format": m.hint.format if m.hint is not None else None,
        "role": m.hint.role if m.hint is not None else None,
        "status": m.status,
    }


# ---------------------------------------------------------------------------
# Internals — receivers compatible-senders view
# ---------------------------------------------------------------------------

async def _build_privacy_view(
    cache: ResourceCache,
    client: RemoteNodeClient,
    request: web.Request,
    senders: list[dict[str, Any]],
    receivers: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Assemble the ``privacy_view`` context for the configure page.

    Fetches each selected resource's IS-05 transport-parameter
    constraints concurrently, computes the intersection via
    ``privacy.compute_privacy_options``, and returns the dict the
    Privacy partial template expects.

    Returns ``None`` when the selection has no PEP fields at all
    (no resource declared any ``ext_privacy_*`` enum with a non-NULL
    value) — the partial renders nothing in that case.
    """
    if not senders and not receivers:
        return None

    # When ``status`` is 200 we got real transport params; ``tp`` is a
    # list of per-leg dicts. When ``status`` is anything else (401,
    # 403, 0/transport error) we couldn't see the data at all — that
    # is *different* from "data says no PEP overlap" and the privacy
    # panel surfaces it as a separate message. ``fetch_status`` is
    # the raw HTTP status (or 0 on transport error) so the template
    # can be specific.
    async def _constraints_for(
        resource: dict[str, Any],
        kind: str,
    ) -> tuple[dict[str, Any] | None, Any, int]:
        device = cache.get_device(resource.get("device_id", "") or "")
        if device is None:
            return None, None, 0
        base = client.connection_api_base(device)
        if base is None:
            return device, None, 0
        forwarded = _headers_with_reservation(
            request, device.get("id", "") or "",
        )
        if kind == "sender":
            result = await client.get_sender_constraints(
                base, resource["id"], forwarded, trace_id=_trace_id(request),
            )
        else:
            result = await client.get_receiver_constraints(
                base, resource["id"], forwarded, trace_id=_trace_id(request),
            )
        # IS-05 /constraints/ returns a per-leg array directly
        # (``[{...}, ...]``). IS-05 /active/ and /staged/ wrap the
        # array under a ``transport_params`` key. Accept either shape.
        tp = None
        if result.status == 200:
            if isinstance(result.body, list):
                tp = result.body
            elif isinstance(result.body, dict):
                tp = result.body.get("transport_params")
        return device, tp, result.status

    sender_results = await asyncio.gather(*(
        _constraints_for(s, "sender") for s in senders
    )) if senders else []
    receiver_results = await asyncio.gather(*(
        _constraints_for(r, "receiver") for r in receivers
    )) if receivers else []

    sender_devices = [d for (d, _tp, _st) in sender_results if d is not None]
    receiver_devices = [d for (d, _tp, _st) in receiver_results if d is not None]
    sender_tp = [tp for (_d, tp, _st) in sender_results]
    receiver_tp = [tp for (_d, tp, _st) in receiver_results]

    # Collect Devices whose IS-05 fetch failed so the template can
    # distinguish "couldn't read the data" from "data says no overlap".
    fetch_failed: list[dict[str, str]] = []
    for d, _tp, status in sender_results + receiver_results:
        if d is None:
            continue
        if status == 200:
            continue
        # Skip Devices that simply have no IS-05 control URL — those
        # were already returning ``None`` for transport params before
        # this change and the panel handled them silently.
        if client.connection_api_base(d) is None:
            continue
        fetch_failed.append({
            "device_id": d.get("id", "") or "",
            "device_serial": (d.get("description") or d.get("label") or ""),
            "status": str(status),
        })

    # Reservation is per-Node. Walk device.node_id back to the Node
    # resources so exclusivity is judged against the Node's
    # ``services`` array (resolved via ``GetNodeManufactuerApi``).
    selected_nodes = _nodes_for_devices(
        cache, sender_devices + receiver_devices,
    )

    opts = compute_privacy_options(
        sender_constraints=sender_tp,
        receiver_constraints=receiver_tp,
        sender_devices=selected_nodes,   # "devices" parameter is misnamed —
        receiver_devices=[],             # the resolver walks the NODE's services
        device_service_resolver=client.exclusive_service_base,
    )

    # Unique node ids — the JS posts these to /api/privacy/acquire.
    node_ids: list[str] = [
        n.get("id", "") for n in selected_nodes if n.get("id")
    ]

    # Short text summary rendered in the panel footer.
    parts: list[str] = []
    if senders:
        parts.append(f"{len(senders)} sender{'s' if len(senders) != 1 else ''}")
    if receivers:
        parts.append(f"{len(receivers)} receiver{'s' if len(receivers) != 1 else ''}")
    node_count = len(node_ids)
    parts.append(f"{node_count} node{'s' if node_count != 1 else ''}")
    resource_summary = " · ".join(parts)

    # True when at least one Mode in the intersection is ECDH —
    # controls whether the Curve dropdown renders at all.
    has_ecdh_modes = any(is_ecdh_mode(m) for m in opts.modes)

    # If NOTHING about PEP was declared (every enum empty AND no
    # node advertises the reservation service), there's no point
    # rendering the panel. Operators on plain non-PEP deployments see
    # no change.
    if (not opts.protocols and not opts.modes and not opts.curves
            and not opts.exclusivity_ok):
        return None

    # Activation state of the selection. The ``exclusive_key`` +
    # PEP parameters only enter the encryption-key derivation at the
    # ``master_enable=true`` activation edge (NMOS With Node
    # Reservation spec §Acquire line 99). Toggling any of them while
    # a stream is running would be a no-op that *looks* effective —
    # so the UI must lock the dropdowns + Exclusivity switch while
    # any selected sender or receiver is active. The operator must
    # deactivate first, change parameters, then reactivate.
    any_active = any(
        bool((s.get("subscription") or {}).get("active", False))
        for s in senders
    ) or any(
        bool((r.get("subscription") or {}).get("active", False))
        for r in receivers
    )

    return {
        "pep_available":    opts.pep_available,
        "protocols":        opts.protocols,
        "modes":            opts.modes,
        "curves":           opts.curves,
        "has_ecdh_modes":   has_ecdh_modes,
        "exclusivity_ok":   opts.exclusivity_ok,
        "any_active":       any_active,
        "node_ids":         node_ids,
        "resource_summary": resource_summary,
        # Devices whose IS-05 transport-params fetch did NOT return
        # 200. When non-empty, the privacy panel surfaces a
        # "compliance unknown — controller cannot read transport
        # parameters on these devices" message instead of the
        # incompatibility-style error. Common cause: the admin's
        # OAuth2 token doesn't carry grants for the target Node.
        "fetch_failed":     fetch_failed,
    }


async def _sender_state_map(
    cache: ResourceCache,
    client: RemoteNodeClient,
    forwarded: dict[str, str],
    senders: list[dict[str, Any]],
    *,
    trace_id: str = "",
    admin: AdminSessionState | None = None,
    request: web.Request | None = None,
) -> dict[str, dict[str, Any]]:
    """Return ``{sender_id: {"active": bool, "constrained": bool,
    "is11_supported": bool, "inaccessible_reasons": {...}}}`` for the
    configure page.

    Used by the templates to render the Constrain / Activate toggles
    in their actual current state at page load, and to *disable* the
    Constrain toggle when a sender's owning device doesn't expose
    the IS-11 stream-compatibility API (trying to PUT
    ``/senders/{id}/constraints/active`` at that sender would be a
    pointless 404).

    * ``active``     — ``subscription.active`` from the IS-04 cache
                       (free, already live via the RDS WS).
    * ``constrained`` — result of a GET to IS-11
                       ``/senders/{id}/constraints/active``. A sender
                       is considered constrained iff the response is
                       200 AND the body carries a non-empty
                       ``constraint_sets`` array. Unreachable nodes,
                       missing control URL, or errors all round-trip
                       as ``constrained=False`` — the page still
                       renders, just defaults the button off.
    * ``is11_supported`` — True when the sender's owning device
                       advertises a
                       ``urn:x-nmos:control:stream-compat/v1.*``
                       entry (i.e. ``streamcompat_api_base`` returns
                       non-None). The template ANDs this across the
                       selection to decide whether the master
                       Constrain toggle should be ``disabled``.
    * ``inaccessible_reasons`` — ``{"read": [...], "write": [...]}``
                       output of :func:`_device_inaccessible_reasons`
                       for the sender's owning Device. Empty lists
                       mean fully reachable; non-empty drives the
                       Phase 5 UI degradation (red row + disabled
                       buttons). Computed only when ``admin`` is
                       supplied; otherwise both lists are empty.

    GETs are issued concurrently via ``asyncio.gather`` so page load
    scales with the slowest node, not the sum.
    """
    async def _one(s: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        sid = s.get("id", "") or ""
        device_id = s.get("device_id", "") or ""
        sub = s.get("subscription") or {}
        active = bool(sub.get("active", False))
        constrained = False
        is11_supported = False
        device = cache.get_device(device_id)
        if device is not None:
            base = client.streamcompat_api_base(device)
            is11_supported = base is not None
            if base is not None:
                # Build per-sender forwarded headers when ``request`` was
                # supplied — IS-11 needs the OAuth2 bearer (and the
                # reservation token) keyed to this sender's owning
                # Device. Falling back to the caller-supplied
                # ``forwarded`` (typically empty) preserves the
                # pre-Phase-5 contract for callers that haven't
                # threaded ``request`` through yet.
                hdrs = (
                    _headers_with_reservation(request, device_id)
                    if request is not None else forwarded
                )
                res = await client.get_sender_active_constraints(
                    base, sid, hdrs, trace_id=trace_id,
                )
                if res.status == 200 and isinstance(res.body, dict):
                    sets = res.body.get("constraint_sets")
                    constrained = (
                        isinstance(sets, list) and len(sets) > 0
                    )
        if admin is not None:
            inaccessible = _device_inaccessible_reasons(
                cache, admin, device_id,
            )
        else:
            inaccessible = {"read": [], "write": []}
        return sid, {
            "active": active,
            "constrained": constrained,
            "is11_supported": is11_supported,
            "inaccessible_reasons": inaccessible,
        }

    if not senders:
        return {}
    results = await asyncio.gather(*(_one(s) for s in senders))
    return {sid: st for sid, st in results}


def _receiver_state_map(
    cache: ResourceCache,
    receivers: list[dict[str, Any]],
    *,
    admin: AdminSessionState | None = None,
) -> dict[str, dict[str, Any]]:
    """Return ``{receiver_id: {"active": bool, "inaccessible_reasons": {...}}}``
    for the configure page.

    Synchronous (unlike :func:`_sender_state_map`) because IS-11 has no
    receiver-side ``active_constraints`` endpoint to GET — the only
    field that matters here is ``subscription.active``, already present
    in the IS-04 cache via the RDS WebSocket. The ``inaccessible_reasons``
    probe is also pure-cache (no outbound HTTP), so the whole function
    stays sync.

    The receiver mirror exists primarily so that Phase 5's UI
    degradation has a single uniform shape across senders and
    receivers (red row + per-row tooltip + disabled action buttons),
    instead of branching the templates on which kind of resource
    they're rendering.
    """
    out: dict[str, dict[str, Any]] = {}
    for r in receivers:
        rid = r.get("id", "") or ""
        sub = r.get("subscription") or {}
        active = bool(sub.get("active", False))
        device_id = r.get("device_id", "") or ""
        if admin is not None:
            inaccessible = _device_inaccessible_reasons(
                cache, admin, device_id,
            )
        else:
            inaccessible = {"read": [], "write": []}
        out[rid] = {
            "active": active,
            "inaccessible_reasons": inaccessible,
        }
    return out


# ---------------------------------------------------------------------------
# Track D — Cross-direction navigation (A/V/D ↔ USB ↔ TB)
# ---------------------------------------------------------------------------
#
# A configure page between two Nodes usually begins with the
# audio/video/data flow (Node A → Node B). Once that's up, the
# operator commonly wants to configure the REVERSE flow for USB and
# Talk-Back (TB), which travels Node B → Node A. The ``reverse_links``
# block on the receivers-configure page lets the operator jump
# directly to the pre-populated caps page for those groups.
#
# Detection signals (per user brief, not a new NMOS role):
#
#   * USB — a USB-transport resource that is ALONE in its natural
#     group on its Node in its direction (no other same-direction
#     resource shares the ``(transport, group_index)`` key).
#   * TB  — an audio resource that is ALONE in its natural group on
#     its Node in its direction.
#   * A/V/D (``avd``) — everything else.
#
# Both USB and TB apply the same "alone in group" shape rule. They
# differ in the format/transport filter they layer on top:
#   - USB filters by ``transport`` URN;
#   - TB  filters by ``format`` = audio.
#
# Tie-break when a Node hosts multiple candidates for a given group:
#
#   * USB — smallest ``natural_group_index`` wins.
#   * TB  — prefer candidates whose ``transport`` matches the A/V/D
#     selection's transport; within that, smallest
#     ``natural_group_index``.
#
# Cardinality: zero matches on either side leaves the button disabled;
# one or more matches enable it (the resolver picks the first by the
# tie-break rule above).

# USB resources can advertise either of two transport URNs per the
# codegen namespaces: ``urn:x-matrox:transport:usb`` is the Matrox-
# specific namespace, while ``urn:x-nmos:transport:usb`` is the
# "canonical" variant the config loader coerces TO (see
# ``namespaces.py::USB_TRANSPORT_NAMESPACE``). Accept both so live
# Nodes publish either variant and the classifier still fires.
_USB_TRANSPORT_URNS: Final[frozenset[str]] = frozenset({
    "urn:x-matrox:transport:usb",
    TransportUsb.s,
})

# Canonical group-identity strings used by the reverse-link resolver
# and its tests. Keeping them as typed constants avoids stringly-typed
# branching in callers.
GROUP_AVD: Final[str] = "avd"
GROUP_USB: Final[str] = "usb"
GROUP_TB:  Final[str] = "tb"

_GROUP_LABELS: Final[dict[str, str]] = {
    GROUP_AVD: "A/V/D",
    GROUP_USB: "USB",
    GROUP_TB:  "TB",
}

# Sentinel for "resource has no parseable group hint" — used as the
# tie-break fallback so unhinted resources sort last.
_NO_GROUP_INDEX: Final[int] = 10**9


def _resource_owner_node_id(
    cache: ResourceCache, resource: dict[str, Any],
) -> str:
    """Return the id of the Node that owns this sender / receiver.

    Walks ``resource.device_id → device.node_id``. Empty string when
    any link is missing — callers treat that as "unknown owner" and
    omit the resource from reverse-direction matches.
    """
    dev_id = resource.get("device_id", "") or ""
    if not dev_id:
        return ""
    device = cache.get_device(dev_id)
    if device is None:
        return ""
    nid = device.get("node_id", "") or ""
    return str(nid) if isinstance(nid, str) else ""


def _resource_group_index(resource: dict[str, Any]) -> int:
    """Extract the natural-group index from the parsed group hint.

    Returns ``_NO_GROUP_INDEX`` when the resource has no parseable
    hint — sorts such resources last in tie-break orderings.
    """
    hint = extract_group_hint(resource.get("tags"))
    return hint.group_index if hint is not None else _NO_GROUP_INDEX


def _is_usb_transport(resource: dict[str, Any]) -> bool:
    """True when the resource advertises a USB transport URN."""
    t_raw: Any = resource.get("transport")
    return isinstance(t_raw, str) and t_raw in _USB_TRANSPORT_URNS


def _alone_in_group_members(
    cache: ResourceCache, node_id: str, is_sender: bool,
) -> list[dict[str, Any]]:
    """Return resources on ``node_id`` in the given direction that
    are ALONE in their natural group — the single same-direction
    member of their ``(transport, group_index)`` key on this Node.

    Shared by USB and TB detection. The caller layers its own
    format/transport filter on top (USB by transport URN, TB by
    audio format).
    """
    pool = cache.all_senders() if is_sender else cache.all_receivers()
    on_node = [
        r for r in pool
        if _resource_owner_node_id(cache, r) == node_id
    ]
    by_group: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for r in on_node:
        hint = extract_group_hint(r.get("tags"))
        if hint is None:
            continue
        by_group.setdefault(hint.key, []).append(r)
    out: list[dict[str, Any]] = []
    for members in by_group.values():
        if len(members) == 1:
            out.append(members[0])
    return out


def _find_usb_candidates(
    cache: ResourceCache, node_id: str, is_sender: bool,
) -> list[dict[str, Any]]:
    """Return USB candidates on ``node_id`` in the given direction.

    Shape rule: the resource must advertise a USB transport URN AND
    be alone in its natural group on this Node. Sorted by smallest
    ``natural_group_index`` first.
    """
    hits = [
        r for r in _alone_in_group_members(cache, node_id, is_sender)
        if _is_usb_transport(r)
    ]
    hits.sort(key=_resource_group_index)
    return hits


def _find_tb_candidates(
    cache: ResourceCache, node_id: str, is_sender: bool,
    avd_transport: str = "",
) -> list[dict[str, Any]]:
    """Return TB candidates on ``node_id`` in the given direction.

    Shape rule: audio format + alone in natural group. Sorted by
    (1) transport matches ``avd_transport`` first, (2) smallest
    ``natural_group_index`` second.
    """
    out = [
        r for r in _alone_in_group_members(cache, node_id, is_sender)
        if "audio" in (r.get("format") or "").lower()
    ]

    def _sort_key(r: dict[str, Any]) -> tuple[int, int]:
        r_trans = r.get("transport") or ""
        match_rank = 0 if r_trans == avd_transport and avd_transport else 1
        return (match_rank, _resource_group_index(r))
    out.sort(key=_sort_key)
    return out


def _classify_selection(
    cache: ResourceCache,
    receivers: list[dict[str, Any]],
) -> str:
    """Classify the current receivers-configure page's selection.

    Used only to suppress the self-link among the reverse-direction
    buttons. Returns one of ``GROUP_AVD`` / ``GROUP_USB`` /
    ``GROUP_TB``.

    Classification logic:
      * If every receiver advertises a USB transport → USB.
      * Else if the selection is a single audio receiver AND it is
        alone in its natural group on its Node (the TB shape rule)
        → TB.
      * Else → A/V/D.
    """
    if not receivers:
        return GROUP_AVD
    if all(_is_usb_transport(r) for r in receivers):
        return GROUP_USB
    if len(receivers) == 1:
        first = receivers[0]
        if "audio" in (first.get("format") or "").lower():
            node_id = _resource_owner_node_id(cache, first)
            hint = extract_group_hint(first.get("tags"))
            if node_id and hint is not None:
                same_group = [
                    r for r in cache.all_receivers()
                    if _resource_owner_node_id(cache, r) == node_id
                    and extract_group_hint(r.get("tags")) is not None
                    and extract_group_hint(r.get("tags")).key == hint.key  # type: ignore[union-attr]
                ]
                if len(same_group) == 1:
                    return GROUP_TB
    return GROUP_AVD


def _resolve_reverse_group(
    cache: ResourceCache,
    sender_node_id: str,
    receiver_node_id: str,
    target_group: str,
    current_group: str = GROUP_AVD,
    avd_transport: str = "",
) -> tuple[list[str], list[str]] | None:
    """Find the ``(receiver_ids, sender_ids)`` pair for the
    ``target_group`` flow between the same two Nodes.

    Direction model: the three groups split into two direction
    classes — A/V/D flows one way (forward), USB and TB flow the
    other way (reverse). Resources live on:

      * forward class → senders on Node X, receivers on Node Y
      * reverse class → senders on Node Y, receivers on Node X

    So when the operator jumps **between** classes (A/V/D ↔ USB or
    A/V/D ↔ TB) we swap the Node ids — the target's senders live
    where the current selection's receivers do, and vice versa.
    Within a single class (USB ↔ TB) there is **no swap** — both
    flow the same direction, so target resources live on the same
    Nodes as the current selection.

    Returns ``None`` only when EITHER side has zero candidates.
    Otherwise picks the first candidate per side after applying the
    group-specific tie-break rules (see ``_find_usb_candidates`` /
    ``_find_tb_candidates``).
    """
    forward_groups = {GROUP_AVD}
    current_is_forward = current_group in forward_groups
    target_is_forward = target_group in forward_groups
    same_direction = current_is_forward == target_is_forward

    if same_direction:
        target_recv_node = receiver_node_id
        target_send_node = sender_node_id
    else:
        target_recv_node = sender_node_id
        target_send_node = receiver_node_id

    if target_group == GROUP_USB:
        recv_cands = _find_usb_candidates(
            cache, target_recv_node, is_sender=False,
        )
        send_cands = _find_usb_candidates(
            cache, target_send_node, is_sender=True,
        )
    elif target_group == GROUP_TB:
        recv_cands = _find_tb_candidates(
            cache, target_recv_node, is_sender=False,
            avd_transport=avd_transport,
        )
        send_cands = _find_tb_candidates(
            cache, target_send_node, is_sender=True,
            avd_transport=avd_transport,
        )
    else:  # avd — every non-USB, non-TB resource on the target pair
        recv_cands = [
            r for r in cache.all_receivers()
            if _resource_owner_node_id(cache, r) == target_recv_node
            and not _is_usb_transport(r)
        ]
        send_cands = [
            s for s in cache.all_senders()
            if _resource_owner_node_id(cache, s) == target_send_node
            and not _is_usb_transport(s)
        ]

    if not recv_cands or not send_cands:
        return None

    if target_group in (GROUP_USB, GROUP_TB):
        # Pick the top candidate on each side — already tie-break
        # sorted. Returns single-ID lists (matches user rule: USB/TB
        # groups carry exactly one sender + receiver).
        return (
            [recv_cands[0].get("id", "")],
            [send_cands[0].get("id", "")],
        )
    # A/V/D: return all ids in enumeration order (no user-imposed
    # cardinality restriction for this group).
    return (
        [r.get("id", "") for r in recv_cands],
        [s.get("id", "") for s in send_cands],
    )


def _avd_pair_transport(
    cache: ResourceCache,
    recv_node: str,
    send_node: str,
    current_group: str,
    selected_receivers: list[dict[str, Any]],
) -> str:
    """Return the transport URN of the A/V/D pair between
    ``recv_node`` and ``send_node`` — the signal TB's tie-break
    uses to prefer matching candidates.

    When the current selection IS A/V/D, read straight off it.
    Otherwise look up the A/V/D resources on the OPPOSITE-direction
    Node — the operator is on a USB/TB page (reverse class) so the
    A/V/D senders live on ``recv_node`` (which currently holds the
    USB/TB receiver).

    Returns "" when nothing usable is found; callers treat that as
    "no preferred transport" and fall through to the group-index
    tie-break.
    """
    if current_group == GROUP_AVD and selected_receivers:
        t = selected_receivers[0].get("transport") or ""
        return str(t) if isinstance(t, str) else ""
    # On a USB/TB page: A/V/D senders live on ``recv_node`` (the
    # ``receiver_node_id`` of the current — opposite-direction —
    # selection). Pick the first non-USB sender there.
    for s in cache.all_senders():
        if _resource_owner_node_id(cache, s) != recv_node:
            continue
        if _is_usb_transport(s):
            continue
        # Skip TB-shape candidates (audio alone in group) — TB is
        # also reverse-class, not A/V/D.
        hint = extract_group_hint(s.get("tags"))
        if hint is not None:
            same_group = [
                x for x in cache.all_senders()
                if _resource_owner_node_id(cache, x) == recv_node
                and extract_group_hint(x.get("tags")) is not None
                and extract_group_hint(x.get("tags")).key == hint.key  # type: ignore[union-attr]
            ]
            fmt = (s.get("format") or "").lower()
            if len(same_group) == 1 and "audio" in fmt:
                continue
        t = s.get("transport") or ""
        if isinstance(t, str) and t:
            return t
    return ""


def _build_reverse_links(
    cache: ResourceCache,
    selected_receivers: list[dict[str, Any]],
    paired_senders: list[dict[str, Any]],
    mode: str,
) -> list[dict[str, Any]]:
    """Assemble the ``reverse_links`` list rendered in the footer of
    the receivers-configure page.

    Shape of each entry::

        {
            "group":   "usb" | "tb" | "avd",
            "label":   "Configure USB capabilities",
            "href":    "/controller/receivers/configure?..." | None,
            "disabled": bool,
            "tooltip": str,
        }

    The link pointing to the page's own group is suppressed (not just
    disabled) — rendering "Configure A/V/D capabilities" on the A/V/D
    page is noise.
    """
    if not selected_receivers or not paired_senders:
        return []

    # "Sender node" = Node that owns the paired senders; "receiver
    # node" = Node that owns the receivers on THIS page.
    recv_node = _resource_owner_node_id(cache, selected_receivers[0])
    send_node = _resource_owner_node_id(cache, paired_senders[0])
    if not recv_node or not send_node or recv_node == send_node:
        # Same-Node pairings have no meaningful "reverse direction" —
        # a reverse flow is inherently between two distinct Nodes.
        return []

    current = _classify_selection(cache, selected_receivers)

    # Transport of the A/V/D pair between these two Nodes — used by
    # TB's tie-break to prefer candidates with the same transport
    # family. When the current selection IS A/V/D we read it
    # straight off the first receiver. When the current selection is
    # USB or TB the A/V/D pair lives on the OPPOSITE-direction
    # Nodes; we look up any A/V/D sender on the appropriate Node so
    # the tie-break still works regardless of which page the
    # operator is on.
    avd_transport = _avd_pair_transport(
        cache, recv_node=recv_node, send_node=send_node,
        current_group=current,
        selected_receivers=selected_receivers,
    )

    out: list[dict[str, Any]] = []
    for target in (GROUP_AVD, GROUP_USB, GROUP_TB):
        if target == current:
            continue
        label = f"Configure {_GROUP_LABELS[target]} capabilities"
        pair = _resolve_reverse_group(
            cache, send_node, recv_node, target,
            current_group=current,
            avd_transport=avd_transport,
        )
        if pair is None:
            out.append({
                "group":    target,
                "label":    label,
                "href":     None,
                "disabled": True,
                "tooltip": (
                    f"No {_GROUP_LABELS[target]} receiver/sender pair found "
                    "between these two Nodes"
                ),
            })
            continue
        rids, sids = pair
        query = (
            f"receiver_ids={','.join(rids)}"
            f"&sender_ids={','.join(sids)}"
            f"&mode={mode}"
        )
        # Land on the CAPABILITIES picker page, not the configure
        # page. The button label is "Configure <X> capabilities …" —
        # the trailing ellipsis signals there's more to pick before
        # the final configure step. Consistent with the normal
        # receivers flow: /receivers → compatible-senders → caps →
        # configure. The reverse-direction shortcut skips the first
        # two steps (we already know the pair) but lands on ``caps``
        # so the operator chooses a constraint set before driving
        # the actual activation.
        out.append({
            "group":    target,
            "label":    label,
            "href":     f"/controller/receivers/caps?{query}",
            "disabled": False,
            "tooltip":  (
                f"Jump to {_GROUP_LABELS[target]} reverse-direction "
                "capabilities picker"
            ),
        })
    return out


def _reject_incompatible_single_pair(
    senders: list[dict[str, Any]], receivers: list[dict[str, Any]],
) -> None:
    """Enforce format + transport compatibility on single-mode
    zip-pairing; raises ``HTTPBadRequest`` on mismatch.

    Single mode lets the operator cross-pair role indices (receiver
    AUDIO 0 ↔ sender AUDIO 1), but format ("audio" vs "video") and
    transport family must still match — otherwise the pair can't be
    activated at all. The compatible-senders page already filters on
    these, so a mismatch at this point means a tampered / stale URL.
    """
    from nmos.controller.compat import (
        format_compatible, transport_compatible,
    )

    for s, r in zip(senders, receivers):
        if not format_compatible(s.get("format"), r.get("format")):
            raise web.HTTPBadRequest(
                reason=f"sender {s.get('id', '?')!r} format "
                       f"{s.get('format')!r} does not match receiver "
                       f"{r.get('id', '?')!r} format {r.get('format')!r}",
            )
        if not transport_compatible(
            s.get("transport"), r.get("transport"),
        ):
            raise web.HTTPBadRequest(
                reason=f"sender {s.get('id', '?')!r} transport is not "
                       f"compatible with receiver {r.get('id', '?')!r}",
            )


def _receivers_as_natural_group(
    receivers: list[dict[str, Any]],
) -> NaturalGroupView | None:
    """Assemble a ``NaturalGroupView`` from the raw receiver resources
    the operator selected in the receivers listing.

    Returns ``None`` when the receivers don't carry a group hint — in
    which case the caller can't do shape-based matching and should
    fall back to single-receiver filtering.
    """
    from nmos.controller.grouping import extract_group_hint

    members: list[GroupedResource] = []
    first_device_id = ""
    hint_key: tuple[str, int] | None = None
    for r in receivers:
        hint = extract_group_hint(r.get("tags"))
        if hint is None:
            continue
        if hint_key is None:
            hint_key = hint.key
            first_device_id = r.get("device_id", "") or ""
        members.append(GroupedResource(
            id=r.get("id", "") or "",
            label=r.get("label", "") or "",
            description=r.get("description", "") or "",
            device_id=r.get("device_id", "") or "",
            device_serial="",
            device_label="",
            hint=hint,
            resource=r,
        ))

    if hint_key is None or not members:
        return None

    return NaturalGroupView(
        device_id=first_device_id,
        device_serial="",
        device_label="",
        hint_key=hint_key,
        members=members,
    )


def _member_compatible_with_all(
    member: GroupedResource, receivers: list[dict[str, Any]],
) -> bool:
    """Is this sender compatible with every selected receiver?

    Single mode (K=1 receiver): the sender must pass format equality
    + transport compatibility + caps intersection. **Role-index is
    NOT required to match** — receiver ``AUDIO 0`` can be routed
    from sender ``AUDIO 0`` OR ``AUDIO 1`` OR any other audio role.
    This is a deliberate relaxation of the strict
    ``senderLayer == layer`` rule, motivated by operator flexibility
    for the 1-to-1 case where no multi-leg pairing ambiguity exists.

    The hint-format (``hint.format``) is still checked when both
    sides declare it — that guards against a mux hint diverging
    from the IS-04 format URN, which would be a data inconsistency.
    Only the role-index component of the hint is relaxed.

    Group and subset modes do NOT use this function — they go
    through ``compatible_sender_groups`` /
    ``compatible_sender_groups_superset``, both of which keep strict
    ``(format, role_index)`` matching because their pairings
    otherwise become ambiguous across multiple legs.
    """
    from nmos.controller.compat import (
        format_compatible, transport_compatible,
    )

    s_res = member.resource
    s_caps = resource_ccf_caps(s_res)
    s_format = s_res.get("format")
    s_transport = s_res.get("transport")
    s_hint = member.hint
    for r in receivers:
        if not format_compatible(s_format, r.get("format")):
            return False
        if not transport_compatible(s_transport, r.get("transport")):
            return False
        # Leaf format identity via group hints (still enforced). The
        # role-index is deliberately NOT checked — see docstring.
        from nmos.controller.grouping import extract_group_hint
        r_hint = extract_group_hint(r.get("tags"))
        if s_hint is not None and r_hint is not None:
            if s_hint.format != r_hint.format:
                return False
        if not is_compatible(s_caps, resource_ccf_caps(r)):
            return False
    return True


def _build_compatible_senders_view(
    cache: ResourceCache,
    receivers: list[dict[str, Any]],
    mode: str,
) -> list[DeviceView]:
    """Return the senders-listing-shaped view, filtered by receiver compat.

    Shape is identical to ``cache.senders_grouped()`` — a list of
    ``DeviceView`` — so the page can reuse ``partials/device_block.html``
    verbatim. Filtering happens here: empty groups and empty devices
    are dropped so the page never shows a device with no candidate
    senders.

    See ``receivers_compatible`` for the mode semantics.
    """
    if not receivers:
        return []

    all_device_views = cache.senders_grouped()

    if mode == "group":
        receiver_group = _receivers_as_natural_group(receivers)
        if receiver_group is None:
            return []
        all_sender_groups: list[NaturalGroupView] = []
        for dv in all_device_views:
            all_sender_groups.extend(dv.groups)
        matched = compatible_sender_groups(receiver_group, all_sender_groups)

        # Re-attach each matched group to a DeviceView copy that carries
        # just that device's surviving groups. The template uses
        # ``dev.device_address`` / ``dev.transports`` for the header so
        # we copy those from the original DeviceView.
        by_device_id: dict[str, DeviceView] = {}
        source_by_id = {dv.device_id: dv for dv in all_device_views}
        for g in matched:
            src = source_by_id.get(g.device_id)
            target = by_device_id.get(g.device_id)
            if target is None:
                target = DeviceView(
                    device_id=g.device_id,
                    device_serial=(src.device_serial if src else g.device_serial),
                    device_label=(src.device_label if src else g.device_label),
                    device_address=(src.device_address if src else ""),
                    transports=(list(src.transports) if src else []),
                )
                by_device_id[g.device_id] = target
            target.groups.append(g)
        return list(by_device_id.values())

    if mode == "subset":
        # Subset mode: receivers are a subset of ONE natural group.
        # Find sender groups whose leaf signature is a multiset-
        # superset of the subset's signature — the sender group may
        # have extra legs that we simply ignore (a MUX V+A+A sender
        # covers an audio-only subset).
        receiver_subset = _receivers_as_natural_group(receivers)
        if receiver_subset is None:
            return []
        all_sender_groups2: list[NaturalGroupView] = []
        for dv in all_device_views:
            all_sender_groups2.extend(dv.groups)
        matches = compatible_sender_groups_superset(
            receiver_subset, all_sender_groups2,
        )

        # Build fresh NaturalGroupView copies carrying only the
        # matched legs so the template renders just those rows. We
        # don't mutate the original views from ``cache.senders_
        # grouped()`` because they're shared state that other page
        # renders also consume.
        by_device_id2: dict[str, DeviceView] = {}
        source_by_id2 = {dv.device_id: dv for dv in all_device_views}
        for match in matches:
            g = match.group
            src = source_by_id2.get(g.device_id)
            target = by_device_id2.get(g.device_id)
            if target is None:
                target = DeviceView(
                    device_id=g.device_id,
                    device_serial=(src.device_serial if src else g.device_serial),
                    device_label=(src.device_label if src else g.device_label),
                    device_address=(src.device_address if src else ""),
                    transports=(list(src.transports) if src else []),
                )
                by_device_id2[g.device_id] = target
            target.groups.append(NaturalGroupView(
                device_id=g.device_id,
                device_serial=g.device_serial,
                device_label=g.device_label,
                hint_key=g.hint_key,
                members=list(match.matched_members),
            ))
        return list(by_device_id2.values())

    # Single mode — per-member compat filter, preserving device/group
    # structure. Empty groups and empty devices are dropped.
    devices: list[DeviceView] = []
    for dv in all_device_views:
        kept_groups: list[NaturalGroupView] = []
        for g in dv.groups:
            kept_members = [
                m for m in g.members if _member_compatible_with_all(m, receivers)
            ]
            if kept_members:
                kept_groups.append(NaturalGroupView(
                    device_id=g.device_id,
                    device_serial=g.device_serial,
                    device_label=g.device_label,
                    hint_key=g.hint_key,
                    members=kept_members,
                ))
        kept_ungrouped = [
            m for m in dv.ungrouped if _member_compatible_with_all(m, receivers)
        ]
        if kept_groups or kept_ungrouped:
            devices.append(DeviceView(
                device_id=dv.device_id,
                device_serial=dv.device_serial,
                device_label=dv.device_label,
                device_address=dv.device_address,
                transports=list(dv.transports),
                groups=kept_groups,
                ungrouped=kept_ungrouped,
            ))
    return devices
