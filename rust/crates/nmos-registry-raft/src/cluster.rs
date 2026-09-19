// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Consensus's view of the cluster, derived from the shared topology.
//!
//! Port of `nmos/raft/cluster.py`.
//!
//! Reuse, not reimplementation. [`nmos_cluster`] already decides the member
//! set, their canonical order, their names, the quorum arithmetic and the
//! cluster token, and it does so identically on every host from the same
//! configured list. That determinism is a correctness property -- two members
//! deriving different names or different tokens would form two clusters that
//! each believed they were the whole thing -- so there is exactly one
//! implementation of it and this module sits on top.
//!
//! What consensus adds is small and specific:
//!
//! * **A member index.** Position in the canonical sorted order, which every
//!   member computes identically. It is what messages carry instead of names,
//!   and it is the `owner` in [`crate::cursors`]'s lane allocation -- so it has
//!   to be stable and distinct, which the total order guarantees.
//! * **A port.** The peer transport, clear of etcd's 2381/2382 so an etcd rig
//!   and a consensus rig can share a developer's machine.
//! * **A flavoured token.** [`RAFT_FLAVOUR`] so a consensus cluster and an etcd
//!   cluster configured on the same hosts under the same namespace cannot
//!   derive the same identity and mistake each other for peers.

use nmos_cluster::ClusterLayout;

/// Default client port, clear of `--etcdClientPort`.
pub const DEFAULT_RAFT_CLIENT_PORT: u16 = 2481;

/// Default peer port, clear of `--etcdPeerPort`.
///
/// Both backends can then be exercised on one machine without a port collision
/// that would surface as a mysterious failure to form a cluster.
pub const DEFAULT_RAFT_PEER_PORT: u16 = 2482;

/// Hashed into the cluster token.
///
/// The trailing newline keeps it from being a prefix of any namespace, so two
/// deployments cannot collide by one namespace happening to begin with the
/// other's flavour.
pub const RAFT_FLAVOUR: &str = "raft\n";

/// One member, as consensus addresses it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RaftMember {
    /// Position in the canonical order.
    ///
    /// Stable, distinct, and derived identically on every member -- which is
    /// what lets it serve as both the wire identity and the cursor lane.
    pub index: u64,
    /// Its derived name.
    pub name: String,
    /// Its advertised host.
    pub host: String,
    /// Its peer port.
    pub port: u16,
}

impl RaftMember {
    /// Where a peer connects to reach this member.
    #[must_use]
    pub fn target(&self) -> (String, u16) {
        (self.host.clone(), self.port)
    }
}

/// The cluster as consensus sees it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RaftLayout {
    /// Every member, in canonical order, indexed by position.
    pub members: Vec<RaftMember>,
    /// Which one this process is.
    pub local: RaftMember,
    /// The flavoured token every member derives identically.
    pub cluster_id: String,
}

impl RaftLayout {
    /// How many members there are.
    #[must_use]
    pub fn size(&self) -> usize {
        self.members.len()
    }

    /// How many must agree.
    #[must_use]
    pub fn quorum(&self) -> usize {
        self.size().div_euclid(2).saturating_add(1)
    }

    /// How many may fail.
    #[must_use]
    pub fn failures_tolerated(&self) -> usize {
        self.size().saturating_sub(self.quorum())
    }

    /// Everyone but us.
    #[must_use]
    pub fn peers(&self) -> Vec<&RaftMember> {
        self.members
            .iter()
            .filter(|m| m.index != self.local.index)
            .collect()
    }

    /// Where each peer is reached.
    #[must_use]
    pub fn peer_targets(&self) -> Vec<(u64, String, u16)> {
        self.peers()
            .into_iter()
            .map(|m| (m.index, m.host.clone(), m.port))
            .collect()
    }

    /// Look a member up by the index its messages carry.
    #[must_use]
    pub fn member_by_index(&self, index: u64) -> Option<&RaftMember> {
        self.members.iter().find(|m| m.index == index)
    }
}

/// Project a shared cluster layout onto consensus's addressing.
///
/// `layout.members` is already in canonical sorted order, so enumerating it
/// assigns every member the same index on every host -- the same argument that
/// makes the member names agree, applied to one more derived value.
///
/// The peer port comes from the member, never from a default chosen here:
/// `--raftPeerPort` has already been folded into the shared layout by the time
/// this runs, and members co-located on one host are distinguished by port
/// alone. Substituting [`DEFAULT_RAFT_PEER_PORT`] for a uniform configured port
/// would silently ignore the operator's flag, and two members on one host would
/// both claim to be reachable at it.
///
/// # Panics
///
/// Never: `layout.local` is one of `layout.members` by construction, so the
/// search below always finds it. The `unwrap_or_else` exists because the type
/// system does not know that, and a silent fallback to member 0 would put this
/// member in another member's cursor lane.
#[must_use]
pub fn derive_raft_layout(layout: &ClusterLayout, cluster_id: String) -> RaftLayout {
    let members: Vec<RaftMember> = layout
        .members
        .iter()
        .enumerate()
        .map(|(index, member)| RaftMember {
            index: index as u64,
            name: member.name.clone(),
            host: member.host.clone(),
            port: member.peer_port,
        })
        .collect();

    let local = members
        .iter()
        .find(|m| m.name == layout.local.name)
        .cloned()
        .unwrap_or_else(|| unreachable!("the local member is always in the member list"));

    RaftLayout {
        members,
        local,
        cluster_id,
    }
}
