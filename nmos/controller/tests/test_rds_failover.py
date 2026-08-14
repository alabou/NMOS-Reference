# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Controller-side registry failover.

A system test found these behaviours; these tests keep them. The system test
originally reported a FALSE PASS (it matched two unrelated log lines), so the
behaviour was briefly believed broken and then briefly believed proven on bad
evidence. Unit coverage here means neither mistake can hide again.
"""

from __future__ import annotations

import aiohttp
import pytest

from nmos.controller.rds_websocket import MAX_BACKOFF_S, RdsWebSocketClient
from nmos.rds_targets import RegistrySelector, build_targets, target_from_scalars

DEFAULT = target_from_scalars(
    host="", registration_port=8447, query_port=8446, ws_port=8448, tls=False,
)


def _selector(n: int = 3) -> RegistrySelector:
    return RegistrySelector(
        build_targets([f"host=10.0.0.{i}" for i in range(1, n + 1)], DEFAULT),
    )


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
        client = RdsWebSocketClient(DEFAULT, s)
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
        client = RdsWebSocketClient(DEFAULT, s)
        before = client._current()[1]
        s.fail(s.current)
        after = client._current()[1]
        assert before.query_host != after.query_host

    def test_without_a_selector_it_stays_pinned(self) -> None:
        """Back-compat: no selector means the old single-registry behaviour."""
        client = RdsWebSocketClient(DEFAULT)
        target, cfg = client._current()
        assert target is None
        assert cfg is DEFAULT


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
    """

    def test_distributed_flag_defaults_off(self) -> None:
        """Fail in the recoverable direction: a refetch, not phantom rows."""
        assert RdsWebSocketClient(DEFAULT)._distributed is False

    @pytest.mark.parametrize("distributed", [True, False])
    def test_flag_is_carried(self, distributed: bool) -> None:
        client = RdsWebSocketClient(DEFAULT, _selector(), distributed=distributed)
        assert client._distributed is distributed
