// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The applier: a committed entry becomes a store mutation and its grains.
//!
//! Port of `nmos/raft/machine.py`.
//!
//! # The only writer
//!
//! Every change to the store goes through here, whether this member proposed it
//! or learned it from the leader. That is the same property the etcd backend
//! gets from "the watch is the only writer", and it buys the same thing: a
//! locally originated change and a remote one take an identical path, so there
//! is nothing to suppress, nothing to deduplicate, and no way for the two to
//! diverge.
//!
//! # Synchronous, and that is load-bearing
//!
//! The store's invariant is that nothing awaits inside a mutation, so no other
//! task can observe a half-applied change. [`StateMachine::apply`] preserves
//! it: the whole of a run, from the first mutation to the last, contains no
//! `await` -- and the compiler enforces that, because the closures handed to
//! `Registry::with_mutation` are not `async` and the guard is `!Send`.
//!
//! The caller is responsible for the other half: apply a *bounded* run, yield
//! between runs, never inside one. A 50,000-entry catch-up applied in one block
//! would stall the HTTP server and, worse, the heartbeat timer -- causing an
//! election, which causes more catch-up.
//!
//! # Determinism, stated as rules
//!
//! Every member applies the same entries and must reach the same state, byte
//! for byte. Three rules, each suppressing a default that would otherwise read
//! local state:
//!
//! 1. `health` is always passed. The store's apply defaults it to the clock.
//! 2. `created`/`updated` are always passed. The store otherwise allocates from
//!    this member's own cursor state.
//! 3. Nothing here reads a clock, a random source, or iterates a map in a way
//!    that reaches output. A cascading delete's events are sorted before
//!    publication, because the store walks a hash set to produce them.
//!
//! # Apply decides
//!
//! `Register::expect_created` carries the proposer's prediction of 201-vs-200,
//! made against its own replica. Apply re-runs `prepare` and *that* answer is
//! authoritative: the committed log order is the truth, and a proposer's view of
//! it can only be as fresh as its replica. The two differ on ordinary client
//! races -- the same new Node registered at two members at once, a retry whose
//! first attempt also commits, an update racing a deletion -- and differing is
//! not divergence: every member applied the same prefix and computes the same
//! answer.
//!
//! A mismatch used to be treated as one ("the two stores disagree") and stopped
//! the applier for good. Because apply is deterministic, every member stopped at
//! the same entry, and the cluster went on committing while applying nothing --
//! the chaos soak's largest single failure class. It is gone; the field stays on
//! the wire for members that still read it, and nothing here consults it. Real
//! divergence between replicas is not something a stale prediction can reveal;
//! the soak's replica-equality check is what looks for it.

use nmos_registry::registry::Registry;
use nmos_registry_core::body::Body;
use nmos_registry_core::event::ResourceEvent;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::RegistryStore;

use crate::cursors::CursorAllocator;
use crate::log::Entry;
use crate::operations::{Operation, OperationKind, ProposalId, Register};
use crate::ownership::OwnershipTable;
use crate::snapshot::SnapshotStore;

/// What one applied operation produced for whoever proposed it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// A registration succeeded. `true` means it created rather than updated.
    Registered {
        /// Whether the resource was created rather than updated.
        created: bool,
    },
    /// A registration was refused, authoritatively, at apply time.
    ///
    /// Not a divergence. The proposer's optimistic check passed and this one
    /// did not, which for a subtree-scoped rule *would* be a divergence -- but
    /// the id-uniqueness check is global and genuinely only decidable here, so
    /// a rejection at this point is expected and is simply the answer.
    Refused {
        /// The registration error's code, for the 400 body.
        error: String,
        /// Its detail.
        detail: String,
    },
    /// A removal: whether anything was there to remove.
    Removed(bool),
    /// How many records an expiry or forget dropped.
    Count(usize),
    /// An ownership change applied.
    Ownership(bool),
}

/// Applies committed operations to the registry.
pub struct StateMachine {
    ownership: OwnershipTable,
    cursors: CursorAllocator,
    member: u64,
    last_applied: u64,
    snapshots: SnapshotStore,
}

impl StateMachine {
    /// A machine for the member at index `member`.
    #[must_use]
    pub fn new(member: u64, cursors: CursorAllocator) -> Self {
        Self {
            ownership: OwnershipTable::new(),
            cursors,
            member,
            last_applied: 0,
            snapshots: SnapshotStore::new(),
        }
    }

    /// The highest log index this machine has applied.
    #[must_use]
    pub const fn last_applied(&self) -> u64 {
        self.last_applied
    }

    /// The replicated ownership table.
    #[must_use]
    pub const fn ownership(&self) -> &OwnershipTable {
        &self.ownership
    }

    /// This member's cursor allocator.
    #[must_use]
    pub const fn cursors(&self) -> &CursorAllocator {
        &self.cursors
    }

    /// The same, for allocating.
    pub const fn cursors_mut(&mut self) -> &mut CursorAllocator {
        &mut self.cursors
    }

    /// The snapshot store, for opening and finishing captures.
    pub const fn snapshots_mut(&mut self) -> &mut SnapshotStore {
        &mut self.snapshots
    }

    /// The snapshot store, for reading an open capture.
    #[must_use]
    pub const fn snapshots(&self) -> &SnapshotStore {
        &self.snapshots
    }

    /// Which member this is.
    #[must_use]
    pub const fn member(&self) -> u64 {
        self.member
    }

    /// Replace the whole local state with a snapshot's.
    ///
    /// The store is swapped in complete rather than filled in place, so Query
    /// never observes a half-loaded registry -- a member serving an empty view
    /// for the length of an install looks, to a Controller, exactly like a
    /// member whose registry was wiped.
    ///
    /// Ownership is replaced too. A member that rebuilt ownership only from
    /// entries *after* the snapshot would believe every Node was unowned and
    /// would start claiming Nodes that already have owners.
    ///
    /// Any capture still open is abandoned first. It was photographing the
    /// store this replaces, pinned at an index the installed snapshot has
    /// passed, and the compaction serialising it -- which yields between
    /// chunks, and so can be overtaken by an install -- used to finish anyway
    /// and store its older snapshot over the installed one: a member holding a
    /// snapshot below its own log boundary, unable to serve the entries between.
    /// The chaos soak measured it (seed 100423: a snapshot through 1046 below a
    /// log discarded through 1120). The compaction finds the capture gone at
    /// its next check and stops.
    pub fn install_snapshot(
        &mut self,
        registry: &Registry,
        store: RegistryStore,
        ownership: OwnershipTable,
        index: u64,
    ) {
        self.snapshots.abandon();
        registry.swap_store(store);
        self.ownership = ownership;
        self.last_applied = index;
    }

    /// Apply a bounded run of entries. Synchronous from first to last.
    ///
    /// Returns one outcome per proposal, for the member that proposed it to
    /// resolve its waiters with. Entries proposed elsewhere still produce an
    /// outcome; the caller simply has nobody waiting on them.
    pub fn apply(
        &mut self,
        registry: &Registry,
        entries: &[Entry<Operation>],
    ) -> Vec<(ProposalId, Outcome)> {
        let mut outcomes = Vec::new();

        for entry in entries {
            if entry.index <= self.last_applied {
                // Already applied. Ordinary after a snapshot install, where the
                // log may still hold entries the snapshot covers.
                continue;
            }
            if let Some(outcome) = self.apply_one(registry, entry) {
                outcomes.push((entry.value.proposal, outcome));
            }
            self.last_applied = entry.index;
        }

        outcomes
    }

    fn apply_one(&mut self, registry: &Registry, entry: &Entry<Operation>) -> Option<Outcome> {
        match entry.value.kind {
            OperationKind::Noop => None,
            OperationKind::Register(ref op) => Some(self.apply_register(registry, op, entry.index)),
            OperationKind::Unregister {
                resource_type,
                ref resource_id,
            } => Some(self.apply_remove(registry, resource_type, resource_id)),
            OperationKind::Expire { ref node_id } => Some(self.apply_expire(registry, node_id)),
            OperationKind::Forget { ref victims } => Some(self.apply_forget(registry, victims)),
            OperationKind::ClaimOwnership { ref node_id, owner } => Some(Outcome::Ownership(
                self.ownership.claim(node_id, owner, entry.index),
            )),
            OperationKind::ReleaseOwnership { ref node_id } => Some(Outcome::Ownership(
                self.ownership.release(node_id, entry.index),
            )),
            OperationKind::MemberDown { member } => {
                let released = self.ownership.member_down(member, entry.index);
                if !released.is_empty() {
                    tracing::info!(
                        member,
                        nodes = released.len(),
                        "raft: member is down; released Node(s)",
                    );
                }
                Some(Outcome::Count(released.len()))
            }
        }
    }

    fn apply_register(&mut self, registry: &Registry, op: &Register, index: u64) -> Outcome {
        // `Body::new`, not `from_value`: the bytes the client sent survive
        // apply exactly. Re-serialising is precisely the normalisation the
        // fidelity guarantee forbids.
        let body = Body::new(op.body_text.clone());

        let capture = self.snapshots.capture_mut();
        let ownership = &mut self.ownership;
        let outcome = registry.with_mutation(|store| {
            let prepared = match store.prepare(op.resource_type, body.data()) {
                Ok(prepared) => prepared,
                Err(failure) => {
                    return (
                        Outcome::Refused {
                            error: failure.error.as_str().to_owned(),
                            detail: failure.detail,
                        },
                        Vec::new(),
                    );
                }
            };
            // Created or updated is whatever `prepare` says of the committed
            // state, not what the proposer predicted -- see the module docs.

            // The fused claim: only for a registration that is accepted -- a
            // refused one registered nothing and must own nothing -- and
            // before the mutation, so a reader that sees the resource also
            // sees who owns it. The Python claims in exactly this place;
            // claiming before `prepare`, as this once did, gave a refused
            // registration its Node, and a mixed cluster two different
            // ownership tables.
            if let Some(owner) = op.claim_owner {
                ownership.claim(&op.node_id, owner, index);
            }

            // Before, not after: apply mutates records in place, so once it has
            // run there is nothing left to photograph.
            if let Some(capture) = capture {
                match store.get_including_tombstoned(op.resource_type, &op.resource_id) {
                    Some(existing) => capture.capture(existing),
                    // Nothing to preserve, and the snapshot must not invent it:
                    // this resource did not exist at the pinned index.
                    None => capture.capture_created(op.resource_type, &op.resource_id),
                }
            }

            // A resource this entry creates was created *at* this entry, whose
            // cursor is `updated`. `created` is the proposer's copy of the
            // existing record's creation cursor when it predicted an update;
            // stamped on a resource that turns out to be new, it would sit
            // behind cursors a client has already paged past. For a predicted
            // create the two are equal, so nothing else changes.
            let creation = if prepared.creates {
                op.updated
            } else {
                op.created
            };
            let applied = store.apply_committed(
                &prepared,
                body,
                Some(creation),
                Some(op.updated),
                Some(i64::try_from(op.health).unwrap_or(i64::MAX)),
            );
            let created = applied.created;
            (Outcome::Registered { created }, applied.events)
        });

        self.cursors.observe(op.resource_type, op.updated);
        outcome
    }

    fn apply_remove(
        &mut self,
        registry: &Registry,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Outcome {
        let capture = self.snapshots.capture_mut();
        registry.with_mutation(|store| {
            capture_subtree(store, capture, resource_type, resource_id);
            match store.delete(resource_type, resource_id) {
                None => (Outcome::Removed(false), Vec::new()),
                Some(events) => (Outcome::Removed(true), ordered(events)),
            }
        })
    }

    fn apply_expire(&mut self, registry: &Registry, node_id: &str) -> Outcome {
        let capture = self.snapshots.capture_mut();
        let removed = registry.with_mutation(|store| {
            capture_subtree(store, capture, ResourceType::Node, node_id);
            match store.delete(ResourceType::Node, node_id) {
                None => (0, Vec::new()),
                Some(events) => (events.len(), ordered(events)),
            }
        });
        if removed > 0 {
            tracing::info!(
                node = node_id,
                sub_resources = removed.saturating_sub(1),
                "raft: expired node and its sub-resources",
            );
        }
        Outcome::Count(removed)
    }

    fn apply_forget(&mut self, registry: &Registry, victims: &[(ResourceType, String)]) -> Outcome {
        let mut capture = self.snapshots.capture_mut();
        let forgotten = registry.with_mutation(|store| {
            let mut forgotten: usize = 0;
            for &(resource_type, ref resource_id) in victims {
                if let Some(capture) = capture.as_deref_mut() {
                    match store.get_including_tombstoned(resource_type, resource_id) {
                        Some(existing) => capture.capture(existing),
                        None => capture.capture_created(resource_type, resource_id),
                    }
                }
                if store.forget(resource_type, resource_id) {
                    forgotten = forgotten.saturating_add(1);
                }
            }
            // No events: a tombstone was already invisible to every client, so
            // dropping it changes nothing anyone can observe.
            (forgotten, Vec::new())
        });
        Outcome::Count(forgotten)
    }
}

/// Photograph a whole cascade before it is erased.
fn capture_subtree(
    store: &RegistryStore,
    capture: Option<&mut crate::snapshot::SnapshotCapture>,
    resource_type: ResourceType,
    resource_id: &str,
) {
    let Some(capture) = capture else {
        return;
    };
    // `subtree` returns keys, not records, so each is looked up -- and a key
    // whose record is already gone is skipped rather than invented. A cascade
    // photographs what is there to photograph.
    for (kind, id) in store.subtree(resource_type, resource_id) {
        if let Some(resource) = store.get_including_tombstoned(kind, &id) {
            capture.capture(resource);
        }
    }
}

/// Impose a total order on removal events before they are published.
///
/// The Python needs this: its child index is a `set`, so a cascade's order
/// depends on hash iteration and differs between members. Subscribers would
/// then see the same deletion described in a different order on each -- a
/// divergence, even though every member ends in the same state.
///
/// **Here it is redundant today, and kept deliberately.** The core's child
/// index is a `BTreeSet`, so the store already hands over a cascade in
/// children-then-id order and removing this sort changes nothing observable --
/// measured, and no test in this crate can catch its removal.
///
/// It stays because that is a property of a *different module*, chosen for
/// paging reasons rather than for this one, and a core optimised later to a
/// `HashSet` would reintroduce the divergence here silently.
/// `the_store_already_orders_a_cascade` pins the assumption so it cannot lapse
/// without something failing.
///
/// Deepest first, as the store already intends, then by type name and id within
/// a depth: a subscriber must never see a parent disappear while its children
/// are still present.
fn ordered(mut events: Vec<ResourceEvent>) -> Vec<ResourceEvent> {
    events.sort_by(|a, b| {
        let left = (
            std::cmp::Reverse(depth(a.resource_type)),
            a.resource_type.singular(),
            a.resource_id.as_str(),
        );
        let right = (
            std::cmp::Reverse(depth(b.resource_type)),
            b.resource_type.singular(),
            b.resource_id.as_str(),
        );
        left.cmp(&right)
    });
    events
}

/// Node 0, Device 1, everything else 2 -- the subtree depth.
const fn depth(resource_type: ResourceType) -> u8 {
    match resource_type {
        ResourceType::Node => 0,
        ResourceType::Device => 1,
        _ => 2,
    }
}
