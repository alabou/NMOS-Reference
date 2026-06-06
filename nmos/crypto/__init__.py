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
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from nmos.errors import Busy, NotAllowed, Skip


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
        lifetime: float = 3600.0,
        alive_time: float = 60.0,
    ) -> None:
        """Initialize session manager.

        Args:
            lifetime: Total session lifetime in seconds (default 60 min, range 60s-24h).
            alive_time: Inactivity timeout in seconds (default 60s, range 1s-60s).
        """
        self.lifetime: float = max(60.0, min(lifetime, 86400.0))
        self.alive_time: float = max(1.0, min(alive_time, 60.0))
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
        """Get a new token (same session ID, new HMAC key).

        Only allowed after 1/3 of lifetime has elapsed.
        Raises Skip if called too early.
        Raises NotAllowed if not the session owner.
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
                raise Skip("too early to renew")

            # Generate new HMAC key, keep session ID
            s.hmac_key = os.urandom(16)
            s.token = _make_token(s.session_id, s.hmac_key)
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
