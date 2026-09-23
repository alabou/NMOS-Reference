# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""One-shot RDS Query API client.

At controller startup we bootstrap the ``ResourceCache`` with the full
snapshot of senders, receivers, devices and flows from the NMOS
Registry's Query API (IS-04 Query section). The long-lived
``RdsWebSocketClient`` then keeps the cache current via push grains.

URL shape — per IS-04 Query §Behaviour:
  * ``GET /x-nmos/query/v1.3/senders``
  * ``GET /x-nmos/query/v1.3/receivers``
  * ``GET /x-nmos/query/v1.3/devices``
  * ``GET /x-nmos/query/v1.3/flows``

The response is a JSON array of resources. Paging headers are ignored
for v1 — the registry size we target fits comfortably in one page.

TLS context follows the same conventions as
[nmos.node.registry.RegistryClient](../node/registry.py): optional mTLS
client cert + trusted root CA; falls back to system trust if neither
is supplied.
"""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from typing import Any

import aiohttp

from nmos.api.tr10_tls import apply_tr10_tls_restrictions
from nmos.tls_identity import (
    PeerRejectedAllIdentities,
    client_identity_order,
    is_peer_rejected_identity,
    leaf_public_key_algorithm,
    pair_identities,
)
from nmos.controller.cache import ResourceCache, ResourceKind

log = logging.getLogger(__name__)


QUERY_API_VERSION = "v1.3"
QUERY_TIMEOUT = 10.0


@dataclass
class RdsQueryConfig:
    """Configuration for the query client.

    Mirrors ``RegistryConfig`` — same host / cert layout — but targets
    the query port rather than the registration port.
    """

    host: str
    port: int
    tls: bool = True
    trusted_root_ca: tuple[str, ...] = ()
    # Repeatable, as the trust anchors are: TR-10-SEC TCT=2 (Both) configures
    # one identity per certificate type. The Nth key pairs with the Nth
    # certificate.
    client_certificate: tuple[str, ...] = ()
    client_key: tuple[str, ...] = ()


class RdsQueryClient:
    """Bootstraps the ``ResourceCache`` from the Query API."""

    def __init__(self, config: RdsQueryConfig) -> None:
        self._config = config
        self._base_url = self._build_base_url()

    def _build_base_url(self) -> str:
        scheme = "https" if self._config.tls else "http"
        return f"{scheme}://{self._config.host}:{self._config.port}/x-nmos/query/{QUERY_API_VERSION}"

    def _identities(self) -> list[tuple[str, str]]:
        """This registry's client identities, ECDSA first."""
        return client_identity_order(pair_identities(
            self._config.client_certificate, self._config.client_key,
            cert_flag="--rdsClientCertificate", key_flag="--rdsClientKey",
        ))

    def _build_ssl_context(
        self, identity: tuple[str, str] | None = None,
    ) -> ssl.SSLContext | None:
        if not self._config.tls:
            return None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        apply_tr10_tls_restrictions(ctx)
        # Exactly one identity per context: a client context holding two does
        # not answer the peer's CertificateRequest with the one it asked for,
        # so the choice is made by offering them in turn. See
        # nmos/tls_identity.py.
        if identity is None:
            candidates = self._identities()
            identity = candidates[0] if candidates else None
        if identity is not None:
            certificate, key = identity
            ctx.load_cert_chain(certificate, key)
        if self._config.trusted_root_ca:
            for ca in self._config.trusted_root_ca:
                ctx.load_verify_locations(ca)
        else:
            ctx.load_default_certs()
        return ctx

    async def bootstrap(self, cache: ResourceCache) -> None:
        """Fetch all four resource kinds and populate the cache.

        Silently skips kinds that fail to fetch (registry temporarily
        unavailable) — the WebSocket will retry via reconnect.
        """
        timeout = aiohttp.ClientTimeout(total=QUERY_TIMEOUT)

        # ``sources`` is pulled because BCP-008 monitor resources are
        # published as IS-04 Sources (``format=urn:x-nmos:format:data``,
        # ``monitor_type=sender|receiver``, ``monitor_sibling_id=<peer>``).
        # The cache's ``extract_status`` uses them to drive the
        # listing-page / configure-page status dots; without ingest
        # every sender/receiver falls back to the
        # subscription-activity placeholder.
        # Nodes are fetched because controllers need ``node.services``
        # to discover per-Node APIs (like the Node Reservation service
        # at ``urn:x-matrox:service:exclusive/v1.0``) — the session
        # manager looks up the acquire/renew/release base URL by
        # walking this array, as implemented by
        # ``GetNodeManufactuerApi``.
        kinds: tuple[tuple[str, ResourceKind], ...] = (
            ("nodes", "node"),
            ("devices", "device"),
            ("sources", "source"),
            ("senders", "sender"),
            ("receivers", "receiver"),
            ("flows", "flow"),
        )

        # One attempt per client identity. The identity is attached to the
        # connector rather than to a request, so offering the next one means a
        # new session -- and the whole bootstrap is re-run, which is safe
        # because ``replace_all`` is idempotent per kind.
        # ``[None]`` is one attempt with no client certificate, which is the
        # ordinary unauthenticated case rather than a missing identity.
        identities: list[tuple[str, str] | None] = list(self._identities()) or [None]
        failures: list[tuple[str, BaseException]] = []
        for attempt, identity in enumerate(identities):
            rejected = await self._bootstrap_once(
                cache, kinds, timeout, identity,
            )
            if rejected is None:
                return
            if identity is not None:
                failures.append((
                    leaf_public_key_algorithm(identity[0]) or "unreadable",
                    rejected,
                ))
            if attempt + 1 < len(identities):
                log.warning(
                    "rds_query: registry refused our %s client certificate "
                    "- offering the next", failures[-1][0],
                )
        if failures:
            # Named rather than swallowed. Six identical "bootstrap failed"
            # warnings and an empty cache was the old behaviour, and it told
            # the operator nothing about why.
            raise PeerRejectedAllIdentities(failures)

    async def _bootstrap_once(
        self,
        cache: ResourceCache,
        kinds: tuple[tuple[str, ResourceKind], ...],
        timeout: aiohttp.ClientTimeout,
        identity: tuple[str, str] | None,
    ) -> BaseException | None:
        """One pass over every kind. Returns the rejection that stopped it.

        ``None`` means the pass completed -- individual kinds may still have
        been skipped, which is the pre-existing "registry temporarily
        unavailable" behaviour the WebSocket retries.
        """
        ssl_ctx = self._build_ssl_context(identity)
        connector: aiohttp.TCPConnector = aiohttp.TCPConnector(
            ssl=ssl_ctx if ssl_ctx is not None else False,
        )
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout,
        ) as session:
            for path, kind in kinds:
                try:
                    resources = await self._fetch_list(session, path)
                    await cache.replace_all(kind, resources)
                    log.info(
                        "rds_query: bootstrap %s → %d resources",
                        path, len(resources),
                    )
                except Exception as exc:
                    # A refused identity stops the pass: every remaining kind
                    # would be refused for the same reason, and the caller has
                    # another identity to offer.
                    if is_peer_rejected_identity(exc):
                        return exc
                    log.warning("rds_query: bootstrap %s failed: %s", path, exc)
        return None

    async def _fetch_list(
        self, session: aiohttp.ClientSession, path: str,
    ) -> list[dict[str, Any]]:
        url = f"{self._base_url}/{path}"
        async with session.get(url) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(
                    f"GET {url} returned {resp.status}: {text[:200]}",
                )
            body = await resp.json()
        if not isinstance(body, list):
            raise RuntimeError(f"GET {url} returned non-list body: {type(body).__name__}")
        return [b for b in body if isinstance(b, dict)]


__all__ = ["RdsQueryClient", "RdsQueryConfig", "ResourceKind"]
