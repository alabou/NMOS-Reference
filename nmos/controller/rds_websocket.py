# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Long-lived RDS WebSocket subscriber — keeps the cache live.

Controller's subscription flow:

1. ``POST /x-nmos/query/v1.3/subscriptions/`` with a
   ``{"resource_path": "/senders" | "/receivers" | "/devices" | "/flows",
     "max_update_rate_ms": 100, "persist": false}`` body.
2. The registry responds with a subscription resource containing a
   ``ws_href`` — the WebSocket URL to connect to for push updates.
3. Connect to the ``ws_href`` and consume NMOS "grain" messages
   (``{"grain_type": "event", "grain": {"data": [ ... ]}}``). Each
   data entry carries a ``path`` (resource id) plus ``pre`` and/or
   ``post`` resource representations:
     * ``post`` only → added
     * ``pre`` and ``post`` → updated
     * ``pre`` only → removed
4. On connection drop or 4xx/5xx: exponential back-off reconnect.

Per-resource-kind subscriptions run in parallel; each owns one
subscription + one WebSocket. All feed the same ``ResourceCache``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import ssl
from dataclasses import dataclass
from typing import Any

import aiohttp

from nmos.api.tr10_tls import apply_tr10_tls_restrictions
from nmos.controller.cache import ResourceCache, ResourceKind
from nmos.enums import Http, Https

log = logging.getLogger(__name__)


QUERY_API_VERSION = "v1.3"
MAX_UPDATE_RATE_MS = 100
INITIAL_BACKOFF_S = 1.0
MAX_BACKOFF_S = 30.0


@dataclass
class RdsWebSocketConfig:
    """Configuration for the WebSocket subscriber."""

    query_host: str
    query_port: int
    ws_host: str = ""     # Defaults to query_host when empty
    ws_port: int = 0      # Defaults to query_port when zero
    tls: bool = True
    trusted_root_ca: tuple[str, ...] = ()
    client_certificate: str = ""
    client_key: str = ""


class RdsWebSocketClient:
    """Subscribes to all resource kinds we care about and forwards
    events to the cache.

    ``/sources`` is included because BCP-008 monitor resources are
    IS-04 Sources whose changes drive the per-facet status dots in
    the UI (see ``extract_status`` in ``cache.py``). Without a
    subscription here the cache would only see monitor Sources at
    bootstrap and never pick up mid-session status changes —
    exactly the "receiver stays all-green after the sender
    deactivates" symptom.
    """

    # NMOS resource_path → our ResourceCache kind. ``/nodes`` is
    # included so ``node.services`` reaches the cache — the Privacy
    # flow needs it to discover the per-Node reservation service
    # (``urn:x-matrox:service:exclusive/v1.0``).
    _KINDS: tuple[tuple[str, ResourceKind], ...] = (
        ("/nodes", "node"),
        ("/devices", "device"),
        ("/sources", "source"),
        ("/senders", "sender"),
        ("/receivers", "receiver"),
        ("/flows", "flow"),
    )

    def __init__(self, config: RdsWebSocketConfig) -> None:
        self._config = config

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

    async def run(self, dg: Any, cache: ResourceCache) -> None:
        """Run one subscriber per resource kind until dg is cancelled."""
        tasks = [
            asyncio.create_task(self._run_one(dg, cache, path, kind))
            for path, kind in self._KINDS
        ]
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    async def _run_one(
        self,
        dg: Any,
        cache: ResourceCache,
        resource_path: str,
        kind: ResourceKind,
    ) -> None:
        backoff = INITIAL_BACKOFF_S
        ssl_ctx = self._build_ssl_context()
        connector_ssl: bool | ssl.SSLContext = (
            ssl_ctx if ssl_ctx is not None else False
        )

        while not dg.is_done:
            try:
                connector = aiohttp.TCPConnector(ssl=connector_ssl)
                timeout = aiohttp.ClientTimeout(total=None, connect=10)
                async with aiohttp.ClientSession(
                    connector=connector, timeout=timeout,
                ) as session:
                    ws_href = await self._create_subscription(session, resource_path)
                    log.info("rds_ws[%s]: connecting %s", kind, ws_href)
                    async with session.ws_connect(ws_href) as ws:
                        backoff = INITIAL_BACKOFF_S  # reset on successful connect
                        await self._consume_grains(ws, cache, kind, dg)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("rds_ws[%s]: %s; reconnect in %.1fs", kind, exc, backoff)

            # Back-off and retry (unless we're shutting down).
            if dg.is_done:
                return
            try:
                await asyncio.sleep(backoff + random.random())
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, MAX_BACKOFF_S)

    async def _create_subscription(
        self, session: aiohttp.ClientSession, resource_path: str,
    ) -> str:
        """POST /x-nmos/query/v1.3/subscriptions/ and extract ws_href."""
        scheme = Https.s if self._config.tls else Http.s
        url = (
            f"{scheme}://{self._config.query_host}:{self._config.query_port}"
            f"/x-nmos/query/{QUERY_API_VERSION}/subscriptions/"
        )
        body = {
            "max_update_rate_ms": MAX_UPDATE_RATE_MS,
            "resource_path": resource_path,
            "persist": False,
            "secure": self._config.tls,
            "params": {},
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with session.post(url, json=body, headers=headers) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RuntimeError(
                    f"POST {url} returned {resp.status}: {text[:200]}",
                )
            data = await resp.json()
        ws_href = data.get("ws_href")
        if not isinstance(ws_href, str) or not ws_href:
            raise RuntimeError(f"subscription response missing ws_href: {data}")
        return ws_href

    async def _consume_grains(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        cache: ResourceCache,
        kind: ResourceKind,
        dg: Any,
    ) -> None:
        async for msg in ws:
            if dg.is_done:
                return
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._handle_text(msg.data, cache, kind)
            elif msg.type in (aiohttp.WSMsgType.CLOSE,
                              aiohttp.WSMsgType.CLOSED,
                              aiohttp.WSMsgType.ERROR):
                return

    async def _handle_text(
        self, text: str, cache: ResourceCache, kind: ResourceKind,
    ) -> None:
        try:
            import json
            payload = json.loads(text)
        except ValueError:
            log.debug("rds_ws[%s]: non-JSON frame ignored", kind)
            return

        grain = payload.get("grain") if isinstance(payload, dict) else None
        data = grain.get("data") if isinstance(grain, dict) else None
        if not isinstance(data, list):
            return

        for entry in data:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            pre = entry.get("pre")
            post = entry.get("post")

            if isinstance(post, dict):
                await cache.upsert(kind, post)
            elif isinstance(pre, dict):
                rid = pre.get("id") or path
                if isinstance(rid, str) and rid:
                    await cache.remove(kind, rid)
