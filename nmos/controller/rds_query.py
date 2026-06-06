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
    client_certificate: str = ""
    client_key: str = ""


class RdsQueryClient:
    """Bootstraps the ``ResourceCache`` from the Query API."""

    def __init__(self, config: RdsQueryConfig) -> None:
        self._config = config
        self._base_url = self._build_base_url()

    def _build_base_url(self) -> str:
        scheme = "https" if self._config.tls else "http"
        return f"{scheme}://{self._config.host}:{self._config.port}/x-nmos/query/{QUERY_API_VERSION}"

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        if not self._config.tls:
            return None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        apply_tr10_tls_restrictions(ctx)
        if self._config.client_certificate and self._config.client_key:
            ctx.load_cert_chain(
                self._config.client_certificate, self._config.client_key,
            )
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
        ssl_ctx = self._build_ssl_context()
        connector: aiohttp.TCPConnector = aiohttp.TCPConnector(
            ssl=ssl_ctx if ssl_ctx is not None else False,
        )
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
                    log.warning("rds_query: bootstrap %s failed: %s", path, exc)

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
