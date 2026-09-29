// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! What can go wrong, and which failures are the protocol's.
//!
//! Port of `nmos/raft/errors.py`.

/// A peer sent something this member cannot act on.
///
/// **Every one of these drops the link.** A frame that does not decode is not a
/// request that failed -- it is evidence that the stream is no longer
/// understood, and continuing to read from it would be guessing at where the
/// next frame starts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RaftProtocolError(pub String);

impl std::fmt::Display for RaftProtocolError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RaftProtocolError {}

/// An invariant the algorithm guarantees did not hold.
///
/// Distinct from every other error here, and it is not recoverable: if a
/// Figure 3 property is violated, this member's state machine may already have
/// applied something the cluster did not agree on, and carrying on would spread
/// it. The Python takes it past its own catch-all for the same reason.
///
/// One comes from a peer and is no exception to that: a leader contradicting an
/// entry this member committed (`NodeState::contradicting_committed`). Raft
/// makes it impossible -- every leader holds every committed entry -- so it too
/// means a defect, one that lost committed data from the cluster.
///
/// **What happens is a stop** (`RaftNode::fail`): the member stops leading,
/// campaigning and applying at once, answers every waiting caller
/// "unavailable", closes its transport, and signals its owner
/// (`RaftNode::wait_for_failure`), which ends the process with status 1
/// (`main.rs`). A restart -- automatic under a service manager that restarts
/// on failure -- brings the member back with nothing, to be caught up by the
/// leader as a non-voting learner. Logging it and ending the apply loop, as
/// this once did, stopped nothing else: the member served on from a store that
/// no longer moved.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RaftInvariantViolated(pub String);

impl std::fmt::Display for RaftInvariantViolated {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RaftInvariantViolated {}

/// An index the log no longer holds.
///
/// Not a failure of the caller: a follower that has fallen far behind is
/// *supposed* to produce this, and the leader's answer is to send a snapshot
/// rather than entries. Distinguished from a protocol error for exactly that
/// reason -- one drops the link, this one changes what is sent next.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RaftLogCompacted(pub String);

impl std::fmt::Display for RaftLogCompacted {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RaftLogCompacted {}

/// A paging-cursor reservation could not be made durable.
///
/// No cursor leaves a member until an upper bound on it is on disk
/// (`cursors.rs`, "A reservation that outlives the process"), so the mutation
/// that asked for one cannot go ahead. Unlike `RaftUnavailable` something *is*
/// wrong -- a disk refused a write -- but the answer to the client is the same
/// retryable 503: the Node tries again, here or at another member, and nothing
/// was proposed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RaftCursorReservationFailed(pub String);

impl std::fmt::Display for RaftCursorReservationFailed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RaftCursorReservationFailed {}
