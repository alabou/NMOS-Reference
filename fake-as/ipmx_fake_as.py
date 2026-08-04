# Copyright (C) 2026 Matrox Graphics Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A small, real OAuth 2.0 / OIDC Authorization Server, for testing and demos.

What the AS serves:

  * ``GET /.well-known/oauth-authorization-server`` —
    RFC 8414 §3.1 "metadata at the host root" form (no api_selector).
  * ``GET /.well-known/oauth-authorization-server/<api_selector>`` —
    RFC 8414 §3.1 "host-root-with-trailing-path" form.
  * ``GET /<api_selector>/.well-known/oauth-authorization-server`` —
    Keycloak-style "api_selector first" form.
  * ``GET /<api_selector>/.well-known/openid-configuration`` —
    OIDC Discovery 1.0 form.
  * ``GET /<api_selector>/jwks`` — the JWKS the metadata documents
    advertise (matches whichever ``jwks_uri`` the DUT discovered).
  * ``GET|POST /<api_selector>/authorize`` — the authorization endpoint.
    GET renders a sign-in form; POST checks the credentials and 302s
    back to the client's registered redirect URI with a single-use
    ``code``. One pre-canned operator account exists (see
    ``FakeASConfig.operator_username``); there is no user directory.
  * ``POST /<api_selector>/token`` — ``client_credentials``,
    ``authorization_code``, and ``refresh_token`` grants, all returning
    tokens minted with the AS's current signing key. (The validator
    usually doesn't use this; it mints adversarial tokens directly via
    ``ipmx_security_tokens.mint_token``.)

Serving the authorization endpoint is what lets the reference
Controller complete a browser login against this fixture instead of
requiring a Keycloak deployment. It works because the Controller
locates endpoints through the metadata document above, per IS-10
``Behaviour - Clients.md``; nothing here mimics Keycloak's URL layout.

All three TR-10-SEC §14.3.2 metadata URL forms are served, so a Node
that implements any of the spec's three discovery probes converges
on the same JWKS document and the same key set.

The TLS cert must be trusted by the DUT. The simplest setup for our
reference-node bring-up: the operator points the DUT's ``--oauth2Host``
at a hostname (e.g. ``XYZ-SNX00000``) whose cert is already in the
workspace's ``Certificates/build.0/`` directory and whose CA
(``ExampleRootCA.pem``) is already in the DUT's trust store.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import secrets
import ssl
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from aiohttp import web

from ipmx_security_tokens import SigningKey, jwks_for, mint_token, TokenTemplate


# ---------------------------------------------------------------------------
# OAuth 2.0 protocol vocabulary
# ---------------------------------------------------------------------------

class GrantType(StrEnum):
    """``grant_type`` values this AS honours at the token endpoint."""

    CLIENT_CREDENTIALS = "client_credentials"
    """Machine-to-machine. The OAuth client is itself the principal."""

    AUTHORIZATION_CODE = "authorization_code"
    """Browser redirect flow — what the reference Controller uses."""

    REFRESH_TOKEN = "refresh_token"
    """Exchange a refresh token for a fresh access/refresh pair."""


class ResponseType(StrEnum):
    """``response_type`` values accepted at the authorization endpoint.

    Only ``code`` is supported. The implicit grant (``token``) is
    deliberately absent: it returns the access token in the URL
    fragment, which the OAuth 2.0 Security BCP deprecates.
    """

    CODE = "code"


class OAuthError(StrEnum):
    """RFC 6749 §4.1.2.1 / §5.2 error codes used by this AS."""

    INVALID_REQUEST = "invalid_request"
    INVALID_CLIENT = "invalid_client"
    INVALID_GRANT = "invalid_grant"
    UNAUTHORIZED_CLIENT = "unauthorized_client"
    UNSUPPORTED_GRANT_TYPE = "unsupported_grant_type"
    UNSUPPORTED_RESPONSE_TYPE = "unsupported_response_type"
    ACCESS_DENIED = "access_denied"


class OperatorAccess(StrEnum):
    """How much the pre-canned operator's token is allowed to do.

    Read-only is a supported configuration rather than a degraded one: it is
    what an NMOS monitoring station should be issued, and it makes the
    difference between reading and operating demonstrable on one rig.
    """

    READ_WRITE = "readwrite"
    """Read and configure — what a Controller operator needs."""

    READ = "read"
    """Read only. Every state-changing call returns 403."""


#: Scopes that are not NMOS API names and get no ``x-nmos-*`` privilege claim.
_NON_NMOS_SCOPES: frozenset[str] = frozenset({"openid", "profile", "email"})


AUTHORIZATION_CODE_TTL_SECONDS: int = 60
"""How long an issued authorization code stays redeemable.

RFC 6749 §4.1.2 says a code "MUST be short lived" and recommends a
maximum of 10 minutes. A browser round-trip between our own two
processes takes milliseconds, so 60s is generous while keeping the
replay window small.
"""

REFRESH_TOKEN_TTL_SECONDS: int = 8 * 3600
"""Refresh-token lifetime. Longer than the 1h access-token TTL so the
Controller's refresh path is exercisable within a tutorial session."""


# ---------------------------------------------------------------------------
# Client registry and issued-grant records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegisteredClient:
    """One OAuth 2.0 client registration.

    IS-10 ``Behaviour - Authorization Servers.md`` states plainly that
    "Authorization Servers MUST NOT grant tokens to unregistered
    clients", so an unknown ``client_id`` is rejected rather than
    auto-provisioned.
    """

    client_id: str
    client_secret: str | None
    """``None`` marks a public client. IS-10: "Public clients MUST NOT be
    issued with a client secret, and Authorization Servers MUST NOT
    accept client credentials as valid authentication for a public
    client"."""

    redirect_uris: tuple[str, ...] = ()
    """Exact-match redirect URIs. No pattern matching, per IS-10
    ``Behaviour - Clients.md``: "Redirect URIs MUST be complete
    (fully-qualified) and not use pattern-matching, as this makes them
    susceptible to Redirect URI Validation Attacks"."""

    def allows_redirect(self, uri: str) -> bool:
        """Exact string comparison against the registered set."""
        return uri in self.redirect_uris

    def authenticates_with(self, secret: str | None) -> bool:
        """Is ``secret`` acceptable client authentication for this client?"""
        if self.client_secret is None:
            # Public client: credentials must NOT be accepted at all.
            return secret is None or secret == ""
        return secrets.compare_digest(secret or "", self.client_secret)


@dataclass
class AuthorizationCode:
    """A single-use code issued by the authorization endpoint."""

    client_id: str
    redirect_uri: str
    scope: str
    subject: str
    """The authenticated end user — becomes the token's ``sub``."""
    expires_at: float
    redeemed: bool = False

    def is_valid_at(self, now: float) -> bool:
        return not self.redeemed and now < self.expires_at


@dataclass
class RefreshGrant:
    """State behind an opaque refresh token."""

    client_id: str
    scope: str
    subject: str
    expires_at: float


# ---------------------------------------------------------------------------
# AS configuration
# ---------------------------------------------------------------------------

@dataclass
class FakeASConfig:
    """Run-time configuration for :class:`FakeAuthorizationServer`."""
    host: str
    port: int
    cert_chain: Path
    private_key: Path
    api_selector: str = "TR-10-SEC"
    """The path component inserted between the host and ``/.well-known/``.

    Common conventions: Keycloak uses ``realms/<realm>``; Hydra and
    minimal-config OAuth deployments leave this empty.

    .. note::
       This default does **not** match the reference Node, whose
       ``--oauth2ApiSelector`` defaults to ``realms/TR-10-SEC``. Callers
       driving a real Node must pass the matching selector explicitly
       (the validator and the tutorial launcher both do).
    """

    clients: tuple[RegisteredClient, ...] = ()
    """Registered OAuth 2.0 clients. Empty means the authorization and
    token endpoints reject everything — see :class:`RegisteredClient`."""

    operator_username: str = "tr-10-sec-operator"
    """The single pre-canned end-user account the login form accepts.

    This AS is a test fixture: there is no user directory, no
    registration, and no password reset. One account is enough to drive
    the ``authorization_code`` flow end to end."""

    operator_password: str = "admin"
    """Password for :attr:`operator_username`. Defaults to the reference
    Controller's own admin password so a tutorial operator types the
    same secret at both gates."""

    operator_access: OperatorAccess = OperatorAccess.READ_WRITE
    """Whether the operator's token authorises configuration or only reading.

    Defaults to read/write: an operator signing in to a Controller is there
    to operate the system, and a scope-only token would let them read every
    Node and then fail every configuration call with 403. See
    :meth:`FakeAuthorizationServer._nmos_privileges`."""

    @property
    def issuer(self) -> str:
        if self.api_selector:
            return f"https://{self.host}:{self.port}/{self.api_selector}"
        return f"https://{self.host}:{self.port}"

    def client(self, client_id: str) -> RegisteredClient | None:
        """Look up a registration by ``client_id``, or ``None``."""
        for entry in self.clients:
            if entry.client_id == client_id:
                return entry
        return None


# ---------------------------------------------------------------------------
# FakeAuthorizationServer
# ---------------------------------------------------------------------------

class FakeAuthorizationServer:
    """A minimal RFC 8414 / OIDC-Discovery / JWKS server.

    Usage::

        as_cfg = FakeASConfig(host="XYZ-SNX00000", port=9443,
                              cert_chain=Path(".../cert.pem"),
                              private_key=Path(".../key.pem"))
        as_ = FakeAuthorizationServer(as_cfg)
        await as_.start()
        # ... drive tests against the DUT ...
        await as_.stop()
    """

    def __init__(
        self,
        config: FakeASConfig,
        *,
        signing_keys: list[SigningKey] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        # Default: one RS256 key (the algorithm TR-10-SEC mandates support
        # for; the simplest default that exercises the full validation
        # path on the DUT). Tests that need ES256/ES512/RS512 coverage
        # can pass an extra key set here.
        self._keys: list[SigningKey] = signing_keys if signing_keys is not None \
            else [SigningKey.generate(alg="RS256", kid="fake-as-rs256")]
        self._logger = logger if logger is not None else logging.getLogger(__name__)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        # ----- Test-mode toggles -----
        self._broken: bool = False
        """When True, every endpoint returns HTTP 503. Used by the
        §14.3.2-3 / §14.3.2-5 fail-closed probes — the validator sets
        the AS to broken, rotates signing keys to invalidate the DUT's
        cache, and verifies the DUT refuses access until the AS is
        un-broken and a fresh JWKS is fetched."""
        self._min_tls_version: ssl.TLSVersion = ssl.TLSVersion.TLSv1_2
        """Minimum TLS version the AS accepts. Default is TR-10-SEC
        compliant (1.2); the §14.3.2-11 probe lowers it to TLS 1.0 so
        the validator can verify the DUT does NOT negotiate < 1.2."""
        self._connection_log: list[dict[str, str]] = []
        """One entry per incoming TLS handshake: {tls_version, cipher,
        path, method}. The §14.3.2-11 probe inspects this to verify
        the DUT negotiated 1.2 or 1.3 on its JWKS fetch."""
        self._codes: dict[str, AuthorizationCode] = {}
        """Outstanding authorization codes, keyed by the code itself.
        Entries are single-use (see :meth:`_handle_authorization_code_grant`)
        and expire after :data:`AUTHORIZATION_CODE_TTL_SECONDS`."""
        self._refresh_grants: dict[str, RefreshGrant] = {}
        """Outstanding refresh tokens, keyed by the opaque token string."""
        self._audit_log: list[dict[str, Any]] = []
        """Append-only authorization audit trail.

        IS-10 ``Behaviour - Authorization Servers.md`` § Audit
        Requirements: "Authorization Servers MUST securely provide a log
        of each authorization and client registration which is performed,
        including initial token generation and token refreshes. Logs MUST
        include an accurate timestamp and an identifier for the user who
        authorized the action. Logs MUST NOT contain sensitive information
        such as secrets." Entries here carry the subject and client but
        never a password, code, or token value."""
        self._default_aud_entry: str | None = None
        """The string this AS bakes into every token's ``aud[0]`` by
        default. The validator sets this to the DUT's hostname (a
        cert SAN that contains the BCP-002-02 instance_id as a
        substring) so the Node's cert-binding aud rule from §14.3.3.4
        is satisfied. When ``None``, ``mint_token`` falls back to
        the instance_id alone — sufficient only for unit tests that
        run with ``allow_non_tls_for_testing()``."""

    # ----- Public API -----

    @property
    def primary_key(self) -> SigningKey:
        """The signing key the AS mints tokens with by default."""
        return self._keys[0]

    @property
    def signing_keys(self) -> list[SigningKey]:
        """All signing keys currently advertised in the AS's JWKS."""
        return list(self._keys)

    def key_for_alg(self, alg: str) -> SigningKey | None:
        """Return the signing key registered for ``alg``, or None.

        Used by the §14.3.3.2 algorithm-matrix tests so the validator
        can mint tokens with the right key per algorithm (RS256, RS512,
        ES256, ES512) — provided the AS was started with the
        corresponding keys.
        """
        for key in self._keys:
            if key.alg == alg:
                return key
        return None

    @property
    def issuer(self) -> str:
        return self._config.issuer

    def token_template(self, *, instance_id: str, client_id: str) -> TokenTemplate:
        """Helper: produce a TokenTemplate with the AS's issuer baked in.

        The template's ``aud_entry`` is filled from
        :attr:`default_aud_entry` if set (the validator sets it from
        the DUT hostname at startup, so tokens carry an aud value that
        actually matches the DUT's cert SAN — without this, a bare
        instance_id like ``SNX00001`` is a substring of the SAN but
        not a cert identity, and every token gets 403).
        """
        return TokenTemplate(
            iss=self._config.issuer,
            instance_id=instance_id,
            client_id=client_id,
            aud_entry=self._default_aud_entry,
        )

    @property
    def default_aud_entry(self) -> str | None:
        return self._default_aud_entry

    @default_aud_entry.setter
    def default_aud_entry(self, value: str | None) -> None:
        self._default_aud_entry = value

    def rotate_keys(self, new_keys: list[SigningKey]) -> None:
        """Replace the signing key set.

        Used by the §14.3.2 key-rotation test: the validator rotates
        keys and asserts the DUT picks up the new set on its next
        scheduled refresh (or fails closed when keys go stale).
        """
        if not new_keys:
            raise ValueError("FakeAuthorizationServer needs at least one key")
        self._keys = list(new_keys)

    def set_broken(self, broken: bool) -> None:
        """Enable or disable the "broken AS" mode.

        When ``broken=True`` every endpoint (metadata, jwks, token)
        returns HTTP 503. Used by the §14.3.2-3 / §14.3.2-5 fail-closed
        probes: the validator breaks the AS, signs a token with a
        rotated key the DUT has not yet seen, and asserts the DUT
        refuses access — it cannot fetch the new key set because the
        AS is unreachable / broken.
        """
        self._broken = broken
        self._logger.info("FakeAS: broken=%s", broken)

    def set_min_tls_version(self, version: ssl.TLSVersion) -> None:
        """Lower the AS's minimum-accepted TLS version. Must be called
        BEFORE :meth:`start` — the SSL context is built at start time."""
        self._min_tls_version = version

    @property
    def connection_log(self) -> list[dict[str, str]]:
        """Read-only view of the per-request TLS / HTTP log. The
        §14.3.2-11 probe inspects this to verify the DUT negotiated
        TLS 1.2 or 1.3 (never 1.0 / 1.1) on its JWKS fetch."""
        return list(self._connection_log)

    def clear_connection_log(self) -> None:
        """Empty the connection log. The validator calls this before
        the probe so only fresh connections are observed."""
        self._connection_log.clear()

    async def start(self) -> None:
        """Begin serving on ``host:port`` over HTTPS."""
        app = self._build_app()
        ssl_ctx = self._build_ssl_context()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            host=self._config.host,
            port=self._config.port,
            ssl_context=ssl_ctx,
        )
        await self._site.start()
        self._logger.info(
            "FakeAS: serving on https://%s:%s (issuer %s)",
            self._config.host, self._config.port, self._config.issuer,
        )

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def __aenter__(self) -> "FakeAuthorizationServer":
        await self.start()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.stop()

    # ----- Internals -----

    def _build_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = self._min_tls_version
        ctx.load_cert_chain(
            str(self._config.cert_chain),
            str(self._config.private_key),
        )
        return ctx

    @web.middleware
    async def _broken_and_log_middleware(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        """Records each request's TLS / HTTP context, and short-
        circuits the handler with 503 when broken-mode is on."""
        ssl_obj = request.transport.get_extra_info("ssl_object") \
            if request.transport is not None else None
        tls_version = ssl_obj.version() if ssl_obj is not None else "none"
        cipher_info = ssl_obj.cipher() if ssl_obj is not None else None
        self._connection_log.append({
            "tls_version": tls_version or "unknown",
            "cipher": cipher_info[0] if cipher_info else "unknown",
            "method": request.method,
            "path": request.path,
        })
        if self._broken:
            return web.json_response(
                {"error": "service_unavailable",
                 "error_description": "fake AS is in broken-mode test"},
                status=503,
            )
        return await handler(request)

    def _build_app(self) -> web.Application:
        app = web.Application(middlewares=[self._broken_and_log_middleware])
        # All three metadata URL forms from TR-10-SEC §14.3.2 +
        # OIDC Discovery 1.0.
        slug = self._config.api_selector.strip("/")
        if slug:
            # RFC 8414 §3.1 host-root + path form (Keycloak default).
            app.router.add_get(
                f"/.well-known/oauth-authorization-server/{slug}",
                self._handle_oauth_metadata,
            )
            # Keycloak's actual placement — api_selector first.
            app.router.add_get(
                f"/{slug}/.well-known/oauth-authorization-server",
                self._handle_oauth_metadata,
            )
            # OIDC Discovery (api_selector first only).
            app.router.add_get(
                f"/{slug}/.well-known/openid-configuration",
                self._handle_oauth_metadata,  # same payload — extra fields are tolerated
            )
            app.router.add_get(f"/{slug}/jwks", self._handle_jwks)
            app.router.add_post(f"/{slug}/token", self._handle_token)
            # Authorization endpoint. GET renders the login form; POST
            # carries the submitted credentials. Both live at the same
            # URL so the form can target its own location and the
            # request parameters survive the round trip unchanged.
            app.router.add_get(f"/{slug}/authorize", self._handle_authorize_get)
            app.router.add_post(f"/{slug}/authorize", self._handle_authorize_post)
        # Host-root form (no api_selector). RFC 8414's plain
        # ``/.well-known/oauth-authorization-server``.
        app.router.add_get(
            "/.well-known/oauth-authorization-server",
            self._handle_oauth_metadata,
        )
        app.router.add_get(
            "/.well-known/openid-configuration",
            self._handle_oauth_metadata,
        )
        # Fallback JWKS at the host root, for clients that ignore the
        # ``jwks_uri`` we advertise.
        app.router.add_get("/jwks", self._handle_jwks)
        # Host-root token / authorize, used when no api_selector is set.
        app.router.add_post("/token", self._handle_token)
        app.router.add_get("/authorize", self._handle_authorize_get)
        app.router.add_post("/authorize", self._handle_authorize_post)
        return app

    # ----- Handlers -----

    async def _handle_oauth_metadata(self, _req: web.Request) -> web.Response:
        slug = self._config.api_selector.strip("/")
        base = f"https://{self._config.host}:{self._config.port}"
        prefix = f"{base}/{slug}" if slug else base
        # RFC 8414 / OIDC Discovery metadata.
        #
        # Every advertised capability below is one this server actually
        # implements. That matters more than it looks: a conformant client
        # (including this project's own Controller since it gained RFC 8414
        # discovery) routes *all* of its traffic by these values, so an
        # aspirational entry here becomes a 404 at the client.
        return web.json_response({
            "issuer": self._config.issuer,
            "jwks_uri": f"{prefix}/jwks",
            "token_endpoint": f"{prefix}/token",
            "authorization_endpoint": f"{prefix}/authorize",
            # ``code`` only. The implicit grant ("token") was advertised
            # here previously but has never been implemented, and the
            # OAuth 2.0 Security BCP deprecates it regardless.
            "response_types_supported": [str(ResponseType.CODE)],
            "grant_types_supported": [
                str(GrantType.CLIENT_CREDENTIALS),
                str(GrantType.AUTHORIZATION_CODE),
                str(GrantType.REFRESH_TOKEN),
            ],
            "token_endpoint_auth_methods_supported": [
                "client_secret_post", "client_secret_basic",
            ],
            # RECOMMENDED by RFC 8414 §2, and genuinely useful to a client
            # deciding what to ask for.
            "scopes_supported": [
                "openid", "node", "connection", "streamcompatibility",
                "channelmapping", "manufacturer", "query", "registration",
            ],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": list({k.alg for k in self._keys}),
        })

    async def _handle_jwks(self, _req: web.Request) -> web.Response:
        return web.json_response(jwks_for(*self._keys))

    # ----- Authorization endpoint (RFC 6749 §4.1) -----

    async def _handle_authorize_get(self, req: web.Request) -> web.Response:
        """Render the login form for an authorization request.

        The end user's browser arrives here after the Controller 302s it
        away from its own login gate. We validate the request *before*
        showing anything, so a malformed request never reaches a form
        that would later fail.
        """
        params = dict(req.query)
        error = self._validate_authorization_request(params)
        if error is not None:
            return error
        return self._render_login_form(params, message=None)

    async def _handle_authorize_post(self, req: web.Request) -> web.Response:
        """Check submitted credentials and issue an authorization code."""
        form = await req.post()
        params = {k: v for k, v in form.items() if isinstance(v, str)}
        error = self._validate_authorization_request(params)
        if error is not None:
            return error

        username = params.get("username", "")
        password = params.get("password", "")
        # compare_digest on both fields so a wrong username and a wrong
        # password take the same time to reject.
        user_ok = secrets.compare_digest(
            username, self._config.operator_username)
        pass_ok = secrets.compare_digest(
            password, self._config.operator_password)
        if not (user_ok and pass_ok):
            self._audit("authorization_denied", subject=username or "(anonymous)",
                        client_id=params.get("client_id", ""),
                        detail="bad username or password")
            # Re-render rather than redirect: the user is still mid-login,
            # and RFC 6749 §4.1.2.1 reserves the error redirect for
            # request-level problems, not failed authentication.
            return self._render_login_form(
                params, message="Incorrect username or password.")

        code = secrets.token_urlsafe(32)
        scope = params.get("scope", "")
        self._codes[code] = AuthorizationCode(
            client_id=params["client_id"],
            redirect_uri=params["redirect_uri"],
            scope=scope,
            subject=self._config.operator_username,
            expires_at=time.time() + AUTHORIZATION_CODE_TTL_SECONDS,
        )
        self._audit("authorization_granted",
                    subject=self._config.operator_username,
                    client_id=params["client_id"], detail=f"scope={scope}")

        redirect_params = {"code": code}
        state = params.get("state")
        if state:
            # RFC 6749 §4.1.2: the state, if present in the request, MUST
            # be returned unmodified. The Controller checks it against its
            # session nonce as CSRF defence.
            redirect_params["state"] = state
        # Raised rather than returned: aiohttp deprecated returning an
        # HTTPException instance (aio-libs/aiohttp#2415).
        raise web.HTTPFound(
            self._append_query(params["redirect_uri"], redirect_params))

    def _validate_authorization_request(
        self, params: dict[str, str],
    ) -> web.Response | None:
        """Validate an authorization request.

        Returns an error page to send back, or ``None`` when the request
        is good. May instead raise :class:`aiohttp.web.HTTPFound` to bounce
        an error to the client's redirect URI — see below for which
        failures take which route.

        RFC 6749 §4.1.2.1 draws a line that matters here: if the
        ``client_id`` is unknown or the ``redirect_uri`` is invalid, the
        server MUST NOT redirect — doing so would turn the AS into an open
        redirector for an attacker-supplied URI. Those two failures render
        an error page instead. Everything else is reported by redirecting
        back to the (now trusted) redirect URI with an ``error`` parameter.
        """
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")

        client = self._config.client(client_id)
        if client is None:
            return self._error_page(
                OAuthError.INVALID_CLIENT,
                f"No client is registered with client_id {client_id!r}. "
                f"Registered: "
                f"{', '.join(c.client_id for c in self._config.clients) or '(none)'}",
            )
        if not redirect_uri:
            return self._error_page(
                OAuthError.INVALID_REQUEST, "redirect_uri is required.")
        if not client.allows_redirect(redirect_uri):
            return self._error_page(
                OAuthError.INVALID_REQUEST,
                f"redirect_uri {redirect_uri!r} is not registered for "
                f"client {client_id!r}. Registered redirect URIs are matched "
                f"exactly (no wildcards): "
                f"{', '.join(client.redirect_uris) or '(none)'}",
            )

        response_type = params.get("response_type", "")
        if response_type != ResponseType.CODE:
            # Client and redirect URI are now trusted, so RFC 6749
            # §4.1.2.1 wants this reported back at the redirect URI.
            raise web.HTTPFound(self._append_query(redirect_uri, {
                "error": str(OAuthError.UNSUPPORTED_RESPONSE_TYPE),
                "error_description":
                    f"response_type {response_type!r} is not supported; "
                    f"this server implements {ResponseType.CODE!s} only",
                **({"state": params["state"]} if params.get("state") else {}),
            }))
        return None

    @staticmethod
    def _append_query(url: str, params: dict[str, str]) -> str:
        """Append query parameters to ``url``, preserving any it already has."""
        import urllib.parse as urlp
        parts = urlp.urlsplit(url)
        merged = urlp.parse_qsl(parts.query, keep_blank_values=True)
        merged.extend(params.items())
        return urlp.urlunsplit(
            (parts.scheme, parts.netloc, parts.path,
             urlp.urlencode(merged), parts.fragment))

    def _render_login_form(
        self, params: dict[str, str], *, message: str | None,
    ) -> web.Response:
        """The sign-in page.

        Deliberately one self-contained HTML document with inline CSS and
        no scripts: it has to render in a locked-down browser profile, and
        a reader learning the flow should be able to see the whole thing
        in one view. The hidden fields round-trip the authorization
        request so the POST carries the same parameters as the GET.
        """
        hidden = "\n".join(
            f'      <input type="hidden" name="{html.escape(k)}" '
            f'value="{html.escape(v)}">'
            for k, v in params.items()
            if k in ("client_id", "redirect_uri", "response_type", "scope", "state")
        )
        banner = (
            f'    <p class="error">{html.escape(message)}</p>\n'
            if message else ""
        )
        status = 401 if message else 200
        body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sign in — IPMX test Authorization Server</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #14181d; color: #e8edf2;
           display: flex; align-items: center; justify-content: center;
           min-height: 100vh; margin: 0; }}
    form {{ background: #1e242b; padding: 2rem 2.25rem; border-radius: 10px;
            border: 1px solid #2c343d; min-width: 22rem; }}
    h1 {{ font-size: 1.1rem; margin: 0 0 0.25rem; }}
    p.sub {{ margin: 0 0 1.25rem; color: #93a1b0; font-size: 0.85rem; }}
    label {{ display: block; margin: 0.75rem 0 0.25rem; font-size: 0.85rem; }}
    input[type=text], input[type=password] {{
      width: 100%; padding: 0.5rem; border-radius: 6px; box-sizing: border-box;
      border: 1px solid #39434e; background: #131820; color: #e8edf2; }}
    button {{ margin-top: 1.25rem; width: 100%; padding: 0.6rem;
              border: 0; border-radius: 6px; background: #2f6fdb;
              color: #fff; font-weight: 600; cursor: pointer; }}
    p.error {{ background: #4a1d22; border: 1px solid #7d2c34; color: #ffc9cf;
               padding: 0.5rem 0.75rem; border-radius: 6px; font-size: 0.85rem; }}
    code {{ color: #9fc6ff; }}
  </style>
</head>
<body>
  <form method="post" id="as-login">
    <h1>IPMX test Authorization Server</h1>
    <p class="sub">Issuer <code>{html.escape(self._config.issuer)}</code></p>
{banner}    <label for="username">Username</label>
    <input type="text" id="username" name="username" autocomplete="username"
           value="{html.escape(self._config.operator_username)}">
    <label for="password">Password</label>
    <input type="password" id="password" name="password"
           autocomplete="current-password">
    <button type="submit" id="as-signin">Sign in</button>
{hidden}
  </form>
</body>
</html>"""
        return web.Response(text=body, content_type="text/html", status=status)

    def _error_page(self, error: OAuthError, description: str) -> web.Response:
        """A non-redirecting error, for faults that must not bounce back."""
        self._logger.warning("FakeAS: authorization rejected — %s: %s",
                             error, description)
        body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Authorization error</title></head>
<body style="font-family: system-ui, sans-serif; padding: 2rem;">
  <h1>{html.escape(str(error))}</h1>
  <p>{html.escape(description)}</p>
</body>
</html>"""
        return web.Response(text=body, content_type="text/html", status=400)

    # ----- Audit trail -----

    def _audit(self, event: str, *, subject: str, client_id: str,
               detail: str = "") -> None:
        """Record one auditable authorization event.

        Never called with a password, code, or token value — see
        :attr:`_audit_log` for the IS-10 requirement this satisfies.
        """
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            "subject": subject,
            "client_id": client_id,
            "detail": detail,
        }
        self._audit_log.append(entry)
        self._logger.info(
            "FakeAS audit: %s subject=%s client_id=%s %s",
            event, subject, client_id, detail,
        )

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        """Read-only view of the authorization audit trail."""
        return list(self._audit_log)

    async def _handle_token(self, req: web.Request) -> web.Response:
        """Token endpoint — dispatches on ``grant_type``.

        Supports the three grants advertised in the metadata document:
        ``client_credentials`` (the original behaviour, used by DUTs that
        do a machine-to-machine handshake at boot), ``authorization_code``
        (the browser flow the reference Controller drives), and
        ``refresh_token``.

        The validator's adversarial token tests bypass this endpoint
        entirely and mint via :func:`mint_token`, so the shapes here are
        the compliant ones only.
        """
        form = await req.post()
        data = {k: v for k, v in form.items() if isinstance(v, str)}
        grant_type = data.get("grant_type", GrantType.CLIENT_CREDENTIALS)

        if grant_type == GrantType.CLIENT_CREDENTIALS:
            return self._handle_client_credentials_grant(data)
        if grant_type not in (
            GrantType.AUTHORIZATION_CODE, GrantType.REFRESH_TOKEN,
        ):
            return self._token_error(
                OAuthError.UNSUPPORTED_GRANT_TYPE,
                f"grant_type {grant_type!r} is not supported",
            )

        # Both user-backed grants authenticate the client identically, so
        # it happens once here — which also lets the grant handlers take a
        # non-optional client rather than re-narrowing it.
        client, auth_error = self._authenticate_client(req, data)
        if client is None:
            return auth_error if auth_error is not None else self._token_error(
                OAuthError.INVALID_CLIENT, "client authentication failed")
        if grant_type == GrantType.AUTHORIZATION_CODE:
            return self._handle_authorization_code_grant(client, data)
        return self._handle_refresh_token_grant(client, data)

    def _handle_client_credentials_grant(
        self, data: dict[str, str],
    ) -> web.Response:
        """Machine-to-machine grant. Unchanged from the original fixture.

        Audience: the spec says ``aud`` entries should include the DUT's
        instance-id; this uses the client_id as a placeholder because the
        token endpoint doesn't know which Node to scope to. Adversarial
        tests should mint directly.
        """
        client_id = data.get("client_id", "")
        if not client_id:
            return self._token_error(
                OAuthError.INVALID_REQUEST, "client_id is required")
        tmpl = TokenTemplate(
            iss=self._config.issuer,
            instance_id=client_id,
            client_id=client_id,
        )
        access_token = mint_token(tmpl, self.primary_key)
        self._audit("token_issued", subject=client_id, client_id=client_id,
                    detail=f"grant={GrantType.CLIENT_CREDENTIALS!s}")
        return web.json_response({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": tmpl.ttl,
        })

    def _handle_authorization_code_grant(
        self, client: RegisteredClient, data: dict[str, str],
    ) -> web.Response:
        """Exchange a single-use authorization code for tokens."""
        code_value = data.get("code", "")
        record = self._codes.get(code_value)
        now = time.time()
        if record is None or not record.is_valid_at(now):
            # RFC 6749 §4.1.2: codes are single-use. A replayed code is
            # also a signal the code may have leaked, so the safe response
            # is the same generic invalid_grant either way.
            return self._token_error(
                OAuthError.INVALID_GRANT,
                "authorization code is unknown, expired, or already redeemed",
            )
        if record.client_id != client.client_id:
            return self._token_error(
                OAuthError.INVALID_GRANT,
                "authorization code was issued to a different client",
            )
        if record.redirect_uri != data.get("redirect_uri", ""):
            # RFC 6749 §4.1.3 requires the redirect_uri to be identical to
            # the one in the authorization request when it was included.
            return self._token_error(
                OAuthError.INVALID_GRANT,
                "redirect_uri does not match the authorization request",
            )

        record.redeemed = True
        return self._issue_tokens(
            client_id=client.client_id, subject=record.subject,
            scope=record.scope, grant=GrantType.AUTHORIZATION_CODE,
        )

    def _handle_refresh_token_grant(
        self, client: RegisteredClient, data: dict[str, str],
    ) -> web.Response:
        """Exchange a refresh token for a fresh access/refresh pair."""
        token_value = data.get("refresh_token", "")
        grant = self._refresh_grants.get(token_value)
        if grant is None or time.time() >= grant.expires_at:
            return self._token_error(
                OAuthError.INVALID_GRANT,
                "refresh token is unknown or expired",
            )
        if grant.client_id != client.client_id:
            return self._token_error(
                OAuthError.INVALID_GRANT,
                "refresh token was issued to a different client",
            )

        # Rotate: the old refresh token is invalidated as the new pair is
        # issued. IS-10 ``Behaviour - Clients.md`` tells clients they "MUST
        # discard any old Refresh Tokens once a new Refresh Token is
        # issued", and rotation is what makes that enforceable.
        del self._refresh_grants[token_value]
        return self._issue_tokens(
            client_id=client.client_id, subject=grant.subject,
            scope=grant.scope, grant=GrantType.REFRESH_TOKEN,
        )

    def _nmos_privileges(self, scope: str) -> dict[str, Any]:
        """Build the ``ext`` claim carrying ``x-nmos-<api>`` privileges.

        Without this, a token authorises **reads only**. "NMOS With
        OAuth2.0" § Validation:

            The `scope` claim [...] MUST provide Read access to the complete
            hierarchy of the current API [...] The `write` attribute of an
            `x-nmos-*` claim, if present, MUST provide Write access if the
            array of paths is ["*"] [...] The absence of a `write` attribute
            prevents Write access.

        So a scope-only token lets a Controller read a Node and then fails
        every configuration call with 403 "insufficient permissions" —
        IS-11 ``constraints/active``, IS-05 ``staged``, everything. An
        operator who signed in to *operate* the system needs the privilege
        claims too.

        ``read`` is emitted alongside ``write`` because it is not optional
        here: the same section states that "the presence of an `x-nmos-*`
        claim MUST remove the default Read access from the `scope` claim for
        the associated API", and that "Both Read and Write access MUST be
        allowed in order to get Write access". Emitting ``write`` alone
        would therefore *revoke* the read the scope had granted.

        Placed under ``ext`` per the same document: the private claims
        "SHOULD be placed in an `ext` claim to separate them from standard
        claims".

        ``openid`` is filtered out — it is an OIDC scope, not an NMOS API,
        and an ``x-nmos-openid`` claim would be meaningless.
        """
        apis = [s for s in scope.split() if s and s not in _NON_NMOS_SCOPES]
        if not apis:
            return {}
        allow_write = self._config.operator_access is OperatorAccess.READ_WRITE
        grant: dict[str, Any] = {"read": ["*"]}
        if allow_write:
            grant["write"] = ["*"]
        return {f"x-nmos-{api}": dict(grant) for api in apis}

    def _issue_tokens(
        self, *, client_id: str, subject: str, scope: str, grant: GrantType,
    ) -> web.Response:
        """Mint the access/refresh pair returned by the user-backed grants.

        The access token is minted with ``grant_type="authorization_code"``
        regardless of whether this is the initial exchange or a refresh:
        that selects the claim shape where ``sub`` identifies the end user
        and ``azp`` carries the client, which is what a refreshed
        user-backed token must continue to look like.
        """
        tmpl = TokenTemplate(
            iss=self._config.issuer,
            instance_id=self._default_aud_entry or client_id,
            client_id=client_id,
            aud_entry=self._default_aud_entry,
        )
        if scope:
            # Honour what the client actually asked for. TokenTemplate's
            # own default is the validator's broader probe set, which
            # would over-grant a token issued to a real client.
            tmpl = replace(tmpl, scope=scope)

        def personalise(claims: dict[str, Any]) -> None:
            """Set the real logged-in ``sub`` and a unique token id.

            ``sub``: :func:`mint_token` defaults it to
            ``user-login-of-<client>`` because the fixture historically had
            no login form. Now that a real user authenticated, the token
            should say so.

            ``jti``: RFC 7519 §4.1.7 unique token identifier. Without it,
            two tokens minted for the same subject within the same second
            carry identical claims and therefore serialise to identical
            JWTs — so a refresh would hand back a byte-for-byte copy of the
            token it replaced. No NMOS spec requires ``jti``, and the
            shared minter deliberately omits it so adversarial tests
            control the claim set exactly; adding it here keeps that
            property while making issued tokens distinguishable.
            """
            claims["sub"] = subject
            claims["jti"] = secrets.token_urlsafe(16)
            privileges = self._nmos_privileges(scope)
            if privileges:
                claims["ext"] = privileges

        access_token = mint_token(
            tmpl, self.primary_key,
            mutate=personalise,
            grant_type=str(GrantType.AUTHORIZATION_CODE),
        )
        refresh_token = secrets.token_urlsafe(32)
        self._refresh_grants[refresh_token] = RefreshGrant(
            client_id=client_id, scope=scope, subject=subject,
            expires_at=time.time() + REFRESH_TOKEN_TTL_SECONDS,
        )
        self._audit("token_issued", subject=subject, client_id=client_id,
                    detail=f"grant={grant!s} scope={scope}")
        return web.json_response({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": tmpl.ttl,
            "refresh_expires_in": REFRESH_TOKEN_TTL_SECONDS,
            "scope": scope,
        })

    def _authenticate_client(
        self, req: web.Request, data: dict[str, str],
    ) -> tuple[RegisteredClient | None, web.Response | None]:
        """Authenticate the client on a token request.

        Accepts both methods advertised in the metadata:
        ``client_secret_basic`` (HTTP Basic, which IS-10 requires an AS to
        support) and ``client_secret_post`` (credentials in the form body,
        which is what the reference Controller sends).
        """
        client_id = data.get("client_id", "")
        client_secret: str | None = data.get("client_secret")

        header = req.headers.get("Authorization", "")
        if header.startswith("Basic "):
            import base64 as _b64
            import binascii
            try:
                decoded = _b64.b64decode(header[6:]).decode("utf-8")
                basic_id, _, basic_secret = decoded.partition(":")
            except (ValueError, binascii.Error, UnicodeDecodeError):
                return None, self._token_error(
                    OAuthError.INVALID_CLIENT,
                    "malformed Basic authentication header")
            # RFC 6749 §2.3.1 prefers the Authorization header when both
            # are present.
            client_id = basic_id or client_id
            client_secret = basic_secret

        client = self._config.client(client_id)
        if client is None:
            return None, self._token_error(
                OAuthError.INVALID_CLIENT,
                f"no client is registered with client_id {client_id!r}")
        if not client.authenticates_with(client_secret):
            self._audit("client_authentication_failed", subject="(client)",
                        client_id=client_id, detail="bad client_secret")
            return None, self._token_error(
                OAuthError.INVALID_CLIENT, "client authentication failed")
        return client, None

    @staticmethod
    def _token_error(error: OAuthError, description: str) -> web.Response:
        """RFC 6749 §5.2 error response.

        ``invalid_client`` gets 401; every other code gets 400.
        """
        status = 401 if error is OAuthError.INVALID_CLIENT else 400
        return web.json_response(
            {"error": str(error), "error_description": description},
            status=status,
        )


# ---------------------------------------------------------------------------
# CLI entry — ``python3 ipmx_fake_as.py --host XYZ-SNX00000 --port 9444 ...``
# ---------------------------------------------------------------------------

def _cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Stage 1 fake OAuth 2.0 / OIDC AS — standalone subprocess. "
            "The matrix runner uses this to give the fake AS its own "
            "process so per-curve TLS pinning via OPENSSL_CONF in the "
            "env restricts the AS's TLS handshake to one ECDH group. "
            "In-process use (most validator runs) goes via the "
            "FakeAuthorizationServer class directly."
        ),
    )
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--cert", type=Path, required=True,
                   help="Server cert chain PEM.")
    p.add_argument("--key", type=Path, required=True,
                   help="Server private key PEM.")
    p.add_argument("--api-selector", default="TR-10-SEC")
    p.add_argument(
        "--default-aud", default=None,
        help="DUT hostname baked into minted tokens' aud[0] so the "
             "§14.3.3.4 cert-binding aud rule is satisfied.",
    )
    p.add_argument(
        "--client-id", default=None,
        help="Register an OAuth 2.0 client with this client_id. Required "
             "before the authorization_code flow can be used: IS-10 "
             "forbids granting tokens to unregistered clients.",
    )
    p.add_argument(
        "--client-secret", default=None,
        help="Secret for --client-id. Omit to register it as a public "
             "client (no secret accepted).",
    )
    p.add_argument(
        "--redirect-uri", action="append", default=None, metavar="URI",
        help="Permitted redirect URI for --client-id. Repeatable. Matched "
             "exactly — no wildcards, per IS-10 Behaviour - Clients.md.",
    )
    p.add_argument(
        "--operator-username", default="tr-10-sec-operator",
        help="The single pre-canned account the sign-in form accepts.",
    )
    p.add_argument(
        "--operator-password", default="admin",
        help="Password for --operator-username. Defaults to the reference "
             "Controller's own admin password.",
    )
    p.add_argument(
        "--operator-access", default=str(OperatorAccess.READ_WRITE),
        choices=[str(a) for a in OperatorAccess],
        help="Whether the operator's token authorises configuration "
             "(readwrite, the default) or only reading. A read-only token "
             "lets a Controller display everything and refuses every "
             "state-changing call with 403.",
    )
    return p.parse_args()


async def _amain() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cli = _cli()
    clients: tuple[RegisteredClient, ...] = ()
    if cli.client_id:
        clients = (RegisteredClient(
            client_id=cli.client_id,
            client_secret=cli.client_secret,
            redirect_uris=tuple(cli.redirect_uri or ()),
        ),)
    elif cli.redirect_uri:
        logging.getLogger(__name__).warning(
            "--redirect-uri given without --client-id; no client registered, "
            "so the authorization endpoint will reject every request",
        )
    cfg = FakeASConfig(
        host=cli.host, port=cli.port,
        cert_chain=cli.cert, private_key=cli.key,
        api_selector=cli.api_selector,
        clients=clients,
        operator_username=cli.operator_username,
        operator_password=cli.operator_password,
        operator_access=OperatorAccess(cli.operator_access),
    )
    # Provide one key per TR-10-SEC §14.3.3.2 permitted algorithm so
    # the AS can serve any DUT that prefers RS256/RS512/ES256/ES512.
    keys = [
        SigningKey.generate(alg="RS256", kid="fake-as-rs256"),
        SigningKey.generate(alg="RS512", kid="fake-as-rs512"),
        SigningKey.generate(alg="ES256", kid="fake-as-es256"),
        SigningKey.generate(alg="ES512", kid="fake-as-es512"),
    ]
    fake_as = FakeAuthorizationServer(cfg, signing_keys=keys)
    if cli.default_aud:
        fake_as.default_aud_entry = cli.default_aud
    await fake_as.start()
    logging.getLogger(__name__).info(
        "Fake AS up: https://%s:%d/%s", cli.host, cli.port, cli.api_selector,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    import signal as _sig
    for s in (_sig.SIGTERM, _sig.SIGINT):
        try:
            loop.add_signal_handler(s, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        try:
            await stop.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
    finally:
        await fake_as.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
