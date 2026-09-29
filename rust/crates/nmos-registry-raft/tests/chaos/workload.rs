// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! What a client does, and how its answers are classified.
//!
//! Two levels, because they test different things:
//!
//! * **Consensus** -- operations proposed straight to a node, exactly as
//!   `nmos/raft/tests/test_chaos_soak.py` does: fresh Node ids, register and
//!   unregister. It exercises the log and nothing above it.
//! * **Registry** -- the `RegistryBackend` seam the HTTP layer calls, with the
//!   same gate the Registration API applies (`registration.rs`: anything but
//!   a state that `accepts_mutations` is a 503 before the backend is asked).
//!   Ownership, forwarding, the per-Node gate, local validation and the
//!   `expect_created` tripwire are all on this path, and none of them are on
//!   the other.
//!
//! Every answer is classified the way the client would see it, because the
//! ledger's promises are the client's: an acknowledgement is owed durability,
//! a 400 is owed nothing, and a 503 or a timeout left the outcome open.

use std::sync::Arc;
use std::time::Duration;

use nmos_registry_backend::RegistryBackend;
use nmos_registry_core::body::Body;
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_raft::backend::RaftRegistryBackend;
use nmos_registry_raft::machine::Outcome;
use nmos_registry_raft::node::RaftNode;
use nmos_registry_raft::operations::{Operation, OperationKind, ProposalId, Register};
use parking_lot::Mutex;
use serde_json::json;

/// How a registration was answered.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Answer {
    /// 200/201: acknowledged. `created` is 201.
    Acknowledged {
        /// Created rather than updated.
        created: bool,
    },
    /// 400: refused, authoritatively.
    Refused(String),
    /// 503 or a timeout: the outcome is unknown.
    Unavailable(String),
    /// The Registration API would have answered 503 without asking.
    NotReady,
    /// An outcome that does not belong to this operation at all.
    ///
    /// The symptom of proposal-id reuse (`node.rs`, `sequence` seeded from the
    /// incarnation): an old entry's outcome resolving a new caller's future.
    Mismatched(String),
}

/// How a deletion was answered.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Deletion {
    /// 204: it was there and is gone.
    Removed,
    /// 404: not there.
    NotFound,
    /// 503 or a timeout.
    Unavailable(String),
    /// Refused before the backend was asked.
    NotReady,
    /// An outcome belonging to another operation.
    Mismatched(String),
}

/// How a heartbeat was answered.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Beat {
    /// 200.
    Alive,
    /// 404: the Node is not registered.
    Unknown,
    /// 503.
    Unavailable(String),
    /// Refused before the backend was asked.
    NotReady,
}

// -- bodies ---------------------------------------------------------------------

/// A Node body, as `tests/backend.rs` writes one.
#[must_use]
pub fn node_body(id: &str, version: &str) -> Body {
    Body::from_value(json!({
        "id": id, "version": version, "label": format!("soak node {id}"), "description": "",
        "tags": {}, "href": "http://example/", "hostname": "example", "caps": {},
        "api": {
            "versions": ["v1.3"],
            "endpoints": [{"host": "example", "port": 80, "protocol": "http"}],
        },
        "services": [], "clocks": [], "interfaces": [],
    }))
}

/// A Device body under `node_id`.
#[must_use]
pub fn device_body(id: &str, node_id: &str, version: &str) -> Body {
    Body::from_value(json!({
        "id": id, "version": version, "label": "soak device", "description": "",
        "tags": {}, "type": "urn:x-nmos:device:generic", "node_id": node_id,
        "senders": [], "receivers": [], "controls": [],
    }))
}

/// A Sender body under `device_id`.
#[must_use]
pub fn sender_body(id: &str, device_id: &str, version: &str) -> Body {
    Body::from_value(json!({
        "id": id, "version": version, "label": "soak sender", "description": "",
        "tags": {}, "flow_id": null, "device_id": device_id,
        "manifest_href": null, "transport": "urn:x-nmos:transport:rtp",
        "interface_bindings": [],
        "subscription": {"receiver_id": null, "active": false},
    }))
}

// -- consensus level ------------------------------------------------------------

/// One cursor the consensus workload allocated, and what bears on it.
///
/// The wall clock read just before the allocator read it, the incarnation of
/// the member that allocated it, and how much of the log that member had
/// applied -- the cursors it had observed. What a Cursor Uniqueness failure
/// needs to explain itself: the property says only that two resources share a
/// cursor, and every question after that is about the allocations.
#[derive(Debug, Clone)]
pub struct Allocation {
    /// The member that allocated.
    pub member: u64,
    /// Which run of that member.
    pub incarnation: u64,
    /// The Node the cursor was for.
    pub node_id: String,
    /// The wall clock, read immediately before the allocation.
    pub clock: TaiCursor,
    /// What the allocator returned.
    pub cursor: TaiCursor,
    /// The member's applied index then: everything it had observed.
    pub applied: u64,
    /// Its commit index then.
    pub commit: u64,
    /// When, on the run's clock.
    pub at: tokio::time::Instant,
}

/// Propose a fresh Node's registration, as the Python soak does.
///
/// `health: 0` for the Python's reason: nothing the consensus soak registers
/// may be collected mid-run, since an expiry would remove something the
/// durability check is entitled to expect -- and this workload never runs a
/// collection, so nothing ever reads it.
pub async fn propose_register(
    node: &Arc<RaftNode>,
    member: u64,
    node_id: &str,
    timeout: Duration,
) -> Answer {
    propose_register_recorded(node, member, node_id, timeout, None).await
}

/// [`propose_register`], recording the allocation in `allocations`.
pub async fn propose_register_recorded(
    node: &Arc<RaftNode>,
    member: u64,
    node_id: &str,
    timeout: Duration,
    allocations: Option<&Mutex<Vec<Allocation>>>,
) -> Answer {
    let (clock, applied, commit) = (TaiCursor::now(), node.last_applied(), node.commit_index());
    // A reservation that cannot be written is the 503 a Node would get.
    let cursor = match node.allocate_cursor(ResourceType::Node) {
        Ok(cursor) => cursor,
        Err(failed) => return Answer::Unavailable(failed.0),
    };
    if let Some(allocations) = allocations {
        allocations.lock().push(Allocation {
            member,
            incarnation: node.incarnation(),
            node_id: node_id.to_owned(),
            clock,
            cursor,
            applied,
            commit,
            at: tokio::time::Instant::now(),
        });
    }
    let operation = Operation {
        proposal: ProposalId {
            member,
            sequence: 0,
        },
        kind: OperationKind::Register(Register {
            resource_type: ResourceType::Node,
            resource_id: node_id.to_owned(),
            node_id: node_id.to_owned(),
            body_text: node_body(node_id, "1000:0").text().to_owned(),
            created: cursor,
            updated: cursor,
            health: 0,
            expect_created: true,
            claim_owner: Some(member),
        }),
    };
    match tokio::time::timeout(timeout, node.propose(operation)).await {
        Ok(Ok(Outcome::Registered { created })) => Answer::Acknowledged { created },
        Ok(Ok(Outcome::Refused { error, detail })) => Answer::Refused(format!("{error}: {detail}")),
        Ok(Ok(other)) => Answer::Mismatched(format!("a registration resolved as {other:?}")),
        Ok(Err(error)) => Answer::Unavailable(error.0),
        Err(_) => Answer::Unavailable(format!("no outcome within {timeout:?}")),
    }
}

/// Propose a Node's removal.
pub async fn propose_unregister(
    node: &Arc<RaftNode>,
    member: u64,
    node_id: &str,
    timeout: Duration,
) -> Deletion {
    let operation = Operation {
        proposal: ProposalId {
            member,
            sequence: 0,
        },
        kind: OperationKind::Unregister {
            resource_type: ResourceType::Node,
            resource_id: node_id.to_owned(),
        },
    };
    match tokio::time::timeout(timeout, node.propose(operation)).await {
        Ok(Ok(Outcome::Removed(true))) => Deletion::Removed,
        Ok(Ok(Outcome::Removed(false))) => Deletion::NotFound,
        Ok(Ok(other)) => Deletion::Mismatched(format!("an unregistration resolved as {other:?}")),
        Ok(Err(error)) => Deletion::Unavailable(error.0),
        Err(_) => Deletion::Unavailable(format!("no outcome within {timeout:?}")),
    }
}

// -- registry level -------------------------------------------------------------

/// `POST /resource`, as the Registration API makes it.
pub async fn register(
    backend: &Arc<RaftRegistryBackend>,
    kind: ResourceType,
    body: Body,
) -> Answer {
    if !backend.state().accepts_mutations() {
        return Answer::NotReady;
    }
    match backend.register(kind, body).await {
        Ok(Ok(applied)) => Answer::Acknowledged {
            created: applied.created,
        },
        Ok(Err(failure)) => {
            Answer::Refused(format!("{}: {}", failure.error.as_str(), failure.detail))
        }
        Err(unavailable) => Answer::Unavailable(unavailable.0),
    }
}

/// `DELETE /resource/{type}/{id}`.
pub async fn unregister(
    backend: &Arc<RaftRegistryBackend>,
    kind: ResourceType,
    id: &str,
) -> Deletion {
    if !backend.state().accepts_mutations() {
        return Deletion::NotReady;
    }
    match backend.unregister(kind, id).await {
        Ok(Some(_)) => Deletion::Removed,
        Ok(None) => Deletion::NotFound,
        Err(unavailable) => Deletion::Unavailable(unavailable.0),
    }
}

/// `POST /health/nodes/{id}`.
pub async fn heartbeat(backend: &Arc<RaftRegistryBackend>, node_id: &str) -> Beat {
    if !backend.state().accepts_mutations() {
        return Beat::NotReady;
    }
    match backend.heartbeat(node_id).await {
        Ok(Some(_)) => Beat::Alive,
        Ok(None) => Beat::Unknown,
        Err(unavailable) => Beat::Unavailable(unavailable.0),
    }
}
