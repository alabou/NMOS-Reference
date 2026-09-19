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
/// it. The Python raises this past its own catch-all for the same reason.
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
