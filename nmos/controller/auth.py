# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Controller admin-login session management.

The controller uses a single admin password supplied at Node startup
(``--controllerAdminPassword``). To keep the user experience out of the
browser's native Basic-auth popup (which is visually jarring and does
not support logout / error messaging), the controller serves a proper
HTML login page; successful authentication sets an HMAC-signed session
cookie that the admin carries through subsequent requests.

Session-cookie format::

    "<issued_at_unix>.<base64url(hmac_sha256(secret, issued_at_unix))>"

where ``secret = sha256(admin_password)``. Verifying a token requires
recomputing the HMAC with the server-side admin password — cookies
minted under an old password become invalid the moment the admin
changes the password at the CLI.

The module is deliberately dependency-free (stdlib only) so the
controller doesn't pull in ``itsdangerous`` or ``authlib`` just for
this one-password case.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

if TYPE_CHECKING:
    from nmos.controller.oauth2 import OAuth2Tokens

# Cookie lives for 12 hours by default; admins re-log once a shift.
SESSION_MAX_AGE_SECONDS: Final[int] = 12 * 3600

# Tolerance for clock skew when validating "issued in the future".
_ISSUED_AT_FUTURE_SKEW_SECONDS: Final[int] = 60


def _secret_from_password(admin_password: str) -> bytes:
    """Derive an HMAC key from the admin password.

    The password is never used directly so that token HMACs don't leak
    plaintext guesses when mounted as constant-time comparands.
    """
    return hashlib.sha256(admin_password.encode("utf-8")).digest()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    # Restore padding so ``urlsafe_b64decode`` accepts the token.
    padding = (-len(data)) % 4
    return base64.urlsafe_b64decode(data + ("=" * padding))


def issue_session_token(
    admin_password: str, issued_at: int | None = None,
) -> str:
    """Create a signed session token bound to the current admin password."""
    ts = int(time.time()) if issued_at is None else int(issued_at)
    ts_str = str(ts)
    sig = hmac.new(
        _secret_from_password(admin_password),
        ts_str.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{ts_str}.{_b64url_encode(sig)}"


def verify_session_token(
    token: str, admin_password: str,
    max_age: int = SESSION_MAX_AGE_SECONDS,
) -> bool:
    """Validate a session token. Constant-time.

    Returns ``False`` on any of: malformed token, bad signature, token
    issued in the future (beyond clock skew), token older than
    ``max_age`` seconds.
    """
    if not token:
        return False
    dot = token.find(".")
    if dot <= 0 or dot == len(token) - 1:
        return False
    ts_str, sig_str = token[:dot], token[dot + 1:]
    try:
        ts = int(ts_str)
    except ValueError:
        return False

    now = int(time.time())
    if ts > now + _ISSUED_AT_FUTURE_SKEW_SECONDS:
        return False
    if now - ts > max_age:
        return False

    expected = hmac.new(
        _secret_from_password(admin_password),
        ts_str.encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        actual = _b64url_decode(sig_str)
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(expected, actual)


def check_password(supplied: str, admin_password: str) -> bool:
    """Constant-time compare of the user-supplied password."""
    if not admin_password:
        return False
    return hmac.compare_digest(
        supplied.encode("utf-8"),
        admin_password.encode("utf-8"),
    )


# ---------------------------------------------------------------------------
# Admin session state — holds the 16-byte exclusive_key used for every
# Node-Reservation acquire this admin makes during their session.
# ---------------------------------------------------------------------------
#
# The key is generated at login and kept in memory for the session's
# lifetime. It is NEVER sent to the browser; only the session cookie
# identifies the admin, and the server looks up the state by the
# cookie's token. On logout / session drop, the state is discarded,
# prompting the reservation layer to release every device the admin
# had held.
#
# The Python controller has no user database — there is only one
# role (administrator). Two concurrent admin logins from different
# browser tabs/sessions each get their own state (their own key),
# which is the desired isolation. Each session carries its own
# per-user key without needing a database.

ADMIN_OWNER_NAME: Final[str] = "administrator"


# Two-stage login state machine. ``controller_authed`` is the brief
# window between the local password gate clearing and the Keycloak
# auth_code redirect completing. ``keycloak_authed`` is the only stage
# that grants access to the controller's protected pages — outbound
# Bearer-injection logic reads ``access_token`` from this state.
SessionStage = Literal["controller_authed", "keycloak_authed"]


@dataclass
class AdminSessionState:
    """In-memory state attached to an authenticated admin session.

    ``exclusive_key`` is 16 random bytes generated at login; it's
    the opaque identifier the Node Reservation service binds to
    every ``acquire`` this admin makes. Rendered as a hex string
    when included in a reservation ``acquire`` POST body.

    ``acquired_nodes`` tracks which node_ids this admin has
    currently reserved. The reservation layer adds/removes entries
    here as ``acquire`` / ``release`` completes. On logout the
    reservation layer walks the set to release everything. The set
    is keyed by NODE id (per NMOS: one reservation session per user
    per Node covers every sender/receiver on that Node).

    OAuth2 fields (added for Phase 3 of the controller plan):

    * ``stage``: where the admin is in the two-stage flow.
      ``controller_authed`` means the local password gate cleared but
      the Keycloak auth_code exchange has not yet completed; the
      session middleware redirects these to ``/controller/oauth2/login``
      rather than letting them through to protected pages.
      ``keycloak_authed`` is the fully-authenticated state.
    * ``oauth2_state_nonce``: the per-session ``state`` parameter sent
      to Keycloak's ``/auth`` endpoint. Echoed back by the redirect;
      we compare for equality before exchanging the code (CSRF defense).
    * ``oauth2_tokens``: the access + refresh tokens once the auth_code
      exchange completes. Replaced atomically by the proactive-refresh
      task as expiry approaches.
    """

    token: str                                  # session cookie value
    exclusive_key: bytes = field(
        default_factory=lambda: secrets.token_bytes(16),
    )
    owner: str = ADMIN_OWNER_NAME
    acquired_nodes: set[str] = field(default_factory=set)

    stage: SessionStage = "controller_authed"
    oauth2_state_nonce: str | None = None
    oauth2_tokens: "OAuth2Tokens | None" = None

    # Devices that have rejected a state-changing call with HTTP 401 +
    # ``WWW-Authenticate: ... nmos-mtls ...`` (i.e. the Node is in
    # ``client_auth_required`` mode and the controller has no client
    # cert configured). Populated reactively from outbound-call
    # failures; consulted on the next page render to paint the
    # Device's group box in the inaccessible-light-red style and
    # disable its write action buttons. Cleared on logout.
    cert_required_devices: set[str] = field(default_factory=set)

    @property
    def exclusive_key_hex(self) -> str:
        """Hex string form for the reservation ``acquire`` JSON body."""
        return self.exclusive_key.hex()

    @property
    def is_keycloak_authed(self) -> bool:
        return self.stage == "keycloak_authed" and self.oauth2_tokens is not None


class AdminSessionStore:
    """In-memory store of admin session states keyed by session token.

    Created once per controller app (stored on ``app["controller_admin_sessions"]``).
    Not persistent across process restarts — on restart, admins simply
    re-authenticate and a fresh key is issued. Active reservations on
    remote Nodes expire naturally via the Node's alivetime (60 s) when
    keepalive stops.
    """

    def __init__(self) -> None:
        self._by_token: dict[str, AdminSessionState] = {}

    def get(self, token: str) -> AdminSessionState | None:
        if not token:
            return None
        return self._by_token.get(token)

    def get_or_create(self, token: str) -> AdminSessionState:
        """Return the existing state for ``token`` or mint a fresh one.

        Called at login (where we want a fresh state) and at any
        authenticated request whose token post-dates a controller
        restart (where we want the token to still work even though
        the server-side state was lost).
        """
        state = self._by_token.get(token)
        if state is None:
            state = AdminSessionState(token=token)
            self._by_token[token] = state
        return state

    def discard(self, token: str) -> AdminSessionState | None:
        """Drop the state for ``token`` and return it (if any).

        Returned so callers can pass it to the reservation layer for
        per-device release before the state is garbage-collected.
        """
        return self._by_token.pop(token, None)

    def all_states(self) -> list[AdminSessionState]:
        """Snapshot copy — safe to iterate without lock holding."""
        return list(self._by_token.values())
