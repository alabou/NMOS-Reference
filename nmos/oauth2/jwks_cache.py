# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""TR-10-SEC §14.3.2 / NMOS-IS-10 OAuth 2.0 Public-Key cache.

Reference-node implements the spec's full lifecycle for the OAuth 2.0
Authorization Server's signing keys:

  - Initial fetch at boot/restart, and on explicit administrative
    request.
  - Periodic refresh every **23 hours plus a random 0–3600 s jitter**,
    so a fleet of devices doesn't stampede the AS together.
  - **36-hour hard invalidation** of any previously-fetched key set:
    after that long without a successful refresh, all Bearer-token
    access is refused (fail-closed) until a fresh key set arrives.
  - **Exponential backoff** between fetch failures, doubling from 1 s
    up to a cap of 64 s — per IS-10's "SHOULD use an exponential
    backoff, from 1 to 64 seconds".
  - **Fail-closed on initial fetch failure**: until the first set of
    keys arrives the cache reports ``None`` to the Node, and the
    Node's bearer-validation middleware refuses every authenticated
    request.

The class is structured around dependency-injected callbacks
(``fetch``, ``on_update``, ``sleep``, ``monotonic``, ``random_jitter``)
so the timing behaviour can be exercised in unit tests without
sleeping for 23 hours.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Awaitable, Callable, Optional

from nmos.oauth2 import JWKS


# ---------------------------------------------------------------------------
# Spec-derived timing constants — TR-10-SEC §14.3.2 / IS-10 "Public Keys"
# ---------------------------------------------------------------------------

REFRESH_INTERVAL_SECONDS: int = 23 * 3600
"""Minimum delay between successful fetches, before adding jitter."""

REFRESH_JITTER_MAX_SECONDS: int = 3600
"""Upper bound of the random additive jitter on top of the refresh interval."""

INVALIDATION_AGE_SECONDS: int = 36 * 3600
"""Maximum age of a cached key set; older keys are discarded and
access is refused until a fresh set is fetched."""

BACKOFF_INITIAL_SECONDS: float = 1.0
"""Backoff for the first failed retry."""

BACKOFF_MAX_SECONDS: float = 64.0
"""Backoff ceiling — doubling stops here."""


# Typed aliases for the injection points; these mirror the standard-library
# signatures so production callers can pass ``asyncio.sleep`` / ``time.monotonic``
# verbatim.
_FetchFn = Callable[[], Awaitable[JWKS]]
_OnUpdateFn = Callable[[Optional[JWKS]], None]
_SleepFn = Callable[[float], Awaitable[None]]
_MonotonicFn = Callable[[], float]
_JitterFn = Callable[[], float]
_IsDoneFn = Callable[[], bool]


def _default_jitter() -> float:
    """Default jitter source. Uniform on [0, REFRESH_JITTER_MAX_SECONDS]."""
    return random.uniform(0.0, float(REFRESH_JITTER_MAX_SECONDS))


# ---------------------------------------------------------------------------
# JWKSCache
# ---------------------------------------------------------------------------

class JWKSCache:
    """Periodic JWKS fetcher with TR-10-SEC §14.3.2 lifecycle.

    Usage::

        async def fetch() -> JWKS:
            return await discover_jwks(scheme=..., host=..., port=..., client=session)

        def on_update(jwks: JWKS | None) -> None:
            # ``None`` means "invalidate" — set the Node's keyset to empty
            # so the middleware refuses every authenticated request.
            node.set_oauth2_public_keys(jwks)

        cache = JWKSCache(fetch=fetch, on_update=on_update)
        await cache.run(is_done=lambda: dispatch_group.is_done)

    The class does NOT own the HTTP session, the SSL context, or any
    bookkeeping outside the keys themselves — those belong to the
    caller. The cache only enforces the spec's timing rules.
    """

    def __init__(
        self,
        *,
        fetch: _FetchFn,
        on_update: _OnUpdateFn,
        sleep: _SleepFn | None = None,
        monotonic: _MonotonicFn | None = None,
        random_jitter: _JitterFn | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._fetch = fetch
        self._on_update = on_update
        # The defaults are resolved at __init__ time rather than at the
        # signature so tests can patch ``asyncio.sleep`` etc. globally
        # before the cache is constructed.
        import asyncio
        self._sleep: _SleepFn = sleep if sleep is not None else asyncio.sleep
        self._monotonic: _MonotonicFn = (
            monotonic if monotonic is not None else time.monotonic
        )
        self._random_jitter: _JitterFn = (
            random_jitter if random_jitter is not None else _default_jitter
        )
        self._logger = logger if logger is not None else logging.getLogger(__name__)

        # Cache state
        self._last_fetch: float | None = None
        """Monotonic timestamp of the most recent successful fetch, or
        ``None`` if no fetch has succeeded yet (fail-closed initial)."""

        self._consecutive_failures: int = 0
        """Number of consecutive failures since the last success. Drives
        exponential backoff; resets to 0 on every successful fetch."""

        self._jwks: JWKS | None = None
        """The current cached keyset, or ``None`` if uninitialised or
        invalidated."""

    # ----- Inspection -----

    @property
    def jwks(self) -> JWKS | None:
        """The currently cached keyset, or ``None`` if not yet fetched
        successfully or invalidated by age."""
        return self._jwks

    @property
    def last_fetch(self) -> float | None:
        return self._last_fetch

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    # ----- Run loop -----

    async def run(self, is_done: _IsDoneFn) -> None:
        """Run the refresh loop until ``is_done()`` returns True.

        ``is_done`` is polled at every loop boundary; for nmos-reference
        this is typically ``lambda: dispatch_group.is_done``. The loop
        exits cleanly on cancellation as well — both ``self._fetch``
        and ``self._sleep`` are allowed to raise
        :class:`asyncio.CancelledError` which propagates out.
        """
        while not is_done():
            try:
                jwks = await self._fetch()
            except _CANCELLED_EXC:
                raise
            except Exception as exc:  # pylint: disable=broad-except
                self._handle_fetch_failure(exc)
                try:
                    await self._sleep(self._next_backoff())
                except _CANCELLED_EXC:
                    raise
                continue

            self._handle_fetch_success(jwks)
            try:
                await self._sleep(self._next_refresh_delay())
            except _CANCELLED_EXC:
                raise

    # ----- Internals -----

    def _handle_fetch_success(self, jwks: JWKS) -> None:
        self._jwks = jwks
        self._last_fetch = self._monotonic()
        self._consecutive_failures = 0
        self._on_update(jwks)
        self._logger.info("JWKS: fetched %d public key(s)", len(jwks.keys))

    def _handle_fetch_failure(self, exc: BaseException) -> None:
        self._consecutive_failures += 1
        self._logger.warning(
            "JWKS: fetch failed (attempt %d): %s",
            self._consecutive_failures, exc,
        )
        # If the last successful fetch is older than the invalidation
        # window, drop the cached keys and tell the Node to refuse
        # access. Spec: "shall invalidate the Public Keys from a
        # previous fetch / update operation 36 hours after obtaining
        # them".
        if self._last_fetch is not None:
            age = self._monotonic() - self._last_fetch
            if age > INVALIDATION_AGE_SECONDS:
                self._logger.warning(
                    "JWKS: invalidating keys after %.0fs without refresh "
                    "(threshold %ds)", age, INVALIDATION_AGE_SECONDS,
                )
                self._jwks = None
                self._last_fetch = None
                self._on_update(None)

    def _next_refresh_delay(self) -> float:
        """23h + uniform random jitter in [0, 3600s]."""
        return float(REFRESH_INTERVAL_SECONDS) + self._random_jitter()

    def _next_backoff(self) -> float:
        """Exponential backoff: 1, 2, 4, 8, 16, 32, 64, 64, 64, ..."""
        # ``consecutive_failures`` was already incremented in
        # ``_handle_fetch_failure`` before this is called.
        exponent = self._consecutive_failures - 1  # 0 on first failure
        candidate: float = BACKOFF_INITIAL_SECONDS * (2 ** exponent)
        return float(min(BACKOFF_MAX_SECONDS, candidate))


# Resolve the CancelledError class once at import time. Older asyncio
# implementations placed it under ``concurrent.futures``; this lookup
# tolerates both.
try:
    import asyncio as _asyncio
    _CANCELLED_EXC: type[BaseException] = _asyncio.CancelledError
except ImportError:  # pragma: no cover
    import concurrent.futures as _futures
    _CANCELLED_EXC = _futures.CancelledError
