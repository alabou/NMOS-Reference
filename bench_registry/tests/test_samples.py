# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""``Samples`` wall-clock accounting, and the defect that made four phases one.

The bug these pin: ``phase_cold_burst`` builds four ``Samples`` -- node,
device, sender, receiver -- and used to stamp ``started`` on all four before
the burst and ``finished`` on all four after it. Since ``rate = count / wall``,
that divided a single wall time into four different counts, so the four
reported rates were one measurement in four disguises. Measured against
nmos-cpp it read as 54 / 107 / 322 / 322 requests per second for the four
types; the wall clock behind all four was the same 148.9 ms.

The relative comparison between implementations survived (the divisor was
equally wrong on both sides), but no individual figure was a registration rate,
and none of them belonged in an acceptance criterion.
"""

from __future__ import annotations

import pytest

from bench_registry.loadgen import Samples


class TestWallClockWindow:
    """``mark`` widens a phase's window from the requests themselves."""

    def test_an_unmarked_phase_reports_no_window(self) -> None:
        samples = Samples("empty")
        assert samples.count == 0
        # `wall` floors at 1e-9 rather than dividing by zero, so an empty
        # phase reports an absurd rate rather than raising. That is deliberate
        # -- a benchmark must not crash on a phase that made no requests --
        # but it means `count` is what says whether a phase ran.
        assert samples.wall == pytest.approx(1e-9)

    def test_the_first_mark_opens_the_window(self) -> None:
        samples = Samples("one")
        samples.record(0.010, 201)
        samples.mark(100.0, 100.010)
        assert samples.started == 100.0
        assert samples.finished == 100.010
        assert samples.wall == pytest.approx(0.010)
        assert samples.rate == pytest.approx(100.0)

    def test_later_marks_widen_in_both_directions(self) -> None:
        samples = Samples("three")
        for elapsed in (0.010, 0.010, 0.010):
            samples.record(elapsed, 201)
        samples.mark(100.0, 100.010)
        samples.mark(100.020, 100.030)
        # Out-of-order completion: this request began BEFORE the first one we
        # marked. Concurrent issue makes that ordinary, and a window that only
        # ever moved forwards would silently exclude it.
        samples.mark(99.990, 100.005)

        assert samples.started == 99.990
        assert samples.finished == 100.030
        assert samples.wall == pytest.approx(0.040)
        assert samples.rate == pytest.approx(75.0)

    def test_a_phase_that_ran_longer_reports_a_lower_rate(self) -> None:
        """The property the defect destroyed: rate tracks this phase's span."""
        quick = Samples("quick")
        slow = Samples("slow")
        for _ in range(10):
            quick.record(0.001, 201)
            slow.record(0.001, 201)
        quick.mark(0.0, 0.100)
        slow.mark(0.0, 1.000)

        assert quick.rate == pytest.approx(100.0)
        assert slow.rate == pytest.approx(10.0)
        assert quick.rate > slow.rate


class TestInterleavedPhasesDoNotShareAWindow:
    """The regression guard for the cold-burst defect itself."""

    def test_two_phases_issued_together_keep_separate_windows(self) -> None:
        # Senders and receivers are issued from one `gather`, so their windows
        # overlap -- which is correct and expected. What must NOT happen is
        # the two reporting an identical wall time.
        senders = Samples("sender")
        receivers = Samples("receiver")

        # 4 senders across 40 ms; 2 receivers across 10 ms, inside that span.
        for start in (0.000, 0.010, 0.020, 0.030):
            senders.record(0.010, 201)
            senders.mark(start, start + 0.010)
        for start in (0.005, 0.015):
            receivers.record(0.005, 201)
            receivers.mark(start, start + 0.005)

        assert senders.wall == pytest.approx(0.040)
        assert receivers.wall == pytest.approx(0.015)
        assert senders.wall != receivers.wall
        assert senders.rate == pytest.approx(100.0)
        assert receivers.rate == pytest.approx(133.333, rel=1e-3)

    def test_the_old_shared_stamp_would_have_reported_one_measurement_twice(
        self,
    ) -> None:
        """Demonstrates the defect, so the fix cannot be reverted unnoticed.

        Stamping both phases from one pair of timestamps is what the harness
        used to do. Under that scheme the two rates are pinned to the same
        divisor, so their ratio is purely the ratio of their COUNTS -- it
        carries no information about either registry.
        """
        senders = Samples("sender")
        receivers = Samples("receiver")
        for _ in range(48):
            senders.record(0.010, 201)
        for _ in range(16):
            receivers.record(0.002, 201)

        # The old code path, reproduced:
        for shared in (senders, receivers):
            shared.started = 0.0
            shared.finished = 0.149

        assert senders.wall == receivers.wall
        assert senders.rate / receivers.rate == pytest.approx(48 / 16)

        # The fix: each window comes from that phase's own requests, and the
        # ratio stops being a restatement of the counts.
        rebuilt_senders = Samples("sender")
        rebuilt_receivers = Samples("receiver")
        for index in range(48):
            rebuilt_senders.record(0.010, 201)
            rebuilt_senders.mark(index * 0.001, index * 0.001 + 0.010)
        for index in range(16):
            rebuilt_receivers.record(0.002, 201)
            rebuilt_receivers.mark(index * 0.001, index * 0.001 + 0.002)

        assert rebuilt_senders.wall != rebuilt_receivers.wall
        assert rebuilt_senders.rate / rebuilt_receivers.rate != pytest.approx(
            48 / 16,
        )


class TestErrorAccounting:
    """``record`` counts failures; they must not vanish into the rate."""

    def test_error_statuses_are_counted_by_code(self) -> None:
        samples = Samples("mixed")
        samples.record(0.001, 201)
        samples.record(0.001, 400)
        samples.record(0.001, 400)
        samples.record(0.001, 503)

        assert samples.errors == {400: 2, 503: 1}
        # Failed requests still count toward the rate. That is intentional:
        # a registry that answers 503 quickly is not fast, and hiding the
        # refusals would make it look that way.
        assert samples.count == 4

    def test_percentiles_are_latencies_that_really_happened(self) -> None:
        samples = Samples("nearest-rank")
        for elapsed in (0.001, 0.002, 0.003, 0.004, 0.005):
            samples.record(elapsed, 201)

        # Nearest-rank, so every reported percentile is a member of the sample
        # set rather than an interpolation between two of them.
        assert samples.percentile(0.50) in {0.002, 0.003}
        assert samples.percentile(1.00) == 0.005
