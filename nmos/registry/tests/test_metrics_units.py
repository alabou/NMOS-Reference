# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The ``units`` dimension: counting work, not just timing it.

Latency alone cannot answer the question the distributed backends most need
answered. Every distributed benchmark in this repository runs on loopback,
where a network round trip costs almost nothing -- so a mutation that takes
three of them and one that takes one produce nearly the same p50 here, and
diverge by a factor of three on a real switch. The design claim is about the
*count*, so the count is measured.

``units`` is aggregated on the counter rather than left in ``detail`` because
``detail`` lives only in the bounded ring. "What did the average registration
cost across this benchmark run?" cannot be answered from the last 4096 samples
of a run that made a hundred thousand.
"""

from __future__ import annotations

import pytest

from nmos.registry.metrics import Counter, Event, RegistryMetrics


class TestCounterUnits:
    def test_units_are_ignored_when_not_supplied(self) -> None:
        counter = Counter()
        counter.record(0.001)
        counter.record(0.002)
        assert counter.count == 2
        assert counter.units_count == 0
        assert counter.mean_units == 0.0

    def test_units_aggregate_independently_of_duration(self) -> None:
        counter = Counter()
        counter.record(0.001, units=1)
        counter.record(0.002, units=3)
        assert counter.count == 2
        assert counter.units_count == 2
        assert counter.total_units == 4
        assert counter.max_units == 3
        assert counter.mean_units == pytest.approx(2.0)

    def test_a_zero_unit_event_still_counts_toward_the_mean(self) -> None:
        """A mutation answered locally cost zero round trips, not no data.

        Dropping it from the denominator would let a backend improve its
        average by rejecting more requests without touching the network.
        """
        counter = Counter()
        counter.record(None, units=0)
        counter.record(None, units=2)
        assert counter.units_count == 2
        assert counter.mean_units == pytest.approx(1.0)

    def test_untimed_events_may_still_carry_units(self) -> None:
        counter = Counter()
        counter.record(None, units=5)
        assert counter.mean_seconds == 0.0
        assert counter.mean_units == pytest.approx(5.0)


class TestRecordingUnits:
    def test_record_threads_units_to_the_counter_and_the_trace(self) -> None:
        metrics = RegistryMetrics()
        metrics.record(Event.MUTATION, 0.004, units=2, verb="register")

        assert metrics.counter(Event.MUTATION).mean_units == pytest.approx(2.0)
        sample = metrics.recent(Event.MUTATION)[-1]
        assert sample.units == 2
        assert sample.detail == {"verb": "register"}

    def test_units_do_not_leak_into_detail(self) -> None:
        """They are separate concepts; conflating them would double-report."""
        metrics = RegistryMetrics()
        metrics.record(Event.MUTATION, None, units=3)
        assert metrics.recent(Event.MUTATION)[-1].detail == {}

    def test_render_shows_the_count_in_the_sample_line(self) -> None:
        metrics = RegistryMetrics()
        metrics.record(Event.MUTATION, 0.002, units=2, verb="register")
        rendered = metrics.recent(Event.MUTATION)[-1].render()
        assert "x2" in rendered
        assert "verb=register" in rendered


class TestTimerCount:
    def test_the_timer_records_units_set_inside_the_block(self) -> None:
        metrics = RegistryMetrics()
        with metrics.timer(Event.MUTATION, verb="register") as timer:
            timer.count(2)
        assert metrics.counter(Event.MUTATION).mean_units == pytest.approx(2.0)

    def test_count_is_absolute_not_incremental(self) -> None:
        """A retry loop reports its attempt count, not a running total."""
        metrics = RegistryMetrics()
        with metrics.timer(Event.MUTATION) as timer:
            timer.count(1)
            timer.count(2)
            timer.count(3)
        assert metrics.counter(Event.MUTATION).max_units == 3

    def test_units_survive_an_exception(self) -> None:
        """A mutation that failed after three round trips still spent three."""
        metrics = RegistryMetrics()
        with pytest.raises(RuntimeError):
            with metrics.timer(Event.MUTATION) as timer:
                timer.count(3)
                raise RuntimeError("commit lost")

        counter = metrics.counter(Event.MUTATION)
        assert counter.mean_units == pytest.approx(3.0)
        assert metrics.recent(Event.MUTATION)[-1].detail["failed"] == (
            "RuntimeError"
        )


class TestRoundTripsPerMutation:
    def test_zero_when_nothing_was_recorded(self) -> None:
        """Correct for a standalone registry, not a missing measurement."""
        assert RegistryMetrics().round_trips_per_mutation == 0.0

    def test_it_averages_over_every_mutation(self) -> None:
        metrics = RegistryMetrics()
        for units in (1, 1, 1, 3):
            metrics.record(Event.MUTATION, 0.001, units=units)
        assert metrics.round_trips_per_mutation == pytest.approx(1.5)

    def test_it_is_unaffected_by_other_events(self) -> None:
        metrics = RegistryMetrics()
        metrics.record(Event.MUTATION, 0.001, units=1)
        metrics.record(Event.WATCH_BATCH, 0.001, units=99)
        metrics.record(Event.QUERY, 0.001)
        assert metrics.round_trips_per_mutation == pytest.approx(1.0)


class TestSnapshotShape:
    def test_every_event_reports_the_same_keys(self) -> None:
        """The harness reads one shape; it must not branch per event."""
        metrics = RegistryMetrics()
        metrics.record(Event.QUERY, 0.002)
        metrics.record(Event.MUTATION, 0.004, units=2)
        snapshot = metrics.snapshot()

        assert snapshot["query"].keys() == snapshot["mutation"].keys()
        assert snapshot["query"]["total_units"] == 0
        assert snapshot["query"]["mean_units"] == 0.0
        assert snapshot["mutation"]["mean_units"] == pytest.approx(2.0)
        assert snapshot["mutation"]["max_units"] == 2

    def test_the_existing_keys_are_untouched(self) -> None:
        metrics = RegistryMetrics()
        metrics.record(Event.QUERY, 0.002)
        entry = metrics.snapshot()["query"]
        assert entry["count"] == 1
        assert entry["p50_ms"] == pytest.approx(2.0)

    def test_render_leaves_the_column_blank_where_nothing_was_counted(
        self,
    ) -> None:
        """Blank, not 0.00 -- 'not measured' is not 'measured, and it was none'."""
        metrics = RegistryMetrics()
        metrics.record(Event.QUERY, 0.002)
        metrics.record(Event.MUTATION, 0.004, units=2)
        lines = {
            line.split()[0]: line for line in metrics.render().splitlines()[1:]
        }
        assert lines["mutation"].rstrip().endswith("2.00")
        assert not lines["query"].rstrip().endswith("0.00")
