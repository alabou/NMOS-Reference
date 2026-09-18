// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! One resource as the registry holds it, and the two-stage removal it goes
//! through.
//!
//! # Only the body is kept
//!
//! Not the decoded type. Producing it is the schema validation
//! `APIs.md:22` requires and that still happens on every registration -- at the
//! Registration API boundary only, with the object discarded once it has served
//! as the validator. Nothing downstream ever read it, and the generated types
//! do not model every attribute of every resource: `node.json` declares an
//! optional, deprecated `hostname` that `NNode` has no member for, and a
//! third-party Node may legitimately carry vendor extensions. Serving the
//! original bytes is what keeps the HTTP and WebSocket views agreeing.
//!
//! # Divergence: `health` is an atomic
//!
//! Heartbeat is the highest-rate writer in the system. `Behaviour -
//! Registration.md:51` -- "Nodes only need perform a heartbeat to maintain
//! their Node resource" -- but a heartbeat refreshes the Node **and,
//! recursively, all of its sub-resources**. At AMWA scale, 2,500 Nodes of six
//! resources on a 5 s interval, that is roughly 3,500 writes a second to store
//! a clock value.
//!
//! Holding those under the store's exclusive lock would put the top writer in
//! the critical section for no reason: health is a racing clock value whose
//! only readers are garbage collection and `least_health`. As an
//! `AtomicI64` it is refreshed under a *read* lock, concurrently with every
//! other reader.
//!
//! What is given up is that health is no longer snapshot-consistent with the
//! rest of the record. One ordering rule makes that safe, and it is stated on
//! the heartbeat path rather than here: refresh **children first, the Node
//! last**, so a collector that sees a fresh Node is guaranteed the subtree
//! beneath it was already refreshed.

use std::sync::atomic::{AtomicI64, Ordering};

use crate::body::Body;
use crate::cursor::TaiCursor;
use crate::resource_type::ResourceType;

/// A resource's identifier.
///
/// A `String` for now, and this is a decision with a date on it. The plan's
/// D11 stores it as a `Uuid` -- 16 bytes and `Copy`, which matters because it
/// is half of the paging index's key and is therefore compared on every insert
/// and every page query. That is unblocked by the `\Z` validator fix, which
/// guarantees a canonical lowercase RFC-4122 form with no trailing newline.
///
/// It is deferred to the point where the index exists to measure, so the change
/// is made against a benchmark rather than an expectation.
pub type ResourceId = String;

/// One resource held by the registry.
#[derive(Debug)]
pub struct RegisteredResource {
    /// Which of the six types this is.
    pub resource_type: ResourceType,
    /// The resource's own id, as it appears in the body.
    pub id: ResourceId,
    /// The bytes that arrived, and their parsed form on demand.
    pub body: Body,
    /// The resource's own `version` attribute, `"<sec>:<nsec>"`.
    ///
    /// Node-controlled. Used only for the monotonicity check of
    /// `Behaviour - Registration.md:102` -- **never** as a paging cursor. It
    /// may repeat and it may go backwards, which is precisely why paging uses
    /// the registry-assigned cursors below instead.
    pub version: String,
    /// Registry-assigned creation cursor. Stable across updates.
    pub created: TaiCursor,
    /// Registry-assigned update cursor. Re-stamped on every accepted POST.
    pub updated: TaiCursor,
    /// This type's parent-key value, or `None` for a Node.
    pub parent_id: Option<ResourceId>,
    /// False once deleted or collected, until forgotten.
    ///
    /// A non-extant resource is excluded from every Query API response and
    /// every subscription match, but is still counted in the status line's
    /// non-extant figure and still participates in least-health.
    pub extant: bool,
    /// Liveness timestamp in TAI seconds. See the module docs for why this is
    /// an atomic and what that gives up.
    health: AtomicI64,
}

impl RegisteredResource {
    /// Build a record. `health` starts at zero, as Python's default does.
    #[must_use]
    pub fn new(
        resource_type: ResourceType,
        id: impl Into<ResourceId>,
        body: Body,
        version: impl Into<String>,
        created: TaiCursor,
        updated: TaiCursor,
        parent_id: Option<ResourceId>,
    ) -> Self {
        Self {
            resource_type,
            id: id.into(),
            body,
            version: version.into(),
            created,
            updated,
            parent_id,
            extant: true,
            health: AtomicI64::new(0),
        }
    }

    /// This resource's liveness timestamp.
    ///
    /// `Relaxed` on purpose. The value is a clock reading whose only readers
    /// are garbage collection and least-health, neither of which orders
    /// anything else against it -- the ordering that *does* matter is
    /// children-before-Node, and that is a property of the sequence of writes
    /// rather than of any one write's memory ordering.
    #[must_use]
    pub fn health(&self) -> i64 {
        self.health.load(Ordering::Relaxed)
    }

    /// Refresh this resource's liveness timestamp.
    ///
    /// Takes `&self`, not `&mut self`, which is the whole point: a heartbeat
    /// runs under a read lock and touches thousands of records without
    /// excluding any reader.
    pub fn set_health(&self, seconds: i64) {
        self.health.store(seconds, Ordering::Relaxed);
    }

    /// The resource's own `version`, parsed, or `None` if it is malformed.
    ///
    /// Malformed is reachable: `version` is Node-controlled and validated at
    /// the Registration API boundary, but a body restored from a backend has
    /// not been through that boundary.
    #[must_use]
    pub fn version_cursor(&self) -> Option<TaiCursor> {
        TaiCursor::parse(&self.version)
    }

    /// The key this resource occupies in a cursor-ordered index.
    ///
    /// Two of them exist per type, one per `order`, and the id is the
    /// tie-break: two resources sharing an instant must still have a total
    /// order, or two cluster members would page differently.
    #[must_use]
    pub fn index_key(&self, order: Order) -> (TaiCursor, ResourceId) {
        (
            match order {
                Order::Created => self.created,
                Order::Updated => self.updated,
            },
            self.id.clone(),
        )
    }
}

/// Which cursor a listing is ordered by.
///
/// `APIs - Query Parameters.md` exposes both: `paging.order=create` and the
/// default `update`. They are separate indexes because a resource's position in
/// one does not move when the other is re-stamped.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Order {
    /// Ordered by the creation cursor, which never changes after registration.
    Created,
    /// Ordered by the update cursor, re-stamped on every accepted POST. The
    /// protocol default.
    Updated,
}

impl Order {
    /// Both orders, for iterating the indexes.
    pub const ALL: [Self; 2] = [Self::Created, Self::Updated];

    /// The `paging.order` query value for this order.
    #[must_use]
    pub const fn wire(self) -> &'static str {
        match self {
            Self::Created => "create",
            Self::Updated => "update",
        }
    }

    /// Parse a `paging.order` value. `None` on anything else, so the caller
    /// answers 400 rather than silently defaulting.
    #[must_use]
    pub fn parse(text: &str) -> Option<Self> {
        match text {
            "create" => Some(Self::Created),
            "update" => Some(Self::Updated),
            _ => None,
        }
    }
}

/// A record of a resource that has been removed and not yet forgotten.
///
/// Removal is two-stage, mirroring nmos-cpp's `erase_resource` ->
/// `forget_erased_resources`. The intermediate state is what lets a removal
/// grain carry the resource's final content, and what keeps paging cursors
/// monotonic across a delete.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Tombstone {
    /// The type of the removed resource.
    pub resource_type: ResourceType,
    /// The id of the removed resource.
    pub id: ResourceId,
    /// When it became non-extant, and therefore when it may be forgotten.
    pub erased_at: TaiCursor,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn a_resource() -> RegisteredResource {
        RegisteredResource::new(
            ResourceType::Sender,
            "3b8be755-08ff-452b-b217-c9151eb21193",
            Body::new(r#"{"id":"3b8be755-08ff-452b-b217-c9151eb21193"}"#),
            "1700000000:0",
            TaiCursor::new(10, 0),
            TaiCursor::new(20, 0),
            Some("a370d258-69de-4422-860a-ee4cf32ee9f4".to_owned()),
        )
    }

    #[test]
    fn health_is_writable_through_a_shared_reference() {
        // The property the atomic exists for: a heartbeat under a read lock.
        let resource = a_resource();
        let shared: &RegisteredResource = &resource;
        assert_eq!(shared.health(), 0);
        shared.set_health(1700000042);
        assert_eq!(shared.health(), 1700000042);
    }

    #[test]
    fn health_survives_concurrent_writers() {
        use std::sync::Arc as StdArc;
        use std::thread;

        let resource = StdArc::new(a_resource());
        let mut handles = Vec::new();
        for n in 1..=8 {
            let copy = StdArc::clone(&resource);
            handles.push(thread::spawn(move || {
                for _ in 0..1000 {
                    copy.set_health(n);
                }
            }));
        }
        for handle in handles {
            handle.join().expect("no writer panicked");
        }
        // Any writer may have won; what matters is that a torn or impossible
        // value is not observable.
        assert!(
            (1..=8).contains(&resource.health()),
            "{}",
            resource.health()
        );
    }

    #[test]
    fn the_index_key_uses_the_cursor_the_order_names() {
        let resource = a_resource();
        assert_eq!(resource.index_key(Order::Created).0, TaiCursor::new(10, 0));
        assert_eq!(resource.index_key(Order::Updated).0, TaiCursor::new(20, 0));
        // And the id is carried as the tie-break.
        assert_eq!(resource.index_key(Order::Created).1, resource.id);
    }

    #[test]
    fn a_malformed_version_is_none_rather_than_a_panic() {
        let mut resource = a_resource();
        assert_eq!(
            resource.version_cursor(),
            Some(TaiCursor::new(1700000000, 0))
        );
        resource.version = "not-a-version".to_owned();
        assert_eq!(resource.version_cursor(), None);
    }

    #[test]
    fn the_order_wire_values_are_the_query_parameters() {
        assert_eq!(Order::Updated.wire(), "update");
        assert_eq!(Order::Created.wire(), "create");
        for order in Order::ALL {
            assert_eq!(Order::parse(order.wire()), Some(order));
        }
        // Not "created"/"updated", not a case variant: a bad value is a 400,
        // not a silent fallback to the default.
        for bad in ["created", "updated", "Create", "", "CREATE", " create"] {
            assert_eq!(Order::parse(bad), None, "{bad:?}");
        }
    }
}
