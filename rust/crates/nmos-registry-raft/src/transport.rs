// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Peer connectivity: two mTLS links per peer, and the handshake that gates
//! them.
//!
//! Port of `nmos/raft/transport.py`.
//!
//! # The abstraction comes first
//!
//! [`Transport`] and [`PeerHandler`] are traits, and the node depends only on
//! them. That is not architectural decoration -- it is what makes consensus
//! testable at all. The deterministic harness supplies an in-memory transport
//! with a controllable clock and injectable partitions, so election safety can
//! be driven through exactly the scenario that breaks it, repeatably, with no
//! sockets and no sleeping. A consensus layer that could only be tested over
//! real TCP would be tested only in the cases that are easy to provoke, which
//! are not the cases that matter.
//!
//! # Two links per peer
//!
//! [`Stream::Control`] carries elections, replication and heartbeats.
//! [`Stream::Bulk`] carries snapshot transfers and nothing else.
//!
//! They are separate because a snapshot is the entire registry serialised, and
//! a follower that stops hearing heartbeats starts an election. Sharing one
//! link would let a multi-megabyte transfer stall the very timer whose job is
//! to prevent elections -- so installing a snapshot would cause leadership
//! churn, that churn would cause more members to fall behind, and the
//! resulting instability would be blamed on load rather than on head-of-line
//! blocking.
//!
//! # The handshake is a gate, not a greeting
//!
//! Every connection begins with [`Hello`]/[`HelloAck`], and a mismatch closes
//! the link rather than negotiating:
//!
//! * a different `cluster_id` means these two members belong to different
//!   clusters, which is precisely the split the cluster token exists to detect;
//! * a different protocol *major* is not negotiable, by construction;
//! * a different *minor* is fine in both directions -- unknown fields are
//!   skipped.
//!
//! `incarnation` also travels here, and it is what tells a leader that a peer
//! has restarted and come back with an empty log.
//!
//! # The certificate name is a second gate, and it is the load-bearing one
//!
//! Chain validation alone proves only that the peer holds *a* certificate from
//! a trusted CA. In an IPMX deployment that CA is the Product CA, which has
//! signed every device certificate in the building -- so a camera could
//! complete an mTLS handshake with the registry database and start proposing
//! log entries.
//!
//! `peer_name` closes that. It is one shared SAN carried by the cluster's
//! certificates and by nothing else, checked in **both** directions: outbound
//! as the verified hostname (which matters because co-located members all share
//! one address), and inbound by inspecting the presented certificate before the
//! `HelloAck` is written.

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

use async_trait::async_trait;
use tokio::io::{AsyncReadExt as _, AsyncWriteExt as _};
use tokio::sync::{Mutex, oneshot};

use crate::errors::RaftProtocolError;
use crate::messages::{
    AppendEntries, AppendEntriesReply, Forward, ForwardReply, Hello, HelloAck, InstallSnapshot,
    InstallSnapshotReply, Message, Promote, Propose, ProposeReply, RequestVote, RequestVoteReply,
    decode_message,
};
use crate::wire::{
    FLAG_REPLY, Frame, HEADER_SIZE, MessageType, PROTOCOL_MAJOR, PROTOCOL_MINOR, Stream,
    TRAILER_SIZE, decode_frame, encode_frame, payload_length,
};

/// Initial delay between reconnection attempts.
pub const RECONNECT_INITIAL_MS: u64 = 50;

/// The ceiling that delay backs off to.
pub const RECONNECT_MAX_MS: u64 = 2_000;

/// Default deadline for a correlated request.
pub const RPC_TIMEOUT_MS: u64 = 2_000;

/// A peer is not reachable, or did not answer in time.
///
/// Distinct from [`RaftProtocolError`]: that one means the stream is no longer
/// understood and the link must drop, this one means a message did not get
/// through and replication will try again.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RaftUnavailable(pub String);

impl std::fmt::Display for RaftUnavailable {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RaftUnavailable {}

/// The peer belongs to a different cluster.
///
/// Not transient and not fixable by retrying sooner, which is why it is not a
/// [`RaftUnavailable`]: a member configured into the wrong cluster otherwise
/// looks like a member that is merely unreachable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RaftClusterMismatch(pub String);

impl std::fmt::Display for RaftClusterMismatch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RaftClusterMismatch {}

/// What the consensus layer does with what arrives.
///
/// The consensus messages are **not** `async`, deliberately. Each one is
/// answered from state the node holds behind a synchronous lock, and a handler
/// that could await would let another message interleave between reading the
/// term and acting on it -- which is how a member votes twice in one term. The
/// two that are async, [`Self::on_propose`] and [`Self::on_forward`], are not
/// consensus messages at all: they wait for a quorum round, which is exactly
/// the thing the others must not do.
#[async_trait]
pub trait PeerHandler: Send + Sync + 'static {
    /// Figure 2 RequestVote.
    fn on_request_vote(&self, peer: u64, message: &RequestVote) -> RequestVoteReply;

    /// Figure 2 AppendEntries, heartbeat included.
    fn on_append_entries(&self, peer: u64, message: &AppendEntries) -> AppendEntriesReply;

    /// A snapshot chunk.
    fn on_install_snapshot(&self, peer: u64, message: &InstallSnapshot) -> InstallSnapshotReply;

    /// The leader saying this member's vote now counts.
    fn on_promote(&self, peer: u64, message: &Promote);

    /// A vote reply that no request was awaiting.
    fn on_request_vote_reply(&self, peer: u64, message: &RequestVoteReply);

    /// An append reply that no request was awaiting.
    fn on_append_entries_reply(&self, peer: u64, message: &AppendEntriesReply);

    /// A snapshot reply that no request was awaiting.
    fn on_install_snapshot_reply(&self, peer: u64, message: &InstallSnapshotReply);

    /// A follower's batch, for this member to append as leader.
    async fn on_propose(&self, peer: u64, message: &Propose) -> ProposeReply;

    /// A registry mutation handed to the member that owns its Node.
    async fn on_forward(&self, peer: u64, message: &Forward) -> ForwardReply;

    /// A peer's link came up or went down.
    ///
    /// `incarnation` is meaningful only when `up`; it is what tells a leader
    /// that this peer has restarted and come back with an empty log.
    fn on_peer_state(&self, peer: u64, up: bool, incarnation: u64);
}

/// How the node reaches its peers.
///
/// The node depends on this and never on [`RaftTransport`], so a test can
/// substitute an in-memory implementation with a controllable clock.
#[async_trait]
pub trait Transport: Send + Sync + 'static {
    /// Begin listening and connecting.
    ///
    /// # Errors
    ///
    /// Whatever prevented the listener from binding.
    async fn start(&self, handler: Arc<dyn PeerHandler>) -> std::io::Result<()>;

    /// Stop, dropping every link.
    async fn close(&self);

    /// Send without waiting for an answer.
    ///
    /// Fire-and-forget by design: a message that does not go is a message
    /// replication will send again, and blocking a caller on it would let one
    /// unreachable peer stall the others.
    fn send(&self, peer: u64, message: &Message, stream: Stream);

    /// Send and wait for the correlated reply.
    ///
    /// # Errors
    ///
    /// [`RaftUnavailable`] if there is no link, the write fails, or the peer
    /// does not answer within the deadline.
    async fn request(
        &self,
        peer: u64,
        message: &Message,
        stream: Stream,
        timeout_ms: Option<u64>,
    ) -> Result<Message, RaftUnavailable>;

    /// Which peers currently have a control link.
    fn live(&self) -> Vec<u64>;
}

/// Read one frame from a stream.
///
/// # Errors
///
/// An I/O error, or [`RaftProtocolError`] mapped into one, if the header does
/// not describe a frame this build accepts.
pub async fn read_frame<R>(reader: &mut R) -> std::io::Result<Frame>
where
    R: tokio::io::AsyncRead + Unpin,
{
    let mut header = [0u8; HEADER_SIZE];
    reader.read_exact(&mut header).await?;
    let length = payload_length(&header).map_err(protocol_io)?;

    // `payload_length` is the payload **only**; the CRC trailer follows it and
    // must be read too. Reading `length` alone leaves four bytes in the
    // stream, so every frame fails its checksum and the next read starts
    // mid-frame -- which presents as two members that never link up, with no
    // error naming the cause.
    //
    // The header is re-joined with the body rather than parsed twice: the
    // checksum covers both, so splitting the parse would mean either checking
    // it here or not at all.
    let mut rest = vec![0u8; length.saturating_add(TRAILER_SIZE)];
    reader.read_exact(&mut rest).await?;

    let mut whole = Vec::with_capacity(
        HEADER_SIZE
            .saturating_add(length)
            .saturating_add(TRAILER_SIZE),
    );
    whole.extend_from_slice(&header);
    whole.extend_from_slice(&rest);
    decode_frame(&whole).map_err(protocol_io)
}

fn protocol_io(error: RaftProtocolError) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::InvalidData, error.0)
}

/// The bytes one message becomes on the wire.
#[must_use]
pub fn frame_for(message: &Message, stream: Stream, is_reply: bool) -> Vec<u8> {
    let frame = Frame::new(
        stream,
        message.message_type(),
        if is_reply { FLAG_REPLY } else { 0 },
        message.encode(),
    );
    encode_frame(&frame).unwrap_or_default()
}

/// Stamp a correlation id on the messages that carry one.
///
/// Replies are matched by `request_id`, so a message used with
/// [`Transport::request`] must have the field. Messages that do not --
/// [`Promote`], say -- are fire-and-forget by design, and asking for a reply to
/// one is a programming error rather than a runtime condition. Here that is a
/// `None` the caller must handle, because this crate has no panic to reach for.
#[must_use]
pub fn with_request_id(message: &Message, request_id: u64) -> Option<Message> {
    Some(match *message {
        Message::AppendEntries(ref m) => Message::AppendEntries(AppendEntries {
            request_id,
            ..m.clone()
        }),
        Message::AppendEntriesReply(ref m) => Message::AppendEntriesReply(AppendEntriesReply {
            request_id,
            ..m.clone()
        }),
        Message::Propose(ref m) => Message::Propose(Propose {
            request_id,
            ..m.clone()
        }),
        Message::ProposeReply(ref m) => Message::ProposeReply(ProposeReply {
            request_id,
            ..m.clone()
        }),
        Message::Forward(ref m) => Message::Forward(Forward {
            request_id,
            ..m.clone()
        }),
        Message::ForwardReply(ref m) => Message::ForwardReply(ForwardReply {
            request_id,
            ..m.clone()
        }),
        _ => return None,
    })
}

/// The correlation id a reply carries, or zero for the messages without one.
#[must_use]
pub fn request_id_of(message: &Message) -> u64 {
    match *message {
        Message::AppendEntries(ref m) => m.request_id,
        Message::AppendEntriesReply(ref m) => m.request_id,
        Message::Propose(ref m) => m.request_id,
        Message::ProposeReply(ref m) => m.request_id,
        Message::Forward(ref m) => m.request_id,
        Message::ForwardReply(ref m) => m.request_id,
        _ => 0,
    }
}

/// Decide whether an inbound `Hello` may open a link.
///
/// Returns the refusal, or `None` to accept. Separated from the socket so it
/// can be tested without one -- every branch here is a way two members fail to
/// form a cluster, and each has a message an operator has to be able to act on.
#[must_use]
pub fn refuse_hello(
    hello: &Hello,
    local: u64,
    cluster_id: &str,
    known_peers: &[u64],
) -> Option<String> {
    if hello.major != u64::from(PROTOCOL_MAJOR) {
        return Some(format!(
            "protocol major {}, this member speaks {PROTOCOL_MAJOR}",
            hello.major,
        ));
    }
    if hello.cluster_id != cluster_id {
        return Some(format!(
            "cluster '{}', this member belongs to '{cluster_id}'",
            hello.cluster_id,
        ));
    }
    if hello.member_index == local {
        return Some("that is this member's own index".to_owned());
    }
    if !known_peers.contains(&hello.member_index) {
        return Some(format!(
            "member index {} is not in the member set",
            hello.member_index,
        ));
    }
    None
}

/// Does a peer certificate's DNS SANs admit this cluster's shared name?
///
/// Written out rather than delegated: TLS libraries verify names on the client
/// side, and `peer_name` is one fixed label, never a user-supplied address, so
/// exact comparison against the DNS SANs is the whole rule -- **no wildcards**,
/// which would widen the very set this check exists to narrow.
#[must_use]
pub fn refuse_certificate(peer_name: &str, dns_names: &[String]) -> Option<String> {
    if dns_names.is_empty() {
        // A handshake requiring a certificate would have failed already, so
        // this is belt and braces -- but "no names" is also what a non-TLS
        // connection produces, and silently accepting it here would turn a
        // misconfiguration into an open door.
        return Some("no peer certificate was presented".to_owned());
    }
    if dns_names.iter().any(|name| name == peer_name) {
        return None;
    }
    Some(format!(
        "peer certificate carries {dns_names:?}, and this cluster admits only \
         '{peer_name}'",
    ))
}

/// One direction of one stream to one peer.
struct Link {
    /// Where frames are written. `None` between connection attempts.
    writer: Mutex<Option<Box<dyn AsyncWriteUnpinSend>>>,
    connected: AtomicBool,
    incarnation: AtomicU64,
    /// Callers awaiting a correlated reply, and **what each is waiting for**.
    ///
    /// The type is not decoration. Two id spaces meet in this one map: the
    /// transport mints ids for `request`, while `AppendEntries` carries an id
    /// of the leader's own minting for flow control. Both begin at one and
    /// climb, so they collide -- most readily just after a leader change, when
    /// a member that had been a follower has a low append sequence and a low
    /// request id at the same time.
    ///
    /// Matched on the number alone, an `AppendEntriesReply` could then be
    /// delivered to a caller awaiting a `ForwardReply`. That caller sees the
    /// wrong message and gives up -- a registration refused with 503 -- and the
    /// append reply never reaches the node, so the peer's `match_index` stalls
    /// for a tick. Both failures from one number matching by accident.
    pending: Mutex<HashMap<u64, (MessageType, oneshot::Sender<Message>)>>,
}

impl Default for Link {
    fn default() -> Self {
        Self {
            writer: Mutex::new(None),
            connected: AtomicBool::new(false),
            incarnation: AtomicU64::new(0),
            pending: Mutex::new(HashMap::new()),
        }
    }
}

/// The object safety a writer half needs, named so [`Link`] can hold one.
///
/// Not `Debug`: a TLS stream's write half is not, and requiring it would rule
/// out the only transport this is for.
pub trait AsyncWriteUnpinSend: tokio::io::AsyncWrite + Unpin + Send {}

impl<T> AsyncWriteUnpinSend for T where T: tokio::io::AsyncWrite + Unpin + Send {}

impl Link {
    #[expect(
        clippy::significant_drop_tightening,
        reason = "the guard IS the serialisation: two frames interleaved on \
                  one link are two half-frames, and the reader cannot tell \
                  where either begins"
    )]
    async fn write(&self, bytes: &[u8]) -> Result<(), RaftUnavailable> {
        let mut guard = self.writer.lock().await;
        let Some(writer) = guard.as_mut() else {
            return Err(RaftUnavailable("no link".to_owned()));
        };
        writer
            .write_all(bytes)
            .await
            .map_err(|e| RaftUnavailable(format!("write failed: {e}")))?;
        writer
            .flush()
            .await
            .map_err(|e| RaftUnavailable(format!("flush failed: {e}")))
    }

    /// Mark the link down and release every awaiting request.
    ///
    /// Every pending waiter must be released, not left: a caller awaiting a
    /// reply from a link that has gone would otherwise wait out its whole
    /// deadline to learn what is already known. Dropping the sender is how a
    /// `oneshot` says so.
    async fn take_down(&self) {
        self.connected.store(false, Ordering::SeqCst);
        *self.writer.lock().await = None;
        drop(std::mem::take(&mut *self.pending.lock().await));
    }
}

// ---------------------------------------------------------------------------
// The concrete transport
// ---------------------------------------------------------------------------

/// The TLS material a secured transport needs.
///
/// All three or none: an acceptor without a connector is a member that can be
/// called and cannot call, which forms no cluster, and a `peer_name` without
/// either is a check that never runs.
pub struct PeerTls {
    /// One context for both ends.
    ///
    /// Members talk only to each other and the shared certificate carries both
    /// `serverAuth` and `clientAuth`, so one context serves listening and
    /// dialling -- which is why there is one `--raftCertificate` rather than
    /// two. The role is chosen per connection below.
    pub context: openssl::ssl::SslContext,
    /// The one shared SAN the cluster's certificates carry.
    ///
    /// Verified in both directions. See the module docs for why chain
    /// validation alone is not enough in an IPMX deployment.
    pub peer_name: String,
}

impl std::fmt::Debug for PeerTls {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PeerTls")
            .field("peer_name", &self.peer_name)
            .finish_non_exhaustive()
    }
}

/// Everything the link tasks share.
///
/// Split out so `start` can hand each task an `Arc` of it. The alternative --
/// an `Arc<RaftTransport>` threaded through the trait -- would put the sharing
/// in the trait's signature, where a test transport has no use for it.
struct Inner {
    local: u64,
    peers: HashMap<u64, (String, u16)>,
    cluster_id: String,
    member_name: String,
    incarnation: AtomicU64,
    tls: Option<Arc<PeerTls>>,
    rpc_timeout_ms: u64,
    links: HashMap<(u64, Stream), Arc<Link>>,
    handler: Mutex<Option<Arc<dyn PeerHandler>>>,
    closing: AtomicBool,
    next_request_id: AtomicU64,
    /// Every accepted connection's task.
    ///
    /// Tracked because closing the listener does not close the connections it
    /// already handed over: those tasks are parked on a read from a peer that
    /// has no reason to say anything, and they hold the socket open. A peer
    /// whose outbound link is still attached to one of them never learns this
    /// member has gone, so it keeps counting it toward quorum -- measured, as
    /// a surviving member that never saw its peer go down.
    inbound: Mutex<Vec<tokio::task::JoinHandle<()>>>,
    /// Application handlers running off the link readers.
    ///
    /// Owned rather than detached, so `close` can abort them: a task awaiting
    /// a quorum round that nobody will now answer would otherwise outlive the
    /// transport that started it.
    serving: parking_lot::Mutex<Vec<tokio::task::JoinHandle<()>>>,
    /// How many forwarded mutations this member will serve at once.
    application_slots: Arc<tokio::sync::Semaphore>,
}

/// Everything [`RaftTransport::new`] needs.
///
/// A struct rather than eight positional parameters: `cluster_id` and
/// `member_name` are both `String` and adjacent, and swapping them produces a
/// member that refuses every peer for belonging to the wrong cluster -- which
/// reads, in a log, as a configuration problem somewhere else entirely.
pub struct TransportSettings {
    /// This member's index.
    pub local: u64,
    /// Where each other member is reached.
    pub peers: HashMap<u64, (String, u16)>,
    /// What this member listens on. Port 0 asks the OS to choose.
    pub bind: std::net::SocketAddr,
    /// The derived cluster token. A peer presenting a different one is refused
    /// rather than argued with.
    pub cluster_id: String,
    /// This member's canonical name, for log lines and the handshake.
    pub member_name: String,
    /// This member's start counter.
    pub incarnation: u64,
    /// TLS material, or `None` to run in the clear.
    pub tls: Option<Arc<PeerTls>>,
    /// Default deadline for a correlated request.
    pub rpc_timeout_ms: u64,
}

/// How one member reaches the others, over TCP and optionally TLS.
pub struct RaftTransport {
    inner: Arc<Inner>,
    bind: std::net::SocketAddr,
    bound: Mutex<Option<std::net::SocketAddr>>,
    tasks: Mutex<Vec<tokio::task::JoinHandle<()>>>,
}

impl RaftTransport {
    /// A transport for one member.
    ///
    /// `tls` of `None` runs in the clear, which the configuration layer permits
    /// only on the loopback.
    #[must_use]
    pub fn new(settings: TransportSettings) -> Self {
        let TransportSettings {
            local,
            peers,
            bind,
            cluster_id,
            member_name,
            incarnation,
            tls,
            rpc_timeout_ms,
        } = settings;
        let mut links = HashMap::new();
        for &peer in peers.keys() {
            for stream in [Stream::Control, Stream::Bulk] {
                links.insert((peer, stream), Arc::new(Link::default()));
            }
        }
        Self {
            inner: Arc::new(Inner {
                local,
                peers,
                cluster_id,
                member_name,
                incarnation: AtomicU64::new(incarnation),
                tls,
                rpc_timeout_ms,
                links,
                handler: Mutex::new(None),
                closing: AtomicBool::new(false),
                next_request_id: AtomicU64::new(1),
                inbound: Mutex::new(Vec::new()),
                serving: parking_lot::Mutex::new(Vec::new()),
                application_slots: Arc::new(tokio::sync::Semaphore::new(
                    APPLICATION_CONCURRENCY,
                )),
            }),
            bind,
            bound: Mutex::new(None),
            tasks: Mutex::new(Vec::new()),
        }
    }

    /// Adopt the node's start counter before the first handshake.
    ///
    /// The node is what loads the term store, and loading is what increments
    /// the counter -- so the transport cannot read it independently without
    /// bumping it a second time and telling every peer this member had
    /// restarted once more than it had.
    pub fn set_incarnation(&self, value: u64) {
        self.inner.incarnation.store(value, Ordering::SeqCst);
    }

    /// The address the listener actually bound.
    ///
    /// `None` before `start`. Needed by the test rigs, which bind port 0 and
    /// have to learn what they got before any peer can be told where to
    /// connect.
    pub async fn bound(&self) -> Option<std::net::SocketAddr> {
        *self.bound.lock().await
    }
}

impl Inner {
    fn hello(&self, stream: Stream) -> Message {
        Message::Hello(Hello {
            major: u64::from(PROTOCOL_MAJOR),
            minor: u64::from(PROTOCOL_MINOR),
            cluster_id: self.cluster_id.clone(),
            member_name: self.member_name.clone(),
            member_index: self.local,
            incarnation: self.incarnation.load(Ordering::SeqCst),
            stream,
        })
    }

    fn ack(&self, refusal: Option<&str>) -> Message {
        Message::HelloAck(HelloAck {
            accepted: refusal.is_none(),
            reason: refusal.unwrap_or("").to_owned(),
            minor: u64::from(PROTOCOL_MINOR),
            member_index: self.local,
            incarnation: self.incarnation.load(Ordering::SeqCst),
        })
    }

    fn known_peers(&self) -> Vec<u64> {
        let mut peers: Vec<u64> = self.peers.keys().copied().collect();
        peers.sort_unstable();
        peers
    }

    async fn handler(&self) -> Option<Arc<dyn PeerHandler>> {
        self.handler.lock().await.clone()
    }

    /// Route a reply: to its awaiting request, or to the handler.
    ///
    /// `request` correlates by `request_id`; `send` does not correlate at all,
    /// so a reply to a fire-and-forget message has no waiter and must reach the
    /// handler instead. Dropping it silently is how a leader ends up never
    /// learning that its entries landed.
    async fn resolve(&self, peer: u64, frame: &Frame, message: Message) {
        let request_id = request_id_of(&message);
        if request_id != 0
            && let Some(link) = self.links.get(&(peer, frame.stream))
        {
            // Taken only when the reply is the *kind* that was asked for.
            // Anything else belongs to a different exchange that happens to
            // share the number, and must be left for the handler.
            let mut pending = link.pending.lock().await;
            let matches = pending
                .get(&request_id)
                .is_some_and(|&(expected, _)| expected == message.message_type());
            if matches && let Some((_, sender)) = pending.remove(&request_id) {
                drop(pending);
                // A closed receiver means the caller timed out and gave up;
                // its deadline already told it what it needed to know.
                drop(sender.send(message));
                return;
            }
            drop(pending);
        }

        let Some(handler) = self.handler().await else {
            return;
        };
        match message {
            Message::RequestVoteReply(ref m) => handler.on_request_vote_reply(peer, m),
            Message::AppendEntriesReply(ref m) => handler.on_append_entries_reply(peer, m),
            Message::InstallSnapshotReply(ref m) => handler.on_install_snapshot_reply(peer, m),
            _ => {}
        }
    }

    /// Answer one inbound frame, if it asks for an answer.
    async fn dispatch(&self, peer: u64, frame: &Frame) -> Option<Message> {
        let message = match decode_message(frame.message_type, &frame.payload) {
            Ok(message) => message,
            Err(error) => {
                tracing::warn!(error = %error.0, peer, "raft: undecodable frame");
                return None;
            }
        };

        if frame.is_reply() {
            self.resolve(peer, frame, message).await;
            return None;
        }

        let handler = self.handler().await?;
        Some(match message {
            Message::RequestVote(ref m) => {
                Message::RequestVoteReply(handler.on_request_vote(peer, m))
            }
            Message::AppendEntries(ref m) => {
                Message::AppendEntriesReply(handler.on_append_entries(peer, m))
            }
            Message::InstallSnapshot(ref m) => {
                Message::InstallSnapshotReply(handler.on_install_snapshot(peer, m))
            }
            Message::Promote(ref m) => {
                handler.on_promote(peer, m);
                return None;
            }
            // Never awaited here. `serve` detaches these -- see
            // `serve_application` -- because they wait for a quorum round whose
            // answer arrives on the link this reader is reading.
            Message::Propose(_) | Message::Forward(_) => return None,
            _ => return None,
        })
    }

    /// Keep one outbound link connected, backing off between attempts.
    async fn maintain(self: Arc<Self>, peer: u64, stream: Stream) {
        let mut backoff = RECONNECT_INITIAL_MS;
        while !self.closing.load(Ordering::SeqCst) {
            match self.connect(peer, stream).await {
                Ok(reader) => {
                    backoff = RECONNECT_INITIAL_MS;
                    self.pump(peer, stream, reader).await;
                }
                Err(ConnectFailure::Mismatch(error)) => {
                    // Not transient and not fixable by retrying sooner: logged
                    // loudly, because a member configured into the wrong
                    // cluster otherwise looks like one that is merely
                    // unreachable.
                    tracing::error!(error = %error.0, peer, "raft: cluster mismatch");
                }
                Err(ConnectFailure::Unavailable(error)) => {
                    tracing::debug!(error = %error, peer, "raft: link attempt failed");
                }
            }

            if let Some(link) = self.links.get(&(peer, stream)) {
                let was_up = link.connected.swap(false, Ordering::SeqCst);
                link.take_down().await;
                if was_up
                    && stream == Stream::Control
                    && let Some(handler) = self.handler().await
                {
                    handler.on_peer_state(peer, false, 0);
                }
            }

            if self.closing.load(Ordering::SeqCst) {
                return;
            }
            tokio::time::sleep(std::time::Duration::from_millis(backoff)).await;
            backoff = backoff.saturating_mul(2).min(RECONNECT_MAX_MS);
        }
    }

    /// Open one link and complete its handshake.
    async fn connect(
        &self,
        peer: u64,
        stream: Stream,
    ) -> Result<Box<dyn AsyncReadUnpinSend>, ConnectFailure> {
        let Some(&(ref host, port)) = self.peers.get(&peer) else {
            return Err(ConnectFailure::Unavailable(RaftUnavailable(format!(
                "member {peer} is not in the member set"
            ))));
        };

        let socket = tokio::net::TcpStream::connect((host.as_str(), port))
            .await
            .map_err(|e| ConnectFailure::Unavailable(RaftUnavailable(e.to_string())))?;
        // Consensus is a request/response protocol on a hot path; Nagle would
        // hold a heartbeat back waiting for more to send.
        drop(socket.set_nodelay(true));

        let (reader, writer): (Box<dyn AsyncReadUnpinSend>, Box<dyn AsyncWriteUnpinSend>) =
            match self.tls {
                None => {
                    let (r, w) = tokio::io::split(socket);
                    (Box::new(r), Box::new(w))
                }
                Some(ref tls) => {
                    // The shared cluster SAN is what is verified, not the
                    // address. Members co-located on one host all answer at
                    // 127.0.0.1, so verifying the address would either fail
                    // against every real certificate or have to be turned off
                    // -- and turning it off is what lets any Product-CA device
                    // certificate answer for a member.
                    let mut ssl = openssl::ssl::Ssl::new(&tls.context)
                        .map_err(|e| ConnectFailure::Unavailable(RaftUnavailable(e.to_string())))?;
                    // SNI, and -- separately -- the name actually verified.
                    // Setting only the first would send the name and check
                    // nothing, which is the shape of a check that looks present
                    // and is not.
                    ssl.set_hostname(&tls.peer_name)
                        .map_err(|e| ConnectFailure::Unavailable(RaftUnavailable(e.to_string())))?;
                    ssl.param_mut()
                        .set_host(&tls.peer_name)
                        .map_err(|e| ConnectFailure::Unavailable(RaftUnavailable(e.to_string())))?;
                    let mut secured = tokio_openssl::SslStream::new(ssl, socket)
                        .map_err(|e| ConnectFailure::Unavailable(RaftUnavailable(e.to_string())))?;
                    std::pin::Pin::new(&mut secured)
                        .connect()
                        .await
                        .map_err(|e| ConnectFailure::Unavailable(RaftUnavailable(e.to_string())))?;
                    let (r, w) = tokio::io::split(secured);
                    (Box::new(r), Box::new(w))
                }
            };

        let Some(link) = self.links.get(&(peer, stream)) else {
            return Err(ConnectFailure::Unavailable(RaftUnavailable(
                "no link".to_owned(),
            )));
        };
        *link.writer.lock().await = Some(writer);

        let mut reader = reader;
        link.write(&frame_for(&self.hello(stream), stream, false))
            .await
            .map_err(ConnectFailure::Unavailable)?;

        let frame = read_frame(&mut reader)
            .await
            .map_err(|e| ConnectFailure::Unavailable(RaftUnavailable(e.to_string())))?;
        if frame.message_type != MessageType::HelloAck {
            return Err(ConnectFailure::Unavailable(RaftUnavailable(format!(
                "member {peer} answered {:?} to a Hello",
                frame.message_type,
            ))));
        }
        let Ok(Message::HelloAck(ack)) = decode_message(frame.message_type, &frame.payload) else {
            return Err(ConnectFailure::Unavailable(RaftUnavailable(
                "undecodable HelloAck".to_owned(),
            )));
        };
        if !ack.accepted {
            return Err(ConnectFailure::Mismatch(RaftClusterMismatch(format!(
                "member {peer} refused the connection: {}",
                ack.reason,
            ))));
        }

        link.incarnation.store(ack.incarnation, Ordering::SeqCst);
        link.connected.store(true, Ordering::SeqCst);
        if stream == Stream::Control
            && let Some(handler) = self.handler().await
        {
            handler.on_peer_state(peer, true, ack.incarnation);
        }
        Ok(reader)
    }

    /// Read replies on an outbound link until it fails.
    async fn pump(&self, peer: u64, stream: Stream, mut reader: Box<dyn AsyncReadUnpinSend>) {
        while !self.closing.load(Ordering::SeqCst) {
            match read_frame(&mut reader).await {
                Ok(frame) => {
                    if let Some(reply) = self.dispatch(peer, &frame).await
                        && let Some(link) = self.links.get(&(peer, stream))
                    {
                        drop(link.write(&frame_for(&reply, frame.stream, true)).await);
                    }
                }
                Err(_) => return,
            }
        }
    }
}

/// Why one connection attempt did not produce a link.
enum ConnectFailure {
    /// Retry, with backoff.
    Unavailable(RaftUnavailable),
    /// Do not expect retrying to help.
    Mismatch(RaftClusterMismatch),
}

/// The read half's object safety, named so a link can hold either kind.
pub trait AsyncReadUnpinSend: tokio::io::AsyncRead + Unpin + Send {}

impl<T> AsyncReadUnpinSend for T where T: tokio::io::AsyncRead + Unpin + Send {}

impl Inner {
    /// Serve one accepted connection: handshake, then frames until it ends.
    async fn serve(self: Arc<Self>, socket: tokio::net::TcpStream) {
        drop(socket.set_nodelay(true));

        let (mut reader, mut writer, dns_names): (
            Box<dyn AsyncReadUnpinSend>,
            Box<dyn AsyncWriteUnpinSend>,
            Vec<String>,
        ) = match self.tls {
            None => {
                let (r, w) = tokio::io::split(socket);
                (Box::new(r), Box::new(w), Vec::new())
            }
            Some(ref tls) => {
                let Ok(ssl) = openssl::ssl::Ssl::new(&tls.context) else {
                    return;
                };
                let Ok(mut secured) = tokio_openssl::SslStream::new(ssl, socket) else {
                    return;
                };
                if std::pin::Pin::new(&mut secured).accept().await.is_err() {
                    return;
                }
                let names = secured
                    .ssl()
                    .peer_certificate()
                    .map(|cert| dns_names_of(&cert))
                    .unwrap_or_default();
                let (r, w) = tokio::io::split(secured);
                (Box::new(r), Box::new(w), names)
            }
        };

        let Ok(frame) = read_frame(&mut reader).await else {
            return;
        };
        if frame.message_type != MessageType::Hello {
            tracing::warn!("raft: first frame was not a Hello");
            return;
        }
        let Ok(Message::Hello(hello)) = decode_message(frame.message_type, &frame.payload) else {
            return;
        };

        // The certificate is checked **before** the acknowledgement is written,
        // so a certificate that is merely valid never reaches the point of
        // being told this member's index and incarnation.
        let refusal = match self.tls {
            Some(ref tls) => refuse_certificate(&tls.peer_name, &dns_names),
            None => None,
        }
        .or_else(|| refuse_hello(&hello, self.local, &self.cluster_id, &self.known_peers()));

        let ack = frame_for(&self.ack(refusal.as_deref()), hello.stream, false);
        if writer.write_all(&ack).await.is_err() || writer.flush().await.is_err() {
            return;
        }
        if let Some(reason) = refusal {
            tracing::warn!(
                member = hello.member_name,
                reason,
                "raft: refused a connection",
            );
            return;
        }

        let peer = hello.member_index;
        // Shared, because the quorum-round handlers answer from their own
        // tasks. `write_all` can yield part-way through a frame, so without
        // this two replies could interleave on the wire; the outbound links
        // have had the same lock for the same reason since they were written.
        let writer = Arc::new(tokio::sync::Mutex::new(writer));
        while !self.closing.load(Ordering::SeqCst) {
            let Ok(inbound) = read_frame(&mut reader).await else {
                return;
            };

            if matches!(
                inbound.message_type,
                MessageType::Propose | MessageType::Forward
            ) && !inbound.is_reply()
            {
                // **Served off this reader, not on it.**
                //
                // These two wait for a quorum round, and the answer to that
                // round arrives as `AppendEntries` on *this very link*.
                // Awaiting them here deadlocks whenever the member that sent
                // the forward is the leader: the handler waits for a commit
                // that cannot be read, because the reader is inside the
                // handler. Measured as every refusal taking the whole mutation
                // deadline, in both implementations.
                //
                // Everything else stays synchronous and in order below, which
                // is what keeps a term from being read and acted on across an
                // await.
                Arc::clone(&self).serve_application(
                    peer,
                    inbound,
                    Arc::clone(&writer),
                );
                continue;
            }

            if let Some(reply) = self.dispatch(peer, &inbound).await {
                let bytes = frame_for(&reply, inbound.stream, true);
                if !write_frame(&writer, &bytes).await {
                    return;
                }
            }
        }
    }

    /// Run a quorum-round handler on its own task, answering when it ends.
    ///
    /// The reply goes back on the connection the request arrived on, which is
    /// deliberate: a member whose inbound link works and whose outbound one
    /// does not is a case this cluster's own harness models, and answering on
    /// a different socket would lose the reply exactly there.
    fn serve_application(
        self: Arc<Self>,
        peer: u64,
        frame: Frame,
        writer: Arc<tokio::sync::Mutex<Box<dyn AsyncWriteUnpinSend>>>,
    ) {
        let permit = match Arc::clone(&self.application_slots).try_acquire_owned() {
            Ok(permit) => permit,
            Err(_) => {
                // At capacity. Refusing now is the honest answer -- waiting for
                // a slot would block this reader, which is the whole defect.
                let refusal = application_refusal(&frame);
                tokio::spawn(async move {
                    if let Some(refusal) = refusal {
                        let bytes = frame_for(&refusal, frame.stream, true);
                        // Best effort: a dead connection means the
                        // caller's own deadline has answered it already.
                        let _sent = write_frame(&writer, &bytes).await;
                    }
                });
                return;
            }
        };

        let owner = Arc::clone(&self);
        let task = tokio::spawn(async move {
            let _permit = permit;
            let Some(handler) = owner.handler().await else {
                return;
            };
            let Ok(message) = decode_message(frame.message_type, &frame.payload) else {
                return;
            };
            let reply = match message {
                Message::Propose(ref m) => {
                    Message::ProposeReply(handler.on_propose(peer, m).await)
                }
                Message::Forward(ref m) => {
                    Message::ForwardReply(handler.on_forward(peer, m).await)
                }
                _ => return,
            };
            let bytes = frame_for(&reply, frame.stream, true);
            // Best effort, for the same reason as the refusal above.
            let _sent = write_frame(&writer, &bytes).await;
        });
        self.serving.lock().push(task);
    }
}

/// Write one whole frame to a shared connection.
///
/// The lock is what keeps two replies from interleaving: `write_all` can yield
/// part-way through a frame, and the quorum-round handlers answer from their
/// own tasks. Returns whether the connection is still usable.
async fn write_frame(
    writer: &tokio::sync::Mutex<Box<dyn AsyncWriteUnpinSend>>,
    bytes: &[u8],
) -> bool {
    let mut guard = writer.lock().await;
    let written = guard.write_all(bytes).await.is_ok() && guard.flush().await.is_ok();
    drop(guard);
    written
}

/// How many forwarded mutations one member will serve at once.
///
/// A bound rather than none, because every one of these is a task awaiting a
/// quorum round and a peer under load can offer them faster than they retire.
/// Exceeding it is answered immediately with a refusal rather than by waiting:
/// the whole point of serving these off the reader is that the reader must not
/// block, and a caller that is refused retries, which is what a 503 already
/// means to it.
pub const APPLICATION_CONCURRENCY: usize = 64;

/// The answer when this member has no capacity left to serve a round.
///
/// Shaped as an ordinary refusal rather than an error, because that is what
/// the caller already handles: a forwarded mutation that comes back not-ok
/// becomes a 503, and a 503 is retried. Saying so immediately is strictly
/// better than making the caller wait out a deadline to learn it.
fn application_refusal(frame: &Frame) -> Option<Message> {
    let request_id = decode_message(frame.message_type, &frame.payload)
        .ok()
        .map_or(0, |message| request_id_of(&message));
    match frame.message_type {
        MessageType::Propose => Some(Message::ProposeReply(crate::messages::ProposeReply {
            accepted: false,
            reason: "this member is at capacity for forwarded work".to_owned(),
            term: 0,
            first_index: 0,
            request_id,
            leader: None,
        })),
        MessageType::Forward => Some(Message::ForwardReply(crate::messages::ForwardReply {
            ok: false,
            created: false,
            error: "unavailable".to_owned(),
            detail: "this member is at capacity for forwarded work".to_owned(),
            applied_index: 0,
            not_owner: false,
            request_id,
            owner: None,
        })),
        _ => None,
    }
}

/// Every DNS SAN on a certificate.
///
/// Only DNS entries: an IP SAN would let a member be admitted by address, and
/// the whole point of `peer_name` is that the address is not what identifies a
/// member in a cluster whose members may share one.
fn dns_names_of(certificate: &openssl::x509::X509) -> Vec<String> {
    certificate
        .subject_alt_names()
        .map(|names| {
            names
                .iter()
                .filter_map(|name| name.dnsname().map(ToOwned::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

#[async_trait]
impl Transport for RaftTransport {
    #[expect(
        clippy::significant_drop_tightening,
        reason = "the task list is held until every task is spawned, so a \
                  concurrent close cannot miss one and leave it running"
    )]
    async fn start(&self, handler: Arc<dyn PeerHandler>) -> std::io::Result<()> {
        *self.inner.handler.lock().await = Some(handler);
        self.inner.closing.store(false, Ordering::SeqCst);

        let listener = tokio::net::TcpListener::bind(self.bind).await?;
        *self.bound.lock().await = Some(listener.local_addr()?);

        let mut tasks = self.tasks.lock().await;

        let accepting = Arc::clone(&self.inner);
        tasks.push(tokio::spawn(async move {
            while !accepting.closing.load(Ordering::SeqCst) {
                match listener.accept().await {
                    Ok((socket, _)) => {
                        let serving = tokio::spawn(Arc::clone(&accepting).serve(socket));
                        let mut held = accepting.inbound.lock().await;
                        // Finished connections are reaped here rather than by a
                        // timer: the list is only ever walked on accept and on
                        // close, and a cluster that accepts nothing has nothing
                        // to reap.
                        held.retain(|task| !task.is_finished());
                        held.push(serving);
                    }
                    // A failed accept is not a reason to stop accepting: the
                    // usual cause is a file-descriptor limit, which clears.
                    Err(error) => {
                        tracing::warn!(%error, "raft: accept failed");
                        tokio::time::sleep(std::time::Duration::from_millis(RECONNECT_INITIAL_MS))
                            .await;
                    }
                }
            }
        }));

        for &peer in self.inner.peers.keys() {
            for stream in [Stream::Control, Stream::Bulk] {
                tasks.push(tokio::spawn(Arc::clone(&self.inner).maintain(peer, stream)));
            }
        }
        Ok(())
    }

    async fn close(&self) {
        self.inner.closing.store(true, Ordering::SeqCst);
        // The application handlers first: each is awaiting a quorum round that
        // this transport is about to stop carrying, so none of them can finish.
        let serving = std::mem::take(&mut *self.inner.serving.lock());
        for task in serving {
            task.abort();
        }
        // Hang up on accepted connections first, which is what lets a peer's
        // outbound link notice and report this member down.
        for task in self.inner.inbound.lock().await.drain(..) {
            task.abort();
            drop(task.await);
        }
        for task in self.tasks.lock().await.drain(..) {
            task.abort();
            // The result is discarded on purpose: an aborted task reports
            // cancellation, and that is what was asked for.
            drop(task.await);
        }
        for link in self.inner.links.values() {
            link.take_down().await;
        }
        *self.inner.handler.lock().await = None;
    }

    fn send(&self, peer: u64, message: &Message, stream: Stream) {
        let Some(link) = self.inner.links.get(&(peer, stream)) else {
            return;
        };
        if !link.connected.load(Ordering::SeqCst) {
            return;
        }
        let link = Arc::clone(link);
        let bytes = frame_for(message, stream, false);
        // Spawned because `send` is synchronous by design -- the node calls it
        // from inside its own critical section, and an `async fn` there would
        // be the await the whole locking model excludes. A write that fails is
        // a message that did not go, which replication sends again.
        tokio::spawn(async move {
            if link.write(&bytes).await.is_err() {
                link.take_down().await;
            }
        });
    }

    async fn request(
        &self,
        peer: u64,
        message: &Message,
        stream: Stream,
        timeout_ms: Option<u64>,
    ) -> Result<Message, RaftUnavailable> {
        let Some(link) = self.inner.links.get(&(peer, stream)) else {
            return Err(RaftUnavailable(format!("no link to member {peer}")));
        };
        if !link.connected.load(Ordering::SeqCst) {
            return Err(RaftUnavailable(format!("no link to member {peer}")));
        }

        let request_id = self.inner.next_request_id.fetch_add(1, Ordering::SeqCst);
        let Some(tagged) = with_request_id(message, request_id) else {
            return Err(RaftUnavailable(format!(
                "{:?} carries no request_id and cannot be awaited; use send() \
                 for it",
                message.message_type(),
            )));
        };

        // Registered before the write, never after: the reply can arrive
        // between the two, and a waiter that did not exist yet would be a
        // message answered into the void and a caller waiting out its whole
        // deadline for something that had already happened.
        let Some(expected) = tagged.expected_reply() else {
            return Err(RaftUnavailable(format!(
                "{:?} draws no reply and cannot be awaited; use send() for it",
                message.message_type(),
            )));
        };
        let (sender, receiver) = oneshot::channel();
        link.pending
            .lock()
            .await
            .insert(request_id, (expected, sender));

        if let Err(error) = link.write(&frame_for(&tagged, stream, false)).await {
            link.pending.lock().await.remove(&request_id);
            return Err(error);
        }

        let deadline =
            std::time::Duration::from_millis(timeout_ms.unwrap_or(self.inner.rpc_timeout_ms));
        match tokio::time::timeout(deadline, receiver).await {
            Ok(Ok(reply)) => Ok(reply),
            Ok(Err(_)) => Err(RaftUnavailable(format!("link to member {peer} failed"))),
            Err(_) => {
                link.pending.lock().await.remove(&request_id);
                Err(RaftUnavailable(format!(
                    "member {peer} did not answer within the deadline"
                )))
            }
        }
    }

    fn live(&self) -> Vec<u64> {
        let mut live: Vec<u64> = self
            .inner
            .links
            .iter()
            .filter(|&(&(_, stream), link)| {
                stream == Stream::Control && link.connected.load(Ordering::SeqCst)
            })
            .map(|(&(peer, _), _)| peer)
            .collect();
        live.sort_unstable();
        live
    }
}
