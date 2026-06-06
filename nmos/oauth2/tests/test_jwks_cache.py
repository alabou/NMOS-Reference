# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.oauth2.jwks_cache — VSF TR-10-SEC §14.3.2.

The cache's timing behavior is fully testable thanks to the injected
``sleep`` / ``monotonic`` / ``random_jitter`` callbacks. Each test
substitutes those with fakes so the 23-hour refresh, 36-hour
invalidation, and 1-64s exponential backoff can be observed in
milliseconds.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import pytest

from nmos.oauth2 import JWKS, JSONWebKey
from nmos.oauth2.jwks_cache import (
    BACKOFF_INITIAL_SECONDS,
    BACKOFF_MAX_SECONDS,
    INVALIDATION_AGE_SECONDS,
    REFRESH_INTERVAL_SECONDS,
    REFRESH_JITTER_MAX_SECONDS,
    JWKSCache,
)


# ---------------------------------------------------------------------------
# Test fakes
# ---------------------------------------------------------------------------

class FakeClock:
    """Monotonic time that only advances when ``sleep`` is called.

    Acts as both the time source (via ``monotonic``) and the awaitable
    sleep (via ``sleep``). Records every sleep duration so tests can
    assert on the backoff/refresh schedule that the cache emitted.
    """

    def __init__(self, *, start: float = 0.0) -> None:
        self._now = start
        self.sleep_durations: list[float] = []

    def monotonic(self) -> float:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self.sleep_durations.append(seconds)
        self._now += seconds
        # Yield to the event loop so other coroutines can run.
        await asyncio.sleep(0)


def _make_jwks(n_keys: int = 1) -> JWKS:
    """Build a JWKS with ``n_keys`` minimal keys (no real signing material)."""
    keys = [
        JSONWebKey(
            kty="RSA", alg="RS256", kid=f"key-{i}",
            use="sig", n=f"AAA-{i}", e="AQAB", x="", y="",
        )
        for i in range(n_keys)
    ]
    return JWKS(keys=keys)


class FakeFetcher:
    """Programmable fetch coroutine with success/failure scripting.

    Each call pops the next entry off ``script``: a ``JWKS`` means
    "succeed with this keyset", an :class:`Exception` instance means
    "raise this".

    When the script is exhausted, every subsequent call repeats the
    *last* entry rather than blocking. Using ``await asyncio.sleep(...)``
    here would advance real wall-clock time and stall the test runner.
    """

    def __init__(self, script: list[JWKS | Exception]) -> None:
        assert script, "FakeFetcher needs at least one scripted outcome"
        self._script = list(script)
        self.calls = 0
        # ``_last_outcome`` is reassigned on every call; this initial
        # value satisfies type-checkers and is overwritten before any
        # exhausted-branch lookup can occur.
        self._last_outcome: JWKS | Exception = script[0]

    async def __call__(self) -> JWKS:
        self.calls += 1
        if self._script:
            outcome = self._script.pop(0)
        else:
            # Repeat the last outcome forever. Tests that want bounded
            # iteration must drive ``is_done`` themselves.
            outcome = self._last_outcome
        self._last_outcome = outcome
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _run_until_quiescent(
    cache: JWKSCache,
    *,
    max_iterations: int,
    fetcher: FakeFetcher,
) -> Callable[[], bool]:
    """Build an is_done callback that stops the loop after a bounded
    number of fetch invocations have happened.

    Using fetch-call count rather than a wall-clock deadline keeps the
    tests deterministic across CI environments.
    """
    def is_done() -> bool:
        return fetcher.calls >= max_iterations
    return is_done


async def _run_cache(
    cache: JWKSCache,
    fetcher: FakeFetcher,
    *,
    iterations: int,
) -> None:
    """Drive the cache until ``iterations`` fetches have been attempted."""
    await cache.run(is_done=_run_until_quiescent(
        cache, max_iterations=iterations, fetcher=fetcher,
    ))


# ---------------------------------------------------------------------------
# Constants — sanity-check the spec values are wired correctly
# ---------------------------------------------------------------------------

def test_spec_constants() -> None:
    assert REFRESH_INTERVAL_SECONDS == 23 * 3600
    assert REFRESH_JITTER_MAX_SECONDS == 3600
    assert INVALIDATION_AGE_SECONDS == 36 * 3600
    assert BACKOFF_INITIAL_SECONDS == 1.0
    assert BACKOFF_MAX_SECONDS == 64.0


# ---------------------------------------------------------------------------
# Happy path: first fetch succeeds, then refresh
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_fetch_success_invokes_on_update_with_keys() -> None:
    clock = FakeClock()
    jwks = _make_jwks()
    fetcher = FakeFetcher([jwks])
    updates: list[JWKS | None] = []

    cache = JWKSCache(
        fetch=fetcher, on_update=updates.append,
        sleep=clock.sleep, monotonic=clock.monotonic,
        random_jitter=lambda: 0.0,
    )
    await _run_cache(cache, fetcher, iterations=1)

    assert updates == [jwks]
    assert cache.jwks is jwks
    assert cache.consecutive_failures == 0


@pytest.mark.asyncio
async def test_refresh_delay_is_23h_plus_jitter() -> None:
    clock = FakeClock()
    fetcher = FakeFetcher([_make_jwks(), _make_jwks()])

    # Pin jitter to a known value so we can assert on the exact sleep.
    cache = JWKSCache(
        fetch=fetcher, on_update=lambda _j: None,
        sleep=clock.sleep, monotonic=clock.monotonic,
        random_jitter=lambda: 1234.0,
    )
    await _run_cache(cache, fetcher, iterations=2)

    # After the first successful fetch, the cache should sleep 23h + 1234s.
    assert clock.sleep_durations[0] == pytest.approx(REFRESH_INTERVAL_SECONDS + 1234.0)


@pytest.mark.asyncio
async def test_jitter_is_bounded_within_spec_range() -> None:
    """The default jitter source must produce values in [0, 3600s]."""
    clock = FakeClock()
    fetcher = FakeFetcher([_make_jwks(), _make_jwks(), _make_jwks()])
    cache = JWKSCache(
        fetch=fetcher, on_update=lambda _j: None,
        sleep=clock.sleep, monotonic=clock.monotonic,
        # default random_jitter
    )
    await _run_cache(cache, fetcher, iterations=3)
    for delay in clock.sleep_durations:
        assert REFRESH_INTERVAL_SECONDS <= delay <= REFRESH_INTERVAL_SECONDS + REFRESH_JITTER_MAX_SECONDS


# ---------------------------------------------------------------------------
# Backoff — 1, 2, 4, 8, ..., 64
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exponential_backoff_doubles_to_cap() -> None:
    """Consecutive failures must sleep 1, 2, 4, 8, 16, 32, 64, 64, ..."""
    clock = FakeClock()
    fetcher = FakeFetcher([RuntimeError("AS down")] * 10)
    cache = JWKSCache(
        fetch=fetcher, on_update=lambda _j: None,
        sleep=clock.sleep, monotonic=clock.monotonic,
        random_jitter=lambda: 0.0,
    )
    await _run_cache(cache, fetcher, iterations=10)

    expected_backoffs = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 64.0, 64.0, 64.0]
    assert clock.sleep_durations == expected_backoffs
    assert cache.consecutive_failures == 10


@pytest.mark.asyncio
async def test_failure_counter_resets_on_success() -> None:
    """Three failures, then a success, then another failure: backoff
    restarts at 1s after the success."""
    clock = FakeClock()
    jwks = _make_jwks()
    fetcher = FakeFetcher([
        RuntimeError("transient 1"),
        RuntimeError("transient 2"),
        RuntimeError("transient 3"),
        jwks,
        RuntimeError("after success"),
    ])
    cache = JWKSCache(
        fetch=fetcher, on_update=lambda _j: None,
        sleep=clock.sleep, monotonic=clock.monotonic,
        random_jitter=lambda: 0.0,
    )
    await _run_cache(cache, fetcher, iterations=5)

    # Sleeps: 1 (fail 1), 2 (fail 2), 4 (fail 3), 23h+0 (success), 1 (fail after success)
    assert clock.sleep_durations[:3] == [1.0, 2.0, 4.0]
    assert clock.sleep_durations[3] == REFRESH_INTERVAL_SECONDS
    assert clock.sleep_durations[4] == 1.0


# ---------------------------------------------------------------------------
# 36-hour invalidation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_36h_invalidation_emits_none_to_on_update() -> None:
    """After a successful fetch, if the next-successful-fetch never
    arrives, the cache must invalidate (emit None) after 36 hours of
    age — well past the 23h refresh + 13h grace period."""
    clock = FakeClock()
    jwks = _make_jwks()
    # Script: first fetch succeeds, then fail forever.
    fetcher = FakeFetcher([jwks] + [RuntimeError("AS down")] * 50)
    updates: list[JWKS | None] = []

    cache = JWKSCache(
        fetch=fetcher, on_update=updates.append,
        sleep=clock.sleep, monotonic=clock.monotonic,
        random_jitter=lambda: 0.0,
    )

    # Drive the loop long enough to age past 36h. Each iteration after
    # the first sleeps either the 23h refresh delay or a backoff. After
    # the first refresh-sleep (23h) the clock is at 23h. One more
    # failure sleep (1s) doesn't push us past 36h yet. We need enough
    # iterations to accumulate >36h on the monotonic clock — the 23h
    # refresh sleep happens once, plus enough backoff sleeps.
    # 23h + 13h = 36h; the backoff caps at 64s so 13*3600/64 ≈ 730
    # iterations to traverse. Cheat: bump the clock forward via a
    # custom fetch that advances time.
    iterations = 800
    await _run_cache(cache, fetcher, iterations=iterations)

    # Updates: first the keys, then somewhere along the way a None.
    assert updates[0] is jwks
    assert None in updates, "cache must invalidate after 36 hours"
    none_index = updates.index(None)
    # All updates after the invalidation must remain absent (the cache
    # doesn't try to "re-publish" None after it already invalidated).
    assert all(u is None for u in updates[none_index:])


@pytest.mark.asyncio
async def test_no_invalidation_before_36h() -> None:
    """A failure within the first 36h after a success must NOT
    invalidate. The Node must keep using the cached keys."""
    clock = FakeClock()
    jwks = _make_jwks()
    # Success, then one failure shortly after.
    fetcher = FakeFetcher([jwks, RuntimeError("transient")])
    updates: list[JWKS | None] = []

    cache = JWKSCache(
        fetch=fetcher, on_update=updates.append,
        sleep=clock.sleep, monotonic=clock.monotonic,
        random_jitter=lambda: 0.0,
    )

    await _run_cache(cache, fetcher, iterations=2)

    # Only the success update happened; no None invalidation yet.
    assert updates == [jwks]
    assert cache.jwks is jwks


# ---------------------------------------------------------------------------
# Initial-fetch-failure semantics: cache never publishes None initially
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failures_before_any_success_never_invalidate() -> None:
    """The spec requires fail-closed-until-initial-fetch — but that
    behavior is on the consumer (Node middleware refuses access when
    keys are None). The cache itself starts with jwks=None and never
    explicitly publishes None to on_update until after a real success
    is followed by a 36h gap. Until then, ``on_update`` is never
    called at all on failure."""
    clock = FakeClock()
    fetcher = FakeFetcher([RuntimeError("never works")] * 50)
    updates: list[JWKS | None] = []

    cache = JWKSCache(
        fetch=fetcher, on_update=updates.append,
        sleep=clock.sleep, monotonic=clock.monotonic,
        random_jitter=lambda: 0.0,
    )
    await _run_cache(cache, fetcher, iterations=50)

    # No on_update calls — the cache neither has keys to publish nor
    # has anything to invalidate. The consumer (Node) is responsible
    # for fail-closing on the initial None state.
    assert updates == []
    assert cache.jwks is None
