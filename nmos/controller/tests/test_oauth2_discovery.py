# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Authorization Server endpoint discovery in :mod:`nmos.controller.oauth2`.

These tests exist because the controller used to *guess* where an
Authorization Server's endpoints lived, by appending Keycloak's
``/protocol/openid-connect/<x>`` suffixes to the issuer URL. IS-10
``Behaviour - Clients.md`` § Discovery forbids exactly that:

    When first contacting an Authorization Server, a Client MUST identify
    the location of API endpoints using the Authorization Server Metadata
    resource as specified in RFC 8414. Clients MUST NOT assume that every
    Authorization Server instance on a network uses the same endpoint
    locations.

The guessing worked against Keycloak and silently failed against every
other conformant server. So the cases below pin two things: that the
controller reads endpoint locations out of the metadata document, and
that it refuses to use a document whose ``issuer`` does not match
(RFC 8414 §3.3), which is what stops a substituted metadata response
from redirecting token traffic somewhere else.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nmos.controller.oauth2 import OAuth2Client, OAuth2Config, OAuth2Error


# ---------------------------------------------------------------------------
# Metadata-server fixture
# ---------------------------------------------------------------------------

class _MetadataServer:
    """A stand-in AS that serves only ``/.well-known/…``.

    Deliberately *not* the fake AS: these tests are
    about what the controller does with a metadata document, so the
    document has to be arbitrary — including malformed and hostile
    shapes a real server would never emit.
    """

    def __init__(self, document: dict[str, Any] | None, *,
                 well_known_path: str) -> None:
        self.document = document
        self.hits: list[str] = []
        self.app = web.Application()
        self.app.router.add_get(well_known_path, self._handle)

    async def _handle(self, request: web.Request) -> web.Response:
        self.hits.append(request.path)
        if self.document is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(self.document)


async def _start(document: dict[str, Any] | None, *,
                 api_selector: str,
                 well_known_style: str = "rfc8414") -> tuple[
                     TestServer, _MetadataServer]:
    """Boot a metadata server and return it plus its harness.

    ``well_known_style`` picks which of the URL forms
    :func:`nmos.oauth2.discover_metadata` probes the document is served
    at, so a test can prove the controller finds it wherever a
    conformant-but-different server chooses to put it.
    """
    sel = api_selector.strip("/")
    paths = {
        # RFC 8414 §3.1 normative form, and the one IS-10 mandates.
        "rfc8414": f"/.well-known/oauth-authorization-server"
                   + (f"/{sel}" if sel else ""),
        # Keycloak's actual placement.
        "keycloak": (f"/{sel}/.well-known/oauth-authorization-server" if sel
                     else "/.well-known/oauth-authorization-server"),
        # OpenID Connect Discovery 1.0 fallback.
        "oidc": (f"/{sel}/.well-known/openid-configuration" if sel
                 else "/.well-known/openid-configuration"),
    }
    meta = _MetadataServer(document, well_known_path=paths[well_known_style])
    server = TestServer(meta.app)
    await server.start_server()
    return server, meta


def _client(server: TestServer, *, api_selector: str,
            issuer_override: str | None = None) -> OAuth2Client:
    base = f"http://127.0.0.1:{server.port}"
    sel = api_selector.strip("/")
    issuer = issuer_override if issuer_override is not None else (
        f"{base}/{sel}" if sel else base)
    return OAuth2Client(OAuth2Config(
        issuer=issuer, client_id="controller-SNX00001",
        client_secret="secret", api_selector=api_selector,
    ))


def _document(base: str, sel: str, *, style: str = "hydra") -> dict[str, Any]:
    """A metadata document whose endpoints do NOT follow Keycloak's layout.

    That is the whole point: if the controller still worked when these
    paths were unguessable, it would prove it reads them.
    """
    issuer = f"{base}/{sel}" if sel else base
    prefix = issuer
    layouts = {
        "hydra": ("oauth2/auth", "oauth2/token", ".well-known/jwks.json"),
        "flat": ("authorize", "token", "jwks"),
    }
    auth, token, jwks = layouts[style]
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{prefix}/{auth}",
        "token_endpoint": f"{prefix}/{token}",
        "jwks_uri": f"{prefix}/{jwks}",
        "response_types_supported": ["code"],
    }


# ---------------------------------------------------------------------------
# Discovery finds endpoints wherever the server puts them
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["hydra", "flat"])
@pytest.mark.parametrize("api_selector", ["realms/TR-10-SEC", ""])
async def test_endpoints_come_from_the_document(
    style: str, api_selector: str,
) -> None:
    """All three endpoints are read from metadata, not derived."""
    server, _meta = await _start(None, api_selector=api_selector)
    base = f"http://127.0.0.1:{server.port}"
    sel = api_selector.strip("/")
    await server.close()

    server, meta = await _start(
        _document(base, sel, style=style), api_selector=api_selector)
    # Rebind the document to the port actually allocated this time.
    meta.document = _document(
        f"http://127.0.0.1:{server.port}", sel, style=style)
    try:
        client = _client(server, api_selector=api_selector)
        assert await client.authorization_endpoint() == \
            meta.document["authorization_endpoint"]
        assert await client.token_endpoint() == meta.document["token_endpoint"]
        assert await client.jwks_uri() == meta.document["jwks_uri"]
    finally:
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("well_known_style", ["rfc8414", "keycloak", "oidc"])
async def test_all_probed_well_known_forms_are_found(
    well_known_style: str,
) -> None:
    """The document is found at any of the three well-known placements.

    RFC 8414 §3.1 defines one form and IS-10 mandates it, but Keycloak
    serves a different one and OIDC Discovery 1.0 a third. A controller
    that only probed the normative form would fail against Keycloak.
    """
    sel = "realms/TR-10-SEC"
    server, meta = await _start(
        None, api_selector=sel, well_known_style=well_known_style)
    meta.document = _document(f"http://127.0.0.1:{server.port}", sel)
    try:
        client = _client(server, api_selector=sel)
        assert await client.token_endpoint() == meta.document["token_endpoint"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_no_keycloak_path_is_ever_assumed() -> None:
    """A server that publishes no Keycloak-shaped path still works.

    The regression guard for the original bug: previously the controller
    would have driven traffic at ``…/protocol/openid-connect/token``
    regardless of what the document said.
    """
    sel = "realms/TR-10-SEC"
    server, meta = await _start(None, api_selector=sel)
    meta.document = _document(f"http://127.0.0.1:{server.port}", sel)
    try:
        client = _client(server, api_selector=sel)
        resolved = " ".join([
            await client.authorization_endpoint(),
            await client.token_endpoint(),
            await client.jwks_uri(),
        ])
        assert "protocol/openid-connect" not in resolved
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_auth_url_targets_the_discovered_endpoint() -> None:
    """``build_auth_url`` builds on the discovered authorization endpoint."""
    sel = "realms/TR-10-SEC"
    server, meta = await _start(None, api_selector=sel)
    meta.document = _document(f"http://127.0.0.1:{server.port}", sel)
    try:
        client = _client(server, api_selector=sel)
        url = await client.build_auth_url(
            redirect_uri="https://node:5050/controller/oauth2/callback",
            state="nonce-123")
        assert url.startswith(meta.document["authorization_endpoint"] + "?")
        assert "response_type=code" in url
        assert "state=nonce-123" in url
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_document_is_fetched_once_and_cached() -> None:
    """Three endpoint reads cause exactly one metadata fetch."""
    sel = "realms/TR-10-SEC"
    server, meta = await _start(None, api_selector=sel)
    meta.document = _document(f"http://127.0.0.1:{server.port}", sel)
    try:
        client = _client(server, api_selector=sel)
        await client.authorization_endpoint()
        await client.token_endpoint()
        await client.jwks_uri()
        assert len(meta.hits) == 1, meta.hits
    finally:
        await server.close()


# ---------------------------------------------------------------------------
# RFC 8414 §3.3 metadata validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issuer_mismatch_is_rejected() -> None:
    """RFC 8414 §3.3: a mismatched ``issuer`` invalidates the document.

        The "issuer" value returned MUST be identical to the authorization
        server's issuer identifier value into which the well-known URI
        string was inserted to create the URL used to retrieve the
        metadata. If these values are not identical, the data contained in
        the response MUST NOT be used.

    Without this the controller would adopt whatever ``token_endpoint``
    an substituted document carried, and post client credentials there.
    """
    sel = "realms/TR-10-SEC"
    server, meta = await _start(None, api_selector=sel)
    doc = _document(f"http://127.0.0.1:{server.port}", sel)
    doc["issuer"] = "https://attacker.example/realms/TR-10-SEC"
    meta.document = doc
    try:
        client = _client(server, api_selector=sel)
        with pytest.raises(OAuth2Error, match="issuer mismatch"):
            await client.token_endpoint()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_trailing_slash_on_issuer_still_matches() -> None:
    """A lone trailing ``/`` is not treated as a mismatch.

    RFC 8414 §3.1 has the client strip a terminating slash from the
    issuer identifier when building the well-known URL, so the two
    spellings denote the same identifier. Rejecting on that alone would
    lock out compliant servers without closing any attack.
    """
    sel = "realms/TR-10-SEC"
    server, meta = await _start(None, api_selector=sel)
    doc = _document(f"http://127.0.0.1:{server.port}", sel)
    doc["issuer"] = doc["issuer"] + "/"
    meta.document = doc
    try:
        client = _client(server, api_selector=sel)
        assert await client.token_endpoint() == doc["token_endpoint"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_missing_issuer_is_rejected() -> None:
    """``issuer`` is REQUIRED by RFC 8414 §2; absence is fatal."""
    sel = "realms/TR-10-SEC"
    server, meta = await _start(None, api_selector=sel)
    doc = _document(f"http://127.0.0.1:{server.port}", sel)
    del doc["issuer"]
    meta.document = doc
    try:
        client = _client(server, api_selector=sel)
        with pytest.raises(OAuth2Error, match="no 'issuer' field"):
            await client.token_endpoint()
    finally:
        await server.close()


# ---------------------------------------------------------------------------
# Missing endpoints fail loudly rather than falling back to a guess
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("field_name,accessor", [
    ("authorization_endpoint", "authorization_endpoint"),
    ("token_endpoint", "token_endpoint"),
    ("jwks_uri", "jwks_uri"),
])
async def test_missing_endpoint_raises_instead_of_guessing(
    field_name: str, accessor: str,
) -> None:
    """A document lacking an endpoint is an error, never a silent fallback.

    Falling back to a vendor URL convention here is what the old code did.
    It has to fail instead: a wrong endpoint is indistinguishable from a
    working one until credentials have already been sent to it.
    """
    sel = "realms/TR-10-SEC"
    server, meta = await _start(None, api_selector=sel)
    doc = _document(f"http://127.0.0.1:{server.port}", sel)
    del doc[field_name]
    meta.document = doc
    try:
        client = _client(server, api_selector=sel)
        with pytest.raises(OAuth2Error, match=field_name):
            await getattr(client, accessor)()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_unreachable_metadata_raises_oauth2_error() -> None:
    """A server with no metadata document at all fails cleanly."""
    sel = "realms/TR-10-SEC"
    server, _meta = await _start(None, api_selector=sel)
    try:
        client = _client(server, api_selector=sel)
        with pytest.raises(OAuth2Error, match="discovery failed"):
            await client.token_endpoint()
    finally:
        await server.close()


# ---------------------------------------------------------------------------
# Issuer decomposition
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("issuer,expected", [
    ("https://XYZ-SNX00000:9443/realms/TR-10-SEC",
     ("https", "xyz-snx00000", 9443)),
    ("https://as.example.com/realms/x", ("https", "as.example.com", 443)),
    ("http://127.0.0.1:8080", ("http", "127.0.0.1", 8080)),
    ("http://as.example.com", ("http", "as.example.com", 80)),
])
def test_issuer_components(issuer: str, expected: tuple[str, str, int]) -> None:
    """The issuer splits into the components discovery needs.

    The port has to be defaulted from the scheme: an issuer that omits it
    is perfectly legal, and ``urlsplit`` reports ``None`` rather than 443.
    """
    config = OAuth2Config(issuer=issuer, client_id="c", client_secret="s",
                          api_selector="")
    assert config.issuer_components == expected


def test_config_exposes_no_endpoint_urls() -> None:
    """The config must not regrow guessed endpoint properties.

    A direct guard on the original bug: if someone reintroduces
    ``token_endpoint`` on the config, discovery can be bypassed without
    any test noticing.
    """
    config = OAuth2Config(issuer="https://as:9443/realms/x", client_id="c",
                          client_secret="s", api_selector="realms/x")
    for attr in ("auth_endpoint", "token_endpoint", "jwks_endpoint",
                 "end_session_endpoint"):
        assert not hasattr(config, attr), (
            f"OAuth2Config.{attr} is back; endpoint locations must come "
            f"from the Authorization Server metadata document only"
        )
