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
//! second is the important one: `on_propose` and `on_forward` are async, so a
//! direct call could not make them; and a handler that ran inside `send` would
//! re-enter the sender's own lock, which is a deadlock the real transport
//! cannot have because a socket is always in between.

#![allow(dead_code)]
// The stream a message arrived on is only read by the recursive call that sends
// the reply back -- which is exactly its job: an answer travels on the link its
// question came in on, so a snapshot's acknowledgement stays off the control
// link.
#![allow(clippy::only_used_in_recursion)]

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use nmos_registry_raft::messages::Message;
use nmos_registry_raft::transport::{PeerHandler, RaftUnavailable, Transport};
use nmos_registry_raft::wire::Stream;
use parking_lot::Mutex;

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
    /// Messages that were dropped, for a test to assert about.
    dropped: Mutex<Vec<(u64, u64)>>,
    /// Messages delivered, by type name. The cost of one registration in
    /// messages is what distinguishes an algorithmic problem from a machine
    /// that was simply busy.
    delivered: Mutex<HashMap<&'static str, u64>>,
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
        let name = match *message {
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
            _ => "other",
        };
        *self.delivered.lock().entry(name).or_insert(0) += 1;
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
            self.dropped.lock().push((from, to));
            return;
        }
        let Some(handler) = self.handler(to) else {
            return;
        };
        self.count(&message);
        let fabric = Arc::clone(self);
        tokio::spawn(async move {
            let reply = match message {
                Message::RequestVote(ref m) => {
                    Some(Message::RequestVoteReply(handler.on_request_vote(from, m)))
                }
                Message::AppendEntries(ref m) => Some(Message::AppendEntriesReply(
                    handler.on_append_entries(from, m),
                )),
                Message::InstallSnapshot(ref m) => Some(Message::InstallSnapshotReply(
                    handler.on_install_snapshot(from, m),
                )),
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
                Message::RequestVoteReply(ref m) => {
                    handler.on_request_vote_reply(from, m);
                    None
                }
                Message::AppendEntriesReply(ref m) => {
                    handler.on_append_entries_reply(from, m);
                    None
                }
                Message::InstallSnapshotReply(ref m) => {
                    handler.on_install_snapshot_reply(from, m);
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
        _message: &Message,
        _stream: Stream,
        _timeout_ms: Option<u64>,
    ) -> Result<Message, RaftUnavailable> {
        // The node never uses correlated requests -- it sends and handles the
        // reply through `on_*_reply` -- so this is deliberately unimplemented
        // rather than approximated. An approximation here would be a second
        // delivery path that the real transport does not have.
        Err(RaftUnavailable(format!(
            "the fabric does not correlate requests (to member {peer})"
        )))
    }

    fn live(&self) -> Vec<u64> {
        self.fabric
            .members()
            .into_iter()
            .filter(|&other| other != self.local && self.fabric.reaches(self.local, other))
            .collect()
    }
}
