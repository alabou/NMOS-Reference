# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Controller-side OAuth2 authorization_code client.

Drives the second stage of the controller's two-stage admin login:
after the operator has cleared the local password gate, the controller
redirects the browser to the Authorization Server's authorization
endpoint with this client's credentials, then exchanges the returned
``code`` at the token endpoint for an ``access_token`` +
``refresh_token``.

Endpoint locations are **discovered**, never assumed. IS-10
``Behaviour - Clients.md`` requires a client to "identify the location
of API endpoints using the Authorization Server Metadata resource as
specified in RFC 8414" and states that clients "MUST NOT assume that
every Authorization Server instance on a network uses the same endpoint
locations". Earlier revisions of this module appended Keycloak's
``/protocol/openid-connect/<x>`` suffixes to the issuer, which worked
against Keycloak and silently failed against every other conformant AS
(ORY Hydra, Authlib, the project's own ``security/ipmx_fake_as.py``).

Uses :mod:`aiohttp` for the outbound HTTP and reuses :mod:`nmos.oauth2`
for metadata discovery plus JWT signature + claim validation (no
parallel implementation).

Layout:

* :class:`OAuth2Config` — holds the ``oauth2ClientId`` /
  ``oauth2ClientSecret`` / ``oauth2Realm`` flags plus the issuer URL
  and CA bundle. Built in ``nmos_node.py`` from CLI args.
* :class:`OAuth2Tokens` — what the ``/token`` endpoint returns.
* :class:`OAuth2Client` — a stateless façade over the auth_code +
  refresh_token grants. One instance per controller process; instance
  state is just the JWKS cache.

The per-admin tokens themselves live on
:class:`AdminSessionState` (see :mod:`nmos.controller.auth`); this
module is a pure protocol layer.
"""

from __future__ import annotations

import secrets
import ssl
import time
import urllib.parse as urlp
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from nmos.api.tr10_tls import apply_tr10_tls_restrictions
from nmos.oauth2 import (
    JWKS, discover_metadata, fetch_jwks, validate_token_with_claims,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The set of NMOS API scopes the controller requests on every login.
# ``openid`` is required by Keycloak for the auth_code flow; the rest
# match the APIs the controller will actually call against remote
# Nodes (IS-04 query / IS-05 / IS-11 / IS-08).
DEFAULT_SCOPES = (
    "openid", "node", "connection",
    "streamcompatibility", "channelmapping",
    # ``manufacturer`` covers ``/x-manufacturer/*`` endpoints,
    # including the Node Reservation service
    # (``/x-manufacturer/exclusive/v1.0/acquire`` / renew / release /
    # keepalive). Without this scope the AS issues a token that the
    # Node's bearer middleware accepts (signature OK) but the
    # ``check_oauth2`` decorator rejects with 403 "insufficient
    # permissions" on every reservation call. Per "NMOS With
    # OAuth2.0" §"Paths".
    "manufacturer",
)


# ---------------------------------------------------------------------------
# Config + token dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OAuth2Config:
    """Controller-side OAuth2 client configuration.

    Built in ``nmos_node.py:go_controller_server`` from the CLI flags
    (``--oauth2Host``, ``--oauth2Port``, ``--oauth2ApiSelector``,
    ``--oauth2ClientId``, ``--oauth2ClientSecret``,
    ``--oauth2TrustedRootCA``). Frozen so a single instance can be
    safely shared across coroutines.

    Deliberately carries **no endpoint URLs**. Earlier revisions derived
    them by appending Keycloak's ``/protocol/openid-connect/<x>`` suffixes
    to :attr:`issuer`, which IS-10 forbids — see
    :meth:`OAuth2Client.authorization_endpoint`. Endpoint locations now
    come exclusively from the Authorization Server metadata document.
    """
    issuer: str
    """Full issuer URL, e.g. ``https://XYZ-SNX00000:9443/realms/TR-10-SEC``
    (Keycloak) or ``https://hydra:4444`` (Hydra)."""

    client_id: str
    """OAuth2 ``client_id`` registered with the AS (e.g. ``controller-SNX00001``)."""

    client_secret: str
    """OAuth2 ``client_secret`` paired with ``client_id``."""

    api_selector: str
    """IS-10 / RFC 8414 §3.1 ``api_selector`` — the path component of
    the issuer identifier with leading and trailing ``/`` removed.
    Empty for ORY Hydra; ``realms/<realm>`` for Keycloak. Kept here
    alongside ``issuer`` for logs and for builders that prefer the
    components separately."""

    ca_bundle: tuple[str, ...] = ()
    """Paths to the CA bundle(s) that verify the AS's TLS cert; empty tuple to use system CAs.

    May contain one or more PEM file paths — each is appended to the
    verify store so any one of them can anchor the AS chain. Matches
    the multi-root semantics of the ``--*TrustedRootCA`` CLI flags."""

    scopes: tuple[str, ...] = DEFAULT_SCOPES
    """Scopes requested at the authorization endpoint."""

    @property
    def issuer_components(self) -> tuple[str, str, int]:
        """``issuer`` split into ``(scheme, host, port)``.

        :func:`nmos.oauth2.discover_metadata` takes the AS location as
        separate components rather than a URL, because it has to splice
        ``/.well-known/…`` between the host and the path (RFC 8414 §3.1).
        The port is defaulted from the scheme when the issuer omits it,
        so ``https://as.example.com/realms/x`` resolves to port 443.
        """
        parts = urlp.urlsplit(self.issuer)
        scheme = parts.scheme or "https"
        default_port = 443 if scheme == "https" else 80
        return scheme, parts.hostname or "", parts.port or default_port


@dataclass
class OAuth2Tokens:
    """The triplet returned by the ``/token`` endpoint plus a derived expiry."""
    access_token: str
    refresh_token: str
    expires_at: float
    """Monotonic-clock deadline for the access token."""

    refresh_expires_at: float
    """Monotonic-clock deadline for the refresh token."""

    claims: dict[str, Any] = field(default_factory=dict)
    """Decoded + signature-verified access-token claims."""

    def needs_refresh(self, now: float, fraction: float = 0.25) -> bool:
        """True when the access token has burned through ``1-fraction`` of
        its lifetime. With ``fraction=0.25`` (default), refresh fires at
        the 75% mark — a proactive-refresh model.
        """
        if self.expires_at <= now:
            return True
        # If lifetime were known we'd compare to it, but the only data
        # we have is the absolute deadline. Conservative shape: refresh
        # whenever the remaining window is below 25% of a typical
        # 1-hour Keycloak access-token lifetime, i.e. < 15 minutes.
        return (self.expires_at - now) < (3600 * fraction)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class OAuth2Client:
    """Façade over the auth_code + refresh_token grants of any RFC 8414 AS.

    Single instance per controller process; threaded via
    ``app["controller_oauth2_client"]``. Instance state is two lazily
    populated caches: the Authorization Server metadata document (fetched
    once, on first use) and a JWKS (fetched on first token validation and
    re-fetched on a ``kid`` miss).

    Both caches race benignly — two coroutines arriving together may each
    issue a fetch and the last write wins. They fetch the same immutable
    document, so no lock is warranted.
    """

    def __init__(self, config: OAuth2Config) -> None:
        self._config = config
        self._jwks: JWKS | None = None
        self._metadata: dict[str, Any] | None = None
        self._ssl_ctx = self._build_ssl_context()

    # --- Public API ---

    @property
    def config(self) -> OAuth2Config:
        return self._config

    async def build_auth_url(self, *, redirect_uri: str, state: str) -> str:
        """Build the authorization-endpoint URL the browser must be 302'd to.

        ``state`` is an opaque per-session nonce the caller persists in
        the admin session; we echo it back from the callback to defend
        against CSRF on the redirect.

        Coroutine rather than a plain function because the endpoint has
        to be discovered — see :meth:`authorization_endpoint`.
        """
        qs = urlp.urlencode({
            "client_id": self._config.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(self._config.scopes),
            "state": state,
        })
        return f"{await self.authorization_endpoint()}?{qs}"

    @staticmethod
    def new_state_nonce() -> str:
        """Cryptographically-random state nonce for the auth_code redirect.

        128 bits of entropy from :func:`secrets.token_urlsafe` — same
        order as a UUIDv4 but URL-safe out of the box.
        """
        return secrets.token_urlsafe(16)

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> OAuth2Tokens:
        """Trade an authorization ``code`` for access + refresh tokens.

        Validates the access-token signature against the cached JWKS;
        on ``kid`` miss the JWKS is re-fetched once before failing.
        """
        return await self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
        })

    async def refresh(self, *, refresh_token: str) -> OAuth2Tokens:
        """Trade a ``refresh_token`` for a fresh access + refresh pair."""
        return await self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
        })

    # --- Authorization Server metadata (RFC 8414 / IS-10) ---

    async def authorization_endpoint(self) -> str:
        """Discovered ``authorization_endpoint``.

        IS-10 ``Behaviour - Clients.md`` § Discovery is explicit that a
        client may not guess this:

            When first contacting an Authorization Server, a Client MUST
            identify the location of API endpoints using the Authorization
            Server Metadata resource as specified in RFC 8414. Clients MUST
            NOT assume that every Authorization Server instance on a network
            uses the same endpoint locations.

        So there is deliberately no fallback to a vendor URL convention
        here: an AS that publishes no metadata is unusable, and failing
        loudly is the only honest outcome. RFC 8414 §2 marks this field
        REQUIRED "unless no grant types are supported that use the
        authorization endpoint" — the controller uses ``authorization_code``,
        so its absence is a broken AS.
        """
        return await self._metadata_url("authorization_endpoint")

    async def token_endpoint(self) -> str:
        """Discovered ``token_endpoint`` (RFC 8414 §2: REQUIRED here —
        the exemption covers implicit-only servers, which we are not)."""
        return await self._metadata_url("token_endpoint")

    async def jwks_uri(self) -> str:
        """Discovered ``jwks_uri``.

        RFC 8414 §2 lists this OPTIONAL, but IS-10 ``Behaviour -
        Authorization Servers.md`` overrides that for NMOS deployments:
        "Authorization Servers MUST provide all public keys used for
        signing tokens at a non-protected endpoint, the location of which
        will correspond with the value of the 'jwks_uri' property at the
        server metadata endpoint." Without it we cannot validate a single
        token, so a missing value is an error rather than a degraded mode.
        """
        return await self._metadata_url("jwks_uri")

    async def _metadata_url(self, field_name: str) -> str:
        """Read one URL-valued field out of the discovered metadata."""
        metadata = await self._discover()
        value = metadata.get(field_name)
        if not isinstance(value, str) or not value:
            raise OAuth2Error(
                f"Authorization Server metadata for {self._config.issuer} "
                f"has no usable {field_name!r}; cannot continue without it",
            )
        return value

    async def _discover(self) -> dict[str, Any]:
        """Fetch and cache the AS metadata document.

        Cached for the process lifetime, mirroring the JWKS cache below:
        endpoint locations are part of an AS's deployment identity, not
        rotating material like signing keys.
        """
        if self._metadata is not None:
            return self._metadata

        scheme, host, port = self._config.issuer_components
        connector = aiohttp.TCPConnector(ssl=self._ssl_ctx or False)
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                metadata = await discover_metadata(
                    scheme=scheme, host=host, port=port,
                    api_selector=self._config.api_selector,
                    client=session,
                )
            except Exception as exc:
                raise OAuth2Error(
                    f"Authorization Server metadata discovery failed for "
                    f"{self._config.issuer}: {exc}",
                ) from exc

        self._validate_issuer(metadata)
        self._metadata = metadata
        return metadata

    def _validate_issuer(self, metadata: dict[str, Any]) -> None:
        """Enforce RFC 8414 §3.3 Authorization Server Metadata Validation.

            The "issuer" value returned MUST be identical to the
            authorization server's issuer identifier value into which the
            well-known URI string was inserted to create the URL used to
            retrieve the metadata. If these values are not identical, the
            data contained in the response MUST NOT be used.

        This is what stops a metadata document served from one origin from
        redirecting our token and JWKS traffic to an attacker's endpoints:
        without the check, discovery would happily adopt whatever
        ``token_endpoint`` the response carried.

        A single trailing ``/`` is normalised away on both sides before
        comparing. RFC 8414 §3.1 has the client strip a terminating slash
        from the issuer identifier when building the well-known URL, so
        the two spellings denote the same identifier; rejecting on that
        alone would fail compliant servers without closing any attack.
        """
        published = metadata.get("issuer")
        if not isinstance(published, str):
            raise OAuth2Error(
                f"Authorization Server metadata for {self._config.issuer} "
                f"has no 'issuer' field (RFC 8414 §2 marks it REQUIRED)",
            )
        if published.rstrip("/") != self._config.issuer.rstrip("/"):
            raise OAuth2Error(
                f"Authorization Server metadata issuer mismatch "
                f"(RFC 8414 §3.3): configured {self._config.issuer!r}, "
                f"document declares {published!r}; discarding the document",
            )

    # --- Internals ---

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        """SSL context for the outbound HTTPS to Keycloak. ``None`` when
        the issuer uses plain HTTP (test mode). When ``ca_bundle`` is
        non-empty we trust ONLY those CAs; otherwise system CAs."""
        if not self._config.issuer.startswith("https://"):
            return None
        ctx = ssl.create_default_context()
        apply_tr10_tls_restrictions(ctx)
        if self._config.ca_bundle:
            for ca in self._config.ca_bundle:
                ctx.load_verify_locations(ca)
        return ctx

    async def _token_request(self, payload: dict[str, str]) -> OAuth2Tokens:
        connector = aiohttp.TCPConnector(ssl=self._ssl_ctx or False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                await self.token_endpoint(), data=payload,
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise OAuth2Error(
                        f"{payload['grant_type']} grant failed: "
                        f"HTTP {resp.status} {body[:200]}"
                    )
                data = await resp.json(content_type=None)
        return await self._build_tokens(data)

    async def _build_tokens(self, data: dict[str, Any]) -> OAuth2Tokens:
        now = time.monotonic()
        access = data["access_token"]
        refresh = data["refresh_token"]
        expires_in = int(data.get("expires_in", 3600))
        refresh_expires_in = int(data.get("refresh_expires_in", expires_in * 2))
        claims = await self._verify(access)
        return OAuth2Tokens(
            access_token=access,
            refresh_token=refresh,
            expires_at=now + expires_in,
            refresh_expires_at=now + refresh_expires_in,
            claims=claims,
        )

    async def _verify(self, token: str) -> dict[str, Any]:
        """Validate signature against the JWKS and return claims.

        Lazily fetches the JWKS the first time, and re-fetches once
        on a ``kid`` miss before failing.
        """
        for attempt in (1, 2):
            if self._jwks is None:
                self._jwks = await self._fetch_jwks()
            ok, claims = validate_token_with_claims(token, self._jwks)
            if ok:
                return claims
            # First attempt: maybe Keycloak rotated keys; force refetch.
            if attempt == 1:
                self._jwks = None
                continue
            raise OAuth2Error("access token signature did not verify against JWKS")
        # Unreachable but keeps mypy happy.
        raise OAuth2Error("access token signature did not verify against JWKS")

    async def _fetch_jwks(self) -> JWKS:
        connector = aiohttp.TCPConnector(ssl=self._ssl_ctx or False)
        async with aiohttp.ClientSession(connector=connector) as session:
            return await fetch_jwks(await self.jwks_uri(), client=session)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class OAuth2Error(RuntimeError):
    """Raised when the OAuth2 protocol layer rejects a response."""
