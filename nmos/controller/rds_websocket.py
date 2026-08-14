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

**Failing over to an independent registry** is where that parallelism
stops being free. Six subscribers notice one outage separately, so the
switch has to be a *coordinated* act rather than six local ones — see
``_on_selection_moved``, which owns the whole of it. With
``--rdsDistributed`` none of that applies: cluster members serve the
same shared state, so there is nothing to invalidate and the switch
stays what it always was, a log line and a counter reset.
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
# Capped well below the operational rule that a failed registry stays down
# long enough for every participant to observe it. A subscriber must get
# FAILOVER_AFTER attempts inside that window, and each attempt costs at most
# (connect timeout + backoff); at 30 s the worst case was 3 x 40 s = 120 s, so
# a subscriber could sleep through the whole outage and never notice.
MAX_BACKOFF_S = 5.0


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

    #: Consecutive failed connect/consume cycles against one registry before
    #: this subscriber reports it. Matches the Node client's threshold so the
    #: two halves of a process reach the same conclusion at about the same
    #: time rather than one dragging the other back and forth.
    FAILOVER_AFTER = 3

    def __init__(
        self,
        config: RdsWebSocketConfig,
        selector: Any = None,
        *,
        distributed: bool = False,
    ) -> None:
        self._config = config
        # Whether the configured registries share state. The Controller cannot
        # work this out for itself: it holds no registration to probe, and a
        # subscription succeeds identically against a clustered member and an
        # unrelated registry. See ``--rdsDistributed``.
        self._distributed = distributed
        # Optional: without it this client behaves exactly as it did before,
        # pinned to ``config``. With it, every subscriber re-reads the current
        # registry before each attempt, so one member failing moves all six.
        self._selector = selector
        # The selection generation a resync has already been performed for.
        # ``RegistrySelector.failover_count`` increments exactly once per
        # advance, so it already IS the epoch -- there is no second counter to
        # keep in step with it. Starts below every real generation so the
        # first switch is never mistaken for one already handled.
        self._resynced_generation = -1
        # Every connected subscriber's socket, so a switch can force the ones
        # that never failed off the registry the process has just abandoned.
        # Registered on connect and removed in a ``finally``, so a crashed
        # subscriber cannot leave a closed socket here to be closed again.
        self._live_sockets: dict[ResourceKind, aiohttp.ClientWebSocketResponse] = {}

    def _config_for(self, target: Any) -> RdsWebSocketConfig:
        """The Query/WebSocket half of a ``RegistryTarget``."""
        return RdsWebSocketConfig(
            query_host=target.host,
            query_port=target.query_port,
            ws_host=target.host,
            ws_port=target.ws_port,
            tls=target.tls,
            trusted_root_ca=tuple(target.trusted_root_ca),
            client_certificate=target.client_certificate,
            client_key=target.client_key,
        )

    def _current(self) -> tuple[Any, RdsWebSocketConfig]:
        """The registry to use for the next attempt, and its config.

        Read fresh each time rather than cached: that is the whole mechanism
        by which a subscriber follows a failover another subscriber -- or the
        Node's registration loop -- has already decided.
        """
        if self._selector is None:
            return None, self._config
        target = self._selector.current
        return target, self._config_for(target)

    def _build_ssl_context(
        self, cfg: RdsWebSocketConfig | None = None,
    ) -> ssl.SSLContext | None:
        cfg = cfg if cfg is not None else self._config
        if not cfg.tls:
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
        failures = 0

        while not dg.is_done:
            # Re-read before every attempt. If another subscriber -- or the
            # Node's registration loop -- has already moved on, this one
            # follows without needing to be told.
            target, cfg = self._current()
            # The generation this attempt belongs to. Captured here, next to
            # the target it goes with, so a grain can be matched against the
            # registry it actually came from rather than the one selected by
            # the time it is applied.
            epoch = self._generation()
            ssl_ctx = self._build_ssl_context(cfg)
            connector_ssl: bool | ssl.SSLContext = (
                ssl_ctx if ssl_ctx is not None else False
            )
            superseded = False
            try:
                connector = aiohttp.TCPConnector(ssl=connector_ssl)
                timeout = aiohttp.ClientTimeout(total=None, connect=10)
                async with aiohttp.ClientSession(
                    connector=connector, timeout=timeout,
                ) as session:
                    ws_href = await self._create_subscription(
                        session, resource_path, cfg,
                    )
                    log.info("rds_ws[%s]: connecting %s", kind, ws_href)
                    async with session.ws_connect(ws_href) as ws:
                        backoff = INITIAL_BACKOFF_S  # reset on successful connect
                        failures = 0
                        self._live_sockets[kind] = ws
                        try:
                            await self._consume_grains(ws, cache, kind, dg, epoch)
                        finally:
                            self._live_sockets.pop(kind, None)
                        # Returning while the generation has moved means this
                        # connection was abandoned rather than broken -- either
                        # by the epoch guard or by ``_drop_stale_sockets``.
                        superseded = self._generation() != epoch
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("rds_ws[%s]: %s; reconnect in %.1fs", kind, exc, backoff)
                failures += 1
                if (
                    target is not None
                    and self._selector is not None
                    and failures >= self.FAILOVER_AFTER
                ):
                    successor = self._selector.fail(target)
                    if successor != target:
                        log.warning(
                            "rds_ws[%s]: %s failed %d times - switching to %s"
                            " (%s)",
                            kind, target.label, failures, successor.label,
                            "clustered: keeping cache" if self._distributed
                            else f"independent: reloading all kinds from "
                                 f"{successor.label}",
                        )
                        # One call, no mode test here: which of the two
                        # behaviours applies is decided in exactly one place.
                        await self._on_selection_moved(cache)
                        # This subscriber is the one that noticed, so nothing
                        # has closed its socket -- but it is now just as
                        # re-targeted as the five that were closed, and the
                        # backoff it earned was earned against a registry it
                        # will not contact again. Same reasoning as the
                        # ``failures`` reset below, applied to the wait.
                        superseded = True
                        backoff = INITIAL_BACKOFF_S
                    # Reset either way: on a switch the count belongs to the
                    # new registry, and with no alternative there is nothing to
                    # gain by counting past the threshold forever.
                    failures = 0

            # Back-off and retry (unless we're shutting down).
            if dg.is_done:
                return
            if superseded:
                # Not a failure and not something to wait out: this subscriber
                # was moved off a registry the process abandoned, and the one
                # it should be on is already known. Reconnecting immediately
                # keeps the gap to the new registry as short as the handshake.
                continue
            try:
                await asyncio.sleep(backoff + random.random())
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, MAX_BACKOFF_S)

    def _generation(self) -> int:
        """Which selection generation we are on.

        Zero when there is no selector, so a client pinned to one registry has
        a single generation for its whole life and every epoch test is a no-op.
        """
        return 0 if self._selector is None else int(self._selector.failover_count)

    async def _on_selection_moved(self, cache: ResourceCache) -> None:
        """React, once, to the selection having advanced to a new registry.

        Six subscribers notice one outage independently and each reports it, so
        the reaction has to be idempotent per generation rather than per
        subscriber. Everything a switch does lives here, which is what makes
        "what happens on failover" a question with one answer.
        """
        if self._distributed:
            # Cluster members serve the same shared state, so there is nothing
            # to invalidate: not the cache, and not the connections either. A
            # subscriber still attached to an earlier member is receiving
            # correct data. Deliberately the first line -- the clustered path
            # is a visible no-op rather than something to be traced through
            # three guards to confirm it does nothing.
            return

        generation = self._generation()
        if generation <= self._resynced_generation:
            return
        # Assigned before the first ``await``. asyncio is single-threaded, so
        # nothing can run between the test and this line -- which is what makes
        # six concurrent reports produce exactly one resync.
        self._resynced_generation = generation

        # 1. Delete, FIRST. Independent registries hold DIFFERENT resources and
        #    grains only ever upsert -- a SYNC burst carries pre == post and
        #    removes nothing -- so anything not deleted here survives as a
        #    resource that exists in no live registry. Every kind, not just the
        #    one whose subscriber happened to notice first.
        #
        #    Before the sockets are dropped, not after, and the order matters:
        #    a dropped subscriber reconnects immediately, and its SYNC burst
        #    from the NEW registry can land while this loop is still running.
        #    Clearing afterwards would wipe data that was already correct, and
        #    that subscriber will not send it again until its connection next
        #    breaks -- so a failed reload at step 3 would leave the kind empty
        #    indefinitely. Clearing first, nothing has reached the new registry
        #    yet, so this can only ever delete the old one's contents.
        for _path, kind in self._KINDS:
            await cache.replace_all(kind, [])

        # 2. No subscriber may keep feeding the cache from the registry we have
        #    just abandoned. The ones that failed are already reconnecting; the
        #    ones whose sockets are healthy would otherwise never notice, and
        #    two independent registries would merge into one cache.
        await self._drop_stale_sockets()

        # 3. Reload. Best effort by design: if it fails the cache stays empty,
        #    which is the honest state, and the six subscribers are already
        #    reconnecting -- their SYNC bursts repopulate it. The reconnect loop
        #    IS the retry, so there is no retry task here.
        await self._reload_from_current(cache)

    async def _drop_stale_sockets(self) -> None:
        """Close every live subscription so all six re-target together."""
        for kind, ws in list(self._live_sockets.items()):
            try:
                await ws.close()
            except Exception as exc:  # pragma: no cover - close is best effort
                log.debug("rds_ws[%s]: closing stale socket: %s", kind, exc)

    async def _reload_from_current(self, cache: ResourceCache) -> None:
        """Refill the cache from whichever registry is now selected."""
        # Imported here rather than at module scope: rds_query imports the same
        # cache module, and this is the only path that needs it.
        from nmos.controller.rds_query import RdsQueryClient, RdsQueryConfig

        _target, cfg = self._current()
        query_config = RdsQueryConfig(
            host=cfg.query_host,
            port=cfg.query_port,
            tls=cfg.tls,
            trusted_root_ca=cfg.trusted_root_ca,
            client_certificate=cfg.client_certificate,
            client_key=cfg.client_key,
        )
        try:
            await RdsQueryClient(query_config).bootstrap(cache)
        except Exception as exc:
            # Not an error path. An empty cache that fills from the WebSocket a
            # few seconds later is a correct outcome; a populated one holding
            # the previous registry's resources would not be.
            log.warning(
                "rds_ws: reload from %s:%d failed (%s) - the cache stays empty "
                "until the subscriptions resync",
                cfg.query_host, cfg.query_port, exc,
            )

    async def _create_subscription(
        self, session: aiohttp.ClientSession, resource_path: str,
        cfg: RdsWebSocketConfig | None = None,
    ) -> str:
        """POST /x-nmos/query/v1.3/subscriptions/ and extract ws_href."""
        cfg = cfg if cfg is not None else self._config
        scheme = Https.s if cfg.tls else Http.s
        url = (
            f"{scheme}://{cfg.query_host}:{cfg.query_port}"
            f"/x-nmos/query/{QUERY_API_VERSION}/subscriptions/"
        )
        body = {
            "max_update_rate_ms": MAX_UPDATE_RATE_MS,
            "resource_path": resource_path,
            "persist": False,
            "secure": cfg.tls,
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
        epoch: int = 0,
    ) -> None:
        async for msg in ws:
            if dg.is_done:
                return
            if not self._distributed and self._generation() != epoch:
                # This connection belongs to a registry the process has
                # abandoned. Closing its socket is not enough on its own:
                # ``close()`` does not preempt a frame already being handled,
                # so without this test a late grain could re-upsert the old
                # registry's resources into a cache that was just cleared.
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
