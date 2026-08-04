# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""aiohttp application factory for the embedded NMOS controller.

The controller is the *orchestrator* of NMOS-side authorization
(OAuth2 tokens, Reservation sessions, mTLS client certs for outbound
calls) — so gating the controller itself with OAuth2 + Reservation is
circular. Access to the controller UI is protected instead by a
single admin password provided on the Node command line
(``--controllerAdminPassword``). Administrators authenticate through
a proper HTML login page (``/controller/login``); a successful POST
sets an HMAC-signed session cookie which the admin carries through
subsequent requests (see ``nmos.controller.auth`` for the cookie
format).

Transport TLS follows the Node's own mode: same certificate, same
``--nodeOptionalClientAuth`` flag (server-auth or mutual-auth). Only
the application-level auth scheme differs from the Node API's.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Final
from urllib.parse import urlsplit

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from nmos.api import _trailing_slash_middleware
from nmos.api.middleware import cors_middleware
from nmos.controller import handlers, sse
from nmos.controller.api_client import RemoteNodeClient
from nmos.controller.auth import (
    AdminSessionStore,
    SESSION_MAX_AGE_SECONDS,
    check_password,
    issue_session_token,
    verify_session_token,
)
from nmos.controller.cache import ResourceCache
from nmos.controller.debug_trace import DebugTrace
from nmos.controller.reservation import SessionStore


_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

URL_PREFIX: Final[str] = "/controller"

SESSION_COOKIE_NAME: Final[str] = "nmos_controller_session"
LOGIN_PATH: Final[str] = f"{URL_PREFIX}/login"
LOGOUT_PATH: Final[str] = f"{URL_PREFIX}/logout"
OAUTH2_LOGIN_PATH: Final[str] = f"{URL_PREFIX}/oauth2/login"
OAUTH2_CALLBACK_PATH: Final[str] = f"{URL_PREFIX}/oauth2/callback"
STATIC_PREFIX: Final[str] = f"{URL_PREFIX}/static/"
API_PREFIX: Final[str] = f"{URL_PREFIX}/api/"


Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def _is_public_path(path: str) -> bool:
    """Paths that bypass the session-cookie gate.

    Static assets must load for the login page's shell; the login,
    logout and OAuth2 endpoints handle authentication themselves
    (the OAuth2 paths run their own per-stage check inside the
    handler — see :func:`oauth2_login_handler` / :func:`oauth2_callback_handler`).
    """
    return (
        path.startswith(STATIC_PREFIX)
        or path == LOGIN_PATH
        or path == LOGOUT_PATH
        or path == OAUTH2_LOGIN_PATH
        or path == OAUTH2_CALLBACK_PATH
    )


def _safe_next_url(value: str) -> str:
    """Accept a ``next`` query/form parameter only if it points back at
    the controller. Protects against open-redirect attacks.
    """
    if not value:
        return f"{URL_PREFIX}/"
    parts = urlsplit(value)
    # Reject absolute URLs (netloc present) and any path outside the
    # controller namespace.
    if parts.scheme or parts.netloc:
        return f"{URL_PREFIX}/"
    if not parts.path.startswith(URL_PREFIX + "/") and parts.path != URL_PREFIX:
        return f"{URL_PREFIX}/"
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _wants_html(request: web.Request) -> bool:
    """Detect "user navigating with a browser" vs "JS fetch/SSE/API call".

    Page requests get redirected to the login form; anything else gets
    a 401 JSON so callers see a clean error instead of a redirect to
    HTML.
    """
    if request.path.startswith(API_PREFIX):
        return False
    accept = request.headers.get("Accept", "")
    return "text/html" in accept


def _redirect(location: str) -> web.Response:
    """Emit a plain 302 redirect response.

    We don't raise ``web.HTTPFound`` here because the shared CORS
    middleware (``nmos.api.middleware.cors_middleware``) catches
    every ``HTTPException`` and rewrites it into a JSON error body,
    stripping the ``Location`` header in the process. A plain
    ``web.Response`` with the redirect status passes through the
    middleware unmodified.
    """
    return web.Response(status=302, headers={"Location": location})


def _redirect_to_login(request: web.Request) -> web.Response:
    """Redirect an unauthenticated page request to the login form,
    preserving the originally-requested path in the ``next`` query
    so we can return there after a successful login.
    """
    from urllib.parse import quote
    target = request.path_qs
    return _redirect(f"{LOGIN_PATH}?next={quote(target)}")


def _debug_trace_middleware_factory(debug_trace: DebugTrace) -> Any:
    """Build a middleware that stamps every controller request with a
    trace id and logs start/end to the debug log.

    When ``debug_trace.enabled`` is False the middleware is still
    mounted but effectively pass-through — the tracer's ``emit`` is
    a no-op, and ``new_trace_id`` is cheap. Keeps the middleware
    chain shape identical across debug / non-debug runs so nothing
    else in the stack has to condition on the flag.

    The trace id comes from the client's ``X-Trace-Id`` header when
    present (so a browser ``fetch`` can carry the same id through
    both its own request log and any server-initiated outbound
    calls). Otherwise the middleware mints a fresh one.
    """

    @web.middleware
    async def debug_trace_middleware(
        request: web.Request, handler: Handler,
    ) -> web.StreamResponse:
        trace_id = request.headers.get("X-Trace-Id") or DebugTrace.new_trace_id()
        request["trace_id"] = trace_id
        if debug_trace.enabled:
            debug_trace.emit(
                "request_in",
                trace_id=trace_id,
                method=request.method,
                path=request.path_qs,
                remote=request.remote or "",
            )
        try:
            response = await handler(request)
        except Exception as exc:
            if debug_trace.enabled:
                debug_trace.emit(
                    "request_error",
                    trace_id=trace_id,
                    method=request.method,
                    path=request.path_qs,
                    error=repr(exc),
                )
            raise
        if debug_trace.enabled:
            debug_trace.emit(
                "request_out",
                trace_id=trace_id,
                method=request.method,
                path=request.path_qs,
                status=getattr(response, "status", 0),
            )
            # Echo the trace id on the response so the browser can
            # correlate its own fetch with the server log. Only set
            # when tracing is on — the header is a debug signal, and
            # leaking trace ids on every production response would
            # just bloat the wire for no benefit.
            response.headers["X-Trace-Id"] = trace_id
        return response

    return debug_trace_middleware


def _admin_session_middleware_factory(admin_password: str) -> Any:
    """Build a session-cookie middleware bound to the admin password.

    Two-stage gate when OAuth2 is configured on the app:

    1. Cookie missing / signature invalid → redirect to ``/login``
       (HTML) or 401 (JSON/SSE).
    2. Cookie valid but session ``stage != keycloak_authed`` → redirect
       to ``/oauth2/login`` to complete the auth_code flow.

    When OAuth2 is NOT configured (``app["controller_oauth2_client"]``
    is ``None``), step 2 is skipped — the password gate alone grants
    access, preserving the pre-Phase-3 behaviour.
    """

    @web.middleware
    async def admin_session_middleware(
        request: web.Request, handler: Handler,
    ) -> web.StreamResponse:
        # Only protect routes under the controller prefix.
        if not request.path.startswith(URL_PREFIX):
            return await handler(request)

        # Public paths (login, logout, oauth2/*, static) bypass the gate.
        if _is_public_path(request.path):
            return await handler(request)

        token = request.cookies.get(SESSION_COOKIE_NAME, "")
        if verify_session_token(token, admin_password):
            # Attach the admin's server-side state to the request so
            # downstream handlers can reach the exclusive_key and the
            # acquired-devices tracking without re-deriving anything.
            #
            # We deliberately do NOT auto-create the state on a cache
            # miss. A miss means either the controller restarted
            # (in-memory store wiped) or the original login expired —
            # in both cases the operator's controller-password gate
            # has not been re-cleared in THIS process lifetime, so
            # we route them through ``/controller/login`` again. The
            # 12-hour HMAC cookie alone must not be enough to bypass
            # the local password layer when the OAuth2 flow is also
            # configured.
            store: AdminSessionStore = request.app["controller_admin_sessions"]
            session = store.get(token)
            if session is None:
                if _wants_html(request):
                    return _redirect_to_login(request)
                return web.json_response(
                    {"error": "session_expired"},
                    status=401,
                    headers={
                        "WWW-Authenticate":
                            'Session realm="nmos-controller"',
                    },
                )
            request["admin_session"] = session

            # Second-stage gate: when OAuth2 is configured the session
            # must have completed the Keycloak round-trip. Sessions
            # still in ``controller_authed`` are bounced through
            # ``/oauth2/login``.
            oauth2_client = request.app.get("controller_oauth2_client")
            if oauth2_client is not None and not session.is_keycloak_authed:
                if _wants_html(request):
                    return _redirect(OAUTH2_LOGIN_PATH)
                return web.json_response(
                    {"error": "oauth2_required"},
                    status=401,
                    headers={
                        "WWW-Authenticate":
                            'Session realm="nmos-controller", '
                            'oauth2_required',
                    },
                )

            return await handler(request)

        # Unauthenticated — route page requests through the login UI
        # and other requests (JSON, SSE) through a clean 401.
        if _wants_html(request):
            return _redirect_to_login(request)
        return web.json_response(
            {"error": "unauthenticated"},
            status=401,
            headers={"WWW-Authenticate": 'Session realm="nmos-controller"'},
        )

    return admin_session_middleware


# ---------------------------------------------------------------------------
# Login / logout handlers
# ---------------------------------------------------------------------------

def _render(
    request: web.Request, name: str, context: dict[str, Any],
) -> web.Response:
    env = request.app["jinja_env"]
    # Expose the debug flag so ``base.html`` can stamp
    # ``<html data-debug="1">`` on the login page too — operators
    # debugging a failed login benefit from the same capture logs.
    trace: Any = request.app.get("controller_debug_trace")
    debug_enabled = bool(trace is not None and getattr(trace, "enabled", False))
    ctx = dict(context)
    ctx.setdefault("debug_enabled", debug_enabled)
    html = env.get_template(name).render(**ctx)
    return web.Response(text=html, content_type="text/html")


async def login_page_handler(request: web.Request) -> web.StreamResponse:
    """GET /controller/login — render the admin login form."""
    next_url = _safe_next_url(request.query.get("next", ""))
    return _render(request, "login.html", {"error": None, "next_url": next_url})


async def login_submit_handler(request: web.Request) -> web.StreamResponse:
    """POST /controller/login — verify password, set session cookie.

    When the app has been configured with an OAuth2 client (auth_code
    mode), the session is initially created in stage
    ``controller_authed`` and the response 302s to
    ``/controller/oauth2/login`` to start the Keycloak round-trip.
    Without OAuth2 the redirect points directly to ``next_url`` and the
    session is treated as fully authenticated by the middleware (the
    second-stage check is skipped when no client is configured).
    """
    admin_password: str = request.app["controller_admin_password"]

    form = await request.post()
    supplied = str(form.get("password", ""))
    next_url = _safe_next_url(str(form.get("next", "")))

    if not check_password(supplied, admin_password):
        error_response: web.Response = _render(
            request, "login.html",
            {"error": "Incorrect password.", "next_url": next_url},
        )
        error_response.set_status(401)
        return error_response

    token = issue_session_token(admin_password)
    # Mint the server-side state (with a fresh 16-byte exclusive_key)
    # as soon as the cookie is issued, so every subsequent acquire this
    # admin makes reuses the same key.
    store: AdminSessionStore = request.app["controller_admin_sessions"]
    session = store.get_or_create(token)
    session.stage = "controller_authed"
    # Wipe any stale OAuth2 state from a prior login under the same
    # token (rare, but safe) — the auth_code redirect mints fresh
    # values.
    session.oauth2_state_nonce = None
    session.oauth2_tokens = None

    # Stash the post-login destination so /oauth2/callback can redirect
    # the admin back where they were headed.
    if request.app.get("controller_oauth2_client") is not None:
        request.app["controller_pending_next"] = {
            **request.app.get("controller_pending_next", {}),
            token: next_url,
        }
        ok_response: web.Response = _redirect(OAUTH2_LOGIN_PATH)
    else:
        ok_response = _redirect(next_url)

    # SameSite=Lax (not Strict) so the cookie survives the
    # OAuth2 callback navigation. After the admin authenticates at
    # Keycloak (a different origin), Keycloak 302s the browser back
    # to /controller/oauth2/callback?code=...&state=...; that's a
    # cross-site top-level navigation. With Strict the browser drops
    # the cookie and the callback handler 401s the admin back to
    # /controller/login. Lax allows the cookie on top-level GETs
    # (which is exactly what the OAuth2 redirect is) while still
    # blocking it on cross-site POSTs, iframes, and XHRs — the
    # standard cookie posture for sites that participate in OAuth2
    # redirect flows.
    ok_response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=request.scheme == "https",
        samesite="Lax",
        path=URL_PREFIX,
    )
    return ok_response


async def oauth2_login_handler(request: web.Request) -> web.StreamResponse:
    """GET /controller/oauth2/login — kick off the Keycloak auth_code redirect.

    Requires a session cookie minted by the local password gate
    (i.e. ``stage == "controller_authed"``). Mints a fresh state nonce,
    persists it on the session, and 302s the browser to Keycloak's
    ``/auth`` endpoint.
    """
    oauth2_client = request.app.get("controller_oauth2_client")
    if oauth2_client is None:
        return web.Response(status=404, text="OAuth2 not configured")

    admin_password: str = request.app["controller_admin_password"]
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not verify_session_token(token, admin_password):
        return _redirect_to_login(request)

    store: AdminSessionStore = request.app["controller_admin_sessions"]
    session = store.get_or_create(token)

    # If the admin is already fully authenticated, skip the redirect
    # — they're probably reloading after a successful login.
    if session.is_keycloak_authed:
        next_url = request.app.get("controller_pending_next", {}).pop(
            token, f"{URL_PREFIX}/",
        )
        return _redirect(next_url)

    state_nonce = oauth2_client.new_state_nonce()
    session.oauth2_state_nonce = state_nonce
    redirect_uri = _oauth2_callback_uri(request)
    auth_url = await oauth2_client.build_auth_url(
        redirect_uri=redirect_uri, state=state_nonce,
    )
    return _redirect(auth_url)


async def oauth2_callback_handler(request: web.Request) -> web.StreamResponse:
    """GET /controller/oauth2/callback — exchange the code for tokens.

    Verifies the state nonce against the session (CSRF defense), POSTs
    the code at Keycloak's ``/token`` endpoint, validates the access
    token signature against JWKS, then promotes the session to
    ``keycloak_authed`` and 302s back to the admin's original
    destination.
    """
    oauth2_client = request.app.get("controller_oauth2_client")
    if oauth2_client is None:
        return web.Response(status=404, text="OAuth2 not configured")

    admin_password: str = request.app["controller_admin_password"]
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not verify_session_token(token, admin_password):
        return _redirect_to_login(request)

    store: AdminSessionStore = request.app["controller_admin_sessions"]
    session = store.get_or_create(token)

    code = request.query.get("code", "")
    state = request.query.get("state", "")
    error = request.query.get("error", "")

    if error:
        return web.Response(
            status=400,
            text=f"Keycloak rejected the auth_code request: {error}",
        )
    if not code or not state:
        return web.Response(status=400, text="missing code or state in callback")

    if (
        not session.oauth2_state_nonce
        or session.oauth2_state_nonce != state
    ):
        # CSRF: the state we sent does not match what came back. Burn
        # the nonce and force the admin to restart the flow.
        session.oauth2_state_nonce = None
        return web.Response(
            status=400,
            text="oauth2 state mismatch — restart the login from /controller/login",
        )

    redirect_uri = _oauth2_callback_uri(request)
    try:
        tokens = await oauth2_client.exchange_code(
            code=code, redirect_uri=redirect_uri,
        )
    except Exception as exc:  # OAuth2Error or network errors
        return web.Response(
            status=502,
            text=f"oauth2 token exchange failed: {exc}",
        )

    session.oauth2_tokens = tokens
    session.stage = "keycloak_authed"
    session.oauth2_state_nonce = None  # one-shot

    next_url = request.app.get("controller_pending_next", {}).pop(
        token, f"{URL_PREFIX}/",
    )
    return _redirect(next_url)


def _oauth2_callback_uri(request: web.Request) -> str:
    """Build the absolute callback URL Keycloak should 302 back to.

    Uses the request's own scheme + host so the URL matches whatever
    the admin's browser is talking to. Must align with one of the
    redirect URIs registered on the ``controller-<serial>`` Keycloak
    client (see ``nmos_keycloak.py:_controller_redirect_uris``).
    """
    scheme = request.scheme
    host = request.host
    return f"{scheme}://{host}{OAUTH2_CALLBACK_PATH}"


async def logout_handler(request: web.Request) -> web.StreamResponse:
    """GET or POST /controller/logout — clear the session cookie.

    Before discarding the server-side state, release every Node
    Reservation the admin had held. Running release FIRST (with the
    state still alive) means the polling task won't try to reacquire
    a session the admin is actively giving up.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if token:
        store: AdminSessionStore = request.app["controller_admin_sessions"]
        state = store.get(token)
        if state is not None:
            reservations: SessionStore = request.app["controller_reservations"]
            await reservations.release_all(state)
        store.discard(token)
    response: web.Response = _redirect(LOGIN_PATH)
    response.del_cookie(SESSION_COOKIE_NAME, path=URL_PREFIX)
    return response


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_controller_app(
    node: Any,
    *,
    cache: ResourceCache | None = None,
    remote_client: RemoteNodeClient | None = None,
    admin_password: str = "",
    debug_log_path: str | None = None,
    oauth2_config: Any = None,
) -> web.Application:
    """Create the controller aiohttp app.

    Args:
        node: the active ``Node``.
        cache: injected ``ResourceCache``. Fresh empty one if ``None``.
        remote_client: injected ``RemoteNodeClient``. Fresh one if ``None``.
        admin_password: the password the admin must supply on the login
            page. Must be non-empty — an empty password is rejected at
            startup.
        debug_log_path: when set, enables deep-debug tracing — a
            rotating JSONL log at this path, a per-request trace id
            middleware, and the ``/api/debug/*`` endpoints. None
            disables all of it (normal production behaviour).
        oauth2_config: optional :class:`nmos.controller.oauth2.OAuth2Config`.
            When provided the controller enables the two-stage admin
            login (local password → Keycloak auth_code) and starts a
            background task that refreshes session tokens
            proactively. ``None`` keeps the controller in the
            password-only mode used today.
    """
    if not admin_password:
        raise ValueError(
            "create_controller_app: admin_password is required — "
            "set --controllerAdminPassword on the Node command line",
        )

    if cache is None:
        cache = ResourceCache()

    # Build the tracer first — the middleware factory below needs it,
    # and the remote client wants it too so every outbound HTTP call
    # gets logged alongside the browser request that triggered it.
    # When ``debug_log_path`` is None every emit() is a no-op, so this
    # is safe to instantiate unconditionally.
    debug_trace = DebugTrace(debug_log_path)

    if remote_client is None:
        remote_client = RemoteNodeClient(ssl_context=None, debug_trace=debug_trace)
    elif remote_client.debug is None and debug_trace.enabled:
        # Caller-provided client without its own tracer — graft ours on
        # so outbound calls still correlate with request trace ids.
        remote_client.attach_debug(debug_trace)

    middlewares: list[Any] = [
        _trailing_slash_middleware,
        cors_middleware,
        _debug_trace_middleware_factory(debug_trace),
        _admin_session_middleware_factory(admin_password),
    ]
    app = web.Application(middlewares=middlewares)
    app["node"] = node
    app["controller_cache"] = cache
    app["controller_remote_client"] = remote_client
    app["controller_admin_password"] = admin_password
    admin_sessions = AdminSessionStore()
    app["controller_admin_sessions"] = admin_sessions
    # Node Reservation session manager — depends on the remote client
    # (for acquire/renew/release/keepalive HTTP) and the admin session
    # store (so its poll task can cull sessions whose admin logged
    # out). Started / stopped via the app lifecycle hooks below.
    reservations = SessionStore(remote_client, admin_sessions, debug_trace)
    app["controller_reservations"] = reservations
    # Tracer was created above (needed by the middleware factory);
    # expose it on the app so handlers / other modules can emit
    # without re-importing the factory.
    app["controller_debug_trace"] = debug_trace

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    app["jinja_env"] = env

    # OAuth2 wiring — only when the operator has configured it on the
    # command line. Absence of a client preserves the password-only
    # behaviour and skips the second-stage middleware check.
    oauth2_client_obj: Any = None
    if oauth2_config is not None:
        from nmos.controller.oauth2 import OAuth2Client
        oauth2_client_obj = OAuth2Client(oauth2_config)
    app["controller_oauth2_client"] = oauth2_client_obj
    # Per-token "next URL" map. Populated when /login redirects through
    # the OAuth2 flow; consumed by /oauth2/callback. Plain dict — keys
    # are short-lived (one round-trip).
    app["controller_pending_next"] = {}
    # Slot for the background refresh task; populated below.
    app["controller_oauth2_refresh_task"] = None

    _register_routes(app)

    async def _on_startup(_app: web.Application) -> None:
        await remote_client.start()
        await reservations.start()
        if oauth2_client_obj is not None:
            _app["controller_oauth2_refresh_task"] = asyncio.create_task(
                _oauth2_refresh_loop(_app),
                name="controller-oauth2-refresh",
            )

    async def _on_cleanup(_app: web.Application) -> None:
        # Reservation poll task + release of every held session must
        # run BEFORE the HTTP client closes — otherwise release calls
        # fail with ClientError.
        refresh_task = _app.get("controller_oauth2_refresh_task")
        if refresh_task is not None and not refresh_task.done():
            refresh_task.cancel()
            try:
                await refresh_task
            except (asyncio.CancelledError, Exception):
                pass
        await reservations.stop()
        await remote_client.close()

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)

    return app


# ---------------------------------------------------------------------------
# Proactive-refresh loop
# ---------------------------------------------------------------------------

async def _oauth2_refresh_loop(app: web.Application) -> None:
    """Background task that walks the admin session store and refreshes
    OAuth2 access tokens before they expire.

    Implements a proactive-refresh model. Each tick:

    * iterate every session whose stage is ``keycloak_authed``,
    * call ``OAuth2Tokens.needs_refresh`` (default: <25% of a 1h
      access-token lifetime remaining → refresh),
    * call ``OAuth2Client.refresh`` and atomically replace
      ``session.oauth2_tokens`` on success,
    * on failure, drop the session back to ``controller_authed`` so
      the next request 302s the admin through ``/oauth2/login``
      again.

    Polls every 30 s — coarse enough to avoid hot-looping yet fine
    enough to refresh well within Keycloak's default 1 h token
    lifetime.
    """
    POLL_SECONDS = 30.0
    oauth2_client = app["controller_oauth2_client"]
    store: AdminSessionStore = app["controller_admin_sessions"]
    while True:
        try:
            await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            return
        now = time.monotonic()
        for session in store.all_states():
            tokens = session.oauth2_tokens
            if session.stage != "keycloak_authed" or tokens is None:
                continue
            if not tokens.needs_refresh(now):
                continue
            try:
                fresh = await oauth2_client.refresh(
                    refresh_token=tokens.refresh_token,
                )
                session.oauth2_tokens = fresh
            except Exception:
                # On any failure (refresh-token expired, revoked,
                # network blip past retry budget) drop the OAuth2
                # half of the session. The admin gets bounced back
                # through /oauth2/login on their next request.
                session.stage = "controller_authed"
                session.oauth2_tokens = None


def _register_routes(app: web.Application) -> None:
    prefix = URL_PREFIX

    # Auth endpoints (public — the middleware whitelists these).
    app.router.add_get(LOGIN_PATH, login_page_handler)
    app.router.add_post(LOGIN_PATH, login_submit_handler)
    app.router.add_get(LOGOUT_PATH, logout_handler)
    app.router.add_post(LOGOUT_PATH, logout_handler)
    # OAuth2 auth_code endpoints (public — handlers re-check the
    # session cookie themselves; 404 when oauth2_config was None).
    app.router.add_get(OAUTH2_LOGIN_PATH, oauth2_login_handler)
    app.router.add_get(OAUTH2_CALLBACK_PATH, oauth2_callback_handler)

    # Pages (session-protected).
    app.router.add_get(f"{prefix}/", handlers.index)
    app.router.add_get(f"{prefix}/senders", handlers.senders_list)
    app.router.add_get(f"{prefix}/senders/caps", handlers.senders_caps)
    app.router.add_get(f"{prefix}/senders/configure", handlers.senders_configure)
    app.router.add_get(f"{prefix}/receivers", handlers.receivers_list)
    app.router.add_get(f"{prefix}/receivers/compatible-senders",
                       handlers.receivers_compatible)
    app.router.add_get(f"{prefix}/receivers/view-caps",
                       handlers.receivers_view_caps)
    app.router.add_get(f"{prefix}/receivers/caps", handlers.receivers_caps)
    app.router.add_get(f"{prefix}/receivers/configure",
                       handlers.receivers_configure)

    # NMOS resource inspector (read-only detail pages). Dynamic transport /
    # SDP pages fetch live from the node; flow/source/device/node read the
    # cache. The 3-segment dynamic paths don't collide with the 2-segment
    # static ones above (e.g. /senders/caps).
    app.router.add_get(f"{prefix}/senders/{{sender_id}}/transport",
                       handlers.sender_transport_detail)
    app.router.add_get(f"{prefix}/receivers/{{receiver_id}}/transport",
                       handlers.receiver_transport_detail)
    app.router.add_get(f"{prefix}/senders/{{sender_id}}/sdp",
                       handlers.sender_sdp_view)
    app.router.add_get(f"{prefix}/receivers/{{receiver_id}}/sdp",
                       handlers.receiver_sdp_view)
    app.router.add_get(f"{prefix}/senders/{{sender_id}}/is11",
                       handlers.sender_is11_status)
    app.router.add_get(f"{prefix}/receivers/{{receiver_id}}/is11",
                       handlers.receiver_is11_status)
    app.router.add_get(f"{prefix}/senders/{{sender_id}}/monitor",
                       handlers.sender_monitor_detail)
    app.router.add_get(f"{prefix}/receivers/{{receiver_id}}/monitor",
                       handlers.receiver_monitor_detail)
    app.router.add_get(f"{prefix}/senders/{{sender_id}}/flow",
                       handlers.sender_flow_redirect)
    app.router.add_get(f"{prefix}/receivers/{{receiver_id}}/flow",
                       handlers.receiver_flow_redirect)
    app.router.add_get(f"{prefix}/senders/{{sender_id}}/resource",
                       handlers.sender_detail)
    app.router.add_get(f"{prefix}/receivers/{{receiver_id}}/resource",
                       handlers.receiver_detail)
    app.router.add_get(f"{prefix}/flows/{{flow_id}}", handlers.flow_detail)
    app.router.add_get(f"{prefix}/sources/{{source_id}}", handlers.source_detail)
    app.router.add_get(f"{prefix}/devices/{{device_id}}", handlers.device_detail)
    app.router.add_get(f"{prefix}/nodes/{{node_id}}", handlers.node_detail)

    # JSON list / compat endpoints
    app.router.add_get(f"{prefix}/api/senders", handlers.api_list_senders)
    app.router.add_get(f"{prefix}/api/receivers", handlers.api_list_receivers)
    app.router.add_get(
        f"{prefix}/api/receivers/{{receiver_id}}/compatible-senders",
        handlers.api_compatible_senders,
    )

    # State-changing proxy endpoints
    app.router.add_post(f"{prefix}/api/senders/{{sender_id}}/constrain",
                        handlers.api_sender_constrain)
    app.router.add_post(f"{prefix}/api/senders/{{sender_id}}/unconstrain",
                        handlers.api_sender_unconstrain)
    app.router.add_post(f"{prefix}/api/senders/{{sender_id}}/activate",
                        handlers.api_sender_activate)
    app.router.add_post(f"{prefix}/api/senders/{{sender_id}}/deactivate",
                        handlers.api_sender_deactivate)
    app.router.add_post(f"{prefix}/api/receivers/{{receiver_id}}/activate",
                        handlers.api_receiver_activate)
    app.router.add_post(f"{prefix}/api/receivers/{{receiver_id}}/deactivate",
                        handlers.api_receiver_deactivate)

    # Privacy / Node Reservation endpoints
    app.router.add_get(f"{prefix}/api/privacy/options",
                       handlers.api_privacy_options)
    app.router.add_post(f"{prefix}/api/privacy/acquire",
                        handlers.api_privacy_acquire)
    app.router.add_post(f"{prefix}/api/privacy/release",
                        handlers.api_privacy_release)

    # Debug endpoints — always registered; handlers 404 when
    # --debug-in-depth is off. Keeping the route table constant across
    # debug/non-debug runs means no routing-table divergence to test.
    app.router.add_post(f"{prefix}/api/debug/client-event",
                        handlers.api_debug_client_event)
    app.router.add_get(f"{prefix}/api/debug/snapshot",
                       handlers.api_debug_snapshot)

    # SSE stream
    app.router.add_get(f"{prefix}/api/status-events", sse.status_events_handler)

    # Static files (public — no session required)
    if _STATIC_DIR.is_dir():
        app.router.add_static(
            f"{prefix}/static/", path=str(_STATIC_DIR), show_index=False,
        )
