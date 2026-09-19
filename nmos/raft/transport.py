# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Peer connectivity: two mTLS links per peer, and the handshake that gates them.

The abstraction comes first
---------------------------
:class:`Transport` and :class:`PeerHandler` are protocols, and ``node.py``
depends only on them. That is not architectural decoration -- it is what makes
consensus testable at all. The deterministic harness supplies an in-memory
transport with a controllable clock and injectable partitions, so election
safety can be driven through exactly the scenario that breaks it, repeatably,
with no sockets and no sleeping. A consensus layer that could only be tested
over real TCP would be tested only in the cases that are easy to provoke, which
are not the cases that matter.

Two links per peer
------------------
CONTROL carries elections, replication and heartbeats. BULK carries snapshot
transfers and nothing else.

They are separate because a snapshot is the entire registry serialised, and a
follower that stops hearing heartbeats starts an election. Sharing one link
would let a multi-megabyte transfer stall the very timer whose job is to
prevent elections -- so installing a snapshot would cause leadership churn,
that churn would cause more members to fall behind, and the resulting
instability would be blamed on load rather than on head-of-line blocking. The
etcd client makes the same split for the same reason.

The handshake is a gate, not a greeting
---------------------------------------
Every connection begins with ``Hello``/``HelloAck``, and a mismatch closes the
link rather than negotiating:

* a different ``cluster_id`` means these two members belong to different
  clusters, which is precisely the split the cluster token exists to detect;
* a different protocol *major* is not negotiable, by construction;
* a different *minor* is fine in both directions -- unknown fields are skipped.

``incarnation`` also travels here, and it is what tells a leader that a peer
has restarted and come back with an empty log. See ``persist.py`` for why that
matters more than it looks.

The certificate name is a second gate, and it is the load-bearing one
---------------------------------------------------------------------
Chain validation alone proves only that the peer holds *a* certificate from a
trusted CA. In an IPMX deployment that CA is the Product CA, which has signed
every device certificate in the building -- so a camera could complete an mTLS
handshake with the registry database and start proposing log entries.

``peer_name`` closes that. It is one shared SAN carried by the cluster's
certificates and by nothing else, checked in **both** directions: outbound as
``server_hostname`` (so ``check_hostname`` verifies it instead of the address,
which matters because co-located members all share one address), and inbound by
inspecting the presented certificate before the ``HelloAck`` is written. This
is the same posture ``nmos/etcd/supervisor.py`` gets from etcd's
``--peer-cert-allowed-hostname`` and ``--client-cert-allowed-hostname``.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from nmos.raft.errors import RaftClusterMismatch, RaftProtocolError, RaftUnavailable
from nmos.raft.messages import (
    EXPECTED_REPLY,
    AppendEntries,
    AppendEntriesReply,
    Forward,
    ForwardReply,
    Hello,
    HelloAck,
    InstallSnapshot,
    InstallSnapshotReply,
    Promote,
    Propose,
    ProposeReply,
    RequestVote,
    RequestVoteReply,
    decode_message,
)
from nmos.raft.wire import (
    FLAG_REPLY,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    Frame,
    MessageType,
    Stream,
    encode_frame,
    read_frame,
)

log = logging.getLogger(__name__)

_RECONNECT_INITIAL = 0.05
_RECONNECT_MAX = 2.0


@runtime_checkable
class PeerHandler(Protocol):
    """What a transport delivers inbound messages to.

    The consensus handlers are **synchronous**, and that is required rather
    than convenient: ``on_append_entries`` can advance the commit index, which
    applies entries, which mutates the store -- and ``store.py``'s invariant is
    that no mutation yields. Making them coroutines would put an ``await``
    somewhere inside that path sooner or later.

    ``on_propose`` and ``on_forward`` are coroutines because both genuinely
    wait for a commit before they can answer.
    """

    def on_request_vote(self, peer: int, message: RequestVote) -> RequestVoteReply: ...

    def on_append_entries(
        self, peer: int, message: AppendEntries,
    ) -> AppendEntriesReply: ...

    def on_install_snapshot(
        self, peer: int, message: InstallSnapshot,
    ) -> InstallSnapshotReply: ...

    def on_promote(self, peer: int, message: Promote) -> None: ...

    def on_request_vote_reply(
        self, peer: int, message: RequestVoteReply,
    ) -> None:
        """A vote arrived for an election this member is running.

        Replies to ``send``-dispatched messages come back as unsolicited
        frames rather than as the resolution of a correlated request, because
        the consensus messages are fire-and-forget: a leader that awaited each
        follower's acknowledgement in turn would serialise replication behind
        its slowest peer.
        """
        ...

    def on_append_entries_reply(
        self, peer: int, message: AppendEntriesReply,
    ) -> None: ...

    def on_install_snapshot_reply(
        self, peer: int, message: InstallSnapshotReply,
    ) -> None: ...

    async def on_propose(self, peer: int, message: Propose) -> ProposeReply: ...

    async def on_forward(self, peer: int, message: Forward) -> ForwardReply: ...

    def on_peer_state(self, peer: int, *, up: bool, incarnation: int) -> None:
        """A peer's link came up or went down.

        ``incarnation`` is meaningful only when ``up`` is True. A change in it
        is how a leader learns that this peer restarted, which is the trigger
        for catching it up before counting it toward quorum.
        """
        ...


@runtime_checkable
class Transport(Protocol):
    """Sending to peers, and being told who is reachable."""

    async def start(self, handler: PeerHandler) -> None: ...

    async def close(self) -> None: ...

    def send(self, peer: int, message: Any, *, stream: Stream = Stream.CONTROL) -> None:
        """Fire and forget. Never raises for an unreachable peer.

        Replication is built on retry: a leader that had to handle "this peer
        is down" at every send would interleave error handling with the
        algorithm, when the algorithm's answer is always the same -- try again
        next tick.
        """
        ...

    async def request(
        self, peer: int, message: Any, *, timeout: float,
        stream: Stream = Stream.CONTROL,
    ) -> Any:
        """Send and await the correlated reply.

        Raises:
            RaftUnavailable: The peer is unreachable or did not answer in time.
        """
        ...

    @property
    def live(self) -> frozenset[int]:
        """Peers with a healthy CONTROL link, excluding this member."""
        ...


@dataclass
class _Link:
    """One direction of one stream to one peer."""

    peer: int
    stream: Stream
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    task: asyncio.Task[None] | None = None
    incarnation: int = 0
    connected: bool = False
    pending: dict[int, tuple[MessageType, asyncio.Future[Any]]] = field(
        default_factory=dict,
    )
    """Callers awaiting a correlated reply, and what each is waiting for.

    The kind is not decoration -- see ``EXPECTED_REPLY``.
    """


APPLICATION_CONCURRENCY = 64
"""How many forwarded mutations one member will serve at once.

A bound rather than none, because every one of these is a task awaiting a
quorum round and a peer under load can offer them faster than they retire.
Exceeding it is answered immediately with a refusal rather than by waiting: the
whole point of serving these off the reader is that the reader must not block,
and a caller that is refused retries, which is the behaviour a 503 already has.
"""


class RaftTransport:
    """mTLS peer connectivity over ``asyncio`` streams.

    Args:
        local: This member's index.
        peers: ``{index: (host, port)}`` for every other member.
        bind: ``(host, port)`` this member listens on.
        cluster_id: The derived cluster token. A peer presenting a different
            one is refused rather than argued with.
        member_name: This member's canonical name, for log lines.
        incarnation: This member's start counter, from ``persist.py``.
        server_ssl / client_ssl: Contexts for the listener and for outbound
            connections. Both None runs the transport in the clear, which the
            configuration layer permits only on the loopback.
        peer_name: The shared SAN every peer certificate must carry
            (``--raftCertificateName``). Verified in both directions -- see the
            module docstring for why chain validation alone is not enough.
            Meaningless, and ignored, when TLS is off.
        rpc_timeout: Default deadline for a correlated request.
    """

    def __init__(
        self,
        *,
        local: int,
        peers: dict[int, tuple[str, int]],
        bind: tuple[str, int],
        cluster_id: str,
        member_name: str,
        incarnation: int,
        server_ssl: ssl.SSLContext | None = None,
        client_ssl: ssl.SSLContext | None = None,
        peer_name: str | None = None,
        rpc_timeout: float = 2.0,
    ) -> None:
        self._local = local
        self._peers = peers
        self._bind = bind
        self._cluster_id = cluster_id
        self._member_name = member_name
        self._incarnation = incarnation
        self._server_ssl = server_ssl
        self._client_ssl = client_ssl
        self._peer_name = peer_name
        self._rpc_timeout = rpc_timeout

        self._handler: PeerHandler | None = None
        self._server: asyncio.AbstractServer | None = None
        # Every accepted connection's writer. Tracked because
        # ``Server.wait_closed`` waits for the *handlers* to finish, and ours
        # are parked on ``read_frame`` waiting for a peer that has no reason to
        # say anything -- so closing the listener alone deadlocks shutdown.
        self._inbound: set[asyncio.StreamWriter] = set()
        self._serving: set[asyncio.Task[None]] = set()
        """Application handlers running off the link reader.

        Owned rather than detached, so ``close`` can cancel them: a task
        awaiting a quorum round that nobody will now answer would otherwise
        outlive the transport that started it.
        """
        self._application_slots = asyncio.Semaphore(APPLICATION_CONCURRENCY)
        self._links: dict[tuple[int, Stream], _Link] = {}
        self._next_request_id = 1
        self._closing = False

    def set_incarnation(self, value: int) -> None:
        """Adopt the node's start counter before the first handshake.

        The node is what loads the term store, and loading is what increments
        the counter -- so the transport cannot read it independently without
        bumping it a second time and telling every peer this member had
        restarted once more than it had.
        """
        self._incarnation = value

    # -- lifecycle ------------------------------------------------------

    async def start(self, handler: PeerHandler) -> None:
        self._handler = handler
        self._closing = False
        self._server = await asyncio.start_server(
            self._serve, self._bind[0], self._bind[1], ssl=self._server_ssl,
        )
        for peer in self._peers:
            for stream in (Stream.CONTROL, Stream.BULK):
                link = _Link(peer=peer, stream=stream)
                self._links[(peer, stream)] = link
                link.task = asyncio.create_task(
                    self._maintain(link),
                    name=f"raft-link-{self._member_name}-{peer}-{stream.name}",
                )

    async def close(self) -> None:
        self._closing = True
        # The application handlers first: each is awaiting a quorum round that
        # this transport is about to stop carrying, so none of them can finish.
        for task in list(self._serving):
            task.cancel()
        if self._serving:
            await asyncio.gather(*list(self._serving), return_exceptions=True)
        self._serving.clear()
        for link in self._links.values():
            if link.task is not None:
                link.task.cancel()
        for link in self._links.values():
            if link.task is not None:
                with_suppressed = asyncio.gather(link.task, return_exceptions=True)
                await with_suppressed
            self._drop(link, RaftUnavailable("transport closing"))
        self._links.clear()
        # Hang up on accepted connections first, which is what lets their
        # handlers return, which is what lets ``wait_closed`` finish.
        for writer in list(self._inbound):
            try:
                writer.close()
            except (OSError, RuntimeError):
                pass
        self._inbound.clear()

        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # Shutdown must not be able to block forever on a peer that
                # will not let go. The listener is closed either way.
                log.warning("raft: listener did not close cleanly")
            self._server = None

    # -- sending --------------------------------------------------------

    @property
    def live(self) -> frozenset[int]:
        return frozenset(
            peer for (peer, stream), link in self._links.items()
            if stream is Stream.CONTROL and link.connected
        )

    def send(
        self, peer: int, message: Any, *, stream: Stream = Stream.CONTROL,
    ) -> None:
        link = self._links.get((peer, stream))
        if link is None or link.writer is None or not link.connected:
            return
        try:
            link.writer.write(_frame_for(message, stream))
        except (OSError, RuntimeError):
            # The maintenance task owns reconnection; a failed write here is
            # simply a message that did not go, which replication retries.
            self._drop(link, RaftUnavailable("write failed"))

    async def request(
        self, peer: int, message: Any, *, timeout: float | None = None,
        stream: Stream = Stream.CONTROL,
    ) -> Any:
        link = self._links.get((peer, stream))
        if link is None or link.writer is None or not link.connected:
            raise RaftUnavailable(f"no link to member {peer}")

        request_id = self._next_request_id
        self._next_request_id += 1
        tagged = _with_request_id(message, request_id)

        expected = EXPECTED_REPLY.get(message.TYPE)
        if expected is None:
            raise RaftUnavailable(
                f"{message.TYPE.name} draws no reply and cannot be awaited; "
                f"use send() for it",
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        # The expected reply **kind** is stored with the waiter, not just the
        # id. See ``EXPECTED_REPLY``: the transport's request ids and the
        # leader's append ids are different spaces that meet in this one map.
        link.pending[request_id] = (expected, future)
        try:
            link.writer.write(_frame_for(tagged, stream))
            return await asyncio.wait_for(
                future, timeout if timeout is not None else self._rpc_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise RaftUnavailable(
                f"member {peer} did not answer within the deadline",
            ) from exc
        except (OSError, RuntimeError) as exc:
            raise RaftUnavailable(f"link to member {peer} failed") from exc
        finally:
            link.pending.pop(request_id, None)

    # -- outbound links -------------------------------------------------

    async def _maintain(self, link: _Link) -> None:
        """Keep one outbound link connected, backing off between attempts."""
        backoff = _RECONNECT_INITIAL
        while not self._closing:
            try:
                await self._connect(link)
                backoff = _RECONNECT_INITIAL
                await self._pump(link)
            except asyncio.CancelledError:
                raise
            except (OSError, RaftProtocolError, RaftClusterMismatch) as exc:
                if isinstance(exc, RaftClusterMismatch):
                    # Not transient and not fixable by retrying sooner: log it
                    # loudly, because a member configured into the wrong
                    # cluster otherwise looks like a member that is merely
                    # unreachable.
                    log.error("raft: %s", exc)
            except Exception:
                log.exception("raft: link to member %d failed", link.peer)
            finally:
                self._drop(link, RaftUnavailable("link dropped"))
            if self._closing:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX)

    async def _connect(self, link: _Link) -> None:
        host, port = self._peers[link.peer]
        if self._client_ssl is not None and self._peer_name:
            # Verify the shared cluster SAN, not the address. Members
            # co-located on one host all answer at 127.0.0.1, so verifying the
            # address would either fail against every real certificate or have
            # to be turned off -- and turning it off is what lets any
            # Product-CA device certificate answer for a member.
            reader, writer = await asyncio.open_connection(
                host, port, ssl=self._client_ssl,
                server_hostname=self._peer_name,
            )
        else:
            # ``server_hostname`` is rejected outright without a context, so
            # the plaintext transport cannot simply pass None here.
            reader, writer = await asyncio.open_connection(
                host, port, ssl=self._client_ssl,
            )
        link.writer = writer

        writer.write(_frame_for(
            Hello(
                major=PROTOCOL_MAJOR, minor=PROTOCOL_MINOR,
                cluster_id=self._cluster_id, member_name=self._member_name,
                member_index=self._local, incarnation=self._incarnation,
                stream=link.stream,
            ),
            link.stream,
        ))
        frame = await read_frame(reader)
        if frame.type is not MessageType.HELLO_ACK:
            raise RaftProtocolError(
                f"member {link.peer} answered {frame.type.name} to a Hello",
            )
        ack = decode_message(frame.type, frame.payload)
        if not ack.accepted:
            raise RaftClusterMismatch(
                f"member {link.peer} refused the connection: {ack.reason}",
            )

        link.incarnation = ack.incarnation
        link.connected = True
        link.reader = reader
        if self._handler is not None and link.stream is Stream.CONTROL:
            self._handler.on_peer_state(
                link.peer, up=True, incarnation=ack.incarnation,
            )

    async def _pump(self, link: _Link) -> None:
        """Read replies on an outbound link until it fails."""
        reader = link.reader
        if reader is None:
            raise RaftUnavailable("link has no reader")
        while not self._closing:
            frame = await read_frame(reader)
            await self._dispatch(link.peer, frame, None)

    def _drop(self, link: _Link, error: BaseException) -> None:
        link.connected = False
        link.reader = None
        if link.writer is not None:
            try:
                link.writer.close()
            except (OSError, RuntimeError):
                pass
            link.writer = None
        for _expected, future in link.pending.values():
            if not future.done():
                future.set_exception(error)
        link.pending.clear()
        if self._handler is not None and link.stream is Stream.CONTROL:
            self._handler.on_peer_state(link.peer, up=False, incarnation=0)

    # -- inbound --------------------------------------------------------

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        peer = -1
        self._inbound.add(writer)
        try:
            frame = await read_frame(reader)
            if frame.type is not MessageType.HELLO:
                raise RaftProtocolError("first frame was not a Hello")
            hello = decode_message(frame.type, frame.payload)

            refusal = self._refuse_certificate(writer) or self._refuse(hello)
            writer.write(_frame_for(
                HelloAck(
                    accepted=refusal is None, reason=refusal or "",
                    minor=PROTOCOL_MINOR, member_index=self._local,
                    incarnation=self._incarnation,
                ),
                hello.stream,
            ))
            if refusal is not None:
                log.warning(
                    "raft: refused a connection from %s: %s",
                    hello.member_name, refusal,
                )
                return

            peer = hello.member_index
            while not self._closing:
                inbound = await read_frame(reader)
                await self._dispatch(peer, inbound, writer)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except RaftProtocolError as exc:
            log.warning("raft: dropping a peer connection: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("raft: inbound connection failed")
        finally:
            self._inbound.discard(writer)
            try:
                writer.close()
            except (OSError, RuntimeError):
                pass

    def _refuse_certificate(self, writer: asyncio.StreamWriter) -> str | None:
        """Does the connecting member carry the cluster's shared SAN?

        Checked before the ``HelloAck`` is written, so a certificate that is
        merely *valid* never reaches the point of being told this member's
        index and incarnation.

        The matching is written out here rather than delegated: ``ssl`` verifies
        names only on the client side, and ``ssl.match_hostname`` was removed in
        Python 3.12. ``peer_name`` is one fixed label, never a user-supplied
        address, so exact comparison against the DNS SANs is the whole rule --
        no wildcards, which would widen the very set this check exists to
        narrow.
        """
        if self._server_ssl is None or not self._peer_name:
            return None

        certificate = writer.get_extra_info("peercert")
        if not certificate:
            # CERT_REQUIRED means the handshake would have failed already, so
            # this is belt and braces -- but an empty dict is also what a
            # non-TLS transport returns, and silently accepting it here would
            # turn a misconfiguration into an open door.
            return "no peer certificate was presented"

        names = [
            value for kind, value in certificate.get("subjectAltName", ())
            if kind == "DNS"
        ]
        if self._peer_name in names:
            return None
        return (
            f"peer certificate carries {names or ['no DNS SAN']}, and this "
            f"cluster admits only {self._peer_name!r}"
        )

    def _refuse(self, hello: Hello) -> str | None:
        if hello.major != PROTOCOL_MAJOR:
            return (
                f"protocol major {hello.major}, this member speaks "
                f"{PROTOCOL_MAJOR}"
            )
        if hello.cluster_id != self._cluster_id:
            return (
                f"cluster {hello.cluster_id!r}, this member belongs to "
                f"{self._cluster_id!r}"
            )
        if hello.member_index == self._local:
            return "that is this member's own index"
        if hello.member_index not in self._peers:
            return f"member index {hello.member_index} is not in the member set"
        return None

    async def _dispatch(
        self, peer: int, frame: Frame, writer: asyncio.StreamWriter | None,
    ) -> None:
        handler = self._handler
        if handler is None:
            return

        message = decode_message(frame.type, frame.payload)

        if frame.is_reply:
            self._resolve(peer, frame, message)
            return

        reply: Any | None = None
        if frame.type is MessageType.REQUEST_VOTE:
            reply = handler.on_request_vote(peer, message)
        elif frame.type is MessageType.APPEND_ENTRIES:
            reply = handler.on_append_entries(peer, message)
        elif frame.type is MessageType.INSTALL_SNAPSHOT:
            reply = handler.on_install_snapshot(peer, message)
        elif frame.type is MessageType.PROMOTE:
            handler.on_promote(peer, message)
        elif frame.type in (MessageType.PROPOSE, MessageType.FORWARD):
            # **Served off this reader, not on it.**
            #
            # These two wait for a quorum round -- the trait says so -- and the
            # answer to that round arrives as ``AppendEntries`` on *this very
            # link*. Awaiting them here therefore deadlocks whenever the member
            # that sent the forward is the leader: the handler waits for a
            # commit that cannot be read, because the reader is inside the
            # handler. Measured as every refusal taking the whole mutation
            # deadline, in both implementations.
            #
            # The consensus messages above stay synchronous and in order, which
            # is what keeps a term from being read and acted on across an
            # await.
            self._serve_application(peer, frame, message, writer, handler)
            return

        if reply is not None and writer is not None:
            writer.write(_frame_for(reply, frame.stream, is_reply=True))

    def _serve_application(
        self, peer: int, frame: Frame, message: Any,
        writer: asyncio.StreamWriter | None, handler: PeerHandler,
    ) -> None:
        """Run a quorum-round handler on its own task, answering when it ends.

        The reply goes back on the connection the request arrived on, which is
        deliberate: a member whose inbound link works and whose outbound one
        does not is a case this cluster's own harness models, and answering on
        a different socket would lose the reply exactly there.
        """
        if self._application_slots.locked():
            # At capacity. Refusing now is the honest answer -- waiting for a
            # slot would block this reader, which is the whole defect.
            refusal = _application_refusal(frame, message)
            if refusal is not None and writer is not None:
                writer.write(_frame_for(refusal, frame.stream, is_reply=True))
            return

        task = asyncio.create_task(
            self._run_application(peer, frame, message, writer, handler),
        )
        self._serving.add(task)
        task.add_done_callback(self._serving.discard)

    async def _run_application(
        self, peer: int, frame: Frame, message: Any,
        writer: asyncio.StreamWriter | None, handler: PeerHandler,
    ) -> None:
        async with self._application_slots:
            if frame.type is MessageType.PROPOSE:
                reply: Any = await handler.on_propose(peer, message)
            else:
                reply = await handler.on_forward(peer, message)

        if writer is None or writer.is_closing():
            # The caller is gone; its own deadline has already told it so.
            return
        try:
            # ``write`` is synchronous and appends the whole frame in one call,
            # so several of these cannot interleave and no lock is needed.
            writer.write(_frame_for(reply, frame.stream, is_reply=True))
        except (OSError, RuntimeError):
            pass

    def _resolve(self, peer: int, frame: Frame, message: Any) -> None:
        """Route a reply: to its awaiting request, or to the handler.

        ``request`` correlates by ``request_id``; ``send`` does not correlate
        at all, so a reply to a fire-and-forget message has no future waiting
        and must reach the handler instead. Dropping it silently is how a
        leader ends up never learning that its entries landed.
        """
        link = self._links.get((peer, frame.stream))
        request_id = getattr(message, "request_id", 0)
        if link is not None and request_id:
            # Taken only when the reply is the *kind* that was asked for.
            # Anything else belongs to a different exchange that happens to
            # share the number, and must be left for the handler.
            waiting = link.pending.get(request_id)
            if waiting is not None and waiting[0] is frame.type:
                del link.pending[request_id]
                future = waiting[1]
                if not future.done():
                    future.set_result(message)
                return

        handler = self._handler
        if handler is None:
            return
        if frame.type is MessageType.REQUEST_VOTE_REPLY:
            handler.on_request_vote_reply(peer, message)
        elif frame.type is MessageType.APPEND_ENTRIES_REPLY:
            handler.on_append_entries_reply(peer, message)
        elif frame.type is MessageType.INSTALL_SNAPSHOT_REPLY:
            handler.on_install_snapshot_reply(peer, message)


# ---------------------------------------------------------------------------
# Framing helpers
# ---------------------------------------------------------------------------

def _application_refusal(frame: Frame, message: Any) -> Any | None:
    """The answer when this member has no capacity left to serve a round.

    Shaped as an ordinary refusal rather than as an error, because that is what
    the caller already knows how to handle: a forwarded mutation that comes
    back not-ok becomes a 503, and a 503 is retried. Saying so immediately is
    strictly better than making the caller wait out a deadline to learn it.
    """
    request_id = getattr(message, "request_id", 0)
    if frame.type is MessageType.PROPOSE:
        return ProposeReply(
            accepted=False,
            reason="this member is at capacity for forwarded work",
            term=0, first_index=0, request_id=request_id, leader=None,
        )
    if frame.type is MessageType.FORWARD:
        return ForwardReply(
            ok=False, created=False,
            error="unavailable",
            detail="this member is at capacity for forwarded work",
            applied_index=0, not_owner=False,
            request_id=request_id, owner=None,
        )
    return None


def _frame_for(message: Any, stream: Stream, *, is_reply: bool = False) -> bytes:
    return encode_frame(Frame(
        stream=stream,
        type=message.TYPE,
        flags=FLAG_REPLY if is_reply else 0,
        payload=message.encode(),
    ))


def _with_request_id(message: Any, request_id: int) -> Any:
    """Stamp a correlation id, for the messages that carry one.

    Replies are matched by ``request_id``, so a message used with ``request``
    must have the field. Messages that do not -- ``Promote``, say -- are
    fire-and-forget by design, and asking for a reply to one is a programming
    error rather than a runtime condition.
    """
    import dataclasses

    if not hasattr(message, "request_id"):
        raise TypeError(
            f"{type(message).__name__} carries no request_id and cannot be "
            f"awaited; use send() for it",
        )
    return dataclasses.replace(message, request_id=request_id)


# Re-exported so ``node.py`` need not reach into two modules for the pieces it
# dispatches on.
__all__ = [
    "AppendEntries",
    "AppendEntriesReply",
    "Forward",
    "ForwardReply",
    "InstallSnapshot",
    "InstallSnapshotReply",
    "PeerHandler",
    "Promote",
    "Propose",
    "ProposeReply",
    "RaftTransport",
    "RequestVote",
    "RequestVoteReply",
    "Transport",
]
