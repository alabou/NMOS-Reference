# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.crypto — ExclusiveSession."""

from __future__ import annotations

import time

import pytest

from nmos.crypto import ExclusiveSession
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
        session = ExclusiveSession(lifetime=60.0)  # minimum allowed
        token = session.acquire("owner1", b"\x00" * 16)
        # Manipulate creation time to simulate 1/3 elapsed (20+ seconds)
        session._session.creation_time = time.time() - 21.0  # type: ignore
        new_token = session.renew(token)
        assert new_token != token
        assert session.is_owner(new_token) is True
        assert session.is_owner(token) is False  # old token invalid

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
        session = ExclusiveSession(lifetime=60.0, alive_time=1.0)
        token = session.acquire("owner1", b"\x00" * 16)
        # Force expiry by manipulating creation time
        session._session.creation_time = time.time() - 61.0  # type: ignore
        assert session.is_alive() is False

    def test_inactivity_expiry(self) -> None:
        """Session expires after inactivity timeout."""
        session = ExclusiveSession(lifetime=3600.0, alive_time=1.0)
        token = session.acquire("owner1", b"\x00" * 16)
        # Force inactivity by manipulating keepalive time
        session._session.keepalive_time = time.time() - 2.0  # type: ignore
        assert session.is_alive() is False

    def test_keepalive_prevents_inactivity(self) -> None:
        """KeepAlive resets inactivity timer."""
        session = ExclusiveSession(lifetime=3600.0, alive_time=2.0)
        token = session.acquire("owner1", b"\x00" * 16)
        time.sleep(1.0)
        session.keep_alive(token)
        time.sleep(1.0)
        assert session.is_alive() is True  # still alive due to keepalive


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
