# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for nmos.controller.reservation."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nmos.controller.api_client import (
    RemoteCallResult,
    RemoteNodeClient,
    _parse_retry_after,
)
from nmos.controller.auth import AdminSessionStore
from nmos.controller.reservation import (
    ReservationError,
    ReservationLocked,
    SessionStore,
)


#: Session Lifetime of the hypothetical long-lived Node used by the
#: ``Retry-After`` tests: 24 hours, the specified maximum, and the case the
#: derivation exists for. A Node running the 60-minute default never produces
#: a 425 against a conforming client — the client renews at 30 minutes, past
#: the Node's 20-minute gate — so the interesting behaviour only appears once
#: the Node is configured longer than the minimum a client must assume.
_NODE_LIFETIME: float = 86400.0


def _new_store() -> tuple[SessionStore, RemoteNodeClient, AdminSessionStore]:
    """Build a SessionStore with a stubbed client for unit tests."""
    client = RemoteNodeClient(ssl_context=None)
    # Stub every reservation-service method so we don't hit a network.
    client.acquire_exclusive = AsyncMock()  # type: ignore[method-assign]
    client.renew_exclusive = AsyncMock()  # type: ignore[method-assign]
    client.release_exclusive = AsyncMock()  # type: ignore[method-assign]
    client.keepalive_exclusive = AsyncMock()  # type: ignore[method-assign]
    admin_store = AdminSessionStore()
    return SessionStore(client, admin_store), client, admin_store


class TestAcquire:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("session-aaa")

        stub_acquire: Any = client.acquire_exclusive
        stub_acquire.return_value = RemoteCallResult(
            status=200, body="bearer-xyz",
        )

        token = await store.acquire(admin, "dev1", "https://n/exclusive/v1.0/", oauth2_on_remote=False)
        assert token == "bearer-xyz"
        assert "dev1" in admin.acquired_nodes
        assert store.current_token(admin, "dev1") == "bearer-xyz"

    @pytest.mark.asyncio
    async def test_re_acquire_returns_cached(self) -> None:
        """Calling acquire twice for the same (admin, device) should
        not re-hit the remote — return the cached token."""
        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("session-aaa")
        stub_acquire: Any = client.acquire_exclusive
        stub_acquire.return_value = RemoteCallResult(
            status=200, body="first-token",
        )

        token1 = await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        token2 = await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        assert token1 == token2 == "first-token"
        # Exactly one outbound acquire call.
        assert stub_acquire.call_count == 1

    @pytest.mark.asyncio
    async def test_locked_raises(self) -> None:
        """423 Locked from the Node → ReservationLocked."""
        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_acquire.return_value = RemoteCallResult(
            status=423, body={"error": "locked", "debug": "held by another"},
        )

        with pytest.raises(ReservationLocked):
            await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        assert "dev1" not in admin.acquired_nodes

    @pytest.mark.asyncio
    async def test_5xx_raises_reservation_error(self) -> None:
        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_acquire.return_value = RemoteCallResult(
            status=500, body={"error": "nope"},
        )

        with pytest.raises(ReservationError):
            await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)


class TestRelease:
    @pytest.mark.asyncio
    async def test_release_drops_and_calls_remote(self) -> None:
        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_acquire.return_value = RemoteCallResult(
            status=200, body="t-123",
        )
        stub_release: Any = client.release_exclusive
        stub_release.return_value = RemoteCallResult(status=200, body=None)

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        await store.release(admin, "dev1")

        assert store.current_token(admin, "dev1") is None
        assert "dev1" not in admin.acquired_nodes
        stub_release.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_release_no_session_is_noop(self) -> None:
        store, _, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        # Should not raise.
        await store.release(admin, "never-acquired")


class TestReleaseAll:
    @pytest.mark.asyncio
    async def test_releases_every_device_for_admin(self) -> None:
        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s-a")
        other = admin_store.get_or_create("s-b")

        stub_acquire: Any = client.acquire_exclusive
        stub_release: Any = client.release_exclusive
        stub_acquire.side_effect = [
            RemoteCallResult(status=200, body=f"tok-{i}") for i in range(3)
        ]
        stub_release.return_value = RemoteCallResult(status=200, body=None)

        await store.acquire(admin, "dev1", "https://n1/e/", oauth2_on_remote=False)
        await store.acquire(admin, "dev2", "https://n2/e/", oauth2_on_remote=False)
        await store.acquire(other, "dev1", "https://n1/e/", oauth2_on_remote=False)

        await store.release_all(admin)

        assert admin.acquired_nodes == set()
        assert store.current_token(admin, "dev1") is None
        assert store.current_token(admin, "dev2") is None
        # Other admin's session untouched.
        assert store.current_token(other, "dev1") == "tok-2"


class TestPollingTask:
    @pytest.mark.asyncio
    async def test_keepalive_fires_when_alive_window_elapses(self) -> None:
        """After ALIVETIME/2 has elapsed since acquire, the polling
        task's keepalive branch should POST keepalive. Validated by
        calling the internal tick directly with a patched clock.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_keepalive: Any = client.keepalive_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_keepalive.return_value = RemoteCallResult(status=200, body=None)

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        # Fast-forward monotonic clock so the keepalive branch fires.
        fake_now = time.monotonic() + res_mod.HALF_ALIVETIME + 5
        original = time.monotonic
        time.monotonic = lambda: fake_now  # type: ignore[assignment]
        try:
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]
        stub_keepalive.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_keepalive_failure_zeros_session_for_reacquire(self) -> None:
        """A non-200 keepalive response zeroes the session's token so
        the next tick triggers a fresh acquire.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_keepalive: Any = client.keepalive_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_keepalive.return_value = RemoteCallResult(status=401, body=None)

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        fake_now = time.monotonic() + res_mod.HALF_ALIVETIME + 5
        original = time.monotonic
        time.monotonic = lambda: fake_now  # type: ignore[assignment]
        try:
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        # After the failure, current_token returns None (token cleared).
        assert store.current_token(admin, "dev1") is None

    @pytest.mark.asyncio
    async def test_renew_too_early_keeps_the_token(self) -> None:
        """``425 Too Early`` is not a failure and MUST NOT discard the token.

        §Verifying Ownership: "If the renewal returns a `425 Too Early` status,
        the token SHOULD be considered to be still valid for at least half of
        its 'Session Lifetime'."

        Discarding it sends the next tick to reacquire, which the Node refuses
        with 423 because its own session is still alive — leaving the
        controller holding no usable token, unable to release, and unable to
        reacquire, while the UI still shows the reservation as held.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_renew: Any = client.renew_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_renew.return_value = RemoteCallResult(
            status=res_mod.HTTP_TOO_EARLY, body={"error": "too early to renew"},
        )

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        fake_now = time.monotonic() + res_mod.MIN_LIFETIME / 2 + 5
        original = time.monotonic
        time.monotonic = lambda: fake_now  # type: ignore[assignment]
        try:
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        stub_renew.assert_awaited_once()
        assert store.current_token(admin, "dev1") == "t", (
            "a 425 discarded the session token"
        )
        sess = next(s for s in store.snapshot() if s.node_id == "dev1")
        assert sess.renew_after > fake_now, "no retry deferral was recorded"
        assert sess.expires_at > 0.0, "expiry was zeroed on a 425"

    @pytest.mark.asyncio
    async def test_renew_too_early_is_not_retried_every_tick(self) -> None:
        """The deferral must actually suppress the next attempt.

        The renew trigger is a threshold, not an edge, so without a deferral a
        425 would be re-sent on every one-second tick for as long as the
        threshold held.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_renew: Any = client.renew_exclusive
        stub_keepalive: Any = client.keepalive_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_renew.return_value = RemoteCallResult(
            status=res_mod.HTTP_TOO_EARLY, body=None)
        stub_keepalive.return_value = RemoteCallResult(status=200, body=None)

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        base = time.monotonic() + res_mod.MIN_LIFETIME / 2 + 5
        original = time.monotonic
        try:
            for offset in (0.0, 1.0, 2.0):        # three consecutive ticks
                time.monotonic = lambda o=offset: base + o  # type: ignore
                await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]
        assert stub_renew.await_count == 1, (
            f"renew was retried {stub_renew.await_count}x within the "
            f"deferral window"
        )
        # Asserted together on purpose: a single renew is also what happens if
        # the 425 discarded the token (the later ticks would then be taking the
        # reacquire branch instead), so the count alone does not distinguish a
        # working deferral from the bug it replaced.
        assert store.current_token(admin, "dev1") == "t"

    @pytest.mark.asyncio
    async def test_deferred_renew_still_keeps_the_session_alive(self) -> None:
        """A session waiting out a 425 must still be kept alive.

        Both branches are due at once here: the renew threshold has passed
        (deferred) and the keepalive window has too. If the deferred renew
        short-circuited the tick, the session would lapse on inactivity while
        waiting — turning a benign 425 into a lost reservation.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_renew: Any = client.renew_exclusive
        stub_keepalive: Any = client.keepalive_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_renew.return_value = RemoteCallResult(
            status=res_mod.HTTP_TOO_EARLY, body=None)
        stub_keepalive.return_value = RemoteCallResult(status=200, body=None)

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        original = time.monotonic
        try:
            # Tick 1: renew is due, comes back 425, deferral recorded.
            first = time.monotonic() + res_mod.MIN_LIFETIME / 2 + 5
            time.monotonic = lambda: first  # type: ignore[assignment]
            await store._tick()
            # Tick 2: still inside the deferral, and now the keepalive is due.
            second = first + res_mod.HALF_ALIVETIME + 1
            time.monotonic = lambda: second  # type: ignore[assignment]
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        assert stub_renew.await_count == 1
        stub_keepalive.assert_awaited_once()
        assert store.current_token(admin, "dev1") == "t"

    @pytest.mark.asyncio
    async def test_renew_success_clears_the_deferral(self) -> None:
        """Once a renew succeeds, the 425 deferral must not linger."""
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_renew: Any = client.renew_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_renew.return_value = RemoteCallResult(
            status=res_mod.HTTP_TOO_EARLY, body=None)

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        original = time.monotonic
        try:
            first = time.monotonic() + res_mod.MIN_LIFETIME / 2 + 5
            time.monotonic = lambda: first  # type: ignore[assignment]
            await store._tick()
            sess = next(s for s in store.snapshot() if s.node_id == "dev1")
            assert sess.renew_after > first

            # Past the deferral, and this time the Node accepts.
            stub_renew.return_value = RemoteCallResult(status=200, body="t2")
            later = first + res_mod.RENEW_TOO_EARLY_RETRY + 1
            time.monotonic = lambda: later  # type: ignore[assignment]
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        assert store.current_token(admin, "dev1") == "t2"
        sess = next(s for s in store.snapshot() if s.node_id == "dev1")
        assert sess.renew_after == 0.0, "deferral survived a successful renew"

    @pytest.mark.asyncio
    async def test_retry_after_sets_the_deferral(self) -> None:
        """§Renew: the client defers by the advertised delay, not by a guess.

        Without this the controller falls back to a fixed 60 s poll, which
        against a long-Lifetime Node means hundreds of speculative renews
        before one lands.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_renew: Any = client.renew_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_renew.return_value = RemoteCallResult(
            status=res_mod.HTTP_TOO_EARLY, body=None, retry_after=39600,
        )

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        fake_now = time.monotonic() + res_mod.MIN_LIFETIME / 2 + 5
        original = time.monotonic
        time.monotonic = lambda: fake_now  # type: ignore[assignment]
        try:
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        sess = next(s for s in store.snapshot() if s.node_id == "dev1")
        assert sess.renew_after == pytest.approx(fake_now + 39600), (
            "the advertised Retry-After was ignored in favour of the fallback"
        )
        assert sess.renew_after != pytest.approx(
            fake_now + res_mod.RENEW_TOO_EARLY_RETRY,
        )
        assert store.current_token(admin, "dev1") == "t"

    @pytest.mark.asyncio
    async def test_retry_after_derives_the_session_lifetime(self) -> None:
        """§Renew: the Lifetime is "twice the sum of the `Retry-After` delay
        and the time elapsed since its most recent successful `Acquire` or
        `Renew`".

        A 24-hour Node: the controller renews at 1805 s (half the assumed
        60-minute minimum, plus the 5 s needed to cross a strictly-greater
        threshold), is refused with a delay of 43200 - 1805 = 41395 s, and must
        conclude 2 * (41395 + 1805) = 86400.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_renew: Any = client.renew_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")

        original = time.monotonic
        acquired_at = time.monotonic()
        try:
            time.monotonic = lambda: acquired_at  # type: ignore[assignment]
            await store.acquire(
                admin, "dev1", "https://n/e/", oauth2_on_remote=False,
            )
            sess = next(s for s in store.snapshot() if s.node_id == "dev1")
            assert sess.lifetime == res_mod.MIN_LIFETIME, (
                "a client must assume the 60-minute minimum before it knows"
            )

            elapsed = res_mod.MIN_LIFETIME / 2 + 5           # 1805
            stub_renew.return_value = RemoteCallResult(
                status=res_mod.HTTP_TOO_EARLY, body=None,
                retry_after=int(_NODE_LIFETIME / 2 - elapsed),
            )
            fake_now = acquired_at + elapsed
            time.monotonic = lambda: fake_now  # type: ignore[assignment]
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        sess = next(s for s in store.snapshot() if s.node_id == "dev1")
        assert sess.lifetime == pytest.approx(_NODE_LIFETIME, abs=2.0)
        # The Lifetime runs from the last acquire/renew, not from the moment
        # the Node happened to refuse us.
        assert sess.expires_at == pytest.approx(
            acquired_at + _NODE_LIFETIME, abs=2.0,
        )

    @pytest.mark.asyncio
    async def test_derived_lifetime_stops_the_speculative_renews(self) -> None:
        """The point of deriving it: converge after exactly one rejection.

        Once the controller knows the Node runs 24 hours, the renew threshold
        moves out to 12 hours and no further renew is attempted in between —
        the behaviour a fixed 60 s retry could never reach.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_renew: Any = client.renew_exclusive
        stub_keepalive: Any = client.keepalive_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_keepalive.return_value = RemoteCallResult(status=200, body=None)

        original = time.monotonic
        acquired_at = time.monotonic()
        try:
            time.monotonic = lambda: acquired_at  # type: ignore[assignment]
            await store.acquire(
                admin, "dev1", "https://n/e/", oauth2_on_remote=False,
            )

            # First renew attempt, just past half the assumed minimum → refused.
            elapsed = res_mod.MIN_LIFETIME / 2 + 5
            stub_renew.return_value = RemoteCallResult(
                status=res_mod.HTTP_TOO_EARLY, body=None,
                retry_after=int(_NODE_LIFETIME / 2 - elapsed),
            )
            time.monotonic = lambda: acquired_at + elapsed  # type: ignore
            await store._tick()
            assert stub_renew.await_count == 1

            # Hours later — long past the old fixed fallback window and past
            # the assumed-minimum threshold, but short of the derived 12-hour
            # one — nothing further should be sent.
            for hours in (1, 3, 6, 11):
                at = acquired_at + hours * 3600.0
                time.monotonic = lambda a=at: a  # type: ignore
                await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        assert stub_renew.await_count == 1, (
            f"renew fired {stub_renew.await_count}x before the derived "
            f"12-hour threshold — the derived Lifetime is not being used"
        )

    @pytest.mark.asyncio
    async def test_missing_retry_after_falls_back(self) -> None:
        """A Node that omits the header is non-conformant, but the token is
        still valid and blind retrying still converges — so fall back rather
        than treat it as a failure. The believed Lifetime must be left alone:
        nothing was learned.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_renew: Any = client.renew_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_renew.return_value = RemoteCallResult(
            status=res_mod.HTTP_TOO_EARLY, body=None,      # retry_after=None
        )

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        fake_now = time.monotonic() + res_mod.MIN_LIFETIME / 2 + 5
        original = time.monotonic
        time.monotonic = lambda: fake_now  # type: ignore[assignment]
        try:
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        sess = next(s for s in store.snapshot() if s.node_id == "dev1")
        assert sess.renew_after == pytest.approx(
            fake_now + res_mod.RENEW_TOO_EARLY_RETRY,
        )
        assert sess.lifetime == res_mod.MIN_LIFETIME
        assert store.current_token(admin, "dev1") == "t"

    @pytest.mark.asyncio
    async def test_derived_lifetime_never_falls_below_the_minimum(self) -> None:
        """A conformant Node cannot advertise a sub-minimum Lifetime, so a
        figure below the floor means a broken Node or a clock anomaly.
        Clamping keeps the renew schedule inside what the floor already
        guarantees is safe instead of accelerating it on bad data.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_renew: Any = client.renew_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_renew.return_value = RemoteCallResult(
            status=res_mod.HTTP_TOO_EARLY, body=None, retry_after=1,
        )

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        fake_now = time.monotonic() + res_mod.MIN_LIFETIME / 2 + 5
        original = time.monotonic
        time.monotonic = lambda: fake_now  # type: ignore[assignment]
        try:
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        sess = next(s for s in store.snapshot() if s.node_id == "dev1")
        assert sess.lifetime >= res_mod.MIN_LIFETIME

    @pytest.mark.asyncio
    async def test_successful_renew_rebases_on_the_derived_lifetime(self) -> None:
        """§KeepAlive: "Only the Renew operation extends the 'Session
        Lifetime'." After a successful renew the schedule must restart from
        that instant, using the Lifetime already derived rather than reverting
        to the assumed minimum.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_renew: Any = client.renew_exclusive
        stub_keepalive: Any = client.keepalive_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_keepalive.return_value = RemoteCallResult(status=200, body=None)

        original = time.monotonic
        acquired_at = time.monotonic()
        try:
            time.monotonic = lambda: acquired_at  # type: ignore[assignment]
            await store.acquire(
                admin, "dev1", "https://n/e/", oauth2_on_remote=False,
            )

            elapsed = res_mod.MIN_LIFETIME / 2 + 5
            stub_renew.return_value = RemoteCallResult(
                status=res_mod.HTTP_TOO_EARLY, body=None,
                retry_after=int(_NODE_LIFETIME / 2 - elapsed),
            )
            time.monotonic = lambda: acquired_at + elapsed  # type: ignore
            await store._tick()

            # Come back just past the advertised time; this renew succeeds.
            renewed_at = acquired_at + _NODE_LIFETIME / 2 + 5
            stub_renew.return_value = RemoteCallResult(status=200, body="t2")
            time.monotonic = lambda: renewed_at  # type: ignore[assignment]
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        sess = next(s for s in store.snapshot() if s.node_id == "dev1")
        assert store.current_token(admin, "dev1") == "t2"
        assert sess.renewed_at == pytest.approx(renewed_at)
        assert sess.expires_at == pytest.approx(
            renewed_at + _NODE_LIFETIME, abs=2.0,
        ), "expiry reverted to the assumed minimum after a successful renew"
        assert sess.renew_after == 0.0

    @pytest.mark.asyncio
    async def test_renew_failure_other_than_425_still_reacquires(self) -> None:
        """A genuine renew failure must keep the old zero-the-token behaviour.

        The 425 branch is a carve-out, not a blanket change: a 401 means the
        session really is gone and reacquiring is the correct recovery.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_renew: Any = client.renew_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_renew.return_value = RemoteCallResult(status=401, body=None)

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        fake_now = time.monotonic() + res_mod.MIN_LIFETIME / 2 + 5
        original = time.monotonic
        time.monotonic = lambda: fake_now  # type: ignore[assignment]
        try:
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        assert store.current_token(admin, "dev1") is None

    @pytest.mark.asyncio
    async def test_keepalive_transport_failure_keeps_the_token(self) -> None:
        """A request that never reached the Node must not cost us the session.

        ``RemoteNodeClient`` reports every ``aiohttp.ClientError`` as
        ``status=0``, so a connection refusal, TLS failure or timeout arrives
        here looking like any other non-200. It is not: a 401 is the Node
        saying the session is gone, whereas a transport failure says only that
        we could not ask. The session is still alive on the Node until its
        AliveTime window closes, so the token stays.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_keepalive: Any = client.keepalive_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_keepalive.return_value = RemoteCallResult(
            status=res_mod.TRANSPORT_FAILURE, body=None,
            error="Cannot connect to host",
        )

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        # Inside the AliveTime window: keepalive is due, but the session
        # cannot have lapsed yet.
        fake_now = time.monotonic() + res_mod.HALF_ALIVETIME + 5
        original = time.monotonic
        time.monotonic = lambda: fake_now  # type: ignore[assignment]
        try:
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        stub_keepalive.assert_awaited_once()
        assert store.current_token(admin, "dev1") == "t", (
            "an unreachable Node cost us a token that was never invalidated"
        )

    @pytest.mark.asyncio
    async def test_keepalive_transport_failure_retries_next_tick(self) -> None:
        """The retry needs no extra bookkeeping — and must actually happen.

        A failed keepalive leaves ``alive_until`` untouched, so the branch
        stays true and the next tick tries again. This pins that, because a
        version that merely swallowed the failure would sit idle until the
        window closed and then reacquire — losing the session just as slowly.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_keepalive: Any = client.keepalive_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_keepalive.return_value = RemoteCallResult(
            status=res_mod.TRANSPORT_FAILURE, body=None, error="timeout")

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        base = time.monotonic() + res_mod.HALF_ALIVETIME + 5
        original = time.monotonic
        try:
            for offset in (0.0, 1.0, 2.0):
                time.monotonic = lambda o=offset: base + o  # type: ignore
                await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        assert stub_keepalive.await_count == 3, (
            f"keepalive was attempted {stub_keepalive.await_count}x across "
            f"three ticks inside the AliveTime window"
        )
        assert store.current_token(admin, "dev1") == "t"

    @pytest.mark.asyncio
    async def test_keepalive_transport_failure_reacquires_once_alive_lapses(
        self,
    ) -> None:
        """Retrying is bounded by the AliveTime window, not unbounded.

        Once the window has closed with no successful keepalive the Node has
        dropped the session however unreachable it was — so discarding the
        token becomes correct, because a reacquire can now succeed. Without
        this the controller would hold a dead token forever.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_keepalive: Any = client.keepalive_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_keepalive.return_value = RemoteCallResult(
            status=res_mod.TRANSPORT_FAILURE, body=None, error="unreachable")

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        # Past the whole AliveTime window: the Node cannot still hold it.
        fake_now = time.monotonic() + res_mod.ALIVETIME + 1
        original = time.monotonic
        time.monotonic = lambda: fake_now  # type: ignore[assignment]
        try:
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        assert store.current_token(admin, "dev1") is None, (
            "token retained past the AliveTime window, so no reacquire can "
            "ever be attempted"
        )

    @pytest.mark.asyncio
    async def test_keepalive_401_still_discards_immediately(self) -> None:
        """The carve-out is for transport failures only.

        A 401 is the Node's own answer, and waiting out the AliveTime window
        before reacquiring would just delay recovery.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_keepalive: Any = client.keepalive_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_keepalive.return_value = RemoteCallResult(status=401, body=None)

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        # Well inside the AliveTime window, where a transport failure would
        # have kept the token.
        fake_now = time.monotonic() + res_mod.HALF_ALIVETIME + 5
        original = time.monotonic
        time.monotonic = lambda: fake_now  # type: ignore[assignment]
        try:
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        assert store.current_token(admin, "dev1") is None

    @pytest.mark.asyncio
    async def test_renew_transport_failure_keeps_the_token_and_defers(
        self,
    ) -> None:
        """A renew that never arrived must not discard the token either.

        Renewal is the less urgent of the two operations — its threshold fires
        with at least half the Session Lifetime still to run, and it is
        keepalive that stops a session lapsing — so this defers rather than
        retrying every tick, and leaves the lapse decision to keepalive.
        """
        import time
        import nmos.controller.reservation as res_mod

        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_renew: Any = client.renew_exclusive
        stub_keepalive: Any = client.keepalive_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_renew.return_value = RemoteCallResult(
            status=res_mod.TRANSPORT_FAILURE, body=None, error="conn refused")
        stub_keepalive.return_value = RemoteCallResult(status=200, body=None)

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        base = time.monotonic() + res_mod.MIN_LIFETIME / 2 + 5
        original = time.monotonic
        try:
            for offset in (0.0, 1.0, 2.0):
                time.monotonic = lambda o=offset: base + o  # type: ignore
                await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        assert store.current_token(admin, "dev1") == "t"
        assert stub_renew.await_count == 1, (
            f"renew retried {stub_renew.await_count}x instead of deferring"
        )
        sess = next(s for s in store.snapshot() if s.node_id == "dev1")
        assert sess.renew_after > base

    @pytest.mark.asyncio
    async def test_admin_gone_drops_session(self) -> None:
        """If the admin session has been discarded (logout), the
        polling task releases the orphaned reservation session too."""
        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s-gone")
        stub_acquire: Any = client.acquire_exclusive
        stub_release: Any = client.release_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_release.return_value = RemoteCallResult(status=200, body=None)

        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        admin_store.discard(admin.token)

        await store._tick()
        assert store.current_token(admin, "dev1") is None
        stub_release.assert_awaited_once()


class TestCurrentToken:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_session(self) -> None:
        store, _, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        assert store.current_token(admin, "dev-any") is None

    @pytest.mark.asyncio
    async def test_returns_token_when_session_live(self) -> None:
        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_acquire.return_value = RemoteCallResult(
            status=200, body="live-token",
        )
        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        assert store.current_token(admin, "dev1") == "live-token"


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_stop_cancels_task_and_releases_all(self) -> None:
        """``stop`` cancels the poll task AND releases every held
        session — the shutdown path."""
        store, client, admin_store = _new_store()
        admin = admin_store.get_or_create("s")
        stub_acquire: Any = client.acquire_exclusive
        stub_release: Any = client.release_exclusive
        stub_acquire.return_value = RemoteCallResult(status=200, body="t")
        stub_release.return_value = RemoteCallResult(status=200, body=None)

        await store.start()
        await store.acquire(admin, "dev1", "https://n/e/", oauth2_on_remote=False)
        await store.stop()

        assert store.current_token(admin, "dev1") is None
        stub_release.assert_awaited()
        # Task is re-awaitable after stop (None-ed out).
        await asyncio.sleep(0)


class TestRetryAfterParsing:
    """``Retry-After`` parsing, per RFC 9110 §10.2.3 as narrowed by §Renew.

    ``Retry-After = HTTP-date / delay-seconds`` with
    ``delay-seconds = 1*DIGIT``. The Node Reservation spec mandates the
    delay-seconds form and forbids HTTP-date "so that the delay is unaffected
    by any clock difference between the client and the Node", so this parser
    accepts digits only. Anything else yields ``None``, which the caller reads
    as "no advertised delay" and handles by falling back — never by failing.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("60", 60),
            ("0", 0),
            ("41395", 41395),
            ("86400", 86400),
            ("  120  ", 120),           # surrounding whitespace tolerated
        ],
    )
    def test_delay_seconds_is_parsed(self, raw: str, expected: int) -> None:
        assert _parse_retry_after(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",                                      # header absent
            "Fri, 31 Dec 1999 23:59:59 GMT",         # HTTP-date: forbidden here
            "-1",                                    # not 1*DIGIT
            "60.5",                                  # not 1*DIGIT
            "sixty",
            "60s",
            "1,200",
        ],
    )
    def test_non_conforming_values_yield_none(self, raw: str) -> None:
        """An HTTP-date is the important one.

        RFC 9110 permits it generally, but honouring it here would reintroduce
        exactly the client/Node clock dependency §Renew rules out. Rejecting it
        makes the controller fall back to a fixed retry — safe, and it keeps
        the Node's non-conformance visible instead of papering over it.
        """
        assert _parse_retry_after(raw) is None
