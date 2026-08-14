# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Failover behaviour of the Node's registration client.

Two specs govern this and are quoted where they apply:

* ``Behaviour - Registration.md:118`` enumerates what counts as the registry
  being at fault, and :124 mandates that the first interaction with a new
  Registration API be a heartbeat.
* ``VSF TR-10-9 v2 section 15`` requires every participant to select the best
  priority, move to the next when a service is unresponsive, and never fail
  back -- which is what keeps Nodes and Controllers converging without
  coordinating.
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

from nmos.node.registry import RegistryClient, _NotFoundError, _RegistryError
from nmos.rds_targets import RegistrySelector, build_targets, target_from_scalars

DEFAULT = target_from_scalars(
    host="", registration_port=8447, query_port=8446, ws_port=8448, tls=False,
)


def _client(hosts: int = 3) -> RegistryClient:
    selector = RegistrySelector(
        build_targets([f"host=10.0.0.{i}" for i in range(1, hosts + 1)], DEFAULT),
    )
    return RegistryClient(selector, node=object())


class TestUnresponsiveCriterion:
    """:118 -- 5xx, inability to connect, or timeout. Nothing else."""

    @pytest.mark.parametrize("exc", [
        asyncio.TimeoutError(),
        TimeoutError(),
        aiohttp.ClientConnectionError("refused"),
        _RegistryError("boom", 500),
        _RegistryError("boom", 503),
    ])
    def test_registry_faults_count(self, exc: BaseException) -> None:
        assert RegistryClient._is_registry_unresponsive(exc) is True

    @pytest.mark.parametrize("exc", [
        _RegistryError("bad request", 400),
        _RegistryError("conflict", 409),
        _RegistryError("unknown status", None),
        ValueError("a local bug"),
    ])
    def test_our_own_faults_do_not(self, exc: BaseException) -> None:
        """A 4xx is our request's problem.

        Counting it would move this Node off a registry the rest of the system
        still agrees on, breaking the convergence TR-10-9 section 15 relies on.
        """
        assert RegistryClient._is_registry_unresponsive(exc) is False


class TestFailoverCounting:
    def test_it_takes_FAILOVER_AFTER_registry_faults_to_move(self) -> None:
        c = _client()
        first = c._target
        for _ in range(RegistryClient.FAILOVER_AFTER - 1):
            assert c._note_failure(asyncio.TimeoutError()) is False
        assert c._note_failure(asyncio.TimeoutError()) is True
        assert c._selector.current != first

    def test_client_side_faults_never_accumulate(self) -> None:
        c = _client()
        for _ in range(20):
            assert c._note_failure(_RegistryError("bad", 400)) is False
        assert c._selector.failover_count == 0

    def test_a_single_registry_never_moves(self) -> None:
        c = _client(hosts=1)
        for _ in range(20):
            assert c._note_failure(asyncio.TimeoutError()) is False
        assert c._selector.failover_count == 0

    def test_adopt_resets_the_counter(self) -> None:
        c = _client()
        c._note_failure(asyncio.TimeoutError())
        c._adopt(c._selector.current)
        assert c._failures == 0


class TestHeartbeatFirstOnFailover:
    """:124 -- probe before assuming anything about the new registry."""

    @staticmethod
    def _probing_client(outcome: BaseException | None):
        c = _client()
        calls: list[str] = []

        async def fake_heartbeat() -> None:
            calls.append("heartbeat")
            if outcome is not None:
                raise outcome

        class _PM:
            is_published = True
            def reset_trackers(self) -> None:
                calls.append("reset")
        c._heartbeat = fake_heartbeat            # type: ignore[method-assign]
        c._node = type("N", (), {"publish_manager": _PM()})()
        return c, calls

    async def test_200_means_no_re_registration(self) -> None:
        """The clustered case: the member already holds this Node.

        Deleting and re-POSTing here would emit removal-then-addition grains
        for the whole subtree to every Controller in the cluster.
        """
        c, calls = self._probing_client(None)
        dg = type("DG", (), {"is_done": True})()
        await c._registration_loop(dg, probe_first=True)
        assert calls == ["heartbeat"]          # probed, and nothing reset

    async def test_404_falls_through_to_full_registration(self) -> None:
        """The independent-registry case: it has never heard of this Node."""
        c, calls = self._probing_client(_NotFoundError())
        dg = type("DG", (), {"is_done": True})()
        await c._registration_loop(dg, probe_first=True)
        assert calls == ["heartbeat", "reset"]

    async def test_probe_failure_falls_through_too(self) -> None:
        """The registry we just moved to is not answering either."""
        c, calls = self._probing_client(asyncio.TimeoutError())
        dg = type("DG", (), {"is_done": True})()
        await c._registration_loop(dg, probe_first=True)
        assert calls == ["heartbeat", "reset"]

    async def test_no_probe_on_the_very_first_registry(self) -> None:
        """Startup is not a failover; the normal path already handles it."""
        c, calls = self._probing_client(None)
        dg = type("DG", (), {"is_done": True})()
        await c._registration_loop(dg, probe_first=False)
        assert calls == []
