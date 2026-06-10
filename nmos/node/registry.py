# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NMOS Registration API client.

Handles the full lifecycle of registering a Node and its resources with
an NMOS registry:

1. DELETE existing registration (clean slate on startup)
2. POST all resources in dependency order (node, device, sources, flows, senders, receivers)
3. Periodic heartbeat (POST /health/nodes/{id}) every 5 seconds
4. Re-registration on NOT_FOUND (node was garbage-collected by registry)
5. Garbage collection of deleted sources/flows after 1 minute
6. Clean DELETE on shutdown

Uses the PublishManager's copy-on-write snapshots and tracker deduplication
to avoid sending duplicate updates.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from nmos.api.tr10_tls import apply_tr10_tls_restrictions
from nmos.json.engine import JsonEngine
from nmos.node import _get_flow_core, _get_resource_core, _get_source_core
from nmos.enums import Http, Https

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class RegistryConfig:
    """Configuration for a single NMOS registry."""
    host: str
    port: int
    tls: bool
    certificate_name: str = ""
    trusted_root_ca: tuple[str, ...] = ()
    client_certificate: str = ""
    client_key: str = ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEARTBEAT_PERIOD: float = 5.0       # seconds between heartbeats
REGISTRATION_TIMEOUT: float = 3.0   # HTTP request timeout
GARBAGE_DELETE_AFTER: float = 60.0   # seconds before garbage resources are DELETEd


# ---------------------------------------------------------------------------
# Registry Client
# ---------------------------------------------------------------------------

class RegistryClient:
    """NMOS Registration API client.

    Manages the lifecycle of registering a Node with an NMOS registry.
    """

    def __init__(self, config: RegistryConfig, node: Any) -> None:
        self._config = config
        self._node = node
        self._session: aiohttp.ClientSession | None = None
        self._base_url = self._build_base_url()

    def _build_base_url(self) -> str:
        scheme = Https.s if self._config.tls else Http.s
        return f"{scheme}://{self._config.host}:{self._config.port}"

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        if not self._config.tls:
            return None

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        apply_tr10_tls_restrictions(ctx)

        if self._config.client_certificate and self._config.client_key:
            ctx.load_cert_chain(self._config.client_certificate, self._config.client_key)

        if self._config.trusted_root_ca:
            for ca in self._config.trusted_root_ca:
                ctx.load_verify_locations(ca)
        else:
            ctx.load_default_certs()

        return ctx

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    async def run(self, dg: Any) -> None:
        """Main registration loop.

        Phases:
        1. Wait for node to be published
        2. DELETE existing registration (clean slate)
        3. POST all resources + heartbeat loop
        4. On shutdown: best-effort DELETE
        """
        ssl_ctx = self._build_ssl_context()
        connector_ssl: bool | ssl.SSLContext = ssl_ctx if ssl_ctx is not None else False
        connector = aiohttp.TCPConnector(ssl=connector_ssl)
        timeout = aiohttp.ClientTimeout(total=REGISTRATION_TIMEOUT)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            self._session = session
            try:
                await self._registration_loop(dg)
            finally:
                # Best-effort: unregister from registry on shutdown
                await self._cleanup_on_shutdown()
                self._session = None

    async def _registration_loop(self, dg: Any) -> None:
        """Core registration loop."""
        need_initial_delete = True

        while not dg.is_done:
            # Wait for node to be published
            if not self._node.publish_manager.is_published:
                try:
                    await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    return
                continue

            # Phase 1: DELETE existing registration (clean slate)
            if need_initial_delete:
                try:
                    await self._delete_from_registry()
                    need_initial_delete = False
                    log.info("Registry: initial DELETE succeeded")
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    log.warning(f"Registry: DELETE failed: {exc}")
                    self._node.publish_manager.reset_trackers()
                    need_initial_delete = True
                    try:
                        await asyncio.sleep(1.0)
                    except asyncio.CancelledError:
                        return
                    continue

            # Phase 2: POST all changed resources
            try:
                await self._update_registry()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning(f"Registry: update failed: {exc}")
                self._node.publish_manager.reset_trackers()
                need_initial_delete = True
                try:
                    await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    return
                continue

            # Phase 3: Heartbeat
            try:
                await self._heartbeat()
            except asyncio.CancelledError:
                return
            except _NotFoundError:
                # Node was garbage-collected by registry — restart
                log.warning("Registry: heartbeat returned 404 — restarting registration")
                self._node.publish_manager.reset_trackers()
                need_initial_delete = True
                try:
                    await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    return
                continue
            except Exception as exc:
                log.warning(f"Registry: heartbeat failed: {exc}")
                self._node.publish_manager.reset_trackers()
                need_initial_delete = True
                try:
                    await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    return
                continue

            # Phase 4: Garbage collection of deleted resources
            try:
                await self._delete_garbage_resources()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.debug(f"Registry: garbage collection error: {exc}")

            # Wait for next cycle: publish event or heartbeat interval
            try:
                event = self._node.publish_manager.event
                event.clear()
                await asyncio.wait_for(event.wait(), timeout=HEARTBEAT_PERIOD)
            except asyncio.TimeoutError:
                pass  # Normal heartbeat interval
            except asyncio.CancelledError:
                return

    # -----------------------------------------------------------------------
    # Registry HTTP operations
    # -----------------------------------------------------------------------

    async def _delete_from_registry(self) -> None:
        """DELETE /x-nmos/registration/v1.3/resource/nodes/{id}.

        Removes this node and all its children from the registry.
        """
        assert self._session is not None
        node_id = self._node.node_value.ResourceCore.Id.value
        url = f"{self._base_url}/x-nmos/registration/v1.3/resource/nodes/{node_id}"

        async with self._session.delete(url) as resp:
            if resp.status not in (204, 404):
                text = await resp.text()
                raise _RegistryError(f"DELETE {url} returned {resp.status}: {text}")

        log.debug(f"Registry: DELETE node {node_id} → {resp.status}")

    async def _heartbeat(self) -> None:
        """POST /x-nmos/registration/v1.3/health/nodes/{id}.

        Keeps the node alive in the registry.
        """
        assert self._session is not None
        node_id = self._node.node_value.ResourceCore.Id.value
        url = f"{self._base_url}/x-nmos/registration/v1.3/health/nodes/{node_id}"

        async with self._session.post(url) as resp:
            if resp.status == 404:
                raise _NotFoundError("node not found in registry")
            if resp.status != 200:
                text = await resp.text()
                raise _RegistryError(f"heartbeat returned {resp.status}: {text}")

        log.debug("Registry: heartbeat OK")

    async def _update_registry(self) -> None:
        """POST all changed resources to registry in dependency order.

        Uses tracker deduplication — only sends resources whose version
        has changed since last POST.
        """
        state = self._node.publish_manager.get_items()

        # 1. Node
        if self._node.node_value is not None:
            nv = self._node.node_value
            rc = nv.ResourceCore
            static_id = rc.StaticId.value if rc.StaticId.defined else rc.Id.value
            version = rc.Version.value
            if self._node.publish_manager.check_tracker(static_id, version):
                json_str = JsonEngine().encode(nv)
                await self._post_resource("node", json_str)

        # 2. Device
        if self._node.device_value is not None:
            dv = self._node.device_value
            rc = dv.ResourceCore
            static_id = rc.StaticId.value if rc.StaticId.defined else rc.Id.value
            version = rc.Version.value
            if self._node.publish_manager.check_tracker(static_id, version):
                json_str = JsonEngine().encode(dv)
                await self._post_resource("device", json_str)

        # 3. Sources
        for static_id, source in state.sources.items():
            rc = _get_resource_core(source)
            version = rc.Version.value
            if self._node.publish_manager.check_tracker(static_id, version):
                inner = source
                if hasattr(source, 'get') and callable(source.get):
                    got = source.get()
                    if got is not None:
                        inner = got
                json_str = JsonEngine().encode(inner)
                await self._post_resource("source", json_str)

        # 4. Flows
        for static_id, flow in state.flows.items():
            rc = _get_resource_core(flow)
            version = rc.Version.value
            if self._node.publish_manager.check_tracker(static_id, version):
                inner = flow
                if hasattr(flow, 'get') and callable(flow.get):
                    got = flow.get()
                    if got is not None:
                        inner = got
                json_str = JsonEngine().encode(inner)
                await self._post_resource("flow", json_str)

        # 5. Senders
        for static_id, sender in state.senders.items():
            rc = sender.ResourceCore
            version = rc.Version.value
            if self._node.publish_manager.check_tracker(static_id, version):
                json_str = JsonEngine().encode(sender)
                await self._post_resource("sender", json_str)

        # 6. Receivers
        for static_id, receiver in state.receivers.items():
            rc = _get_resource_core(receiver)
            version = rc.Version.value
            if self._node.publish_manager.check_tracker(static_id, version):
                inner = receiver
                if hasattr(receiver, 'get') and callable(receiver.get):
                    got = receiver.get()
                    if got is not None:
                        inner = got
                json_str = JsonEngine().encode(inner)
                await self._post_resource("receiver", json_str)

    async def _post_resource(self, type_name: str, json_str: str) -> None:
        """POST /x-nmos/registration/v1.3/resource.

        Body: {"type": "<type>", "data": <resource_json>}
        """
        assert self._session is not None
        url = f"{self._base_url}/x-nmos/registration/v1.3/resource"

        # Compose body without double-parse
        body = f'{{"type":"{type_name}","data":{json_str}}}'

        async with self._session.post(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise _RegistryError(
                    f"POST {type_name} returned {resp.status}: {text[:200]}"
                )

        log.debug(f"Registry: POST {type_name} → {resp.status}")

    async def _delete_garbage_resources(self) -> None:
        """Delete stale sources/flows from registry after 1 minute.

        GarbageResource has .id (str) and .time (datetime).
        """
        from datetime import datetime, timedelta

        now = datetime.now()
        delete_after = timedelta(seconds=GARBAGE_DELETE_AFTER)

        # Process garbage sources — drain and re-add unexpired items
        count = len(self._node.garbage_sources)
        for _ in range(count):
            if not self._node.garbage_sources:
                break
            gr = self._node.garbage_sources.popleft()
            if now - gr.time >= delete_after:
                try:
                    await self._delete_resource("sources", gr.id)
                except Exception as exc:
                    log.debug(f"Registry: garbage delete source {gr.id}: {exc}")
            else:
                self._node.garbage_sources.append(gr)

        # Process garbage flows — same pattern
        count = len(self._node.garbage_flows)
        for _ in range(count):
            if not self._node.garbage_flows:
                break
            gr = self._node.garbage_flows.popleft()
            if now - gr.time >= delete_after:
                try:
                    await self._delete_resource("flows", gr.id)
                except Exception as exc:
                    log.debug(f"Registry: garbage delete flow {gr.id}: {exc}")
            else:
                self._node.garbage_flows.append(gr)

    async def _delete_resource(self, type_plural: str, resource_id: str) -> None:
        """DELETE /x-nmos/registration/v1.3/resource/{type}/{id}."""
        assert self._session is not None
        url = f"{self._base_url}/x-nmos/registration/v1.3/resource/{type_plural}/{resource_id}"

        async with self._session.delete(url) as resp:
            if resp.status not in (204, 404):
                text = await resp.text()
                raise _RegistryError(f"DELETE {type_plural}/{resource_id}: {resp.status}")

        log.debug(f"Registry: DELETE {type_plural}/{resource_id} → {resp.status}")

    # -----------------------------------------------------------------------
    # Shutdown cleanup
    # -----------------------------------------------------------------------

    async def _cleanup_on_shutdown(self) -> None:
        """Best-effort DELETE node from registry on shutdown."""
        if self._session is None or self._session.closed:
            return
        try:
            node_id = self._node.node_value.ResourceCore.Id.value
            url = f"{self._base_url}/x-nmos/registration/v1.3/resource/nodes/{node_id}"
            async with asyncio.timeout(3.0):
                async with self._session.delete(url) as resp:
                    log.info(f"Registry: shutdown DELETE → {resp.status}")
        except Exception as exc:
            log.debug(f"Registry: shutdown cleanup failed: {exc}")


# ---------------------------------------------------------------------------
# Internal exceptions (not exposed outside this module)
# ---------------------------------------------------------------------------

class _RegistryError(Exception):
    """Registry returned unexpected status."""
    pass


class _NotFoundError(Exception):
    """Registry returned 404 — node was garbage-collected."""
    pass
