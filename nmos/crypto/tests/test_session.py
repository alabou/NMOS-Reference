# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.crypto — ExclusiveSession."""

from __future__ import annotations

import time

import pytest

from nmos.crypto import (
    DEFAULT_ALIVE_TIME_SECONDS,
    DEFAULT_LIFETIME_SECONDS,
    MAX_LIFETIME_SECONDS,
    PERMITTED_ALIVE_TIME_SECONDS,
    ExclusiveSession,
)
from nmos.errors import Busy, NotAllowed, Skip


class TestAcquire:

    def test_acquire_returns_token(self) -> None:
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_acquire_when_busy_raises(self) -> None:
        session = ExclusiveSession()
        session.acquire("owner1", b"\x00" * 16)
        with pytest.raises(Busy):
            session.acquire("owner2", b"\x01" * 16)

    def test_acquire_sets_owner(self) -> None:
        session = ExclusiveSession()
        session.acquire("controller-A", b"\x00" * 16)
        assert session.owner == "controller-A"


class TestIsOwner:

    def test_valid_token(self) -> None:
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        assert session.is_owner(token) is True

    def test_invalid_token(self) -> None:
        session = ExclusiveSession()
        session.acquire("owner1", b"\x00" * 16)
        assert session.is_owner("bogus-token") is False

    def test_tampered_token(self) -> None:
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        # Flip a character
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        assert session.is_owner(tampered) is False

    def test_no_session(self) -> None:
        session = ExclusiveSession()
        assert session.is_owner("some-token") is False


class TestKeepAlive:

    def test_keep_alive_succeeds(self) -> None:
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        session.keep_alive(token)  # should not raise

    def test_keep_alive_wrong_token_raises(self) -> None:
        session = ExclusiveSession()
        session.acquire("owner1", b"\x00" * 16)
        with pytest.raises(NotAllowed):
            session.keep_alive("wrong-token")

    def test_keep_alive_no_session_raises(self) -> None:
        session = ExclusiveSession()
        with pytest.raises(NotAllowed):
            session.keep_alive("any-token")


class TestRenew:

    def test_renew_too_early_raises_skip(self) -> None:
        """Renew before 1/3 of lifetime should raise Skip."""
        session = ExclusiveSession(lifetime=60.0)
        token = session.acquire("owner1", b"\x00" * 16)
        with pytest.raises(Skip):
            session.renew(token)

    def test_renew_after_threshold_succeeds(self) -> None:
        """Renew after 1/3 of lifetime should return new token."""
        session = ExclusiveSession(lifetime=60.0)
        token = session.acquire("owner1", b"\x00" * 16)
        # Manipulate creation time to simulate 1/3 elapsed (20+ seconds)
        session._session.creation_time = time.time() - 21.0  # type: ignore
        new_token = session.renew(token)
        assert new_token != token
        assert session.is_owner(new_token) is True
        assert session.is_owner(token) is False  # old token invalid

    def test_renew_extends_the_lifetime(self) -> None:
        """A renewed session must outlive the original Lifetime window.

        §KeepAlive: "The KeepAlive operation MUST NOT extend the session
        Lifetime. Only the Renew operation extends the session Lifetime."

        Written as a survival check rather than an assertion about
        ``creation_time`` so it tests the guarantee and not the mechanism.
        The elapsed time is simulated by moving the timestamps backwards —
        the pattern the rest of this class uses — so it runs instantly instead
        of sleeping out a 60 s window.
        """
        session = ExclusiveSession(lifetime=60.0)
        token = session.acquire("owner1", b"\x00" * 16)
        internal = session._session  # type: ignore[attr-defined]
        assert internal is not None

        # 50 of the 60 seconds have gone, and the owner is active.
        internal.creation_time = time.time() - 50.0
        internal.keepalive_time = time.time()
        token = session.renew(token)          # allowed: 50 >= 60/3

        # Another 20 seconds pass — 70 in total since the *original* acquire,
        # so the session is past its first Lifetime window and can only still
        # be alive if the renewal restarted the clock. Inactivity is kept out
        # of it by refreshing the keepalive stamp.
        internal.creation_time -= 20.0
        internal.keepalive_time = time.time()

        assert session.is_alive() is True, (
            "session died 70s after acquire despite being renewed at 50s — "
            "renew did not extend the Lifetime"
        )
        assert session.is_owner(token) is True

    def test_renew_gate_applies_to_each_renewal(self) -> None:
        """Restarting the Lifetime must not make the 1/3 gate skippable.

        Resetting ``creation_time`` on renewal is what extends the Lifetime;
        the same reset is what keeps the "too early" gate meaningful for the
        *next* renewal. Without this test, an implementation that extended the
        Lifetime by some other means could allow unlimited back-to-back
        renewals.
        """
        session = ExclusiveSession(lifetime=60.0)
        token = session.acquire("owner1", b"\x00" * 16)
        internal = session._session  # type: ignore[attr-defined]
        internal.creation_time = time.time() - 21.0
        token = session.renew(token)

        # Immediately after a successful renewal, another one is too early.
        with pytest.raises(Skip):
            session.renew(token)

    def test_renew_wrong_token_raises(self) -> None:
        session = ExclusiveSession()
        session.acquire("owner1", b"\x00" * 16)
        with pytest.raises(NotAllowed):
            session.renew("wrong-token")


class TestRelease:

    def test_release_makes_session_dead(self) -> None:
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        session.release(token)
        assert session.is_alive() is False
        assert session.is_owner(token) is False

    def test_release_allows_new_acquire(self) -> None:
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        session.release(token)
        new_token = session.acquire("owner2", b"\x01" * 16)
        assert session.is_owner(new_token) is True

    def test_release_wrong_token_raises(self) -> None:
        session = ExclusiveSession()
        session.acquire("owner1", b"\x00" * 16)
        with pytest.raises(NotAllowed):
            session.release("wrong-token")


class TestExpiry:

    def test_lifetime_expiry(self) -> None:
        """Session expires after total lifetime."""
        session = ExclusiveSession(lifetime=60.0)
        token = session.acquire("owner1", b"\x00" * 16)
        # Force expiry by manipulating creation time
        session._session.creation_time = time.time() - 61.0  # type: ignore
        assert session.is_alive() is False

    def test_inactivity_expiry(self) -> None:
        """Session expires after inactivity timeout.

        The AliveTime is the specification's own 60 s — it cannot be shortened
        to make the test quick — so the elapsed time is simulated by moving
        ``keepalive_time`` backwards past the window.
        """
        session = ExclusiveSession(
            lifetime=DEFAULT_LIFETIME_SECONDS,
            alive_time=DEFAULT_ALIVE_TIME_SECONDS,
        )
        token = session.acquire("owner1", b"\x00" * 16)
        session._session.keepalive_time = (  # type: ignore[union-attr]
            time.time() - (DEFAULT_ALIVE_TIME_SECONDS + 1.0)
        )
        assert session.is_alive() is False

    def test_keepalive_prevents_inactivity(self) -> None:
        """KeepAlive resets the inactivity timer."""
        session = ExclusiveSession(
            lifetime=DEFAULT_LIFETIME_SECONDS,
            alive_time=DEFAULT_ALIVE_TIME_SECONDS,
        )
        token = session.acquire("owner1", b"\x00" * 16)
        # One second short of lapsing...
        session._session.keepalive_time = (  # type: ignore[union-attr]
            time.time() - (DEFAULT_ALIVE_TIME_SECONDS - 1.0)
        )
        session.keep_alive(token)            # ...and the timer is reset
        session._session.keepalive_time -= (  # type: ignore[union-attr]
            DEFAULT_ALIVE_TIME_SECONDS - 1.0
        )
        assert session.is_alive() is True

    def test_keepalive_does_not_extend_the_lifetime(self) -> None:
        """§KeepAlive: "The KeepAlive operation MUST NOT extend the session
        Lifetime." The companion to ``test_renew_extends_the_lifetime``: the
        two operations must differ on exactly this point.
        """
        session = ExclusiveSession(lifetime=60.0)
        token = session.acquire("owner1", b"\x00" * 16)
        internal = session._session  # type: ignore[attr-defined]
        internal.creation_time = time.time() - 59.0
        session.keep_alive(token)            # refreshes activity only
        internal.creation_time -= 2.0        # now 61s since acquire
        assert session.is_alive() is False, (
            "keepalive extended the Lifetime; only renew may do that"
        )


class TestTimingValidation:
    """The specification fixes these timings and offers no way to discover
    them, so a value outside what it permits must be rejected rather than
    quietly adjusted. Clamping is what previously turned a legal 120 s
    AliveTime into 60 s and accepted an illegal 1 s without complaint.
    """

    def test_defaults_match_the_specification(self) -> None:
        session = ExclusiveSession()
        assert session.lifetime == DEFAULT_LIFETIME_SECONDS == 3600.0
        assert session.alive_time == DEFAULT_ALIVE_TIME_SECONDS == 60.0

    @pytest.mark.parametrize("alive_time", PERMITTED_ALIVE_TIME_SECONDS)
    def test_permitted_alive_times_are_accepted_verbatim(
        self, alive_time: float,
    ) -> None:
        """Both legal values must survive unchanged — 120 s in particular,
        which the old upper clamp of 60 s silently halved.
        """
        session = ExclusiveSession(alive_time=alive_time)
        assert session.alive_time == alive_time

    @pytest.mark.parametrize("alive_time", [0.0, 1.0, 2.0, 30.0, 59.0,
                                            61.0, 90.0, 119.0, 121.0, 600.0])
    def test_other_alive_times_are_rejected(self, alive_time: float) -> None:
        with pytest.raises(ValueError, match="AliveTime"):
            ExclusiveSession(alive_time=alive_time)

    def test_lifetime_upper_bound_is_enforced(self) -> None:
        """"...to a maximum of 24 hours"."""
        ExclusiveSession(lifetime=MAX_LIFETIME_SECONDS)      # the boundary is legal
        with pytest.raises(ValueError, match="lifetime"):
            ExclusiveSession(lifetime=MAX_LIFETIME_SECONDS + 1.0)

    @pytest.mark.parametrize("lifetime", [0.0, -1.0])
    def test_non_positive_lifetime_is_rejected(self, lifetime: float) -> None:
        with pytest.raises(ValueError, match="lifetime"):
            ExclusiveSession(lifetime=lifetime)


class TestGetKey:

    def test_returns_key_when_alive(self) -> None:
        key = b"\xab\xcd" * 8
        session = ExclusiveSession()
        session.acquire("owner1", key)
        assert session.get_key() == key

    def test_returns_none_when_expired(self) -> None:
        session = ExclusiveSession()
        assert session.get_key() is None

    def test_returns_none_after_release(self) -> None:
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        session.release(token)
        assert session.get_key() is None
