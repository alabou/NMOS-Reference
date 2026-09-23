# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Offering the next client identity when a registry refuses the first.

TR-10-SEC TCT=2 lets a device hold an RSA *and* an ECDSA identity, but a client
``SSLContext`` holding both does not answer the peer's ``CertificateRequest``
with the one it asked for -- it picks by its own rule and then fails. So each
identity gets its own context and they are offered in turn.

The rule these pin is that **the unit of failure is the registry, not the
certificate**: one refusal is no evidence about the registry and must not count
toward failover, while every identity being refused is, because the cause may
equally be this registry's trust store lacking our issuer -- the "could affect
just one Registration API" case the specification gives as the reason to move.
"""

from __future__ import annotations

import ssl
from typing import Any

import pytest

from nmos.controller.cache import ResourceCache
from nmos.controller.rds_query import RdsQueryClient, RdsQueryConfig
from nmos.tls_identity import PeerRejectedAllIdentities

REJECTION = ssl.SSLError(
    "[SSL: TLSV13_ALERT_CERTIFICATE_REQUIRED] tlsv13 alert certificate required",
)


def _client() -> RdsQueryClient:
    return RdsQueryClient(RdsQueryConfig(
        host="10.0.0.1", port=8446, tls=True,
        client_certificate=("ec.pem", "rsa.pem"),
        client_key=("ec.key", "rsa.key"),
    ))


class TestBootstrapOffersEachIdentity:
    @pytest.mark.asyncio
    async def test_it_stops_at_the_first_identity_the_registry_accepts(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        offered: list[Any] = []

        async def fake_once(self, cache, kinds, timeout, identity):  # type: ignore[no-untyped-def]
            offered.append(identity)
            return REJECTION if len(offered) == 1 else None

        monkeypatch.setattr(RdsQueryClient, "_bootstrap_once", fake_once)
        await _client().bootstrap(ResourceCache())

        assert len(offered) == 2, "should have offered the second identity"
        assert offered[1] != offered[0]

    @pytest.mark.asyncio
    async def test_every_identity_refused_is_reported_naming_both(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Rather than six identical warnings and an empty cache, which was
        the old behaviour and said nothing about why."""
        async def always_rejected(self, cache, kinds, timeout, identity):  # type: ignore[no-untyped-def]
            return REJECTION

        monkeypatch.setattr(RdsQueryClient, "_bootstrap_once", always_rejected)
        with pytest.raises(PeerRejectedAllIdentities) as excinfo:
            await _client().bootstrap(ResourceCache())
        assert len(excinfo.value.failures) == 2

    @pytest.mark.asyncio
    async def test_a_clean_pass_offers_only_the_first(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The happy path costs no extra handshake."""
        offered: list[Any] = []

        async def accepted(self, cache, kinds, timeout, identity):  # type: ignore[no-untyped-def]
            offered.append(identity)
            return None

        monkeypatch.setattr(RdsQueryClient, "_bootstrap_once", accepted)
        await _client().bootstrap(ResourceCache())
        assert len(offered) == 1

    @pytest.mark.asyncio
    async def test_no_identity_configured_still_bootstraps(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A Controller that authenticates with nothing but the server's
        certificate is the ordinary case, not a missing identity."""
        offered: list[Any] = []

        async def accepted(self, cache, kinds, timeout, identity):  # type: ignore[no-untyped-def]
            offered.append(identity)
            return None

        monkeypatch.setattr(RdsQueryClient, "_bootstrap_once", accepted)
        client = RdsQueryClient(RdsQueryConfig(host="h", port=8446, tls=True))
        await client.bootstrap(ResourceCache())
        assert offered == [None]
