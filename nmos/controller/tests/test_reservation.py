# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for nmos.controller.reservation."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nmos.controller.api_client import RemoteCallResult, RemoteNodeClient
from nmos.controller.auth import AdminSessionStore
from nmos.controller.reservation import (
    ReservationError,
    ReservationLocked,
    SessionStore,
)


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
        fake_now = time.monotonic() + res_mod.HALF_LIFETIME + 5
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
        base = time.monotonic() + res_mod.HALF_LIFETIME + 5
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
            first = time.monotonic() + res_mod.HALF_LIFETIME + 5
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
            first = time.monotonic() + res_mod.HALF_LIFETIME + 5
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
        fake_now = time.monotonic() + res_mod.HALF_LIFETIME + 5
        original = time.monotonic
        time.monotonic = lambda: fake_now  # type: ignore[assignment]
        try:
            await store._tick()
        finally:
            time.monotonic = original  # type: ignore[assignment]

        assert store.current_token(admin, "dev1") is None

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
