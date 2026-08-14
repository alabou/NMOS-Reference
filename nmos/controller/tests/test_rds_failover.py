# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Controller-side registry failover.

A system test found these behaviours; these tests keep them. The system test
originally reported a FALSE PASS (it matched two unrelated log lines), so the
behaviour was briefly believed broken and then briefly believed proven on bad
evidence. Unit coverage here means neither mistake can hide again.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import aiohttp
import pytest

from nmos.controller import rds_websocket
from nmos.controller.cache import ResourceCache
from nmos.controller.rds_websocket import (
    MAX_BACKOFF_S,
    RdsWebSocketClient,
    RdsWebSocketConfig,
)
from nmos.rds_targets import RegistrySelector, build_targets, target_from_scalars

#: The fallback every ``--rds`` entry inherits from. A ``RegistryTarget``, and
#: it belongs to ``build_targets`` -- not to the client's constructor.
DEFAULT = target_from_scalars(
    host="", registration_port=8447, query_port=8446, ws_port=8448, tls=False,
)

#: What ``RdsWebSocketClient`` actually takes. Kept separate from ``DEFAULT``
#: because the two are easy to confuse and the mistake is nearly invisible: a
#: client holding a selector derives its config per attempt and never reads the
#: constructor's, so passing the target works at runtime and is only ever
#: caught by the type checker.
CONFIG = RdsWebSocketConfig(
    query_host="10.0.0.1", query_port=8446, ws_port=8448, tls=False,
)

#: Every kind the client subscribes to. Spelled out rather than derived from
#: ``_KINDS`` so that dropping a kind from the client fails these tests instead
#: of quietly shrinking what they check.
ALL_KINDS = ("node", "device", "source", "sender", "receiver", "flow")


def _selector(n: int = 3) -> RegistrySelector:
    return RegistrySelector(
        build_targets([f"host=10.0.0.{i}" for i in range(1, n + 1)], DEFAULT),
    )


def _client(
    selector: RegistrySelector, *, distributed: bool = False,
) -> RdsWebSocketClient:
    """A client over ``selector``, built from a real config."""
    return RdsWebSocketClient(CONFIG, selector, distributed=distributed)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeCache:
    """Records what a switch does to the cache, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def replace_all(self, kind: str, resources: list[Any]) -> None:
        self.calls.append(("replace_all", kind, len(resources)))

    async def upsert(self, kind: str, resource: dict[str, Any]) -> None:
        self.calls.append(("upsert", kind, 1))

    async def remove(self, kind: str, resource_id: str) -> None:
        self.calls.append(("remove", kind, 1))

    @property
    def cleared_kinds(self) -> list[str]:
        return [k for verb, k, n in self.calls if verb == "replace_all" and n == 0]


class _FakeSocket:
    """A subscription socket that records being closed."""

    def __init__(self, messages: tuple[Any, ...] = ()) -> None:
        self._messages = list(messages)
        self.closed = False

    def __aiter__(self) -> _FakeSocket:
        return self

    async def __anext__(self) -> Any:
        if self.closed or not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def close(self) -> None:
        self.closed = True


def _text(payload: str) -> SimpleNamespace:
    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=payload)


_GRAIN = (
    '{"grain": {"data": [{"path": "abc", '
    '"post": {"id": "abc", "label": "phantom"}}]}}'
)


class _RunningDG:
    """A dispatch group that reports done after a fixed number of polls."""

    def __init__(self, stop_after: int) -> None:
        self._polls = 0
        self._stop_after = stop_after

    @property
    def is_done(self) -> bool:
        self._polls += 1
        return self._polls > self._stop_after


class _FakeBootstrap:
    """Stands in for ``RdsQueryClient``, recording where it was pointed."""

    def __init__(self, recorder: list[Any], fail: bool = False) -> None:
        self._recorder = recorder
        self._fail = fail

    def __call__(self, config: Any) -> _FakeBootstrap:
        self._config = config
        return self

    async def bootstrap(self, cache: Any) -> None:
        self._recorder.append(self._config)
        if self._fail:
            raise RuntimeError("query API unreachable")
        for kind in ALL_KINDS:
            await cache.replace_all(kind, [{"id": f"{kind}-1"}])


def _install_fake_query(
    monkeypatch: pytest.MonkeyPatch, *, fail: bool = False,
) -> list[Any]:
    """Point ``_reload_from_current`` at a fake, and report its calls.

    Patched on ``rds_query`` rather than on the client, because the import
    inside ``_reload_from_current`` is what a real run resolves.
    """
    from nmos.controller import rds_query

    calls: list[Any] = []
    monkeypatch.setattr(rds_query, "RdsQueryClient", _FakeBootstrap(calls, fail))
    return calls


class TestSubscriberCoordination:
    def test_only_the_first_subscriber_advances_the_selection(self) -> None:
        """Six subscribers notice one outage; the list must move once.

        The other five must then FOLLOW rather than each skipping ahead --
        which is why only one of them logs a switch in a real run.
        """
        s = _selector()
        dead = s.current
        results = [s.fail(dead) for _ in range(6)]
        assert s.failover_count == 1
        assert all(r == results[0] for r in results)
        assert results[0] != dead

    def test_every_subscriber_reads_the_new_target(self) -> None:
        """A subscriber that has not failed still moves, via ``current``."""
        s = _selector()
        first = s.current
        s.fail(first)
        client = _client(s)
        target, cfg = client._current()
        assert target != first
        assert cfg.query_host == target.host
        assert cfg.ws_port == target.ws_port


class TestConfigDerivation:
    def test_config_follows_the_target_not_the_constructor(self) -> None:
        """Config must be derived per attempt, never cached on self.

        The six subscribers run concurrently and can briefly disagree about
        the target mid-switch; shared mutable config would race.
        """
        s = _selector()
        client = _client(s)
        before = client._current()[1]
        s.fail(s.current)
        after = client._current()[1]
        assert before.query_host != after.query_host

    def test_without_a_selector_it_stays_pinned(self) -> None:
        """Back-compat: no selector means the old single-registry behaviour."""
        client = RdsWebSocketClient(CONFIG)
        target, cfg = client._current()
        assert target is None
        assert cfg is CONFIG


class TestCadence:
    def test_backoff_is_short_enough_to_observe_a_60s_outage(self) -> None:
        """TR-10-9 selection only works if every client observes the outage.

        A subscriber needs FAILOVER_AFTER attempts inside the window that a
        failed registry is required to stay down. Each attempt costs at most
        (connect timeout + backoff). At the old MAX_BACKOFF_S of 30 s the worst
        case was 3 x 40 s = 120 s, so a subscriber could sleep through a whole
        60 s outage and never switch.
        """
        connect_timeout = 10.0
        window = 60.0
        worst_case = RdsWebSocketClient.FAILOVER_AFTER * (
            MAX_BACKOFF_S + connect_timeout
        )
        assert worst_case < window, (
            f"worst-case detection {worst_case}s exceeds the {window}s "
            f"minimum-downtime rule"
        )


class TestCacheDiscipline:
    """Independent registries hold DIFFERENT resources.

    Grains only ever upsert -- a SYNC burst carries pre == post and removes
    nothing -- so without clearing, the cache becomes the UNION of both
    registries and shows resources that exist nowhere.

    The clearing used to be per subscriber, which read as covering all six
    kinds and covered exactly one: only the subscriber that trips
    FAILOVER_AFTER reaches it, and the other five re-read ``current``, connect,
    and reset their counters without ever clearing. Driving the UI through a
    real failover found a node's senders still listed seven minutes after the
    only registry holding them had died. Everything below exists so that the
    gap between "a switch clears the cache" and "a switch clears ONE kind"
    cannot reopen quietly.
    """

    def test_distributed_flag_defaults_off(self) -> None:
        """Fail in the recoverable direction: a refetch, not phantom rows."""
        assert RdsWebSocketClient(CONFIG)._distributed is False

    @pytest.mark.parametrize("distributed", [True, False])
    def test_flag_is_carried(self, distributed: bool) -> None:
        client = _client(_selector(), distributed=distributed)
        assert client._distributed is distributed

    @pytest.mark.asyncio
    async def test_every_kind_is_cleared_and_reloaded_on_an_independent_switch(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The regression test for the one-kind-in-six defect."""
        reloads = _install_fake_query(monkeypatch)
        selector = _selector()
        client = _client(selector, distributed=False)
        cache = _FakeCache()

        successor = selector.fail(selector.current)
        await client._on_selection_moved(cast(ResourceCache, cache))

        assert sorted(cache.cleared_kinds) == sorted(ALL_KINDS), (
            "every kind must be emptied, not just the subscriber's own"
        )
        assert len(reloads) == 1
        assert reloads[0].host == successor.host, "reloaded from the NEW registry"

    @pytest.mark.asyncio
    async def test_a_switch_drops_sockets_that_never_failed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A healthy subscriber would otherwise stay on the old registry.

        It never re-reads the selection while its socket is alive, so without
        this the cache is fed by two independent registries at once -- and no
        registry has to die for it: one GC'd subscription is enough.
        """
        _install_fake_query(monkeypatch)
        selector = _selector()
        client = _client(selector, distributed=False)
        sockets = {kind: _FakeSocket() for kind in ALL_KINDS}
        client._live_sockets.update(
            cast(dict[Any, aiohttp.ClientWebSocketResponse], sockets))

        selector.fail(selector.current)
        await client._on_selection_moved(cast(ResourceCache, _FakeCache()))

        assert all(s.closed for s in sockets.values())

    @pytest.mark.asyncio
    async def test_the_cache_is_emptied_before_the_sockets_are_dropped(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Order matters, and the wrong order is the plausible one.

        Dropping first looks natural -- stop the old registry talking, then
        tidy up. But a dropped subscriber reconnects immediately, and its SYNC
        burst from the NEW registry can land while the clear loop is still
        running; clearing afterwards would wipe data that was already correct,
        and that subscriber will not resend until its connection next breaks.
        A failed reload would then leave the kind empty indefinitely.

        Clearing first, nothing has reached the new registry yet, so the clear
        can only ever delete the abandoned registry's contents.
        """
        _install_fake_query(monkeypatch)
        selector = _selector()
        client = _client(selector, distributed=False)
        cache = _FakeCache()

        order: list[str] = []

        async def _record_drop() -> None:
            order.append("drop")

        monkeypatch.setattr(client, "_drop_stale_sockets", _record_drop)

        original_replace = cache.replace_all

        async def _record_clear(kind: str, resources: list[Any]) -> None:
            if not resources:
                order.append("clear")
            await original_replace(kind, resources)

        monkeypatch.setattr(cache, "replace_all", _record_clear)

        selector.fail(selector.current)
        await client._on_selection_moved(cast(ResourceCache, cache))

        assert order.count("clear") == len(ALL_KINDS)
        assert order.index("drop") > order.index("clear"), (
            "sockets were dropped before the cache was emptied"
        )
        assert order[-1] == "drop", "every kind must be cleared before the drop"

    @pytest.mark.asyncio
    async def test_resync_runs_once_per_generation(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Six subscribers report one outage; the reaction happens once."""
        reloads = _install_fake_query(monkeypatch)
        selector = _selector()
        client = _client(selector, distributed=False)
        cache = _FakeCache()

        selector.fail(selector.current)
        for _ in range(6):
            await client._on_selection_moved(cast(ResourceCache, cache))

        assert len(reloads) == 1
        assert len(cache.cleared_kinds) == len(ALL_KINDS), (
            "six reports must not clear the cache six times over"
        )

    @pytest.mark.asyncio
    async def test_reload_failure_leaves_the_cache_empty_not_stale(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unreachable new registry must not resurrect the old one's rows.

        Empty is the honest state: the subscriptions are already reconnecting
        and their SYNC bursts refill it. Keeping the previous contents would be
        the phantom-resource bug reintroduced through the failure path.
        """
        _install_fake_query(monkeypatch, fail=True)
        selector = _selector()
        client = _client(selector, distributed=False)
        cache = _FakeCache()

        selector.fail(selector.current)
        await client._on_selection_moved(cast(ResourceCache, cache))

        assert sorted(cache.cleared_kinds) == sorted(ALL_KINDS)
        assert not [c for c in cache.calls if c[0] == "upsert"]

    @pytest.mark.asyncio
    async def test_clustered_switch_invalidates_nothing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With --rdsDistributed there is nothing to invalidate.

        Members serve the same shared state, so a subscriber still attached to
        an earlier member is receiving correct data. This pins that decision:
        no clear, no reload, and no socket torn down. It is here so a later
        refactor cannot quietly extend the independent-mode machinery over a
        path that must stay a no-op.

        Scope, stated precisely because "clustered is unchanged" is easy to
        over-claim: what is untouched is the *state* -- cache, connections, and
        which grains get applied. Retry **pacing** does differ, and should: the
        subscriber that noticed reconnects to the new member immediately rather
        than sleeping out a backoff it earned against the old one. That is not
        invalidation, and it is covered by
        ``TestForceDroppedSubscriber`` rather than here.
        """
        reloads = _install_fake_query(monkeypatch)
        selector = _selector()
        client = _client(selector, distributed=True)
        cache = _FakeCache()
        sockets = {kind: _FakeSocket() for kind in ALL_KINDS}
        client._live_sockets.update(
            cast(dict[Any, aiohttp.ClientWebSocketResponse], sockets))

        selector.fail(selector.current)
        await client._on_selection_moved(cast(ResourceCache, cache))

        assert cache.calls == []
        assert reloads == []
        assert not any(s.closed for s in sockets.values())


class _SwitchingSocket(_FakeSocket):
    """A socket closed underneath its consumer, mid-iteration.

    Defined as a class rather than a patched instance because ``async for``
    resolves ``__anext__`` on the type -- an instance attribute is never
    consulted, which is exactly the sort of silent no-op a test must not be
    built on.
    """

    def __init__(self, on_next: Any) -> None:
        super().__init__()
        self._on_next = on_next

    async def __anext__(self) -> Any:
        self._on_next()
        self.closed = True
        raise StopAsyncIteration


class _AsyncCM:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeClientSession:
    """Just enough of ``aiohttp.ClientSession`` to drive ``_run_one``."""

    def __init__(self, sockets: list[_FakeSocket]) -> None:
        self._sockets = sockets

    async def __aenter__(self) -> _FakeClientSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def ws_connect(self, href: str) -> _AsyncCM:
        socket = self._sockets.pop(0) if self._sockets else _FakeSocket()
        return _AsyncCM(socket)


class TestForceDroppedSubscriber:
    """What a subscriber does when the switch closes its socket underneath it.

    Note what is NOT claimed here. "A clean close cannot accumulate towards a
    failover" is true, but it is guaranteed by ``failures = 0`` on every
    successful connect, not by anything this change added -- a mutation that
    counts clean closes as failures still passes, because the next successful
    connect resets the count. Asserting it here would have looked like coverage
    and tested nothing.
    """

    @pytest.mark.asyncio
    async def test_it_reconnects_to_the_new_registry_without_waiting(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The reconnect is immediate, and aimed at the newly selected registry.

        The backoff a subscriber has earned belongs to the registry that failed
        it. Serving that wait out before contacting a registry which never
        failed anything would extend the gap for no reason -- and it is the
        subscriber's own kind that stays empty meanwhile.
        """
        selector = _selector()
        client = _client(selector, distributed=False)
        original = selector.current

        # The order of connects and sleeps is the observable being tested, so
        # sleeping is recorded rather than performed.
        timeline: list[str] = []
        monkeypatch.setattr(rds_websocket, "random",
                            SimpleNamespace(random=lambda: 0.0))

        async def _record_sleep(_seconds: float) -> None:
            timeline.append("sleep")

        monkeypatch.setattr(rds_websocket, "asyncio", SimpleNamespace(
            sleep=_record_sleep, CancelledError=asyncio.CancelledError,
        ))

        # The first socket simulates ``_drop_stale_sockets`` landing mid-consume:
        # the selection moves and this connection is closed underneath it.
        dropped = _SwitchingSocket(lambda: selector.fail(original))
        sockets: list[_FakeSocket] = [dropped]
        monkeypatch.setattr(rds_websocket, "aiohttp", SimpleNamespace(
            TCPConnector=lambda **kw: None,
            ClientTimeout=lambda **kw: None,
            ClientSession=lambda **kw: _FakeClientSession(sockets),
            WSMsgType=aiohttp.WSMsgType,
        ))

        contacted: list[str] = []

        async def _fake_subscription(
            session: Any, resource_path: str, cfg: Any = None,
        ) -> str:
            contacted.append(cfg.query_host)
            timeline.append(f"connect:{cfg.query_host}")
            return "ws://fake/subscription"

        monkeypatch.setattr(client, "_create_subscription", _fake_subscription)

        await client._run_one(
            _RunningDG(6), cast(ResourceCache, _FakeCache()),
            "/senders", "sender",
        )

        assert selector.failover_count == 1, (
            "the subscriber must not advance the selection itself"
        )
        assert len(contacted) >= 2, "it should have reconnected"
        assert contacted[0] == original.host
        assert contacted[1] == selector.current.host, (
            "the reconnect must target the registry selected now"
        )
        assert timeline[:2] == [f"connect:{original.host}",
                                f"connect:{selector.current.host}"], (
            "a superseded connection must be replaced without a backoff wait"
        )


class TestSupersededConnections:
    """A closed socket does not stop a frame already in flight."""

    @pytest.mark.asyncio
    async def test_a_grain_from_a_superseded_connection_is_ignored(self) -> None:
        selector = _selector()
        client = _client(selector, distributed=False)
        cache = _FakeCache()
        ws = _FakeSocket((_text(_GRAIN),))

        epoch = client._generation()
        selector.fail(selector.current)          # the selection moves...
        await client._consume_grains(
            cast(aiohttp.ClientWebSocketResponse, ws),
            cast(ResourceCache, cache), "sender", _RunningDG(99), epoch,
        )

        assert cache.calls == [], "a grain from the abandoned registry was applied"

    @pytest.mark.asyncio
    async def test_a_grain_on_the_current_generation_is_applied(self) -> None:
        """The negative control: the guard must not reject everything."""
        client = _client(_selector(), distributed=False)
        cache = _FakeCache()
        ws = _FakeSocket((_text(_GRAIN),))

        await client._consume_grains(
            cast(aiohttp.ClientWebSocketResponse, ws),
            cast(ResourceCache, cache), "sender", _RunningDG(99),
            client._generation(),
        )

        assert cache.calls == [("upsert", "sender", 1)]

    @pytest.mark.asyncio
    async def test_a_clustered_client_ignores_the_epoch(self) -> None:
        """Clustered members share state, so a moved selection changes nothing."""
        selector = _selector()
        client = _client(selector, distributed=True)
        cache = _FakeCache()
        ws = _FakeSocket((_text(_GRAIN),))

        epoch = client._generation()
        selector.fail(selector.current)
        await client._consume_grains(
            cast(aiohttp.ClientWebSocketResponse, ws),
            cast(ResourceCache, cache), "sender", _RunningDG(99), epoch,
        )

        assert cache.calls == [("upsert", "sender", 1)]
