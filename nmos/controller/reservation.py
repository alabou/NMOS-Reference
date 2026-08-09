# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Node Reservation session management for the controller.

Implements the admin-driven Node-Reservation lifecycle:

* **Acquire** — when the admin ticks the Exclusivity toggle (or the
  activation flow needs a session to inject
  ``PEP-Exclusive-Authorization``), the controller POSTs ``acquire/``
  to the remote Node's reservation service. The token returned is
  cached per (admin-session, node) key.

* **Keepalive / renew** — a single background task ticks every
  second and, for every active session, posts ``keepalive`` when
  ``now + HALF_ALIVETIME > alive_until`` and ``renew`` when
  ``now + HALF_LIFETIME > expires_at``. On failure the session's
  token is zeroed, prompting the next tick to re-``acquire``.

* **Release** — explicit release either from the browser (unticking
  Exclusivity) or on admin logout (``release_all(admin)``). Runs
  against every (admin, node) pair the admin still holds.

Scope: per ``(admin_session_token, node_id)`` — one Node reservation
covers every sender/receiver + device on that Node. Uses a
per-instance map keyed by ``(admin, node)`` and the service is
advertised on the Node (not per-device). Two concurrent admins
each hold their own sessions on their own exclusive_keys; the
Python controller has no user database so we derive "ownership"
from the admin's session cookie.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Final

from nmos.controller.api_client import RemoteCallResult, RemoteNodeClient
from nmos.controller.auth import AdminSessionState, AdminSessionStore
from nmos.controller.debug_trace import DebugTrace


log = logging.getLogger(__name__)


# Reservation policy constants. ``LIFETIME`` is the Node-side
# session lifetime — we renew at half of that. ``ALIVETIME`` is
# the Node's AliveTime window; we keepalive at half.
LIFETIME: Final[float] = 3600.0       # 60 min session lifetime
HALF_LIFETIME: Final[float] = 1800.0  # renew when < 30 min remains
ALIVETIME: Final[float] = 60.0        # 60 s AliveTime window
HALF_ALIVETIME: Final[float] = 30.0   # keepalive when < 30 s remains
POLL_INTERVAL: Final[float] = 1.0     # per-second tick

#: ``425 Too Early`` (RFC 8470), the status §Renew mandates when renewal is
#: attempted "before 1/3 of the session lifetime". Named because a bare 425 in
#: a status comparison reads as a typo for one of the 4xx everyone knows.
HTTP_TOO_EARLY: Final[int] = 425

# How long to wait before retrying a renew that came back ``425 Too Early``.
#
# §Verifying Ownership: "If the renewal returns a `425 Too Early` status, the
# token SHOULD be considered to be still valid for at least half of its
# 'Session Lifetime'." So there is no urgency — the safe move is to keep the
# token and try again later. A retry interval is needed because the renew
# trigger is a threshold rather than an edge: without one, a 425 would be
# re-attempted on every one-second tick for as long as the threshold held.
RENEW_TOO_EARLY_RETRY: Final[float] = 60.0


def _owner_from_link(link: str) -> str:
    """Extract the owner the Node named in its ``Link`` header.

    The Node emits ``Link: <https://{percent-encoded owner}>`` alongside a 423
    (``handlers_exclusive.py``). Parsed defensively: a malformed or absent
    header costs the operator a name, never the error itself.
    """
    if not link:
        return ""
    raw = link.strip()
    if raw.startswith("<") and ">" in raw:
        raw = raw[1:raw.index(">")]
    # Only the ``https://`` form is emitted; tolerate its absence rather than
    # depending on it.
    for prefix in ("https://", "http://"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    return urllib.parse.unquote(raw).strip()


class ReservationError(Exception):
    """Base class for reservation failures."""


class ReservationLocked(ReservationError):
    """The target Node returned 423 Locked on acquire — another admin
    (or another instance of the controller) already holds the session.
    The UI should surface this clearly so the operator can retry once
    the other party releases.

    ``owner`` is whoever the Node says currently holds it, taken from the
    ``Link`` header it returns with the 423. Empty when the Node did not say.
    Reporting the Node's own answer beats the controller guessing: "held by
    administrator" tells an operator something actionable, whereas "held by
    another owner" is a phrase the controller made up.
    """

    def __init__(self, msg: str = "", *, owner: str = "") -> None:
        super().__init__(msg)
        self.owner = owner


@dataclass
class ReservationSession:
    """One live reservation held on a remote Node.

    ``token`` is empty between acquire attempts. The polling task
    reacquires when it sees ``token == ""`` — this is how renew /
    keepalive failures are converted back into an acquire attempt
    (the zero-the-token pattern).

    ``node_id`` is the IS-04 Node UUID that owns the reservation
    service. One session per (admin, node) covers every sender /
    receiver / device hosted on that Node. All timestamps are
    ``time.monotonic()`` values.

    ``oauth2_on_remote`` is captured at acquire time from the Node's
    ``services[]`` entry's ``authorization`` flag and decides which
    HTTP header carries the session bearer on subsequent renew /
    release / keepalive calls — ``PEP-Exclusive-Authorization`` when
    true, ``Authorization`` when false, per the NMOS With Node
    Reservation spec §"Using Reservation along with OAuth2.0
    authorizations".
    """

    admin_session_token: str
    node_id: str
    base_url: str
    owner: str
    exclusive_key_hex: str
    oauth2_on_remote: bool = False
    token: str = ""
    expires_at: float = 0.0
    alive_until: float = 0.0
    renew_after: float = 0.0
    """Earliest monotonic time at which renew may be attempted again.

    Set when the Node answers a renew with ``425 Too Early``: the token stays
    valid (§Verifying Ownership guarantees at least half a Session Lifetime),
    so the right response is to defer rather than to discard. Zero means "no
    deferral", which is the normal state.

    Deliberately a separate field rather than a nudge to ``expires_at``:
    ``expires_at`` is what the keepalive and renew thresholds are both derived
    from, and moving it to encode a retry delay would misstate how long the
    session is believed to last. Keeping them apart also lets a deferred renew
    fall through to the keepalive branch in the same tick, which matters — a
    session waiting to renew must still be kept alive or it lapses on
    inactivity while waiting.
    """


class SessionStore:
    """Per-(admin-session × node) reservation store + polling task.

    Owned by the controller app (``app["controller_reservations"]``).
    Instantiated once at app startup; ``start()`` launches the poll
    task, ``stop()`` cancels it and releases every active session.
    """

    def __init__(
        self,
        client: RemoteNodeClient,
        admin_sessions: AdminSessionStore,
        debug_trace: DebugTrace | None = None,
    ) -> None:
        self._client = client
        self._admin_sessions = admin_sessions
        self._debug: DebugTrace | None = debug_trace
        self._sessions: dict[tuple[str, str], ReservationSession] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    def attach_debug(self, debug_trace: DebugTrace) -> None:
        """Attach the tracer so background renew/keepalive events show
        up in the debug log alongside admin-driven acquire/release.
        """
        self._debug = debug_trace

    def _emit(self, kind: str, **fields: Any) -> None:
        """Emit a reservation-lifecycle event if debug tracing is on."""
        if self._debug is not None and self._debug.enabled:
            self._debug.emit(kind, **fields)

    def _oauth2_authz_headers(
        self, admin_session_token: str, oauth2_on_remote: bool,
    ) -> dict[str, str]:
        """Build the ``Authorization`` header for outbound calls to a
        Node whose exclusive service requires OAuth2.

        Per "NMOS With OAuth2.0" §"Reservation along with OAuth2.0":
        the OAuth2 access token belongs in ``Authorization``; the
        reservation session token (when one exists) moves to
        ``PEP-Exclusive-Authorization`` (handled separately by
        ``_with_exclusive_token`` in :mod:`api_client`).

        For acquire (no session token yet) only the OAuth2 bearer
        is needed. For renew / release / keepalive both go in their
        respective headers.

        Returns ``{}`` when OAuth2 is off on the remote, or when
        the admin's session has no OAuth2 token (will surface as a
        401 from the remote — the controller's reactive 401 handling
        flags the device as inaccessible).
        """
        if not oauth2_on_remote:
            return {}
        admin = self._admin_sessions.get(admin_session_token)
        if admin is None or admin.oauth2_tokens is None:
            return {}
        return {
            "Authorization": f"Bearer {admin.oauth2_tokens.access_token}",
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background keepalive/renew task."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Cancel the background task AND release every active session.

        Called on controller shutdown. Best-effort: failures here are
        logged but do not propagate — we're tearing down anyway.
        """
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Release all remaining sessions.
        async with self._lock:
            snapshot = list(self._sessions.values())
            self._sessions.clear()
        for sess in snapshot:
            await self._release_session(sess)

    # ------------------------------------------------------------------
    # Admin-facing operations
    # ------------------------------------------------------------------

    async def acquire(
        self,
        admin: AdminSessionState,
        node_id: str,
        base_url: str,
        *,
        oauth2_on_remote: bool,
        trace_id: str = "",
    ) -> str:
        """Acquire (or re-use) a reservation session on ``node_id``.

        Returns the bearer token to inject on every subsequent
        state-changing call against this Node. The caller passes
        ``oauth2_on_remote`` from the service entry's
        ``authorization`` flag so renew / release / keepalive know
        which header to use for the bearer (``Authorization`` when
        OAuth2 is off on the remote, ``PEP-Exclusive-Authorization``
        when it is on).

        Raises:

        * ``ReservationLocked`` — 423 Locked (held by someone else);
        * ``ReservationError`` — 5xx / network / unexpected status.
        """
        key = (admin.token, node_id)
        async with self._lock:
            existing = self._sessions.get(key)
            if existing is not None and existing.token:
                self._emit(
                    "reservation_reused",
                    trace_id=trace_id, node_id=node_id,
                    admin=admin.token[:6],
                )
                return existing.token

        result = await self._client.acquire_exclusive(
            base_url,
            owner=admin.owner,
            exclusive_key_hex=admin.exclusive_key_hex,
            forwarded_headers=self._oauth2_authz_headers(
                admin.token, oauth2_on_remote,
            ),
            trace_id=trace_id,
        )
        try:
            self._raise_on_acquire_failure(result, node_id)
        except ReservationError as exc:
            self._emit(
                "reservation_acquire_failed",
                trace_id=trace_id, node_id=node_id,
                status=result.status, error=str(exc),
            )
            raise
        assert isinstance(result.body, str)
        token: str = result.body
        now = time.monotonic()
        sess = ReservationSession(
            admin_session_token=admin.token,
            node_id=node_id,
            base_url=base_url,
            owner=admin.owner,
            exclusive_key_hex=admin.exclusive_key_hex,
            oauth2_on_remote=oauth2_on_remote,
            token=token,
            expires_at=now + LIFETIME,
            alive_until=now + ALIVETIME,
        )
        async with self._lock:
            self._sessions[key] = sess
        admin.acquired_nodes.add(node_id)
        log.info("reservation acquired node=%s admin=%s…",
                 node_id, admin.token[:6])
        self._emit(
            "reservation_acquired",
            trace_id=trace_id, node_id=node_id,
            admin=admin.token[:6],
        )
        return token

    async def release(
        self,
        admin: AdminSessionState,
        node_id: str,
        *,
        trace_id: str = "",
    ) -> None:
        """Release a single session. Safe to call when no session is
        held (no-op).
        """
        key = (admin.token, node_id)
        async with self._lock:
            sess = self._sessions.pop(key, None)
        admin.acquired_nodes.discard(node_id)
        if sess is not None:
            await self._release_session(sess, trace_id=trace_id)

    async def release_all(
        self,
        admin: AdminSessionState,
        *,
        trace_id: str = "",
    ) -> None:
        """Release every session this admin currently holds.

        Called on admin logout and as part of app shutdown. Iterates
        a snapshot so the store isn't held across HTTP calls.
        """
        async with self._lock:
            to_release = [
                s for key, s in self._sessions.items()
                if key[0] == admin.token
            ]
            for sess in to_release:
                del self._sessions[(sess.admin_session_token, sess.node_id)]
        admin.acquired_nodes.clear()
        for sess in to_release:
            await self._release_session(sess, trace_id=trace_id)

    def snapshot(self) -> list[ReservationSession]:
        """Return a shallow copy of every live reservation session.

        Used by the debug snapshot endpoint. Reading the dict is safe
        under the GIL without the async lock; the returned list is
        independent of internal state so callers can iterate freely.
        """
        return list(self._sessions.values())

    def current_token(
        self,
        admin: AdminSessionState,
        node_id: str,
    ) -> str | None:
        """Return the current bearer token for this admin+node, or
        ``None`` if no session is held or the session is between
        acquire attempts.

        Synchronous fast-path used by request handlers to decide
        whether to inject ``PEP-Exclusive-Authorization``. Reading a
        dict under the GIL is safe without the async lock.
        """
        sess = self._sessions.get((admin.token, node_id))
        if sess is None or not sess.token:
            return None
        return sess.token

    # ------------------------------------------------------------------
    # Background polling task
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Once per second, walk every active session and maintain it.

        The per-session branch order is:
          1. If ``token == ""`` — (re)acquire.
          2. Else if ``now + HALF_LIFETIME > expires_at`` — renew.
          3. Else if ``now + HALF_ALIVETIME > alive_until`` — keepalive.
        """
        log.info("reservation poll task started")
        try:
            while True:
                try:
                    await self._tick()
                except Exception:
                    # One bad session shouldn't kill the whole loop.
                    log.exception("reservation tick error")
                await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            log.info("reservation poll task cancelled")
            raise

    async def _tick(self) -> None:
        async with self._lock:
            snapshot = list(self._sessions.items())
        now = time.monotonic()
        for key, sess in snapshot:
            # If the admin session has been discarded (logout without
            # calling release_all), drop the reservation session too —
            # don't try to maintain a reservation for a vanished owner.
            if self._admin_sessions.get(sess.admin_session_token) is None:
                async with self._lock:
                    self._sessions.pop(key, None)
                await self._release_session(sess)
                continue

            if not sess.token:
                await self._try_reacquire(sess)
                continue
            # ``renew_after`` defers a renew the Node called too early. Note
            # this is not a ``continue`` when deferred: the session still
            # needs keeping alive while it waits, or it lapses on inactivity.
            if now + HALF_LIFETIME > sess.expires_at and now >= sess.renew_after:
                await self._try_renew(sess)
                continue
            if now + HALF_ALIVETIME > sess.alive_until:
                await self._try_keepalive(sess)

    async def _try_reacquire(self, sess: ReservationSession) -> None:
        result = await self._client.acquire_exclusive(
            sess.base_url,
            owner=sess.owner,
            exclusive_key_hex=sess.exclusive_key_hex,
            forwarded_headers=self._oauth2_authz_headers(
                sess.admin_session_token, sess.oauth2_on_remote,
            ),
        )
        try:
            self._raise_on_acquire_failure(result, sess.node_id)
        except ReservationError as exc:
            log.warning("reacquire failed node=%s: %s", sess.node_id, exc)
            self._emit(
                "reservation_reacquire_failed",
                node_id=sess.node_id, status=result.status, error=str(exc),
            )
            return
        assert isinstance(result.body, str)
        now = time.monotonic()
        sess.token = result.body
        sess.expires_at = now + LIFETIME
        sess.alive_until = now + ALIVETIME
        sess.renew_after = 0.0     # a fresh session carries no renew deferral
        self._emit("reservation_reacquired", node_id=sess.node_id)

    async def _try_renew(self, sess: ReservationSession) -> None:
        result = await self._client.renew_exclusive(
            sess.base_url, session_token=sess.token,
            forwarded_headers=self._oauth2_authz_headers(
                sess.admin_session_token, sess.oauth2_on_remote,
            ),
            oauth2_on_remote=sess.oauth2_on_remote,
        )
        if result.status == HTTP_TOO_EARLY:
            # Not a failure. The Node refuses renew until a third of the
            # Session Lifetime has passed, and §Verifying Ownership says the
            # token "SHOULD be considered to be still valid for at least half
            # of its 'Session Lifetime'" in exactly this case. So keep it and
            # come back later.
            #
            # Discarding it here — which is what the generic non-200 branch
            # below used to do — was actively harmful: zeroing the token sends
            # the next tick down the reacquire path, and the Node refuses that
            # with 423 because its own session is still very much alive. The
            # session then sticks in a state where the controller holds no
            # usable token, cannot release, and every reacquire is rejected,
            # while the panel still shows the reservation as held.
            sess.renew_after = time.monotonic() + RENEW_TOO_EARLY_RETRY
            log.info(
                "renew too early node=%s — token remains valid, retrying in %.0fs",
                sess.node_id, RENEW_TOO_EARLY_RETRY,
            )
            self._emit(
                "reservation_renew_too_early",
                node_id=sess.node_id, status=result.status,
                retry_in=RENEW_TOO_EARLY_RETRY,
            )
            return
        if result.status != 200 or not isinstance(result.body, str):
            log.warning(
                "renew failed node=%s status=%s — will reacquire",
                sess.node_id, result.status,
            )
            self._emit(
                "reservation_renew_failed",
                node_id=sess.node_id, status=result.status,
            )
            # Zero the session; next tick's reacquire branch fires.
            sess.token = ""
            sess.expires_at = 0.0
            sess.alive_until = 0.0
            sess.renew_after = 0.0
            return
        now = time.monotonic()
        sess.token = result.body
        sess.expires_at = now + LIFETIME
        sess.alive_until = now + ALIVETIME
        sess.renew_after = 0.0     # the deferral, if any, has been discharged
        self._emit("reservation_renewed", node_id=sess.node_id)

    async def _try_keepalive(self, sess: ReservationSession) -> None:
        result = await self._client.keepalive_exclusive(
            sess.base_url, session_token=sess.token,
            forwarded_headers=self._oauth2_authz_headers(
                sess.admin_session_token, sess.oauth2_on_remote,
            ),
            oauth2_on_remote=sess.oauth2_on_remote,
        )
        if result.status != 200:
            log.warning(
                "keepalive failed node=%s status=%s — will reacquire",
                sess.node_id, result.status,
            )
            self._emit(
                "reservation_keepalive_failed",
                node_id=sess.node_id, status=result.status,
            )
            sess.token = ""
            sess.expires_at = 0.0
            sess.alive_until = 0.0
            return
        sess.alive_until = time.monotonic() + ALIVETIME
        self._emit("reservation_keepalive", node_id=sess.node_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _release_session(
        self, sess: ReservationSession, *, trace_id: str = "",
    ) -> None:
        """Best-effort release of a single session on the remote Node.

        Failures are logged, not raised — release is a cleanup path
        and the local state has already been cleared by the caller.
        """
        if not sess.token:
            return
        try:
            result = await self._client.release_exclusive(
                sess.base_url,
                session_token=sess.token,
                forwarded_headers=self._oauth2_authz_headers(
                    sess.admin_session_token, sess.oauth2_on_remote,
                ),
                oauth2_on_remote=sess.oauth2_on_remote,
                trace_id=trace_id,
            )
            if result.status != 200:
                log.warning(
                    "release returned status=%s for node=%s",
                    result.status, sess.node_id,
                )
                self._emit(
                    "reservation_release_failed",
                    trace_id=trace_id, node_id=sess.node_id,
                    status=result.status,
                )
                return
            self._emit(
                "reservation_released",
                trace_id=trace_id, node_id=sess.node_id,
            )
        except Exception as exc:
            log.exception(
                "release network error for node=%s", sess.node_id,
            )
            self._emit(
                "reservation_release_error",
                trace_id=trace_id, node_id=sess.node_id, error=repr(exc),
            )

    @staticmethod
    def _raise_on_acquire_failure(
        result: RemoteCallResult, node_id: str,
    ) -> None:
        if result.status == 200:
            return
        if result.status == 423:
            owner = _owner_from_link(result.link)
            held_by = f"by {owner!r}" if owner else "by another owner"
            raise ReservationLocked(
                f"reservation for node {node_id!r} already held {held_by}",
                owner=owner,
            )
        detail: Any = result.error or result.body
        raise ReservationError(
            f"acquire failed for node {node_id!r} "
            f"status={result.status} detail={detail!r}",
        )
