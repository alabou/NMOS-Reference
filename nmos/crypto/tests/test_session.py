# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.crypto — ExclusiveSession."""

from __future__ import annotations

import time

import pytest

from nmos.crypto import (
    ALIVE_TIME_SECONDS,
    DEFAULT_LIFETIME_SECONDS,
    MAX_LIFETIME_SECONDS,
    MIN_LIFETIME_SECONDS,
    ExclusiveSession,
    TooEarly,
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
        """Renew before 1/3 of lifetime should raise Skip.

        ``TooEarly`` subclasses ``Skip``, so callers written against the older
        control-flow signal keep working; this asserts that compatibility as
        well as the rejection.
        """
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        with pytest.raises(Skip):
            session.renew(token)

    def test_renew_after_threshold_succeeds(self) -> None:
        """Renew after 1/3 of lifetime should return new token."""
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        # Simulate just past 1/3 of the Lifetime having elapsed. The Lifetime
        # can no longer be shortened to keep the test quick — the spec puts a
        # 60-minute floor on it — so every elapsed time here is expressed as a
        # fraction of the real Lifetime and simulated by moving the clock.
        session._session.creation_time = (  # type: ignore[union-attr]
            time.time() - (DEFAULT_LIFETIME_SECONDS / 3 + 1.0)
        )
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
        of sleeping out the Lifetime window.
        """
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        internal = session._session  # type: ignore[attr-defined]
        assert internal is not None

        # 5/6 of the Lifetime has gone, and the owner is active.
        internal.creation_time = time.time() - DEFAULT_LIFETIME_SECONDS * 5 / 6
        internal.keepalive_time = time.time()
        token = session.renew(token)          # allowed: 5/6 >= 1/3

        # Another 1/3 passes — 7/6 of a Lifetime in total since the *original*
        # acquire, so the session is past its first Lifetime window and can
        # only still be alive if the renewal restarted the clock. Inactivity is
        # kept out of it by refreshing the keepalive stamp.
        internal.creation_time -= DEFAULT_LIFETIME_SECONDS / 3
        internal.keepalive_time = time.time()

        assert session.is_alive() is True, (
            "session died 7/6 of a Lifetime after acquire despite being "
            "renewed at 5/6 — renew did not extend the Lifetime"
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
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        internal = session._session  # type: ignore[attr-defined]
        internal.creation_time = time.time() - (DEFAULT_LIFETIME_SECONDS / 3 + 1.0)
        token = session.renew(token)

        # Immediately after a successful renewal, another one is too early.
        with pytest.raises(Skip):
            session.renew(token)

    def test_renew_wrong_token_raises(self) -> None:
        session = ExclusiveSession()
        session.acquire("owner1", b"\x00" * 16)
        with pytest.raises(NotAllowed):
            session.renew("wrong-token")


class TestTooEarlyRetryAfter:
    """§Renew: a rejected renew must carry the delay to the half-Lifetime.

    "The delay MUST be the number of seconds remaining until half of the
    'Session Lifetime' has elapsed, measured from the most recent successful
    `Acquire` or `Renew` of the session."

    This is the only channel through which a client can discover a Node's
    configured Session Lifetime, so the value has to be right rather than
    merely plausible — a client feeds it straight into
    ``lifetime = 2 * (delay + elapsed)``.
    """

    @staticmethod
    def _renew_at(session: ExclusiveSession, elapsed: float) -> TooEarly:
        """Attempt a renew ``elapsed`` seconds after acquire, expecting 425."""
        token = session.acquire("owner1", b"\x00" * 16)
        session._session.creation_time = (  # type: ignore[union-attr]
            time.time() - elapsed
        )
        with pytest.raises(TooEarly) as caught:
            session.renew(token)
        return caught.value

    def test_raises_too_early_not_bare_skip(self) -> None:
        """The handler needs the delay, so the signal must carry it."""
        exc = self._renew_at(ExclusiveSession(), 0.0)
        assert isinstance(exc, Skip)         # back-compatible with callers
        assert isinstance(exc.retry_after, int)

    def test_delay_targets_the_half_lifetime_not_the_gate(self) -> None:
        """The delay must point at the recommended renewal instant, not at the
        1/3 boundary that caused the rejection.

        §Renew: "The delay therefore indicates the point at which the session
        SHOULD be renewed and not the earliest point at which a `Renew` would
        be permitted." Aiming at the gate would send every client back at the
        earliest legal moment instead of the recommended one — and would make
        the client's Lifetime derivation, which assumes the half, wrong by a
        factor of 3/4.
        """
        lifetime = DEFAULT_LIFETIME_SECONDS
        elapsed = lifetime / 4                      # inside the 1/3 gate
        exc = self._renew_at(ExclusiveSession(lifetime=lifetime), elapsed)

        expected_half = lifetime / 2 - elapsed      # 900 s
        expected_gate = lifetime / 3 - elapsed      # 300 s — the wrong answer
        assert exc.retry_after == pytest.approx(expected_half, abs=1.0)
        assert exc.retry_after != pytest.approx(expected_gate, abs=1.0)

    @pytest.mark.parametrize("fraction", [0.0, 0.1, 0.2, 0.3, 0.33])
    def test_delay_stays_within_the_specified_bounds(
        self, fraction: float,
    ) -> None:
        """§Renew: "the delay is always greater than 1/6 and at most 1/2 of the
        'Session Lifetime'."

        Follows from the gate: a 425 only happens while elapsed < L/3, so
        L/2 - elapsed > L/6. Asserted directly because the bound is a
        normative statement clients are entitled to rely on.
        """
        lifetime = DEFAULT_LIFETIME_SECONDS
        exc = self._renew_at(
            ExclusiveSession(lifetime=lifetime), lifetime * fraction,
        )
        assert lifetime / 6 < exc.retry_after <= lifetime / 2

    @pytest.mark.parametrize(
        "lifetime", [MIN_LIFETIME_SECONDS, 7200.0, 43200.0, MAX_LIFETIME_SECONDS],
    )
    def test_client_can_derive_the_lifetime_from_one_rejection(
        self, lifetime: float,
    ) -> None:
        """The round-trip a controller actually performs.

        §Renew: a client "MAY derive the 'Session Lifetime' configured on a
        Node from a `425 Too Early` response, as twice the sum of the
        `Retry-After` delay and the time elapsed since its most recent
        successful `Acquire` or `Renew`." One rejection therefore replaces the
        assumed 60-minute minimum with the Node's real value, whatever it is.
        """
        elapsed = 300.0
        exc = self._renew_at(ExclusiveSession(lifetime=lifetime), elapsed)
        derived = 2.0 * (exc.retry_after + elapsed)
        assert derived == pytest.approx(lifetime, abs=2.0)

    def test_delay_is_measured_from_the_last_renew_not_the_acquire(self) -> None:
        """"...measured from the most recent successful `Acquire` or `Renew`".

        A renew restarts the Lifetime clock, so the next delay must be computed
        against the renewal instant. Measuring from the original acquire would
        shrink every subsequent delay and eventually go negative on a
        long-lived session.
        """
        lifetime = DEFAULT_LIFETIME_SECONDS
        session = ExclusiveSession(lifetime=lifetime)
        token = session.acquire("owner1", b"\x00" * 16)
        internal = session._session      # type: ignore[attr-defined]

        # Renew once, well past the gate.
        internal.creation_time = time.time() - lifetime * 5 / 6
        token = session.renew(token)

        # A renew attempted immediately afterwards is too early again, and the
        # delay must be a full half-Lifetime — counted from the renewal, not
        # from an acquire that is now 5/6 of a Lifetime in the past.
        with pytest.raises(TooEarly) as caught:
            session.renew(token)
        assert caught.value.retry_after == pytest.approx(lifetime / 2, abs=1.0)

    def test_delay_is_rounded_up(self) -> None:
        """Rounded up so a client honouring the delay exactly cannot land a
        fraction of a second short and collect a second 425.
        """
        lifetime = DEFAULT_LIFETIME_SECONDS
        session = ExclusiveSession(lifetime=lifetime)
        token = session.acquire("owner1", b"\x00" * 16)
        # Elapsed must stay inside the 1/3 gate or the renew simply succeeds.
        # 999.5 s elapsed of 3600 leaves a fractional 800.5 s to the half.
        session._session.creation_time = (  # type: ignore[union-attr]
            time.time() - (lifetime / 2 - 800.5)
        )
        with pytest.raises(TooEarly) as caught:
            session.renew(token)
        assert caught.value.retry_after == 801     # ceil(800.5), not 800


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
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        # Force expiry by manipulating creation time
        session._session.creation_time = (  # type: ignore[union-attr]
            time.time() - (DEFAULT_LIFETIME_SECONDS + 1.0)
        )
        assert session.is_alive() is False

    def test_inactivity_expiry(self) -> None:
        """Session expires after inactivity timeout.

        The AliveTime is the specification's own 60 s — it cannot be shortened
        to make the test quick — so the elapsed time is simulated by moving
        ``keepalive_time`` backwards past the window.
        """
        session = ExclusiveSession(
            lifetime=DEFAULT_LIFETIME_SECONDS,
            alive_time=ALIVE_TIME_SECONDS,
        )
        token = session.acquire("owner1", b"\x00" * 16)
        session._session.keepalive_time = (  # type: ignore[union-attr]
            time.time() - (ALIVE_TIME_SECONDS + 1.0)
        )
        assert session.is_alive() is False

    def test_keepalive_prevents_inactivity(self) -> None:
        """KeepAlive resets the inactivity timer."""
        session = ExclusiveSession(
            lifetime=DEFAULT_LIFETIME_SECONDS,
            alive_time=ALIVE_TIME_SECONDS,
        )
        token = session.acquire("owner1", b"\x00" * 16)
        # One second short of lapsing...
        session._session.keepalive_time = (  # type: ignore[union-attr]
            time.time() - (ALIVE_TIME_SECONDS - 1.0)
        )
        session.keep_alive(token)            # ...and the timer is reset
        session._session.keepalive_time -= (  # type: ignore[union-attr]
            ALIVE_TIME_SECONDS - 1.0
        )
        assert session.is_alive() is True

    def test_keepalive_does_not_extend_the_lifetime(self) -> None:
        """§KeepAlive: "The KeepAlive operation MUST NOT extend the session
        Lifetime." The companion to ``test_renew_extends_the_lifetime``: the
        two operations must differ on exactly this point.
        """
        session = ExclusiveSession()
        token = session.acquire("owner1", b"\x00" * 16)
        internal = session._session  # type: ignore[attr-defined]
        internal.creation_time = time.time() - (DEFAULT_LIFETIME_SECONDS - 1.0)
        session.keep_alive(token)            # refreshes activity only
        internal.creation_time -= 2.0        # now past the Lifetime
        assert session.is_alive() is False, (
            "keepalive extended the Lifetime; only renew may do that"
        )


class TestTimingValidation:
    """The specification fixes these timings and offers no way to discover
    them directly, so a value outside what it permits must be rejected rather
    than quietly adjusted. Clamping is what previously let an out-of-spec
    value look accepted.
    """

    def test_defaults_match_the_specification(self) -> None:
        session = ExclusiveSession()
        assert session.lifetime == DEFAULT_LIFETIME_SECONDS == 3600.0
        assert session.alive_time == ALIVE_TIME_SECONDS == 60.0

    def test_the_only_permitted_alive_time_is_accepted(self) -> None:
        """§Session Lifetime versus AliveTime: "The 'Session AliveTime' MUST
        be 60 seconds." One value, so the check is equality rather than
        membership in a range.
        """
        session = ExclusiveSession(alive_time=ALIVE_TIME_SECONDS)
        assert session.alive_time == 60.0

    @pytest.mark.parametrize("alive_time", [0.0, 1.0, 2.0, 30.0, 59.0,
                                            61.0, 90.0, 119.0, 120.0, 600.0])
    def test_other_alive_times_are_rejected(self, alive_time: float) -> None:
        """120 s is in this list deliberately.

        An earlier revision of the specification permitted "60 seconds to 120
        seconds ... as configured by an administrator". It no longer does: the
        AliveTime is not discoverable through the protocol, so every client has
        to schedule keepalives against 60 s regardless, and a Node running 120
        would be undetectably different rather than usefully configurable. A
        Node that still accepts 120 is now non-conformant.
        """
        with pytest.raises(ValueError, match="AliveTime"):
            ExclusiveSession(alive_time=alive_time)

    def test_lifetime_upper_bound_is_enforced(self) -> None:
        """"...to a maximum of 24 hours"."""
        ExclusiveSession(lifetime=MAX_LIFETIME_SECONDS)      # the boundary is legal
        with pytest.raises(ValueError, match="lifetime"):
            ExclusiveSession(lifetime=MAX_LIFETIME_SECONDS + 1.0)

    def test_lifetime_lower_bound_is_enforced(self) -> None:
        """§Session Lifetime versus AliveTime: "The configured 'Session
        Lifetime' MUST be 60 minutes or greater".

        The floor is what makes a client's mandated assumption safe — it
        schedules its first renew against 60 minutes before it knows anything
        about this Node. A Node running shorter would expire the session
        underneath that client with no protocol signal to detect it, so the
        Node has to be incapable of the configuration in the first place.
        """
        ExclusiveSession(lifetime=MIN_LIFETIME_SECONDS)      # the boundary is legal
        with pytest.raises(ValueError, match="lifetime"):
            ExclusiveSession(lifetime=MIN_LIFETIME_SECONDS - 1.0)

    @pytest.mark.parametrize("lifetime", [0.0, -1.0, 1.0, 60.0, 600.0, 3599.0])
    def test_sub_minimum_lifetimes_are_rejected(self, lifetime: float) -> None:
        """Includes values a previous revision accepted: any positive number
        up to 24 hours used to pass, so 60 s and 600 s were legal.
        """
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
