// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! An in-process network with the production transport's semantics.
//!
//! # Why not `tests/fabric`
//!
//! The fabric is right for what it was built for -- arranging one scenario and
//! asserting one outcome -- and wrong for a soak in three ways that matter:
//!
//! * it spawns a task **per message**, so on a multi-threaded runtime two
//!   messages sent in order on one link can arrive in the other order. TCP
//!   never does that, and a soak that reports failures against a network the
//!   implementation will never meet is a soak nobody can trust;
//! * it does not implement correlated `request`, deliberately, so the
//!   forwarding path -- a mutation handed to the member that owns its Node --
//!   is unreachable through it;
//! * it has no delay, no stall and no reconnection time.
//!
//! # The model, and where each rule comes from
//!
//! Read from `src/transport.rs`, not assumed:
//!
//! * **A connection is directed by who dialed.** Member `a` holds an outbound
//!   CONTROL and BULK connection to each peer `b`; everything `a` *initiates*
//!   towards `b` travels on it, and every reply `b` gives travels **back on
//!   that same connection** (`fabric/mod.rs` and the Python memory network
//!   instead route a reply by the reverse link's reachability, which is a
//!   different fault shape from the one TCP produces).
//! * **`on_peer_state` and `live()` follow the outbound CONTROL link**
//!   (`transport.rs:773`, `:877`, `:1303`): `a` learns `b` is up when its own
//!   dial and handshake succeed, carrying `b`'s incarnation from the
//!   `HelloAck`, and learns it is down when that connection fails.
//! * **FIFO within a connection, nothing across them.** Each `(connection,
//!   stream, direction)` is a queue with monotone release times, so a slow
//!   link holds messages back without shuffling them, while CONTROL and BULK
//!   drift apart freely -- a snapshot chunk overtaking a heartbeat, or the
//!   reverse, is a real interleaving.
//! * **A broken connection loses what was in flight**, in both directions,
//!   and fails every correlated request waiting on it -- a dropped TCP
//!   connection loses its send buffer and the transport fails the futures.
//! * **Reconnection takes time.** A link that could come back does so after a
//!   random delay, standing in for the transport's backoff, so "healed" and
//!   "connected" are different instants exactly as they are in production.
//!
//! One fault the fabric cannot express at all is modelled too: a **stall**.
//! The connection stays up -- no `on_peer_state`, still `live()` -- but
//! nothing is delivered until it thaws, and then everything is, in order. That
//! is a frozen process, or a partition that TCP rides out: the peer that "is
//! present and useless", which is what check-quorum and the leader lease exist
//! to handle and what a clean disconnect never exercises.
//!
//! No per-message loss, duplication or reordering *within* a connection:
//! TCP delivers in order or breaks, and injecting anything else tests a
//! network this transport cannot meet.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, OnceLock, Weak};
use std::time::Duration;

use async_trait::async_trait;
use nmos_registry_raft::messages::Message;
use nmos_registry_raft::transport::{PeerHandler, RaftUnavailable, Transport};
use nmos_registry_raft::wire::Stream;
use parking_lot::Mutex;
use tokio::sync::{Notify, mpsc, oneshot};
use tokio::time::Instant;

use super::audit::DecisionAudit;
use super::forensics::{Fate, Forensics};
use super::rng::Rng;

/// A connection, named `(dialer, acceptor)`.
pub type LinkKey = (u64, u64);

/// What one stall froze: every connection touching a member, or one connection.
#[derive(Debug, Clone, Copy)]
enum Stalled {
    Member(u64),
    Link(LinkKey),
}

/// Which way a packet travels on its connection.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
enum Way {
    /// From the dialer to the acceptor: requests and one-way messages.
    Out,
    /// From the acceptor back to the dialer: replies.
    Back,
}

enum Payload {
    /// A message whose reply, if it has one, comes back on this connection.
    Message(Message),
    /// A correlated request; its reply completes a future on the dialer.
    Request { id: u64, message: Message },
    /// The reply to a correlated request.
    Reply { id: u64, message: Message },
}

impl Payload {
    const fn message(&self) -> &Message {
        match *self {
            Self::Message(ref message)
            | Self::Request { ref message, .. }
            | Self::Reply { ref message, .. } => message,
        }
    }
}

struct Packet {
    epoch: u64,
    release: Instant,
    payload: Payload,
}

type Waiter = oneshot::Sender<Result<Message, RaftUnavailable>>;

/// One directed connection.
#[derive(Default)]
struct Link {
    /// Bumped every time the connection drops, so a packet can tell whether
    /// the connection it was sent on is the one that still exists.
    epoch: u64,
    connected: bool,
    /// A reconnect is scheduled.
    connecting: bool,
    queues: HashMap<(Stream, Way), mpsc::UnboundedSender<Packet>>,
    last_release: HashMap<(Stream, Way), Instant>,
    /// Packets queued and not yet delivered or lost, per queue.
    in_flight: HashMap<(Stream, Way), u64>,
    pending: HashMap<u64, Waiter>,
    /// Woken when the connection drops, so a delivery task asleep on a packet
    /// of the dead connection lets go of it at once. Without this, one stale
    /// packet with a distant release time blocks every packet of the
    /// connection that replaces it -- a head-of-line block across two
    /// connections, which TCP cannot produce because a new connection is a
    /// new queue.
    dropped: Arc<Notify>,
}

struct Slot {
    handler: Option<Weak<dyn PeerHandler>>,
    incarnation: u64,
}

/// A snapshot transfer in progress: which snapshot -- `(term, last_index,
/// last_term)` -- and the offset its next chunk should carry.
type Transfer = ((u64, u64, u64), u64);

/// How far one connection has carried a snapshot from its sender to its
/// receiver: what the storm detector needs to tell a long transfer from one
/// going round in circles.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Carried {
    /// The connection (`Link::epoch`). A new one may begin again legitimately.
    epoch: u64,
    /// `(term, last_index, last_term)` of the snapshot.
    identity: (u64, u64, u64),
    /// The end of the furthest chunk delivered.
    sent: u64,
    /// The most any answer has acknowledged.
    acknowledged: u64,
}

/// Does `message` carry a snapshot transfer further than its connection has
/// carried it before?
///
/// A chunk does when it ends past every byte of its snapshot already delivered
/// on this connection; an answer does when it acknowledges more than any before
/// it. A chunk of another snapshot, or on a new connection, begins a new count.
/// Anything else -- a chunk sent again, a restart of the same snapshot on the
/// same connection, an answer of zero, every other kind of message -- does not.
pub fn advances(
    carried: &mut BTreeMap<(u64, u64), Carried>,
    from: u64,
    to: u64,
    epoch: u64,
    message: &Message,
) -> bool {
    match *message {
        Message::InstallSnapshot(ref chunk) => {
            let identity = (chunk.term, chunk.last_index, chunk.last_term);
            let end = chunk.offset.saturating_add(chunk.data.len() as u64);
            let fresh = Carried {
                epoch,
                identity,
                sent: 0,
                acknowledged: 0,
            };
            let mark = carried.entry((from, to)).or_insert(fresh);
            if mark.epoch != epoch || mark.identity != identity {
                *mark = fresh;
            }
            if end > mark.sent {
                mark.sent = end;
                true
            } else {
                false
            }
        }
        // An answer travels back on the connection its chunk came by, so the
        // transfer it acknowledges is the reverse pair's.
        Message::InstallSnapshotReply(ref reply) => match carried.get_mut(&(to, from)) {
            Some(mark) if mark.epoch == epoch && reply.bytes_received > mark.acknowledged => {
                mark.acknowledged = reply.bytes_received;
                true
            }
            _ => false,
        },
        _ => false,
    }
}

/// Tunables, all changeable mid-run.
#[derive(Debug, Clone, Copy)]
pub struct Knobs {
    /// Largest ordinary per-message delay, in microseconds.
    pub max_delay_us: u64,
    /// One message in this many is delayed ten times as long. Zero disables.
    pub spike_one_in: u64,
    /// Largest wait before a link that can reconnect does, in microseconds.
    pub reconnect_max_us: u64,
}

struct Inner {
    slots: BTreeMap<u64, Slot>,
    down: BTreeSet<u64>,
    blocked: BTreeSet<LinkKey>,
    /// Stalled members and connections, each with the serial of the stall
    /// in effect -- so the timer a stall starts can tell, when it fires,
    /// whether that stall is still the one in effect.
    stalled_members: BTreeMap<u64, u64>,
    stalled_links: BTreeMap<LinkKey, u64>,
    stall_serial: u64,
    /// Stalls that outlasted the transport's read timeout. The transport
    /// closes a connection that carries nothing for that long
    /// (`transport::CONN_READ_TIMEOUT_MS`), so theirs are down -- what they
    /// held lost with them -- and stay down until the stall ends: a stalled
    /// path completes no handshake either.
    severed_members: BTreeSet<u64>,
    severed_links: BTreeSet<LinkKey>,
    /// Per-connection slowness, as a percentage of the ordinary delay.
    slowness: BTreeMap<LinkKey, u64>,
    links: BTreeMap<LinkKey, Link>,
    rng: Rng,
    knobs: Knobs,
    /// Per `(sender, receiver)`: the snapshot transfer in progress.
    transfers: BTreeMap<(u64, u64), Transfer>,
    /// Per `(sender, receiver)`: how far a transfer has been carried, for the
    /// storm detector (see [`advances`]).
    carried: BTreeMap<(u64, u64), Carried>,
    /// Chunks that continued a transfer of a *different* snapshot.
    splices: Vec<String>,
    /// Storm detection: the instant deliveries are being counted at, how many
    /// there have been at it, and what they were.
    storm_at: Option<Instant>,
    storm_count: u64,
    storm_mix: BTreeMap<(u64, u64, &'static str), u64>,
    /// A storm was seen; the network delivers nothing more.
    storms: Vec<String>,
    /// Forwards per `(from, to, resource)`, for spotting a forwarding loop.
    forwards: HashMap<(u64, u64, String), u64>,
    /// Forwarding loops seen; once one is, every further forward fails.
    forward_loops: Vec<String>,
}

impl Inner {
    fn attached(&self, member: u64) -> bool {
        self.slots
            .get(&member)
            .is_some_and(|slot| slot.handler.is_some())
    }

    /// Whether `key` should have a connection right now.
    fn wanted(&self, key: LinkKey) -> bool {
        let (dialer, acceptor) = key;
        self.attached(dialer)
            && self.attached(acceptor)
            && !self.down.contains(&dialer)
            && !self.down.contains(&acceptor)
            && !self.blocked.contains(&key)
            && !self.severed_members.contains(&dialer)
            && !self.severed_members.contains(&acceptor)
            && !self.severed_links.contains(&key)
    }

    fn stalled(&self, key: LinkKey) -> bool {
        self.stalled_members.contains_key(&key.0)
            || self.stalled_members.contains_key(&key.1)
            || self.stalled_links.contains_key(&key)
    }

    fn delay(&mut self, key: LinkKey) -> Duration {
        let knobs = self.knobs;
        let mut micros = self.rng.below(knobs.max_delay_us.saturating_add(1));
        if knobs.spike_one_in > 0 && self.rng.below(knobs.spike_one_in) == 0 {
            micros = micros.saturating_mul(10).max(knobs.max_delay_us);
        }
        if let Some(&percent) = self.slowness.get(&key) {
            micros = micros.saturating_mul(percent) / 100;
        }
        Duration::from_micros(micros)
    }
}

/// A notification owed to one member, delivered after the lock is released.
struct Note {
    to: u64,
    peer: u64,
    up: bool,
    incarnation: u64,
}

/// What the network did, for the run report.
#[derive(Debug, Default)]
pub struct NetStats {
    /// Messages delivered.
    pub delivered: AtomicU64,
    /// Messages refused at send for want of a connection.
    pub unlinked: AtomicU64,
    /// Messages lost with the connection they were on.
    pub lost: AtomicU64,
    /// Correlated requests made.
    pub requests: AtomicU64,
    /// Correlated requests that failed.
    pub request_failures: AtomicU64,
    /// Connections established.
    pub connects: AtomicU64,
    /// Connections dropped.
    pub drops: AtomicU64,
    /// Packets that had to wait out a stall.
    pub stalled: AtomicU64,
    /// Stalls that outlasted the read timeout and severed their connections.
    pub severed: AtomicU64,
}

/// The network.
pub struct ChaosNet {
    inner: Mutex<Inner>,
    unstalled: Notify,
    next_request: AtomicU64,
    rpc_timeout_ms: u64,
    this: Weak<ChaosNet>,
    /// Every message's fate.
    pub forensics: Arc<Forensics>,
    /// Counters.
    pub stats: NetStats,
    /// Audits each delivery's commit and promotion decisions, where that is exact.
    audit: OnceLock<Arc<DecisionAudit>>,
}

impl ChaosNet {
    /// A network for `members`, none attached yet.
    #[must_use]
    pub fn new(
        members: &[u64],
        rng: Rng,
        knobs: Knobs,
        forensics: Arc<Forensics>,
        rpc_timeout_ms: u64,
    ) -> Arc<Self> {
        let slots = members
            .iter()
            .map(|&member| {
                (
                    member,
                    Slot {
                        handler: None,
                        incarnation: 0,
                    },
                )
            })
            .collect();
        Arc::new_cyclic(|this| Self {
            inner: Mutex::new(Inner {
                slots,
                down: BTreeSet::new(),
                blocked: BTreeSet::new(),
                stalled_members: BTreeMap::new(),
                stalled_links: BTreeMap::new(),
                stall_serial: 0,
                severed_members: BTreeSet::new(),
                severed_links: BTreeSet::new(),
                slowness: BTreeMap::new(),
                links: BTreeMap::new(),
                rng,
                knobs,
                transfers: BTreeMap::new(),
                carried: BTreeMap::new(),
                splices: Vec::new(),
                storm_at: None,
                storm_count: 0,
                storm_mix: BTreeMap::new(),
                storms: Vec::new(),
                forwards: HashMap::new(),
                forward_loops: Vec::new(),
            }),
            unstalled: Notify::new(),
            next_request: AtomicU64::new(1),
            rpc_timeout_ms,
            this: this.clone(),
            forensics,
            stats: NetStats::default(),
            audit: OnceLock::new(),
        })
    }

    /// Audit the commit and promotion decisions every delivery causes.
    ///
    /// Sound only where a handler call runs alone -- a current-thread runtime --
    /// so the driver installs it for virtual runs only; see [`DecisionAudit`].
    /// Installed once, before any member is built, so that every member is
    /// tracked from its first delivery.
    pub fn install_audit(&self, audit: Arc<DecisionAudit>) {
        assert!(
            self.audit.set(audit).is_ok(),
            "the decision audit is installed once, when the run is built"
        );
    }

    /// The decision audit, when this run has one.
    #[must_use]
    pub fn audit(&self) -> Option<&Arc<DecisionAudit>> {
        self.audit.get()
    }

    /// One member's transport.
    #[must_use]
    pub fn transport(self: &Arc<Self>, local: u64) -> Arc<ChaosTransport> {
        Arc::new(ChaosTransport {
            local,
            net: Arc::clone(self),
        })
    }

    /// The incarnation peers will be told `member` has, from its next attach.
    pub fn set_incarnation(&self, member: u64, incarnation: u64) {
        if let Some(slot) = self.inner.lock().slots.get_mut(&member) {
            slot.incarnation = incarnation;
        }
    }

    /// The current tunables.
    #[must_use]
    pub fn knobs(&self) -> Knobs {
        self.inner.lock().knobs
    }

    /// Replace the tunables. Affects messages sent from now on.
    pub fn set_knobs(&self, knobs: Knobs) {
        self.inner.lock().knobs = knobs;
    }

    // -- faults -------------------------------------------------------------

    /// Take a member off the network: every connection to and from it drops.
    pub fn stop(&self, member: u64) {
        self.inner.lock().down.insert(member);
        self.reconcile();
    }

    /// Put it back. Its connections re-form after their reconnect delays.
    pub fn resume(&self, member: u64) {
        self.inner.lock().down.remove(&member);
        self.reconcile();
    }

    /// `dialer` can no longer connect to `acceptor`; the reverse is untouched.
    ///
    /// Connection-level asymmetry, the shape a firewall produces: `acceptor`'s
    /// own connection to `dialer` keeps working in both directions, so
    /// `dialer` still hears `acceptor` and still answers it.
    pub fn block(&self, dialer: u64, acceptor: u64) {
        self.inner.lock().blocked.insert((dialer, acceptor));
        self.reconcile();
    }

    /// Isolate the groups from each other, healing everything else.
    pub fn partition(&self, groups: &[BTreeSet<u64>]) {
        {
            let mut inner = self.inner.lock();
            inner.blocked.clear();
            let members: Vec<u64> = inner.slots.keys().copied().collect();
            for group in groups {
                for &inside in group {
                    for &outside in members.iter().filter(|m| !group.contains(m)) {
                        inner.blocked.insert((inside, outside));
                        inner.blocked.insert((outside, inside));
                    }
                }
            }
        }
        self.reconcile();
    }

    /// Remove every block and partition. Stopped members stay stopped.
    pub fn heal(&self) {
        self.inner.lock().blocked.clear();
        self.reconcile();
    }

    /// Freeze every connection touching `member`, without breaking any.
    ///
    /// Until the transport's read timeout: a stall that outlasts it severs
    /// those connections, as the transport closes a connection that carries
    /// nothing for that long (see `Inner::severed_members`).
    pub fn stall(&self, member: u64) {
        let serial = {
            let mut inner = self.inner.lock();
            if inner.stalled_members.contains_key(&member) {
                // Already stalled: a stall is one stretch, and its clock
                // started when it did.
                return;
            }
            inner.stall_serial = inner.stall_serial.saturating_add(1);
            let serial = inner.stall_serial;
            inner.stalled_members.insert(member, serial);
            serial
        };
        self.sever_if_still_stalled(Stalled::Member(member), serial);
    }

    /// Freeze one connection, without breaking it -- until the read timeout,
    /// as [`Self::stall`].
    pub fn stall_link(&self, key: LinkKey) {
        let serial = {
            let mut inner = self.inner.lock();
            if inner.stalled_links.contains_key(&key) {
                return;
            }
            inner.stall_serial = inner.stall_serial.saturating_add(1);
            let serial = inner.stall_serial;
            inner.stalled_links.insert(key, serial);
            serial
        };
        self.sever_if_still_stalled(Stalled::Link(key), serial);
    }

    /// Once the transport's read timeout has passed, sever what a stall has
    /// frozen -- if it is still the same stall.
    fn sever_if_still_stalled(&self, stalled: Stalled, serial: u64) {
        let this = self.this.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(
                nmos_registry_raft::transport::CONN_READ_TIMEOUT_MS,
            ))
            .await;
            let Some(net) = this.upgrade() else {
                return;
            };
            {
                let mut inner = net.inner.lock();
                let current = match stalled {
                    Stalled::Member(member) => inner.stalled_members.get(&member).copied(),
                    Stalled::Link(key) => inner.stalled_links.get(&key).copied(),
                };
                if current != Some(serial) {
                    // Thawed in time -- or thawed and stalled afresh, and that
                    // stall has its own timer.
                    return;
                }
                match stalled {
                    Stalled::Member(member) => inner.severed_members.insert(member),
                    Stalled::Link(key) => inner.severed_links.insert(key),
                };
            }
            net.stats.severed.fetch_add(1, Ordering::Relaxed);
            net.reconcile();
        });
    }

    /// Thaw `member`. What its connections held is delivered, in order -- or,
    /// were they severed, lost with them; they reconnect now.
    pub fn unstall(&self, member: u64) {
        {
            let mut inner = self.inner.lock();
            inner.stalled_members.remove(&member);
            inner.severed_members.remove(&member);
        }
        self.unstalled.notify_waiters();
        self.reconcile();
    }

    /// Thaw every single-connection stall, leaving member stalls alone.
    pub fn unstall_links(&self) {
        {
            let mut inner = self.inner.lock();
            inner.stalled_links.clear();
            inner.severed_links.clear();
        }
        self.unstalled.notify_waiters();
        self.reconcile();
    }

    /// Thaw everything.
    pub fn unstall_all(&self) {
        {
            let mut inner = self.inner.lock();
            inner.stalled_members.clear();
            inner.stalled_links.clear();
            inner.severed_members.clear();
            inner.severed_links.clear();
        }
        self.unstalled.notify_waiters();
        self.reconcile();
    }

    /// Make one connection `percent`% as slow as ordinary, both ways.
    pub fn slow(&self, key: LinkKey, percent: u64) {
        self.inner.lock().slowness.insert(key, percent);
    }

    /// Every connection back to ordinary speed.
    pub fn unslow_all(&self) {
        self.inner.lock().slowness.clear();
    }

    /// Whether `dialer`'s connection to `acceptor` is up.
    #[must_use]
    pub fn connected(&self, dialer: u64, acceptor: u64) -> bool {
        self.inner
            .lock()
            .links
            .get(&(dialer, acceptor))
            .is_some_and(|link| link.connected)
    }

    /// Whether every wanted connection is up and nothing is stalled or blocked.
    #[must_use]
    pub fn settled(&self) -> bool {
        let inner = self.inner.lock();
        let keys: Vec<LinkKey> = pairs(&inner);
        inner.blocked.is_empty()
            && inner.down.is_empty()
            && inner.stalled_members.is_empty()
            && inner.stalled_links.is_empty()
            && keys.iter().all(|&key| {
                !inner.wanted(key) || inner.links.get(&key).is_some_and(|link| link.connected)
            })
    }

    // -- attachment ---------------------------------------------------------

    fn attach(&self, member: u64, handler: &Arc<dyn PeerHandler>) {
        if let Some(slot) = self.inner.lock().slots.get_mut(&member) {
            slot.handler = Some(Arc::downgrade(handler));
        }
        self.reconcile();
    }

    fn detach(&self, member: u64) {
        if let Some(slot) = self.inner.lock().slots.get_mut(&member) {
            slot.handler = None;
        }
        self.reconcile();
    }

    /// Bring every connection in line with what the faults now allow.
    ///
    /// Drops are immediate; connects are scheduled after a random delay.
    /// Notifications and failed requests are delivered after the lock is
    /// released, because a handler that is told a peer went down immediately
    /// sends -- and sending takes this lock.
    fn reconcile(&self) {
        let mut notes = Vec::new();
        let mut failed: Vec<Waiter> = Vec::new();
        let mut connects: Vec<(LinkKey, u64, Duration)> = Vec::new();
        {
            let mut inner = self.inner.lock();
            let keys = pairs(&inner);
            for key in keys {
                let wanted = inner.wanted(key);
                let reconnect_max = inner.knobs.reconnect_max_us;
                let wait = Duration::from_micros(inner.rng.below(reconnect_max.saturating_add(1)));
                let link = inner.links.entry(key).or_default();
                if !wanted {
                    if link.connected || link.connecting {
                        let was_up = link.connected;
                        link.connected = false;
                        link.connecting = false;
                        link.epoch = link.epoch.saturating_add(1);
                        link.last_release.clear();
                        link.dropped.notify_waiters();
                        failed.extend(link.pending.drain().map(|(_, waiter)| waiter));
                        if was_up {
                            self.stats.drops.fetch_add(1, Ordering::Relaxed);
                            notes.push(Note {
                                to: key.0,
                                peer: key.1,
                                up: false,
                                incarnation: 0,
                            });
                        }
                    }
                } else if !link.connected && !link.connecting {
                    link.connecting = true;
                    connects.push((key, link.epoch, wait));
                }
            }
        }
        for waiter in failed {
            self.stats.request_failures.fetch_add(1, Ordering::Relaxed);
            drop(waiter.send(Err(RaftUnavailable(
                "the connection dropped with the request in flight".to_owned(),
            ))));
        }
        for (key, epoch, wait) in connects {
            let this = self.this.clone();
            tokio::spawn(async move {
                tokio::time::sleep(wait).await;
                if let Some(net) = this.upgrade() {
                    net.complete_connect(key, epoch);
                }
            });
        }
        self.notify(notes);
    }

    fn complete_connect(&self, key: LinkKey, epoch: u64) {
        let note = {
            let mut inner = self.inner.lock();
            let wanted = inner.wanted(key);
            let incarnation = inner.slots.get(&key.1).map_or(0, |slot| slot.incarnation);
            let Some(link) = inner.links.get_mut(&key) else {
                return;
            };
            if !link.connecting || link.epoch != epoch || !wanted {
                // Superseded: the link dropped again, or was never wanted by
                // the time the reconnect came due.
                return;
            }
            link.connecting = false;
            link.connected = true;
            self.stats.connects.fetch_add(1, Ordering::Relaxed);
            Note {
                to: key.0,
                peer: key.1,
                up: true,
                incarnation,
            }
        };
        self.notify(vec![note]);
    }

    fn notify(&self, notes: Vec<Note>) {
        for note in notes {
            let handler = self.handler(note.to);
            if let Some(handler) = handler {
                handler.on_peer_state(note.peer, note.up, note.incarnation);
            }
        }
    }

    fn handler(&self, member: u64) -> Option<Arc<dyn PeerHandler>> {
        self.inner
            .lock()
            .slots
            .get(&member)
            .and_then(|slot| slot.handler.as_ref())
            .and_then(Weak::upgrade)
    }

    // -- sending ------------------------------------------------------------

    /// Queue a packet on one direction of one connection.
    ///
    /// Release times are monotone per queue, which is what keeps a delayed
    /// connection FIFO; the delivery task for a queue is spawned the first
    /// time the queue is used.
    fn enqueue(&self, inner: &mut Inner, key: LinkKey, stream: Stream, way: Way, payload: Payload) {
        let delay = inner.delay(key);
        let Some(link) = inner.links.get_mut(&key) else {
            return;
        };
        let now = Instant::now();
        let previous = link
            .last_release
            .get(&(stream, way))
            .copied()
            .unwrap_or(now);
        let release = if inner_queueing() {
            // Each packet's delay is added after the previous packet's release:
            // a queue whose service times accumulate. This is the Python
            // memory network's formula, and it is unstable whenever packets
            // are sent faster than they are served -- a heartbeating leader on
            // a slow link builds a backlog that grows faster than time passes.
            let floor = previous.max(now);
            floor.checked_add(delay).unwrap_or(floor)
        } else {
            // Latency, not service time: each packet arrives `delay` after it
            // was sent, but never before the packet ahead of it. FIFO without
            // accumulation -- what a TCP connection over a slow path does.
            let arrival = now.checked_add(delay).unwrap_or(now);
            arrival.max(previous)
        };
        link.last_release.insert((stream, way), release);
        *link.in_flight.entry((stream, way)).or_insert(0) += 1;
        let packet = Packet {
            epoch: link.epoch,
            release,
            payload,
        };
        let dropped = Arc::clone(&link.dropped);
        let queue = link.queues.entry((stream, way)).or_insert_with(|| {
            let (sender, receiver) = mpsc::unbounded_channel();
            let this = self.this.clone();
            tokio::spawn(deliver_loop(this, key, stream, way, receiver, dropped));
            sender
        });
        // A closed queue means the delivery task is gone, which only happens
        // when the network itself is being dropped.
        drop(queue.send(packet));
    }

    fn send(&self, from: u64, to: u64, message: &Message, stream: Stream) {
        let sent = {
            let mut inner = self.inner.lock();
            let connected = inner
                .links
                .get(&(from, to))
                .is_some_and(|link| link.connected);
            if connected {
                self.enqueue(
                    &mut inner,
                    (from, to),
                    stream,
                    Way::Out,
                    Payload::Message(message.clone()),
                );
            }
            connected
        };
        if !sent {
            self.stats.unlinked.fetch_add(1, Ordering::Relaxed);
            self.forensics
                .note(from, to, stream, Fate::Unlinked, message);
        }
    }

    async fn request(
        &self,
        from: u64,
        to: u64,
        message: &Message,
        stream: Stream,
        timeout_ms: Option<u64>,
    ) -> Result<Message, RaftUnavailable> {
        self.stats.requests.fetch_add(1, Ordering::Relaxed);
        if let Message::Forward(ref forward) = *message
            && self.forwarding_loop(from, to, &forward.resource_id)
        {
            self.stats.request_failures.fetch_add(1, Ordering::Relaxed);
            return Err(RaftUnavailable(
                "the soak network stopped a forwarding loop".to_owned(),
            ));
        }
        let id = self.next_request.fetch_add(1, Ordering::Relaxed);
        let (waiter, answer) = oneshot::channel();
        {
            let mut inner = self.inner.lock();
            let connected = match inner.links.get_mut(&(from, to)) {
                Some(link) if link.connected => {
                    link.pending.insert(id, waiter);
                    true
                }
                _ => false,
            };
            if !connected {
                drop(inner);
                self.stats.request_failures.fetch_add(1, Ordering::Relaxed);
                self.forensics
                    .note(from, to, stream, Fate::Unlinked, message);
                return Err(RaftUnavailable(format!("no connection to member {to}")));
            }
            self.enqueue(
                &mut inner,
                (from, to),
                stream,
                Way::Out,
                Payload::Request {
                    id,
                    message: message.clone(),
                },
            );
        }
        let limit = Duration::from_millis(timeout_ms.unwrap_or(self.rpc_timeout_ms));
        match tokio::time::timeout(limit, answer).await {
            Ok(Ok(result)) => {
                if result.is_err() {
                    self.stats.request_failures.fetch_add(1, Ordering::Relaxed);
                }
                result
            }
            Ok(Err(_)) => {
                self.stats.request_failures.fetch_add(1, Ordering::Relaxed);
                Err(RaftUnavailable("the request was abandoned".to_owned()))
            }
            Err(_) => {
                if let Some(link) = self.inner.lock().links.get_mut(&(from, to)) {
                    link.pending.remove(&id);
                }
                self.stats.request_failures.fetch_add(1, Ordering::Relaxed);
                Err(RaftUnavailable(format!(
                    "member {to} did not answer within {limit:?}"
                )))
            }
        }
    }

    fn live_of(&self, local: u64) -> Vec<u64> {
        let inner = self.inner.lock();
        inner
            .links
            .iter()
            .filter(|&(&(dialer, _), link)| dialer == local && link.connected)
            .map(|(&(_, acceptor), _)| acceptor)
            .collect()
    }

    // -- delivery -----------------------------------------------------------

    /// A packet's release time has come: deliver it, hold it, or lose it.
    async fn arrive(&self, key: LinkKey, stream: Stream, way: Way, packet: Packet) {
        self.departed(key, stream, way);
        let (from, to) = match way {
            Way::Out => (key.0, key.1),
            Way::Back => (key.1, key.0),
        };
        let mut waited = false;
        let handler = loop {
            // Enabled before the check, so a thaw between the check and the
            // await cannot be missed.
            let thawed = self.unstalled.notified();
            tokio::pin!(thawed);
            thawed.as_mut().enable();
            let verdict = {
                let inner = self.inner.lock();
                match inner.links.get(&key) {
                    Some(link) if link.connected && link.epoch == packet.epoch => {
                        if inner.stalled(key) {
                            Verdict::Hold
                        } else {
                            inner
                                .slots
                                .get(&to)
                                .and_then(|slot| slot.handler.as_ref())
                                .and_then(Weak::upgrade)
                                .map_or(Verdict::Lose, Verdict::Deliver)
                        }
                    }
                    _ => Verdict::Lose,
                }
            };
            match verdict {
                Verdict::Deliver(handler) => break handler,
                Verdict::Lose => {
                    self.stats.lost.fetch_add(1, Ordering::Relaxed);
                    self.forensics
                        .note(from, to, stream, Fate::Lost, packet.payload.message());
                    return;
                }
                Verdict::Hold => {
                    if !waited {
                        waited = true;
                        self.stats.stalled.fetch_add(1, Ordering::Relaxed);
                    }
                    thawed.await;
                }
            }
        };

        if self.storming(from, to, packet.epoch, packet.payload.message()) {
            self.stats.lost.fetch_add(1, Ordering::Relaxed);
            return;
        }
        self.stats.delivered.fetch_add(1, Ordering::Relaxed);
        self.forensics
            .note(from, to, stream, Fate::Delivered, packet.payload.message());
        if let Message::InstallSnapshot(ref chunk) = *packet.payload.message() {
            self.watch_transfer(from, to, chunk);
        }

        // The audit brackets the handler call, which is synchronous: nothing
        // between the two readings but the handler itself.
        let kind = message_kind(packet.payload.message());
        let audit = self.audit.get();
        let before = audit.and_then(|audit| audit.before(to));
        match way {
            Way::Out => self.dispatch_out(key, stream, packet, handler, from),
            Way::Back => self.dispatch_back(key, packet, &handler, from),
        }
        if let (Some(audit), Some(before)) = (audit, before) {
            audit.after(to, from, kind, &before, self.forensics.now_us() / 1000);
        }
    }

    /// A packet has reached the head of its queue and its release time.
    ///
    /// Counted here rather than at delivery so a packet held by a stall still
    /// counts as in flight, which is what it is.
    fn departed(&self, key: LinkKey, stream: Stream, way: Way) {
        if let Some(link) = self.inner.lock().links.get_mut(&key)
            && let Some(count) = link.in_flight.get_mut(&(stream, way))
        {
            *count = count.saturating_sub(1);
        }
    }

    /// Every queue with packets still in it: `(connection, stream, direction,
    /// packets, how far past now its last release lies)`.
    ///
    /// What a liveness failure needs beside the members' states: a cluster
    /// whose messages are still queued is a cluster that has not been given
    /// the chance to converge, whatever the members look like.
    #[must_use]
    pub fn backlog(&self) -> Vec<String> {
        let now = Instant::now();
        let inner = self.inner.lock();
        let mut rows = Vec::new();
        for (&key, link) in &inner.links {
            for (&(stream, way), &count) in &link.in_flight {
                if count == 0 {
                    continue;
                }
                let ahead = link
                    .last_release
                    .get(&(stream, way))
                    .map_or(Duration::ZERO, |release| {
                        release.saturating_duration_since(now)
                    });
                rows.push(format!(
                    "{}->{} {stream:?} {way:?}: {count} queued, last release {ahead:?} from now \
                     (connected={} epoch={})",
                    key.0, key.1, link.connected, link.epoch
                ));
            }
        }
        rows
    }

    /// Watch snapshot transfers for chunks of two snapshots spliced into one.
    ///
    /// The follower reassembles by offset alone (`on_install_snapshot` checks
    /// `offset == buffer.len()`), so if the leader's snapshot changes between
    /// two chunks -- a compaction mid-transfer replaces it -- the next chunk
    /// continues the old buffer with bytes of the new snapshot. What arrives
    /// is then neither: a payload that fails to decode if the soak is lucky,
    /// and one that decodes into a state no member ever had if it is not.
    /// Every chunk carries its snapshot's `(last_index, last_term)`, so the
    /// splice is visible here, at the moment it is delivered.
    fn watch_transfer(
        &self,
        from: u64,
        to: u64,
        chunk: &nmos_registry_raft::messages::InstallSnapshot,
    ) {
        let identity = (chunk.term, chunk.last_index, chunk.last_term);
        let next = chunk.offset.saturating_add(chunk.data.len() as u64);
        let mut inner = self.inner.lock();
        if chunk.offset > 0
            && let Some(&(previous, expected)) = inner.transfers.get(&(from, to))
            && expected == chunk.offset
            && previous != identity
        {
            let line = format!(
                "m{from}->m{to}: chunk at offset {} belongs to snapshot through=({},t{}) in term {} \
                 but continues a transfer of snapshot through=({},t{}) in term {} -- the follower \
                 will splice them",
                chunk.offset,
                identity.1,
                identity.2,
                identity.0,
                previous.1,
                previous.2,
                previous.0,
            );
            inner.splices.push(line.clone());
            drop(inner);
            self.forensics.step(format!("SPLICE {line}"));
            return;
        }
        if chunk.done {
            inner.transfers.remove(&(from, to));
        } else {
            inner.transfers.insert((from, to), (identity, next));
        }
    }

    /// Count a delivery against the current instant, and say whether the
    /// network has seen a storm and stopped delivering.
    ///
    /// On the paused clock, time only moves when every task is idle, so an
    /// exchange that keeps re-triggering itself with no delay between its
    /// steps freezes time: the step's sleep never returns, no check runs, and
    /// the run spins at 100% CPU forever -- which is how the first one
    /// presented. In production the same exchange would not freeze time; it
    /// would burn CPU and bandwidth for as long as it lasted. Either way it is
    /// a finding, and it needs its message mix recorded to be diagnosable, so
    /// the storm is reported and the network is frozen to end it. On the real
    /// clock the "instant" is a millisecond, so the same bound catches a
    /// storm there too.
    ///
    /// Only exchanges that make no progress are counted. A snapshot transfer
    /// at zero delay also runs inside one instant -- every answer sends the
    /// next chunk -- but it ends, because a snapshot has an end. Counted, it was
    /// reported as a storm: all 11 storms in 16 runs of seed 140692 (after the
    /// copy fix) were transfers whose every chunk and answer advanced -- 8 a
    /// single ~666 KB snapshot sent in 16-byte chunks to three members at once,
    /// 3 a completed transfer followed by one of the leader's newer snapshot
    /// (see [`advances`]). What still counts is what goes round in circles: a
    /// chunk sent again, a transfer restarted on the same connection, and every
    /// other kind of message.
    fn storming(&self, from: u64, to: u64, epoch: u64, message: &Message) -> bool {
        const STORM: u64 = 250_000;
        let now = Instant::now();
        let mut inner = self.inner.lock();
        if !inner.storms.is_empty() {
            return true;
        }
        if advances(&mut inner.carried, from, to, epoch, message) {
            return false;
        }
        let same_instant = inner
            .storm_at
            .is_some_and(|at| now.saturating_duration_since(at) < Duration::from_millis(1));
        if !same_instant {
            inner.storm_at = Some(now);
            inner.storm_count = 0;
            inner.storm_mix.clear();
        }
        inner.storm_count += 1;
        let kind = message_kind(message);
        *inner.storm_mix.entry((from, to, kind)).or_insert(0) += 1;
        if inner.storm_count < STORM {
            return false;
        }
        let mut mix: Vec<(u64, (u64, u64, &str))> = inner
            .storm_mix
            .iter()
            .map(|(&key, &count)| (count, key))
            .collect();
        mix.sort_unstable_by_key(|entry| std::cmp::Reverse(entry.0));
        let rendered: Vec<String> = mix
            .iter()
            .take(8)
            .map(|&(count, (a, b, kind))| format!("m{a}->m{b} {kind} x{count}"))
            .collect();
        let line = format!(
            "{STORM} messages delivered within one millisecond of cluster time -- the members are \
             re-triggering each other with no delay between steps. Mix: {}",
            rendered.join(", ")
        );
        inner.storms.push(line.clone());
        drop(inner);
        self.forensics.step(format!("STORM {line}"));
        true
    }

    /// Count a forward of `resource` from `from` to `to`, and say whether a
    /// forwarding loop has been seen and forwards now fail.
    ///
    /// A registration is forwarded once to its owner, and at most once more
    /// after a `not_owner` answer -- that is what the backend documents. Ten
    /// thousand forwards of one resource between the same pair is a loop,
    /// whatever its cause, and on a real stack it ends in an overflow. Failing
    /// the forward unwinds it, so the run can finish and report it.
    fn forwarding_loop(&self, from: u64, to: u64, resource: &str) -> bool {
        const LOOP: u64 = 10_000;
        let mut inner = self.inner.lock();
        if !inner.forward_loops.is_empty() {
            return true;
        }
        let count = inner
            .forwards
            .entry((from, to, resource.to_owned()))
            .or_insert(0);
        *count += 1;
        if *count < LOOP {
            return false;
        }
        let back = inner
            .forwards
            .get(&(to, from, resource.to_owned()))
            .copied()
            .unwrap_or(0);
        let line = format!(
            "member {from} forwarded resource {resource} to member {to} {LOOP} times \
             (and {to} to {from} {back} times) -- the backend's not_owner retry is not bounded"
        );
        inner.forward_loops.push(line.clone());
        drop(inner);
        self.forensics.step(format!("FORWARD LOOP {line}"));
        true
    }

    /// Forwarding loops seen so far.
    #[must_use]
    pub fn forward_loops(&self) -> Vec<String> {
        self.inner.lock().forward_loops.clone()
    }

    /// Storms seen so far.
    #[must_use]
    pub fn storms(&self) -> Vec<String> {
        self.inner.lock().storms.clone()
    }

    /// Whether `epoch` is still the connection `key` has.
    fn current(&self, key: LinkKey, epoch: u64) -> bool {
        self.inner
            .lock()
            .links
            .get(&key)
            .is_some_and(|link| link.connected && link.epoch == epoch)
    }

    /// Spliced transfers seen so far.
    #[must_use]
    pub fn splices(&self) -> Vec<String> {
        self.inner.lock().splices.clone()
    }

    /// At the acceptor: answer on the connection the message came in on.
    fn dispatch_out(
        &self,
        key: LinkKey,
        stream: Stream,
        packet: Packet,
        handler: Arc<dyn PeerHandler>,
        from: u64,
    ) {
        let epoch = packet.epoch;
        match packet.payload {
            Payload::Message(message) => {
                // A handler that could not save what a message required returns
                // `Err` and is answered with nothing. The transport would end
                // the connection too (`serve`, `pump`); a soak's saves do not
                // fail, so this network models only the silence.
                let reply = match message {
                    Message::RequestVote(ref m) => handler
                        .on_request_vote(from, m)
                        .ok()
                        .map(Message::RequestVoteReply),
                    Message::AppendEntries(ref m) => handler
                        .on_append_entries(from, m)
                        .ok()
                        .map(Message::AppendEntriesReply),
                    Message::InstallSnapshot(ref m) => handler
                        .on_install_snapshot(from, m)
                        .ok()
                        .map(Message::InstallSnapshotReply),
                    Message::Promote(ref m) => {
                        handler.on_promote(from, m);
                        None
                    }
                    Message::RequestVoteReply(ref m) => {
                        drop(handler.on_request_vote_reply(from, m));
                        None
                    }
                    Message::AppendEntriesReply(ref m) => {
                        drop(handler.on_append_entries_reply(from, m));
                        None
                    }
                    Message::InstallSnapshotReply(ref m) => {
                        drop(handler.on_install_snapshot_reply(from, m));
                        None
                    }
                    Message::Propose(_) | Message::Forward(_) | Message::ReadIndex(_) => {
                        // Application messages are served concurrently, as the
                        // transport serves them under its application
                        // semaphore: a forward waits for a commit, a read index
                        // for a quorum round, and holding the connection's
                        // queue for that long would stall every heartbeat
                        // behind it.
                        let this = self.this.clone();
                        tokio::spawn(async move {
                            let reply = answer(&handler, from, message).await;
                            if let (Some(net), Some(reply)) = (this.upgrade(), reply) {
                                net.reply(key, stream, epoch, Payload::Message(reply));
                            }
                        });
                        None
                    }
                    _ => None,
                };
                if let Some(reply) = reply {
                    self.reply(key, stream, epoch, Payload::Message(reply));
                }
            }
            Payload::Request { id, message } => {
                let this = self.this.clone();
                tokio::spawn(async move {
                    let reply = answer(&handler, from, message).await;
                    if let (Some(net), Some(message)) = (this.upgrade(), reply) {
                        net.reply(key, stream, epoch, Payload::Reply { id, message });
                    }
                });
            }
            Payload::Reply { .. } => {}
        }
    }

    /// At the dialer: a reply to something it sent on this connection.
    fn dispatch_back(
        &self,
        key: LinkKey,
        packet: Packet,
        handler: &Arc<dyn PeerHandler>,
        from: u64,
    ) {
        match packet.payload {
            // `Err` -- a save that failed -- ends nothing here: see
            // `dispatch_out`.
            Payload::Message(Message::RequestVoteReply(ref m)) => {
                drop(handler.on_request_vote_reply(from, m));
            }
            Payload::Message(Message::AppendEntriesReply(ref m)) => {
                drop(handler.on_append_entries_reply(from, m));
            }
            Payload::Message(Message::InstallSnapshotReply(ref m)) => {
                drop(handler.on_install_snapshot_reply(from, m));
            }
            Payload::Reply { id, message } => {
                let waiter = self
                    .inner
                    .lock()
                    .links
                    .get_mut(&key)
                    .and_then(|link| link.pending.remove(&id));
                if let Some(waiter) = waiter {
                    drop(waiter.send(Ok(message)));
                }
            }
            // A `ProposeReply`, `ForwardReply` or `ReadIndexReply` to an
            // uncorrelated send: nothing in the node reads one, and the
            // transport drops it too.
            _ => {}
        }
    }

    /// Queue a reply back along `key`, if the connection it answers survives.
    fn reply(&self, key: LinkKey, stream: Stream, epoch: u64, payload: Payload) {
        let lost = {
            let mut inner = self.inner.lock();
            let current = inner
                .links
                .get(&key)
                .is_some_and(|link| link.connected && link.epoch == epoch);
            if current {
                self.enqueue(&mut inner, key, stream, Way::Back, payload);
                None
            } else {
                Some(payload)
            }
        };
        if let Some(payload) = lost {
            self.stats.lost.fetch_add(1, Ordering::Relaxed);
            self.forensics
                .note(key.1, key.0, stream, Fate::Lost, payload.message());
        }
    }
}

enum Verdict {
    Deliver(Arc<dyn PeerHandler>),
    Hold,
    Lose,
}

/// A message's type name, for a storm's mix.
const fn message_kind(message: &Message) -> &'static str {
    match *message {
        Message::Hello(_) => "Hello",
        Message::HelloAck(_) => "HelloAck",
        Message::RequestVote(_) => "RequestVote",
        Message::RequestVoteReply(_) => "RequestVoteReply",
        Message::AppendEntries(_) => "AppendEntries",
        Message::AppendEntriesReply(_) => "AppendEntriesReply",
        Message::InstallSnapshot(_) => "InstallSnapshot",
        Message::InstallSnapshotReply(_) => "InstallSnapshotReply",
        Message::Promote(_) => "Promote",
        Message::ReadIndex(_) => "ReadIndex",
        Message::ReadIndexReply(_) => "ReadIndexReply",
        Message::Propose(_) => "Propose",
        Message::ProposeReply(_) => "ProposeReply",
        Message::Forward(_) => "Forward",
        Message::ForwardReply(_) => "ForwardReply",
        Message::Ping(_) => "Ping",
        Message::Pong(_) => "Pong",
    }
}

/// Whether the accumulating (Python) delay model was asked for.
///
/// An environment switch rather than a knob drawn from the seed, so that
/// choosing it does not change what any seed's plan is.
fn inner_queueing() -> bool {
    static QUEUEING: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *QUEUEING.get_or_init(|| std::env::var_os("RAFT_RUST_SOAK_QUEUEING").is_some())
}

/// Every ordered pair of distinct members.
fn pairs(inner: &Inner) -> Vec<LinkKey> {
    let members: Vec<u64> = inner.slots.keys().copied().collect();
    let mut keys = Vec::with_capacity(members.len() * members.len());
    for &a in &members {
        for &b in &members {
            if a != b {
                keys.push((a, b));
            }
        }
    }
    keys
}

/// Serve one application message or correlated request, as the acceptor.
async fn answer(handler: &Arc<dyn PeerHandler>, from: u64, message: Message) -> Option<Message> {
    match message {
        Message::Forward(ref m) => Some(Message::ForwardReply(handler.on_forward(from, m).await)),
        Message::Propose(ref m) => Some(Message::ProposeReply(handler.on_propose(from, m).await)),
        Message::ReadIndex(ref m) => Some(Message::ReadIndexReply(
            handler.on_read_index(from, m).await,
        )),
        // Answered with nothing when the save failed: see `dispatch_out`.
        Message::RequestVote(ref m) => handler
            .on_request_vote(from, m)
            .ok()
            .map(Message::RequestVoteReply),
        Message::AppendEntries(ref m) => handler
            .on_append_entries(from, m)
            .ok()
            .map(Message::AppendEntriesReply),
        Message::InstallSnapshot(ref m) => handler
            .on_install_snapshot(from, m)
            .ok()
            .map(Message::InstallSnapshotReply),
        _ => None,
    }
}

/// One queue's delivery task: FIFO, each packet at its release time.
///
/// The wait for a release time is abandoned the moment the packet's
/// connection drops: the packet is lost either way, and waiting it out would
/// hold back the next connection's packets behind it.
async fn deliver_loop(
    net: Weak<ChaosNet>,
    key: LinkKey,
    stream: Stream,
    way: Way,
    mut packets: mpsc::UnboundedReceiver<Packet>,
    dropped: Arc<Notify>,
) {
    while let Some(packet) = packets.recv().await {
        loop {
            let notified = dropped.notified();
            tokio::pin!(notified);
            notified.as_mut().enable();
            let current = net
                .upgrade()
                .is_some_and(|net| net.current(key, packet.epoch));
            if !current || Instant::now() >= packet.release {
                break;
            }
            tokio::select! {
                () = tokio::time::sleep_until(packet.release) => break,
                () = &mut notified => {}
            }
        }
        let Some(net) = net.upgrade() else {
            return;
        };
        net.arrive(key, stream, way, packet).await;
    }
}

/// One member's view of the network.
pub struct ChaosTransport {
    local: u64,
    net: Arc<ChaosNet>,
}

#[async_trait]
impl Transport for ChaosTransport {
    async fn start(&self, handler: Arc<dyn PeerHandler>) -> std::io::Result<()> {
        self.net.attach(self.local, &handler);
        Ok(())
    }

    async fn close(&self) {
        self.net.detach(self.local);
    }

    fn send(&self, peer: u64, message: &Message, stream: Stream) {
        self.net.send(self.local, peer, message, stream);
    }

    async fn request(
        &self,
        peer: u64,
        message: &Message,
        stream: Stream,
        timeout_ms: Option<u64>,
    ) -> Result<Message, RaftUnavailable> {
        self.net
            .request(self.local, peer, message, stream, timeout_ms)
            .await
    }

    fn live(&self) -> Vec<u64> {
        self.net.live_of(self.local)
    }
}
