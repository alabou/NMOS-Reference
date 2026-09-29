// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! An in-memory transport, so consensus can be driven through the scenarios
//! that break it.
//!
//! This is the reason [`nmos_registry_raft::transport::Transport`] is a trait.
//! Election safety fails in interleavings that are hard to provoke over real
//! TCP and trivial to arrange here: partition two members, let a third time
//! out, heal, and assert what the cluster did. A consensus layer that could
//! only be tested over sockets would be tested only in the cases that are easy
//! to reach, which are not the cases that matter.
//!
//! Delivery is a spawned task rather than a direct call. Two reasons, and the
//! second is the important one: `on_propose`, `on_forward` and `on_read_index`
//! are async, so a direct call could not make them; and a handler that ran
//! inside `send` would re-enter the sender's own lock, which is a deadlock the
//! real transport cannot have because a socket is always in between.

#![allow(dead_code)]
// The stream a message arrived on is only read by the recursive call that sends
// the reply back -- which is exactly its job: an answer travels on the link its
// question came in on, so a snapshot's acknowledgement stays off the control
// link.
#![allow(clippy::only_used_in_recursion)]

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use async_trait::async_trait;
use nmos_registry_raft::messages::{Message, ReadIndex};
use nmos_registry_raft::transport::{PeerHandler, RaftUnavailable, Transport};
use nmos_registry_raft::wire::Stream;
use parking_lot::Mutex;
use tokio::sync::oneshot;

/// How long a correlated request waits when its caller names no deadline.
const REQUEST_TIMEOUT_MS: u64 = 1_000;

/// Decides, for a message about to be delivered, whether it is.
type Interceptor = Arc<dyn Fn(u64, u64, &Message) -> bool + Send + Sync>;

/// Who can reach whom, and who is listening.
#[derive(Default)]
pub struct Fabric {
    /// The members, held **weakly**, as `RaftTransport` holds its handler.
    ///
    /// A node owns its transport and the transport is handed the node back as
    /// its handler, so holding it strongly here would be the same cycle the
    /// real transport avoids -- and a harness that keeps nodes alive cannot be
    /// used to assert that they are dropped.
    handlers: Mutex<HashMap<u64, std::sync::Weak<dyn PeerHandler>>>,
    /// Ordered pairs that cannot deliver, as `(from, to)`.
    ///
    /// Directed on purpose: a one-way partition is a real failure mode and the
    /// one that produces the most interesting elections -- a member that can
    /// hear a leader but not answer it.
    cut: Mutex<HashSet<(u64, u64)>>,
    /// Messages that were dropped, as `(from, to, kind)`, for a test to assert
    /// about.
    dropped: Mutex<Vec<(u64, u64, &'static str)>>,
    /// Messages whose recipient could not save what they required, as `(from,
    /// to, kind)`: answered with nothing (`Fabric::deliver`).
    unanswered: Mutex<Vec<(u64, u64, &'static str)>>,
    /// Deliveries on their way, per `(from, to)`: counted from the moment one
    /// is spawned until its handler has returned (`Fabric::drained`).
    in_flight: Mutex<HashMap<(u64, u64), u64>>,
    /// Messages delivered, by type name. The cost of one registration in
    /// messages is what distinguishes an algorithmic problem from a machine
    /// that was simply busy.
    delivered: Mutex<HashMap<&'static str, u64>>,
    /// Correlated requests in flight (`request`), by an id unique across the
    /// fabric.
    awaiting: Mutex<HashMap<u64, oneshot::Sender<Message>>>,
    next_request: AtomicU64,
    /// Consulted before every delivery -- see [`Fabric::intercept`].
    interceptor: Mutex<Option<Interceptor>>,
}

impl Fabric {
    #[must_use]
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    /// A transport for one member.
    #[must_use]
    pub fn transport(self: &Arc<Self>, local: u64) -> Arc<FabricTransport> {
        Arc::new(FabricTransport {
            local,
            fabric: Arc::clone(self),
        })
    }

    /// Stop delivering from `from` to `to`, in that direction only.
    pub fn cut(&self, from: u64, to: u64) {
        self.cut.lock().insert((from, to));
    }

    /// Stop delivering in both directions between `a` and `b`.
    pub fn isolate_pair(&self, a: u64, b: u64) {
        self.cut(a, b);
        self.cut(b, a);
    }

    /// Cut a member off from every other.
    pub fn isolate(&self, member: u64, others: &[u64]) {
        for &other in others {
            if other != member {
                self.isolate_pair(member, other);
            }
        }
    }

    /// Consult `decide` before every delivery from now on, with `(from, to,
    /// message)`: a message it refuses is dropped, as on a cut link.
    ///
    /// For a scenario that has to act at the instant a particular message
    /// arrives -- a cut made then is in place before that message's handler
    /// runs, so whatever the handler sends in answer meets it -- or that has
    /// to hold back one kind of message while the rest flow.
    pub fn intercept(&self, decide: impl Fn(u64, u64, &Message) -> bool + Send + Sync + 'static) {
        *self.interceptor.lock() = Some(Arc::new(decide));
    }

    /// Restore every link.
    pub fn heal(&self) {
        self.cut.lock().clear();
    }

    /// Whether a message from `from` to `to` would be delivered.
    #[must_use]
    pub fn reaches(&self, from: u64, to: u64) -> bool {
        !self.cut.lock().contains(&(from, to))
    }

    /// How many messages have been dropped by a cut.
    #[must_use]
    pub fn dropped(&self) -> usize {
        self.dropped.lock().len()
    }

    /// How many messages of `kind` (as [`Fabric::delivered`] names them) sent by
    /// `from` have been dropped.
    #[must_use]
    pub fn dropped_from(&self, from: u64, kind: &str) -> usize {
        self.dropped
            .lock()
            .iter()
            .filter(|&&(sender, _, dropped)| sender == from && dropped == kind)
            .count()
    }

    /// How many messages of `kind` that `member` received it could not save
    /// what they required for, and so answered with nothing.
    #[must_use]
    pub fn unanswered_by(&self, member: u64, kind: &str) -> usize {
        self.unanswered
            .lock()
            .iter()
            .filter(|&&(_, recipient, failed)| recipient == member && failed == kind)
            .count()
    }

    /// Wait until nothing sent from `from` to `to` is still being delivered.
    ///
    /// A cut stops what is sent after it, and each delivery checks the cut again
    /// on arrival -- but one that passed that check a moment before still runs
    /// its handler, on whichever thread it landed. A test that reads a member's
    /// state right after cutting it off races that handler: under load, one run
    /// in forty, measured as a peer's position read as 4 and then found at 5,
    /// and as an isolated member made a candidate by a pre-vote grant already
    /// on its way. This is the wait that ends the race, and it ends: a cut link
    /// spawns nothing new.
    pub async fn drained(&self, from: u64, to: u64) {
        while self
            .in_flight
            .lock()
            .get(&(from, to))
            .is_some_and(|&count| count > 0)
        {
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    }

    /// [`Fabric::drained`] both ways between `member` and each of `others`.
    pub async fn settled(&self, member: u64, others: &[u64]) {
        for &other in others {
            self.drained(other, member).await;
            self.drained(member, other).await;
        }
    }

    /// Every message delivered so far, by type.
    #[must_use]
    pub fn delivered(&self) -> Vec<(&'static str, u64)> {
        let mut counts: Vec<(&'static str, u64)> = self
            .delivered
            .lock()
            .iter()
            .map(|(&k, &v)| (k, v))
            .collect();
        counts.sort_unstable();
        counts
    }

    /// Forget what has been counted, so a measurement starts from zero.
    pub fn reset_counts(&self) {
        self.delivered.lock().clear();
    }

    fn count(&self, message: &Message) {
        *self.delivered.lock().entry(kind_of(message)).or_insert(0) += 1;
    }

    fn handler(&self, member: u64) -> Option<Arc<dyn PeerHandler>> {
        self.handlers
            .lock()
            .get(&member)
            .and_then(std::sync::Weak::upgrade)
    }

    fn members(&self) -> Vec<u64> {
        let mut members: Vec<u64> = self.handlers.lock().keys().copied().collect();
        members.sort_unstable();
        members
    }

    /// Deliver one message, and route whatever it answers back.
    /// `stream` is carried through so a reply travels back on the link its
    /// request arrived on, which is what keeps a snapshot's acknowledgement off
    /// the control link. Only recursion reads it, which is the point.
    fn deliver(self: &Arc<Self>, from: u64, to: u64, message: Message, stream: Stream) {
        if !self.reaches(from, to) {
            self.dropped.lock().push((from, to, kind_of(&message)));
            return;
        }
        *self.in_flight.lock().entry((from, to)).or_insert(0) += 1;
        let fabric = Arc::clone(self);
        tokio::spawn(async move {
            let _landing = Landing {
                fabric: Arc::clone(&fabric),
                pair: (from, to),
            };
            // Decided again on arrival, not only when sent: a cut made while a
            // message is in flight loses it, as a torn-down connection loses
            // what it was carrying, and as the Python harness decides at both
            // points (`_harness.py`, `_dispatch`). Checked only when sent, a
            // member a test had just isolated still received what had been sent
            // to it a moment before -- measured as a heartbeat that raised a
            // follower's commit index under the scenario built on it, and as
            // pre-vote grants that made an isolated member a candidate: 3
            // failing runs in 40 of this suite under load, 1 in 40 without the
            // read tests' share of it.
            if !fabric.reaches(from, to) {
                fabric.dropped.lock().push((from, to, kind_of(&message)));
                return;
            }
            let Some(handler) = fabric.handler(to) else {
                return;
            };
            let interceptor = fabric.interceptor.lock().clone();
            if let Some(decide) = interceptor
                && !decide(from, to, &message)
            {
                fabric.dropped.lock().push((from, to, kind_of(&message)));
                return;
            }
            fabric.count(&message);
            // A handler that could not save what the message required answers
            // nothing. The real transport also ends the connection the message
            // came by (`serve`, `pump`); the fabric has no connections, so what
            // it models is the silence.
            let kind = kind_of(&message);
            let unanswered = || fabric.unanswered.lock().push((from, to, kind));
            let reply = match message {
                Message::RequestVote(ref m) => match handler.on_request_vote(from, m) {
                    Ok(reply) => Some(Message::RequestVoteReply(reply)),
                    Err(_) => {
                        unanswered();
                        None
                    }
                },
                Message::AppendEntries(ref m) => match handler.on_append_entries(from, m) {
                    Ok(reply) => Some(Message::AppendEntriesReply(reply)),
                    Err(_) => {
                        unanswered();
                        None
                    }
                },
                Message::InstallSnapshot(ref m) => match handler.on_install_snapshot(from, m) {
                    Ok(reply) => Some(Message::InstallSnapshotReply(reply)),
                    Err(_) => {
                        unanswered();
                        None
                    }
                },
                Message::Promote(ref m) => {
                    handler.on_promote(from, m);
                    None
                }
                Message::Propose(ref m) => {
                    Some(Message::ProposeReply(handler.on_propose(from, m).await))
                }
                Message::Forward(ref m) => {
                    Some(Message::ForwardReply(handler.on_forward(from, m).await))
                }
                Message::ReadIndex(ref m) => Some(Message::ReadIndexReply(
                    handler.on_read_index(from, m).await,
                )),
                Message::ReadIndexReply(ref m) => {
                    // The answer to a correlated request: to its asker, not to
                    // the handler.
                    let waiting = fabric.awaiting.lock().remove(&m.request_id);
                    if let Some(waiting) = waiting {
                        drop(waiting.send(Message::ReadIndexReply(m.clone())));
                    }
                    None
                }
                Message::RequestVoteReply(ref m) => {
                    if handler.on_request_vote_reply(from, m).is_err() {
                        unanswered();
                    }
                    None
                }
                Message::AppendEntriesReply(ref m) => {
                    if handler.on_append_entries_reply(from, m).is_err() {
                        unanswered();
                    }
                    None
                }
                Message::InstallSnapshotReply(ref m) => {
                    if handler.on_install_snapshot_reply(from, m).is_err() {
                        unanswered();
                    }
                    None
                }
                _ => None,
            };
            if let Some(reply) = reply {
                // The answer travels back the way it came, and is cut the same
                // way: a one-way partition that delivered replies would be a
                // failure mode no real network has.
                fabric.deliver(to, from, reply, stream);
            }
        });
    }
}

/// A message's type, as [`Fabric::delivered`] and [`Fabric::dropped_from`] name
/// it.
fn kind_of(message: &Message) -> &'static str {
    match *message {
        Message::RequestVote(_) => "RequestVote",
        Message::RequestVoteReply(_) => "RequestVoteReply",
        Message::AppendEntries(ref m) => {
            if m.entries.is_empty() {
                "AppendEntries(heartbeat)"
            } else {
                "AppendEntries(entries)"
            }
        }
        Message::AppendEntriesReply(_) => "AppendEntriesReply",
        Message::Promote(_) => "Promote",
        Message::Propose(_) => "Propose",
        Message::ProposeReply(_) => "ProposeReply",
        Message::Forward(_) => "Forward",
        Message::ForwardReply(_) => "ForwardReply",
        Message::ReadIndex(_) => "ReadIndex",
        Message::ReadIndexReply(_) => "ReadIndexReply",
        _ => "other",
    }
}

/// Counts a delivery down when it is over, however it ends: dropped on
/// arrival, handled, or cut short by the runtime.
struct Landing {
    fabric: Arc<Fabric>,
    pair: (u64, u64),
}

impl Drop for Landing {
    fn drop(&mut self) {
        if let Some(count) = self.fabric.in_flight.lock().get_mut(&self.pair) {
            *count = count.saturating_sub(1);
        }
    }
}

/// One member's view of the fabric.
pub struct FabricTransport {
    local: u64,
    fabric: Arc<Fabric>,
}

#[async_trait]
impl Transport for FabricTransport {
    async fn start(&self, handler: Arc<dyn PeerHandler>) -> std::io::Result<()> {
        self.fabric
            .handlers
            .lock()
            .insert(self.local, Arc::downgrade(&handler));
        // Every other member that has already started is announced as up, and
        // this member is announced to them: the fabric has no sockets, so
        // "connected" is simply "both ends are listening".
        let members = self.fabric.members();
        for other in members {
            if other == self.local {
                continue;
            }
            if let Some(theirs) = self.fabric.handler(other) {
                theirs.on_peer_state(self.local, true, 1);
            }
            if let Some(ours) = self.fabric.handler(self.local) {
                ours.on_peer_state(other, true, 1);
            }
        }
        Ok(())
    }

    async fn close(&self) {
        self.fabric.handlers.lock().remove(&self.local);
        let members = self.fabric.members();
        for other in members {
            if let Some(theirs) = self.fabric.handler(other) {
                theirs.on_peer_state(self.local, false, 0);
            }
        }
    }

    fn send(&self, peer: u64, message: &Message, stream: Stream) {
        self.fabric
            .deliver(self.local, peer, message.clone(), stream);
    }

    async fn request(
        &self,
        peer: u64,
        message: &Message,
        stream: Stream,
        timeout_ms: Option<u64>,
    ) -> Result<Message, RaftUnavailable> {
        // A read index is correlated: the node asks its leader for one and
        // needs the answer. Nothing else is -- the node sends and handles
        // every other reply through `on_*_reply` -- so a forwarded mutation
        // over this fabric still finds no owner to answer it, rather than an
        // approximation of one: a second delivery path the real transport
        // does not have.
        if !matches!(*message, Message::ReadIndex(_)) {
            return Err(RaftUnavailable(format!(
                "the fabric correlates only read indexes (to member {peer})"
            )));
        }
        // Refused at once over a cut link, as the transport refuses a request
        // it has no connection for. Both legs travel by `deliver`, so a cut
        // made while one is in flight loses it like any other message, and the
        // asker learns it at its deadline.
        if !self.fabric.reaches(self.local, peer) {
            return Err(RaftUnavailable(format!("no link to member {peer}")));
        }
        let id = self
            .fabric
            .next_request
            .fetch_add(1, Ordering::Relaxed)
            .saturating_add(1);
        let (waiter, answer) = oneshot::channel();
        self.fabric.awaiting.lock().insert(id, waiter);
        self.fabric.deliver(
            self.local,
            peer,
            Message::ReadIndex(ReadIndex { request_id: id }),
            stream,
        );
        let limit = Duration::from_millis(timeout_ms.unwrap_or(REQUEST_TIMEOUT_MS));
        let answered = tokio::time::timeout(limit, answer).await;
        self.fabric.awaiting.lock().remove(&id);
        match answered {
            Ok(Ok(reply)) => Ok(reply),
            Ok(Err(_)) => Err(RaftUnavailable("the request was abandoned".to_owned())),
            Err(_) => Err(RaftUnavailable(format!(
                "member {peer} did not answer within the deadline"
            ))),
        }
    }

    fn live(&self) -> Vec<u64> {
        self.fabric
            .members()
            .into_iter()
            .filter(|&other| other != self.local && self.fabric.reaches(self.local, other))
            .collect()
    }
}
