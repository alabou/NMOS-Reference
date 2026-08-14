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


def _config_of(target: Any) -> RegistryConfig:
    """The registration half of a ``RegistryTarget``.

    ``RegistryTarget`` carries all three of a member's ports because failover
    has to move them together; this client speaks only to the registration one.
    """
    return RegistryConfig(
        host=target.host,
        port=target.registration_port,
        tls=target.tls,
        certificate_name=target.certificate_name,
        trusted_root_ca=tuple(target.trusted_root_ca),
        client_certificate=target.client_certificate,
        client_key=target.client_key,
    )


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

    #: Consecutive failed cycles against one registry before moving to the
    #: next. Not 1: a single failure is far more often a restart, a GC pass or
    #: a lost packet than a dead member, and failing over on it would make the
    #: Node re-register on every transient hiccup. Three cycles is ~3 s, well
    #: inside the 12 s collection interval, so the move still happens long
    #: before the registry would drop us.
    FAILOVER_AFTER = 3

    def __init__(self, selector: Any, node: Any) -> None:
        self._selector = selector
        self._node = node
        self._session: aiohttp.ClientSession | None = None
        self._failures = 0
        self._target = selector.current
        self._config = _config_of(self._target)
        self._base_url = self._build_base_url()

    def _adopt(self, target: Any) -> None:
        """Point this client at a different registry."""
        self._target = target
        self._config = _config_of(target)
        self._base_url = self._build_base_url()
        self._failures = 0

    @staticmethod
    def _is_registry_unresponsive(exc: BaseException) -> bool:
        """Does this exception mean the REGISTRY is at fault?

        ``Behaviour - Registration.md:118`` enumerates it exactly:

            "On registration or heartbeat, any of the following conditions
            indicates a server side or connectivity issue:
             - 500 (Internal Server Error) or other 5xx error
             - Inability to connect
             - Timeout"

        The distinction matters for more than tidiness. TR-10-9 section 15
        makes every participant switch on the *service* being unresponsive,
        which is what keeps Nodes and Controllers converging on the same
        registry without talking to each other. Counting a local fault -- a
        4xx from our own malformed request, say -- as unresponsiveness would
        move this Node off a registry everyone else still considers healthy.
        """
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return True
        if isinstance(exc, aiohttp.ClientConnectionError):
            return True
        if isinstance(exc, aiohttp.ClientResponseError):
            return exc.status >= 500
        if isinstance(exc, _RegistryError):
            # Unknown status is treated as NOT the registry's fault: the
            # conservative direction, since a spurious failover moves this Node
            # away from a registry the rest of the system still agrees on.
            return exc.status is not None and exc.status >= 500
        return False

    def _note_failure(self, exc: BaseException | None = None) -> bool:
        """Count one failed cycle. True if this registry should be abandoned.

        Reports the failure to the shared selector rather than choosing a
        successor here, so that the Controller's subscribers -- which notice
        the same outage independently -- end up on the same member. The
        selector ignores reports for a target that has already been replaced,
        so several clients reporting one outage move the selection once.
        """
        if exc is not None and not self._is_registry_unresponsive(exc):
            # Someone else's problem, or ours. Not evidence about the registry.
            return False
        self._failures += 1
        if self._failures < self.FAILOVER_AFTER:
            return False
        if not self._selector.has_alternatives:
            self._failures = 0   # nowhere to go; keep retrying this one
            return False
        successor = self._selector.fail(self._target)
        log.warning(
            "Registry: %s failed %d cycles - switching to %s",
            self._target.label, self._failures, successor.label,
        )
        return True

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
        # One session per registry. The TLS context and connection pool are
        # bound to a host, so moving to another member means building both
        # again -- which is why the loop is out here rather than inside
        # ``_registration_loop``.
        probe_first = False
        while not dg.is_done:
            self._adopt(self._selector.current)
            ssl_ctx = self._build_ssl_context()
            connector_ssl: bool | ssl.SSLContext = (
                ssl_ctx if ssl_ctx is not None else False
            )
            connector = aiohttp.TCPConnector(ssl=connector_ssl)
            timeout = aiohttp.ClientTimeout(total=REGISTRATION_TIMEOUT)

            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout,
            ) as session:
                self._session = session
                try:
                    await self._registration_loop(dg, probe_first)
                finally:
                    # Best-effort: unregister from registry on shutdown. On a
                    # failover this is expected to fail -- the registry we are
                    # leaving is the one that stopped answering -- and the
                    # lease expiry cleans up after us either way.
                    await self._cleanup_on_shutdown()
                    self._session = None
            if dg.is_done:
                return
            # ``_registration_loop`` returned without dg being done, which only
            # happens when it asked to move to the next registry. The next pass
            # probes with a heartbeat before assuming anything -- see
            # ``_registration_loop``'s ``probe_first``.
            probe_first = True

    async def _registration_loop(self, dg: Any, probe_first: bool = False) -> None:
        """Core registration loop.

        ``probe_first`` marks the first pass against a registry we have just
        failed over to. ``Behaviour - Registration.md:124`` is explicit about
        what to do there:

            "The first interaction with a new Registration API in this case
            SHOULD be a heartbeat to confirm whether the Node is still present
            in the registry. ... a 200 (OK) code indicates that the Node and
            its resources are still present in the registry cluster and no
            further action is necessary. If a 404 (Not Found) code is
            encountered ... refer to 'Node Encounters HTTP 404 On Heartbeat'."

        That single probe distinguishes the two deployments at runtime, with no
        configuration to get wrong: a member of a distributed cluster already
        holds this Node's registration and answers 200, so nothing is
        re-written; an independent registry has never heard of it and answers
        404, which falls through to the existing full re-registration path.

        Deleting and re-POSTing unconditionally -- as this did before -- is
        wrong for the clustered case in a way that is invisible locally: it
        removes a registration that was perfectly valid and puts it back,
        emitting removal-then-addition grains for the whole subtree to every
        Controller in the cluster.
        """
        need_initial_delete = not probe_first
        if probe_first:
            try:
                await self._heartbeat()
                log.info(
                    "Registry: %s already holds this Node - resuming "
                    "heartbeats, no re-registration needed",
                    self._target.label,
                )
            except asyncio.CancelledError:
                return
            except _NotFoundError:
                log.info(
                    "Registry: %s does not hold this Node - registering",
                    self._target.label,
                )
                self._node.publish_manager.reset_trackers()
                need_initial_delete = True
            except Exception as exc:
                # The registry we just moved to is not answering either. Fall
                # back to the ordinary path, which counts the failure and can
                # move on again.
                log.warning("Registry: probe of %s failed: %s",
                            self._target.label, exc)
                self._node.publish_manager.reset_trackers()
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
                    if self._note_failure(exc):
                        return
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
                if self._note_failure(exc):
                    return
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
                if self._note_failure(exc):
                    return
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
                raise _RegistryError(
                    f"DELETE {url} returned {resp.status}: {text}",
                    resp.status,
                )

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
                raise _RegistryError(
                    f"heartbeat returned {resp.status}: {text}", resp.status,
                )

        log.debug("Registry: heartbeat OK")

    async def _update_registry(self) -> None:
        """POST all changed resources to registry in dependency order.

        Uses tracker deduplication — only sends resources whose version
        has changed since last POST.
        """
        # Read the snapshot once. Every resource POSTed below must come from
        # this one object: there is an await between each POST, so reading the
        # Node's live attributes instead would let a PATCH land mid-cycle and
        # produce an update describing two different points in time.
        state = self._node.publish_manager.get_items()

        # 1. Node
        if state.node is not None:
            nv = state.node
            rc = nv.ResourceCore
            static_id = rc.StaticId.value if rc.StaticId.defined else rc.Id.value
            version = rc.Version.value
            if self._node.publish_manager.check_tracker(static_id, version):
                json_str = JsonEngine().encode(nv)
                await self._post_resource("node", json_str)

        # 2. Device
        if state.device is not None:
            dv = state.device
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
                    f"POST {type_name} returned {resp.status}: {text[:200]}",
                    resp.status,
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
                raise _RegistryError(
                    f"DELETE {type_plural}/{resource_id}: {resp.status}",
                    resp.status,
                )

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
    """Registry returned an unexpected status.

    Carries the HTTP status so callers can apply
    ``Behaviour - Registration.md:118``, which counts only 5xx as evidence
    that the *registry* is at fault. A 4xx is our request's problem and must
    not push this Node off a registry everyone else finds healthy.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _NotFoundError(Exception):
    """Registry returned 404 — node was garbage-collected."""
    pass
