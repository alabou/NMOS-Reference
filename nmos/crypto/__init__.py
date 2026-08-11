# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""ExclusiveSession — token-based mutual exclusion for NMOS Node API.

Grants exclusive device access to one client at a time via cryptographically
secure bearer tokens. Session expires via dual timeout: total lifetime AND
inactivity.

Token format: Base64(session_id[16] + HMAC-SHA256(session_id, hmac_key)[:16])
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Final

from nmos.errors import Busy, NotAllowed, Skip


# ---------------------------------------------------------------------------
# Session timings, fixed by "NMOS With Node Reservation.md"
# ---------------------------------------------------------------------------
#
# The specification pins these, and there is no protocol mechanism for a
# client to discover what a Node uses. An operator who set them per-instance
# would have to configure every device in the deployment identically, with
# nothing able to detect a mismatch — a renew/keepalive schedule computed
# against the wrong window fails silently. So this implementation always runs
# the defaults: ``nmos_node.py`` constructs ``ExclusiveSession()`` with no
# arguments and exposes no option to change them.
#
# The constructor keeps the Lifetime parameter because the specification does
# permit an administrator to lengthen it — but the values are *validated*
# rather than clamped. Silent clamping is what let an out-of-spec AliveTime
# look accepted.

#: §Definitions: "By default the lifetime of a session is 60 minutes."
DEFAULT_LIFETIME_SECONDS: Final[float] = 3600.0

#: §Session Lifetime versus AliveTime: "The configured 'Session Lifetime' MUST
#: be 60 minutes or greater, to a maximum of 24 hours."
#:
#: The floor is what makes the whole scheduling scheme safe. A client that has
#: not yet determined a Node's Lifetime "MUST assume the minimum 'Session
#: Lifetime' of 60 minutes when scheduling its first Renew" — so a Node running
#: anything shorter would silently expire sessions under a conforming client,
#: with no protocol signal to detect it. Enforcing the floor here is what makes
#: that assumption sound rather than hopeful.
MIN_LIFETIME_SECONDS: Final[float] = 3600.0

#: §Session Lifetime versus AliveTime: "... to a maximum of 24 hours".
MAX_LIFETIME_SECONDS: Final[float] = 86400.0

#: §Definitions: "The AliveTime of a session is 60 seconds."
#: §Session Lifetime versus AliveTime: "The 'Session AliveTime' MUST be 60
#: seconds."
#:
#: A single mandated value — not a default and not a range. The protocol gives
#: a client no way to discover the AliveTime in use, so every client must
#: schedule keepalives against 60 s regardless; a Node running anything else
#: would be undetectably incompatible. Fixing it removes the knob rather than
#: leaving one nobody can safely turn.
ALIVE_TIME_SECONDS: Final[float] = 60.0


class TooEarly(Skip):
    """Renew attempted before 1/3 of the Session Lifetime has elapsed.

    Carries the ``Retry-After`` delay the Node must return with its ``425 Too
    Early`` response. §Renew: "The delay MUST be the number of seconds
    remaining until half of the 'Session Lifetime' has elapsed, measured from
    the most recent successful ``Acquire`` or ``Renew`` of the session."

    Note the delay targets the *half* Lifetime, not the 1/3 gate that caused
    the rejection — "The delay therefore indicates the point at which the
    session SHOULD be renewed and not the earliest point at which a ``Renew``
    would be permitted." Pointing at the gate would send every client back at
    the earliest legal instant instead of the recommended one.

    A subclass of :class:`~nmos.errors.Skip` so that callers written against
    the older control-flow signal keep working; new code catches this and
    reads :attr:`retry_after`. Computing the delay here rather than exposing a
    second query method keeps it atomic with the rejection — the session lock
    is held for both, so the value cannot describe a state that has already
    moved on.
    """

    def __init__(self, retry_after: int, msg: str = "too early to renew") -> None:
        super().__init__(msg)
        #: Whole seconds until the half-Lifetime point. Always >= 1.
        self.retry_after: int = retry_after


@dataclass
class _Session:
    """Internal session state."""
    owner: str
    session_id: bytes        # 16 bytes, generated on acquire
    hmac_key: bytes          # 16 bytes, regenerated on renew
    token: str               # base64 encoded bearer token
    exclusive_key: bytes     # 16 bytes, provided by client
    creation_time: float     # POSIX timestamp
    keepalive_time: float    # last activity timestamp
    expired: bool = False


def _make_token(session_id: bytes, hmac_key: bytes) -> str:
    """Create bearer token: Base64(session_id + HMAC-SHA256(session_id, hmac_key)[:16])."""
    mac = hmac.new(hmac_key, session_id, hashlib.sha256).digest()[:16]
    return base64.b64encode(session_id + mac).decode("ascii")


def _verify_token(token: str, session_id: bytes, hmac_key: bytes) -> bool:
    """Verify a bearer token against stored session_id and hmac_key."""
    try:
        raw = base64.b64decode(token)
    except (ValueError, TypeError):
        return False
    if len(raw) != 32:
        return False
    token_id = raw[:16]
    token_mac = raw[16:]
    if token_id != session_id:
        return False
    expected_mac = hmac.new(hmac_key, session_id, hashlib.sha256).digest()[:16]
    return hmac.compare_digest(token_mac, expected_mac)


class ExclusiveSession:
    """Token-based mutual exclusion for device access control.

    Once one client owns the session, no other client can acquire it until
    the session expires (lifetime or inactivity) or is explicitly released.

    Thread-safe: all public methods are mutex-protected.
    """

    def __init__(
        self,
        lifetime: float = DEFAULT_LIFETIME_SECONDS,
        alive_time: float = ALIVE_TIME_SECONDS,
    ) -> None:
        """Initialize session manager.

        Args:
            lifetime: Total session lifetime in seconds. Must be at least
                :data:`MIN_LIFETIME_SECONDS` and no more than
                :data:`MAX_LIFETIME_SECONDS`.
            alive_time: Inactivity timeout in seconds. Must be exactly
                :data:`ALIVE_TIME_SECONDS` — the specification mandates one
                value and forbids every other.

        Raises:
            ValueError: if either argument is outside what the specification
                permits. Both were previously clamped into range, which meant
                an out-of-spec value was silently coerced. Failing loudly is
                the only way a misconfiguration is visible, since the protocol
                gives a peer no way to discover the values in use.
        """
        if not MIN_LIFETIME_SECONDS <= lifetime <= MAX_LIFETIME_SECONDS:
            raise ValueError(
                f"session lifetime {lifetime!r}s is out of range: must be at "
                f"least {MIN_LIFETIME_SECONDS:.0f}s (60 minutes) and at most "
                f"{MAX_LIFETIME_SECONDS:.0f}s (24 hours), per 'NMOS With Node "
                f"Reservation' §Session Lifetime versus AliveTime: \"The "
                f"configured 'Session Lifetime' MUST be 60 minutes or greater, "
                f"to a maximum of 24 hours.\""
            )
        if alive_time != ALIVE_TIME_SECONDS:
            raise ValueError(
                f"session AliveTime {alive_time!r}s is not permitted: must be "
                f"exactly {ALIVE_TIME_SECONDS:.0f} seconds. 'NMOS With Node "
                f"Reservation' §Session Lifetime versus AliveTime: \"The "
                f"'Session AliveTime' MUST be 60 seconds.\""
            )
        self.lifetime: float = lifetime
        self.alive_time: float = alive_time
        self.private: Any = None
        self.user: Any = None
        self._lock: threading.Lock = threading.Lock()
        self._session: _Session | None = None

    def acquire(self, owner: str, exclusive_key: bytes) -> str:
        """Acquire exclusive access. Returns bearer token.

        Raises Busy if session is already alive and owned by another client.
        """
        with self._lock:
            if self._is_alive_internal():
                raise Busy("exclusive session already acquired")

            session_id = os.urandom(16)
            hmac_key = os.urandom(16)
            token = _make_token(session_id, hmac_key)
            now = time.time()

            self._session = _Session(
                owner=owner,
                session_id=session_id,
                hmac_key=hmac_key,
                token=token,
                exclusive_key=exclusive_key,
                creation_time=now,
                keepalive_time=now,
            )
            return token

    def renew(self, token: str) -> str:
        """Get a new token (same session ID, new HMAC key), extending the Lifetime.

        Only allowed after 1/3 of lifetime has elapsed.
        Raises :class:`TooEarly` (a ``Skip``) if called too early, carrying the
        ``Retry-After`` delay the caller must put on the ``425`` response.
        Raises NotAllowed if not the session owner.

        **Renewal restarts the Lifetime clock.** That is the whole purpose of
        the operation, and the specification assigns it to renew alone —
        §KeepAlive: "The KeepAlive operation MUST NOT extend the session
        Lifetime. Only the Renew operation extends the session Lifetime."

        ``creation_time`` is therefore reset here. Leaving it at the original
        acquire — which is what this did — meant no amount of correct renewing
        could keep a session alive: :meth:`_is_alive_internal` measures the
        Lifetime from ``creation_time``, so every session died exactly
        ``lifetime`` seconds after it was first acquired, and the next
        keepalive or renew came back 401. The owner then reacquired and
        silently got a *different* session, releasing exclusivity for the gap
        and changing the exclusive key mixed into the PEP derivation.

        Note the ordering: the 1/3 gate is evaluated against the *previous*
        creation_time before it is overwritten, so each renewal must wait
        another third of a Lifetime — the gate stays meaningful.
        """
        with self._lock:
            s = self._session
            if s is None or s.expired:
                raise NotAllowed("no active session")

            if not _verify_token(token, s.session_id, s.hmac_key):
                raise NotAllowed("invalid token")

            now = time.time()
            elapsed = now - s.creation_time
            if elapsed < self.lifetime / 3:
                # §Renew: the delay counts down to the *half* Lifetime, the
                # point at which the session SHOULD be renewed — not to the
                # 1/3 gate just failed. Rounded up so a client that honours it
                # exactly cannot land a fraction of a second short and collect
                # a second 425; clamped at 1 so the value never reads as
                # "retry immediately".
                #
                # The gate guarantees elapsed < lifetime/3, so the delay
                # exceeds lifetime/6 — 600 s at the minimum Lifetime. The
                # clamp is therefore unreachable in practice and is here to
                # keep the invariant local rather than inherited from the
                # bounds enforced in __init__.
                remaining = self.lifetime / 2 - elapsed
                raise TooEarly(max(1, math.ceil(remaining)))

            # Generate new HMAC key, keep session ID
            s.hmac_key = os.urandom(16)
            s.token = _make_token(s.session_id, s.hmac_key)
            s.creation_time = now      # the Lifetime restarts from this renewal
            s.keepalive_time = now
            return s.token

    def keep_alive(self, token: str) -> None:
        """Reset inactivity timer.

        Raises NotAllowed if not the session owner.
        """
        with self._lock:
            s = self._session
            if s is None or s.expired:
                raise NotAllowed("no active session")

            if not _verify_token(token, s.session_id, s.hmac_key):
                raise NotAllowed("invalid token")

            s.keepalive_time = time.time()

    def release(self, token: str) -> None:
        """End session.

        Raises NotAllowed if not the session owner.
        """
        with self._lock:
            s = self._session
            if s is None or s.expired:
                raise NotAllowed("no active session")

            if not _verify_token(token, s.session_id, s.hmac_key):
                raise NotAllowed("invalid token")

            s.expired = True
            s.exclusive_key = b"\x00" * 16

    def is_owner(self, token: str) -> bool:
        """Validate token and perform keep-alive side effect.

        Returns True if token is valid and session is alive.
        """
        with self._lock:
            s = self._session
            if s is None or s.expired:
                return False

            if not self._is_alive_internal():
                return False

            if not _verify_token(token, s.session_id, s.hmac_key):
                return False

            s.keepalive_time = time.time()
            return True

    def is_alive(self) -> bool:
        """Check if session is alive (both timeouts satisfied).

        Marks session as expired if not alive (important for state transitions).
        """
        with self._lock:
            return self._is_alive_internal()

    def get_key(self) -> bytes | None:
        """Return exclusive_key if session alive, else None."""
        with self._lock:
            if not self._is_alive_internal():
                return None
            s = self._session
            if s is None:
                return None
            return s.exclusive_key

    @property
    def owner(self) -> str | None:
        """Return current session owner, or None."""
        with self._lock:
            s = self._session
            if s is None or s.expired:
                return None
            return s.owner

    # --- Internal (must be called with lock held) ---

    def _is_alive_internal(self) -> bool:
        """Check both timeouts without acquiring lock."""
        s = self._session
        if s is None or s.expired:
            return False

        now = time.time()

        # Lifetime check
        if now - s.creation_time >= self.lifetime:
            s.expired = True
            return False

        # Inactivity check
        if now - s.keepalive_time >= self.alive_time:
            s.expired = True
            return False

        return True
