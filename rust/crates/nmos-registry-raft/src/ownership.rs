// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Which member owns which Node subtree, derived from the log.
//!
//! Port of `nmos/raft/ownership.py`.
//!
//! # Why ownership exists
//!
//! The etcd backend must read before it writes. It validates a registration
//! against its local store, but that store is a read model that may be behind,
//! so a rejection it produces might be a lie -- a parent registered a moment
//! ago on another member simply has not arrived yet. A 400 is terminal,
//! something the Node "MUST NOT" retry, so it can never be returned without a
//! linearizable read first.
//!
//! Ownership removes the premise. If exactly one member is responsible for a
//! Node's subtree, that member's view of the subtree is authoritative by
//! construction, and the four subtree-scoped checks in `store.prepare` can be
//! decided locally and returned immediately. That is where "a 400 costs zero
//! round trips" comes from, and it is most of why a registration costs one
//! round trip here instead of two or three.
//!
//! (The fifth check -- id uniqueness against the type index -- is global, not
//! subtree-scoped, and is *not* covered by this. Apply re-runs `prepare`
//! against the replicated store, and that answer is the authoritative one.)
//!
//! # The table is a replicated derivation, not a negotiation
//!
//! Nothing here talks to anyone. Ownership changes are operations in the log,
//! so every member computes the same table from the same entries, in the same
//! order, and there is no protocol for two members to disagree about. `epoch`
//! is the log index of the entry that set the current owner, which makes it
//! monotonic by construction and makes "who claimed most recently" answerable
//! without a clock.
//!
//! The epoch checks below are therefore defensive rather than load-bearing: in
//! a correctly ordered apply they can never fire. They exist because a table
//! that silently accepted a stale claim would produce two members each
//! believing they owned a Node, and the resulting divergence would be
//! discovered somewhere far away from the cause.

use std::collections::BTreeMap;

use crate::errors::RaftProtocolError;
use crate::wire::{Reader, Writer};

/// Who owns a Node, and the log index that decided it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Ownership {
    /// The owning member's index.
    pub owner: u64,
    /// The log index of the entry that set this owner.
    pub epoch: u64,
}

/// The per-Node ownership map, applied from the log.
///
/// Mutated only from the synchronous apply step, like the store, so there is no
/// interleaving for a lock to protect against.
///
/// A `BTreeMap` rather than a hash map, and that is a determinism decision
/// rather than a performance one: two members must produce byte-identical
/// snapshots from equal tables, and `member_down` must release the same Nodes
/// in the same order everywhere. The Python sorts at each of those call sites;
/// here the ordering is a property of the container, so a call site added later
/// cannot forget to sort.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct OwnershipTable {
    by_node: BTreeMap<String, Ownership>,
}

impl OwnershipTable {
    /// An empty table.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    // -- reading ------------------------------------------------------------

    /// The current owner, or `None` when the Node is unowned.
    ///
    /// Unowned is a normal state, not an error: it is what a Node looks like
    /// between its owner dying and whichever member it re-registers with
    /// claiming it.
    #[must_use]
    pub fn owner_of(&self, node_id: &str) -> Option<Ownership> {
        self.by_node.get(node_id).copied()
    }

    /// Whether `member` owns this Node.
    #[must_use]
    pub fn is_owned_by(&self, node_id: &str, member: u64) -> bool {
        self.by_node
            .get(node_id)
            .is_some_and(|held| held.owner == member)
    }

    /// Every Node this member owns, in a fixed order.
    ///
    /// The order is what `member_down` releases in, and a member-down entry
    /// must produce the same result on every member that applies it.
    #[must_use]
    pub fn nodes_owned_by(&self, member: u64) -> Vec<String> {
        self.by_node
            .iter()
            .filter(|&(_, held)| held.owner == member)
            .map(|(node_id, _)| node_id.clone())
            .collect()
    }

    /// How many Nodes have an owner.
    #[must_use]
    pub fn len(&self) -> usize {
        self.by_node.len()
    }

    /// Whether no Node has an owner.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.by_node.is_empty()
    }

    /// Whether this Node has an owner.
    #[must_use]
    pub fn contains(&self, node_id: &str) -> bool {
        self.by_node.contains_key(node_id)
    }

    // -- mutation, from apply only ------------------------------------------

    /// Set the owner. Returns whether anything changed.
    ///
    /// A claim at or below the current epoch is ignored. In a correctly ordered
    /// apply that cannot happen -- epochs are log indices -- so this is the
    /// tripwire described in the module docs rather than an expected path.
    pub fn claim(&mut self, node_id: &str, owner: u64, epoch: u64) -> bool {
        if let Some(current) = self.by_node.get(node_id)
            && epoch <= current.epoch
        {
            return false;
        }
        self.by_node
            .insert(node_id.to_owned(), Ownership { owner, epoch });
        true
    }

    /// Drop the owner, leaving the Node unowned. Returns whether it changed.
    ///
    /// Note the Node's *resources* are untouched. Releasing ownership says
    /// nothing about whether the Node is still registered -- a member dying
    /// does not expire the resources it happened to be responsible for, it only
    /// means somebody else has to take over answering for them.
    pub fn release(&mut self, node_id: &str, epoch: u64) -> bool {
        match self.by_node.get(node_id) {
            None => false,
            Some(current) if epoch <= current.epoch => false,
            Some(_) => self.by_node.remove(node_id).is_some(),
        }
    }

    /// Release every Node `member` owned. Returns which ones.
    ///
    /// One operation rather than one per Node: a member holding a thousand
    /// Nodes must not put a thousand entries through consensus at the exact
    /// moment the cluster is already a member short.
    pub fn member_down(&mut self, member: u64, epoch: u64) -> Vec<String> {
        let mut released = Vec::new();
        for node_id in self.nodes_owned_by(member) {
            if self.release(&node_id, epoch) {
                released.push(node_id);
            }
        }
        released
    }

    // -- snapshot transfer --------------------------------------------------

    /// Serialise for `InstallSnapshot`.
    ///
    /// The table travels with the snapshot because it is state derived from
    /// entries the snapshot has replaced. A follower that installed a snapshot
    /// and rebuilt ownership only from entries *after* it would believe every
    /// Node was unowned, and would start claiming Nodes that already have
    /// owners.
    ///
    /// Entries are written in sorted order so two members produce
    /// byte-identical snapshots from equal tables, which is what lets a
    /// snapshot be compared or checksummed at all.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        let mut writer = Writer::new();
        for (node_id, held) in &self.by_node {
            let entry = Writer::new()
                .string(1, node_id)
                .uint(2, held.owner)
                .uint(3, held.epoch)
                .take();
            writer = writer.bytes(1, &entry);
        }
        writer.take()
    }

    /// Read a table back from a snapshot.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut table = Self::new();
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            if number == 1 {
                let (node_id, owner, epoch) = read_entry(reader.bytes()?)?;
                // Inserted directly rather than through `claim`: this is not a
                // claim, it is the table as another member computed it, and
                // running it past the epoch tripwire would silently drop
                // entries whose epochs happen to be out of order in the
                // encoding.
                table.by_node.insert(node_id, Ownership { owner, epoch });
            } else {
                reader.skip(wire)?;
            }
        }
        Ok(table)
    }
}

fn read_entry(payload: &[u8]) -> Result<(String, u64, u64), RaftProtocolError> {
    let mut node_id = String::new();
    let mut owner = 0;
    let mut epoch = 0;
    let mut reader = Reader::new(payload);
    while let Some((number, wire)) = reader.next_field()? {
        match number {
            1 => node_id = reader.string()?,
            2 => owner = reader.uint()?,
            3 => epoch = reader.uint()?,
            _ => reader.skip(wire)?,
        }
    }
    Ok((node_id, owner, epoch))
}
