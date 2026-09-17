# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""In-memory realtime trace buffer for the registry's hot paths.

This exists so that a performance question can be answered with measurements
instead of a hypothesis. The rule it serves: record the **inputs that drove a
decision**, not only the outcome. A trace saying "CAS retried" is nearly
useless; one saying "CAS retried because the target's mod_revision compare
failed, believed 41, actual 47" names the cause.

Two things are recorded, and they answer different questions:

``Counter``/``Timer`` aggregates -- "is this slow, and how often?" Fixed cost,
always on, readable at any moment.

``TraceBuffer`` events -- "why was *that one* slow?" A bounded ring of recent
samples with their inputs attached. Bounded because an unbounded trace on a
registration storm is itself a performance problem.

Always on
---------
Sampling is not conditional on a debug flag. The failures worth diagnosing --
a fence that waited, a CAS that lost a race, a watch batch that arrived late --
are exactly the ones that do not reproduce on demand, so the instrumentation
has to already be running when they happen. The cost is one dataclass and a
deque append per event, on paths that are already doing network I/O.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator


class Event(Enum):
    """What is being measured.

    An enum rather than free-form strings so a typo cannot silently create a
    second metric that nobody is watching.
    """

    QUERY = "query"
    """One Query API handler, end to end."""

    LINEARIZABLE_READ = "linearizable_read"
    """The read half of a pre-validation fence."""

    FENCE_WAIT = "fence_wait"
    """Time spent waiting for the local view to catch up to a read revision."""

    CAS = "cas"
    """One compare-and-swap transaction against etcd."""

    CAS_RETRY = "cas_retry"
    """A transaction whose comparisons failed and had to be re-driven."""

    FAST_PATH_HIT = "fast_path_hit"
    """A speculative CAS that committed without a preceding read fence."""

    FAST_PATH_MISS = "fast_path_miss"
    """A speculative CAS whose compare failed, forcing the fenced path."""

    COMMIT_TO_WATCH = "commit_to_watch"
    """Commit revision to that revision being applied locally."""

    WATCH_BATCH = "watch_batch"
    """One revision group applied from the watch."""

    SUBSCRIPTION_FANOUT = "subscription_fanout"
    """Grains queued for one change."""

    PRELOAD = "preload"
    """A full fixed-revision snapshot load."""

    RESNAPSHOT = "resnapshot"
    """A snapshot rebuilt after compaction."""

    ETCD_RESTART = "etcd_restart"
    """The managed etcd child exited and was restarted."""

    BACKEND_STATE = "backend_state"
    """A backend state transition."""

    HEARTBEAT = "heartbeat"
    """One lease renewal."""

    MUTATION = "mutation"
    """One Registration-API mutation, end to end, in ``units`` round trips.

    Backend-neutral on purpose. "How many network round trips does a
    registration cost?" is the question the distributed backends most need
    answered, and it is the one question latency cannot answer: a fast round
    trip and a skipped round trip look identical on a quiet loopback rig and
    diverge completely on a real network. Counting them directly turns a
    design claim into a measurement.
    """


@dataclass
class Counter:
    """A count and, where meaningful, a duration distribution.

    Percentiles are computed from a bounded reservoir of recent samples rather
    than from every sample ever taken: the interesting question is almost always
    "how is it behaving *now*", and keeping everything would grow without bound
    in a long-running registry.
    """

    count: int = 0
    total_seconds: float = 0.0
    max_seconds: float = 0.0
    units_count: int = 0
    total_units: int = 0
    max_units: int = 0
    _samples: deque[float] = field(
        default_factory=lambda: deque(maxlen=1024), repr=False,
    )

    def record(
        self, seconds: float | None = None, units: int | None = None,
    ) -> None:
        self.count += 1
        if units is not None:
            self.units_count += 1
            self.total_units += units
            if units > self.max_units:
                self.max_units = units
        if seconds is None:
            return
        self.total_seconds += seconds
        if seconds > self.max_seconds:
            self.max_seconds = seconds
        self._samples.append(seconds)

    @property
    def mean_seconds(self) -> float:
        return self.total_seconds / self.count if self.count else 0.0

    @property
    def mean_units(self) -> float:
        """Average countable work per event -- round trips, entries, grains.

        Totalled rather than sampled into the reservoir: a mean over *every*
        event is what a design claim like "one round trip per registration" has
        to be checked against, and a bounded reservoir would silently answer
        for the last thousand instead of for the run.
        """
        return self.total_units / self.units_count if self.units_count else 0.0

    def percentile(self, fraction: float) -> float:
        """Nearest-rank percentile over the retained samples.

        Nearest-rank rather than interpolated: with a latency distribution the
        interpolated value between two samples is not a value the system ever
        actually produced, and "p99 = 41 ms" reads better when 41 ms really
        happened.
        """
        if not self._samples:
            return 0.0
        ordered = sorted(self._samples)
        index = max(0, min(len(ordered) - 1, round(fraction * len(ordered)) - 1))
        return ordered[index]


@dataclass(frozen=True)
class Sample:
    """One recorded event, with the inputs that produced it."""

    event: Event
    monotonic: float
    seconds: float | None
    detail: dict[str, Any]
    units: int | None = None

    def render(self) -> str:
        duration = "" if self.seconds is None else f" {self.seconds * 1e3:.3f}ms"
        countable = "" if self.units is None else f" x{self.units}"
        inputs = " ".join(f"{k}={v}" for k, v in self.detail.items())
        return f"{self.event.value}{duration}{countable} {inputs}".rstrip()


class RegistryMetrics:
    """Counters plus a bounded ring of recent samples.

    Args:
        trace_capacity: How many recent samples to retain. The default holds
            several seconds of a busy registration storm, which is the window
            that matters when reconstructing one.
    """

    __slots__ = ("_counters", "_trace", "_enabled")

    def __init__(self, *, trace_capacity: int = 4096) -> None:
        self._counters: dict[Event, Counter] = {}
        self._trace: deque[Sample] = deque(maxlen=trace_capacity)
        self._enabled = True

    # -- recording ------------------------------------------------------

    def counter(self, event: Event) -> Counter:
        existing = self._counters.get(event)
        if existing is None:
            existing = Counter()
            self._counters[event] = existing
        return existing

    def record(
        self,
        event: Event,
        seconds: float | None = None,
        *,
        units: int | None = None,
        **detail: Any,
    ) -> None:
        """Record one event with its decision-driving inputs.

        ``detail`` is deliberately free-form: what makes a CAS failure
        diagnosable is the compare that failed and the revisions involved, and
        what makes a fence wait diagnosable is the revision waited for versus
        the revision applied. Forcing those into a fixed schema would mean
        omitting whichever one the next investigation needs.

        ``units`` is the exception, and it is separate from ``detail`` because
        it is *aggregated* rather than merely retained. Detail lives only in
        the bounded ring, so a question about a whole benchmark run -- "how
        many round trips did the average registration take?" -- cannot be
        answered from it once the ring has turned over. Anything countable
        belongs here: round trips per mutation, entries per batch, grains per
        fan-out.
        """
        if not self._enabled:
            return
        self.counter(event).record(seconds, units)
        self._trace.append(
            Sample(
                event=event,
                monotonic=time.monotonic(),
                seconds=seconds,
                detail=detail,
                units=units,
            ),
        )

    def timer(self, event: Event, **detail: Any) -> _Timer:
        """Context manager timing a block and recording it on exit.

        Records on failure as well as success, tagged with the exception type:
        a path that is slow only when it errors is a real and easily missed
        shape, and dropping the sample on the error path would hide it.
        """
        return _Timer(self, event, detail)

    # -- reading --------------------------------------------------------

    def recent(
        self, event: Event | None = None, limit: int = 100,
    ) -> list[Sample]:
        """The most recent samples, newest last, optionally for one event."""
        if event is None:
            samples = list(self._trace)
        else:
            samples = [s for s in self._trace if s.event is event]
        return samples[-limit:]

    def __iter__(self) -> Iterator[tuple[Event, Counter]]:
        return iter(self._counters.items())

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        """Machine-readable counters, for the benchmark harness."""
        return {
            event.value: {
                "count": counter.count,
                "mean_ms": counter.mean_seconds * 1e3,
                "p50_ms": counter.percentile(0.50) * 1e3,
                "p95_ms": counter.percentile(0.95) * 1e3,
                "p99_ms": counter.percentile(0.99) * 1e3,
                "max_ms": counter.max_seconds * 1e3,
                # Always present, zero where nothing countable was recorded,
                # so the benchmark harness can read one shape for every event
                # rather than branching on which ones carry units.
                "total_units": counter.total_units,
                "mean_units": counter.mean_units,
                "max_units": counter.max_units,
            }
            for event, counter in sorted(
                self._counters.items(), key=lambda item: item[0].value,
            )
        }

    def render(self) -> str:
        """Human-readable counter table."""
        if not self._counters:
            return "registry metrics: nothing recorded yet"
        lines = [
            f"{'event':22} {'count':>8} {'mean':>9} {'p50':>9} "
            f"{'p95':>9} {'p99':>9} {'max':>9} {'per-op':>9}",
        ]
        for event, counter in sorted(
            self._counters.items(), key=lambda item: item[0].value,
        ):
            # Blank rather than 0.00 where nothing countable was recorded: a
            # zero would read as "measured, and it was none", which is a
            # different and much more alarming claim than "not measured here".
            per_op = (
                f"{counter.mean_units:>9.2f}" if counter.units_count else " " * 9
            )
            lines.append(
                f"{event.value:22} {counter.count:>8} "
                f"{counter.mean_seconds * 1e3:>8.3f}m "
                f"{counter.percentile(0.50) * 1e3:>8.3f}m "
                f"{counter.percentile(0.95) * 1e3:>8.3f}m "
                f"{counter.percentile(0.99) * 1e3:>8.3f}m "
                f"{counter.max_seconds * 1e3:>8.3f}m {per_op}",
            )
        return "\n".join(lines)

    @property
    def fast_path_hit_rate(self) -> float:
        """Share of speculative transactions that committed without a fence.

        The single number that most directly predicts registration latency: a
        hit skips one full consensus round trip. A rate that is not
        overwhelmingly high in steady state is itself the finding -- it means
        members are contending on the same resources, which the per-Node key
        layout is supposed to make impossible.
        """
        hits = self._counters.get(Event.FAST_PATH_HIT)
        misses = self._counters.get(Event.FAST_PATH_MISS)
        hit_count = hits.count if hits else 0
        miss_count = misses.count if misses else 0
        total = hit_count + miss_count
        return hit_count / total if total else 0.0

    @property
    def round_trips_per_mutation(self) -> float:
        """Measured network round trips per Registration-API mutation.

        The headline number for any distributed backend, and the one the
        acceptance criteria are written against. Latency cannot substitute for
        it: every distributed benchmark in this repository runs on loopback,
        where a round trip costs almost nothing, so a design that takes three
        of them and one that takes one look nearly identical here and diverge
        by a factor of three on a real network.

        Zero when nothing has been recorded, which for a standalone registry
        is the correct answer rather than a missing one.
        """
        counter = self._counters.get(Event.MUTATION)
        return counter.mean_units if counter else 0.0

    def clear(self) -> None:
        self._counters.clear()
        self._trace.clear()


class _Timer:
    """Times a block; records duration and any exception type."""

    __slots__ = ("_metrics", "_event", "_detail", "_started", "_units")

    def __init__(
        self, metrics: RegistryMetrics, event: Event, detail: dict[str, Any],
    ) -> None:
        self._metrics = metrics
        self._event = event
        self._detail = detail
        self._started = 0.0
        self._units: int | None = None

    def __enter__(self) -> _Timer:
        self._started = time.perf_counter()
        return self

    def note(self, **detail: Any) -> None:
        """Attach inputs discovered inside the block.

        The interesting inputs -- which compare failed, which revision was
        waited for -- are usually only known partway through, so they are
        added here rather than at construction.
        """
        self._detail.update(detail)

    def count(self, units: int) -> None:
        """Record how much countable work the block did.

        Absolute, not incremental, so a retry loop that calls it once per
        attempt reports the attempt count rather than a running total that
        depends on where the loop happened to exit.
        """
        self._units = units

    def __exit__(self, exc_type: Any, *_rest: Any) -> None:
        elapsed = time.perf_counter() - self._started
        if exc_type is not None:
            self._detail["failed"] = exc_type.__name__
        self._metrics.record(
            self._event, elapsed, units=self._units, **self._detail,
        )
