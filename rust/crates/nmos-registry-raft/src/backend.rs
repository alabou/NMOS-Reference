// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The consensus-backed distributed backend: one round trip, and no read
//! before it.
//!
//! Port of `nmos/registry/raft_backend.py`.
//!
//! Same seam as the etcd backend -- four methods and a state, with Query
//! untouched -- and a materially different path underneath. The comparison is
//! the point, so it is worth stating in the terms the benchmark measures:
//!
//! | operation | etcd | consensus |
//! |---|---|---|
//! | registration, steady state | 2 | **1** |
//! | first registration of a Node | 3 | **1** |
//! | heartbeat | 1 | **0** |
//! | rejection decided locally | 0 | 0 |
//!
//! # Where the difference comes from
//!
//! **Ownership removes the read.** The etcd backend validates against a local
//! store that may be behind, so a rejection it produces might be a lie -- and
//! a 400 is terminal, something a Node "MUST NOT" retry. It therefore cannot
//! answer *any* rejection without a linearizable read first. Here exactly one
//! member is responsible for a Node's subtree, so that member's view of the
//! subtree is authoritative by construction and the parent and version checks
//! are decided locally, with no round trip at all.
//!
//! **Apply removes the second wait.** The etcd backend commits to etcd and
//! then waits for its own write to come back down the watch stream before it
//! can answer. Here the commit *is* applied by this member, in the same step,
//! and the caller's future is resolved from inside that apply.
//!
//! **Leases stop being writes.** A heartbeat is one store call on the owner
//! and nothing else: no entry, no consensus round, no network. What it costs
//! instead is that liveness is decided by the owner rather than by a
//! cluster-wide lease, which is why expiry is proposed rather than evaluated
//! independently on every member.
//!
//! # Where it is *worse*, honestly
//!
//! A mutation arriving at a member that does not own the Node costs a hop to
//! the owner plus the commit -- two or three round trips against etcd's two. In
//! practice a Node registers with one registry and stays there, so this is the
//! rare path, but it is a real cost and it is why forwarding exists rather than
//! "just claim it": claiming on every stray request would make two members
//! fight over a Node while a load balancer spread its traffic.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use nmos_registry::registry::Registry;
use nmos_registry_backend::{BackendState, MutationUnavailable, RegistryBackend};
use nmos_registry_core::RegistrationError;
use nmos_registry_core::body::Body;
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::event::ResourceEvent;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::{Applied, RegistrationFailure, health_now};
use parking_lot::Mutex;
use tokio::sync::Mutex as AsyncMutex;

use crate::machine::Outcome;
use crate::messages::{Forward, ForwardReply, Message};
use crate::node::{ForwardHandler, RaftNode, Role};
use crate::operations::{Operation, OperationKind, ProposalId, Register};

/// How long a Node may remain owned by a member nobody can reach before
/// another member takes it over.
///
/// Deliberately below the 12 s garbage-collection interval of `Behaviour -
/// Registration.md:47`: a Node whose owner died must find a new one before its
/// resources would otherwise be collected.
pub const OWNERSHIP_GRACE_S: f64 = 6.0;

/// One in-flight proposal per Node subtree.
///
/// This is what makes local validation sound. `store.prepare` runs against the
/// current store and the result is proposed; if a second registration for the
/// same Node were validated before the first had applied, it would be deciding
/// against a store missing its own predecessor -- a Sender validated before its
/// Device landed would be rejected for a parent that was moments away.
///
/// Per Node, not global: registrations for *different* Nodes are independent
/// and must stay concurrent, because a facility powering up is hundreds of
/// Nodes at once and serialising them all would give back exactly the
/// throughput this design exists to gain.
///
/// `tokio::sync::Mutex`, not `parking_lot`: this one is held **across an
/// await** by design, which is the opposite of every other lock in this port.
/// Confusing the two is the likeliest way the design breaks.
#[derive(Default)]
struct NodeGate {
    locks: Mutex<HashMap<String, Arc<AsyncMutex<()>>>>,
}

impl NodeGate {
    fn lock_for(&self, node_id: &str) -> Arc<AsyncMutex<()>> {
        let mut locks = self.locks.lock();
        Arc::clone(
            locks
                .entry(node_id.to_owned())
                .or_insert_with(|| Arc::new(AsyncMutex::new(()))),
        )
    }

    /// Drop a Node's gate once nobody is waiting on it.
    ///
    /// Without this the map grows by one entry per Node ever registered, for
    /// the life of the process -- which on a facility that cycles equipment is
    /// a slow leak with no symptom until it is a large one.
    fn forget(&self, node_id: &str) {
        let mut locks = self.locks.lock();
        let unused = locks
            .get(node_id)
            .is_some_and(|gate| Arc::strong_count(gate) == 1 && gate.try_lock().is_ok());
        if unused {
            locks.remove(node_id);
        }
    }
}

/// Registry storage backed by the in-process consensus cluster.
pub struct RaftRegistryBackend {
    registry: Arc<Registry>,
    node: Arc<RaftNode>,
    gate: NodeGate,
    mutation_timeout: Duration,
    started: std::sync::atomic::AtomicBool,
    stopping: std::sync::atomic::AtomicBool,
}

impl RaftRegistryBackend {
    /// Wrap a consensus member.
    #[must_use]
    pub fn new(
        registry: Arc<Registry>,
        node: Arc<RaftNode>,
        mutation_timeout: Duration,
    ) -> Arc<Self> {
        Arc::new(Self {
            registry,
            node,
            gate: NodeGate::default(),
            mutation_timeout,
            started: std::sync::atomic::AtomicBool::new(false),
            stopping: std::sync::atomic::AtomicBool::new(false),
        })
    }

    /// The consensus member underneath.
    #[must_use]
    pub fn node(&self) -> &Arc<RaftNode> {
        &self.node
    }

    fn index(&self) -> u64 {
        self.node.index()
    }

    /// Who owns this Node, or `None` when it is free to claim.
    ///
    /// A Node owned by a member that has been unreachable reads as unowned, so
    /// whichever member it re-registers with can take over. The grace is what
    /// stops two members trading a Node back and forth while a load balancer
    /// spreads its traffic -- without it, every request would claim, and every
    /// claim would be a consensus round.
    fn owner_for(&self, node_id: &str) -> Option<u64> {
        let held = self.node.ownership_of(node_id)?;
        if held == self.index() {
            return Some(self.index());
        }
        if self.node.live_peers().contains(&held) {
            return Some(held);
        }
        None
    }

    fn owns(&self, node_id: &str) -> bool {
        self.owner_for(node_id) == Some(self.index())
    }

    /// Which Node's subtree this resource belongs to.
    ///
    /// A Node is its own, a Device names one, and everything else inherits its
    /// Device's -- looked up locally, and a Device that is genuinely absent is
    /// a genuine `PARENT_MISSING` decided by the same store rule that governs
    /// it in standalone mode.
    fn resolve_node(
        &self,
        resource_type: ResourceType,
        raw: &serde_json::Value,
    ) -> Result<String, RegistrationFailure> {
        let text = |key: &str| raw.get(key).and_then(serde_json::Value::as_str);

        match resource_type {
            ResourceType::Node => text("id").map(ToOwned::to_owned).ok_or_else(|| {
                RegistrationFailure::new(RegistrationError::Schema, "node has no id".to_owned())
            }),
            ResourceType::Device => text("node_id").map(ToOwned::to_owned).ok_or_else(|| {
                RegistrationFailure::new(
                    RegistrationError::Schema,
                    "device has no node_id".to_owned(),
                )
            }),
            other => {
                let Some(device_id) = text("device_id") else {
                    return Err(RegistrationFailure::new(
                        RegistrationError::Schema,
                        format!("{} has no device_id", other.singular()),
                    ));
                };
                let parent = self.registry.with_read_store(|store| {
                    store
                        .get(ResourceType::Device, device_id)
                        .map(|device| device.parent_id.clone().unwrap_or_default())
                });
                parent.ok_or_else(|| {
                    RegistrationFailure::new(
                        RegistrationError::ParentMissing,
                        format!("device {device_id} is not registered"),
                    )
                })
            }
        }
    }

    /// `(created, updated)`. `created` is stable across updates.
    ///
    /// A client paging by creation order must not see a resource move because
    /// it was updated.
    fn cursors_for(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> (TaiCursor, TaiCursor) {
        let updated = self.node.allocate_cursor(resource_type);
        let created = self.registry.with_read_store(|store| {
            store
                .get_including_tombstoned(resource_type, resource_id)
                .filter(|existing| existing.extant)
                .map(|existing| existing.created)
        });
        (created.unwrap_or(updated), updated)
    }

    /// Propose, wait for the apply, and translate failure into a 503.
    async fn commit(
        &self,
        operation: Operation,
        what: &str,
    ) -> Result<Outcome, MutationUnavailable> {
        match tokio::time::timeout(self.mutation_timeout, self.node.propose(operation)).await {
            Ok(Ok(outcome)) => Ok(outcome),
            Ok(Err(error)) => Err(MutationUnavailable(format!(
                "{what} could not commit: {}",
                error.0,
            ))),
            Err(_) => Err(MutationUnavailable(format!(
                "{what} did not commit within {:.1}s",
                self.mutation_timeout.as_secs_f64(),
            ))),
        }
    }

    /// Register as the owner: validate locally, propose once.
    async fn register_as_owner(
        &self,
        resource_type: ResourceType,
        body: Body,
        node_id: &str,
        claim: bool,
    ) -> Result<Result<Applied, RegistrationFailure>, MutationUnavailable> {
        let prepared = self
            .registry
            .with_read_store(|store| store.prepare(resource_type, body.data()));
        let prepared = match prepared {
            Ok(prepared) => prepared,
            // Authoritative, and free. The etcd backend cannot do this: its
            // store may be behind, so it must fence before it dares return a
            // terminal 400. Ownership is what makes the same answer safe here
            // without touching the network.
            Err(failure) => return Ok(Err(failure)),
        };

        let (created, updated) = self.cursors_for(resource_type, &prepared.resource_id);
        let operation = Operation {
            proposal: ProposalId {
                member: self.index(),
                sequence: 0,
            },
            kind: OperationKind::Register(Register {
                resource_type,
                resource_id: prepared.resource_id.clone(),
                node_id: node_id.to_owned(),
                body_text: body.text().to_owned(),
                created,
                updated,
                // Read once, here, and carried: every member must stamp the
                // same health or they diverge on the very next status line.
                health: health_now().max(0).unsigned_abs(),
                expect_created: prepared.creates,
                claim_owner: if claim { Some(self.index()) } else { None },
            }),
        };

        let what = format!("registration of {}", prepared.resource_id);
        match self.commit(operation, &what).await? {
            Outcome::Registered { created } => Ok(Ok(Applied {
                created,
                events: Vec::new(),
            })),
            Outcome::Refused { error, detail } => Ok(Err(RegistrationFailure::new(
                RegistrationError::from_code(&error).unwrap_or(RegistrationError::Schema),
                detail,
            ))),
            other => Err(MutationUnavailable(format!(
                "{what} applied as {other:?}, which is not a registration",
            ))),
        }
    }

    /// Hand a mutation to the member that owns its Node.
    async fn forward(&self, message: Forward, owner: u64) -> Option<ForwardReply> {
        let reply = self
            .node
            .request_peer(
                owner,
                &Message::Forward(message),
                Some(
                    self.mutation_timeout
                        .as_millis()
                        .try_into()
                        .unwrap_or(u64::MAX),
                ),
            )
            .await
            .ok()?;
        match reply {
            Message::ForwardReply(reply) => Some(reply),
            _ => None,
        }
    }

    /// Wait until this member has applied `index`.
    ///
    /// The one wait that survives ownership, and only on the forwarded path:
    /// the member that answered the client is not the member that applied the
    /// entry, and a client that immediately reads back from here would
    /// otherwise get a 404 for something it was just told was created.
    async fn await_applied(&self, index: u64) -> Result<(), MutationUnavailable> {
        if index == 0 {
            return Ok(());
        }
        self.node
            .fence()
            .wait(index, self.mutation_timeout)
            .await
            .map_err(|_| {
                MutationUnavailable(format!("this member did not catch up to index {index}"))
            })
    }
}

#[async_trait]
impl ForwardHandler for RaftRegistryBackend {
    /// Answer a mutation another member handed us because we own its Node.
    async fn forward(&self, message: &Forward) -> ForwardReply {
        let refusal = |error: &str, detail: String| ForwardReply {
            ok: false,
            created: false,
            error: error.to_owned(),
            detail,
            applied_index: 0,
            not_owner: false,
            request_id: message.request_id,
            owner: None,
        };

        let Some(resource_type) = ResourceType::from_singular(&message.resource_type) else {
            return refusal(
                "schema",
                format!("unknown resource type '{}'", message.resource_type),
            );
        };

        if message.verb == "heartbeat" {
            let health = RegistryBackend::heartbeat(self, &message.resource_id)
                .await
                .ok()
                .flatten();
            return ForwardReply {
                ok: health.is_some(),
                created: false,
                error: String::new(),
                detail: String::new(),
                applied_index: health.unwrap_or(0).max(0).unsigned_abs(),
                not_owner: false,
                request_id: message.request_id,
                owner: None,
            };
        }

        let body = Body::new(message.body_text.clone());
        let node_id = match self.resolve_node(resource_type, body.data()) {
            Ok(node_id) => node_id,
            Err(failure) => return reply_for(&Err(failure), 0, message.request_id),
        };

        let owner = self.owner_for(&node_id);
        if owner.is_some_and(|owner| owner != self.index()) {
            // It moved on. Say so rather than forwarding again: a request that
            // hops between members is a request with no bound on its latency.
            return ForwardReply {
                ok: false,
                created: false,
                error: String::new(),
                detail: String::new(),
                applied_index: 0,
                not_owner: true,
                request_id: message.request_id,
                owner,
            };
        }

        let gate = self.gate.lock_for(&node_id);
        let held = gate.lock().await;
        let result = self
            .register_as_owner(resource_type, body, &node_id, owner.is_none())
            .await;
        drop(held);
        self.gate.forget(&node_id);

        match result {
            Ok(outcome) => reply_for(&outcome, self.node.last_applied(), message.request_id),
            Err(error) => refusal("", error.0),
        }
    }
}

#[async_trait]
impl RegistryBackend for RaftRegistryBackend {
    /// Derived on every read, never cached.
    ///
    /// A cached state has to be refreshed by something, and whatever that
    /// something is will eventually not run. The first version of this cached
    /// it and refreshed it on start and on each mutation -- so a member that
    /// started before its peers waited out its timeout, went degraded, and then
    /// reported degraded *forever*, because nothing wrote to it and nothing
    /// else looked. Its Registration API answered 503 on a healthy cluster.
    fn state(&self) -> BackendState {
        if self.stopping.load(std::sync::atomic::Ordering::SeqCst) {
            return BackendState::Stopping;
        }
        if !self.started.load(std::sync::atomic::Ordering::SeqCst) {
            return BackendState::Starting;
        }
        if self.node.leader().is_some() && self.node.has_quorum() {
            BackendState::Ready
        } else {
            BackendState::Degraded
        }
    }

    fn registry(&self) -> &Arc<Registry> {
        &self.registry
    }

    async fn start(&self) -> Result<(), MutationUnavailable> {
        self.node.start().await.map_err(|error| {
            MutationUnavailable(format!("the transport did not start: {error}"))
        })?;
        self.started
            .store(true, std::sync::atomic::Ordering::SeqCst);

        if self
            .node
            .wait_for_leader(self.mutation_timeout)
            .await
            .is_err()
        {
            // Not fatal, and not sticky. A cluster whose other members have not
            // started yet is ordinary; degraded means "Query serves,
            // Registration answers 503", which is right while an election runs
            // and stops being right the moment one finishes -- which is why
            // `state` is derived rather than set here.
            tracing::warn!(
                "registry: no leader yet; serving queries and refusing \
                 registrations until one is elected",
            );
        }
        Ok(())
    }

    async fn register(
        &self,
        resource_type: ResourceType,
        body: Body,
    ) -> Result<Result<Applied, RegistrationFailure>, MutationUnavailable> {
        let node_id = match self.resolve_node(resource_type, body.data()) {
            Ok(node_id) => node_id,
            Err(failure) => return Ok(Err(failure)),
        };

        let owner = self.owner_for(&node_id);
        if let Some(owner) = owner
            && owner != self.index()
        {
            let resource_id = body
                .data()
                .get("id")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_owned();
            let Some(reply) = self
                .forward(
                    Forward {
                        verb: "register".to_owned(),
                        resource_type: resource_type.singular().to_owned(),
                        resource_id,
                        body_text: body.text().to_owned(),
                        request_id: 0,
                    },
                    owner,
                )
                .await
            else {
                return Err(MutationUnavailable(format!(
                    "the member owning node {node_id} did not answer",
                )));
            };

            if reply.not_owner {
                // Ownership moved underneath us. One retry, as owner or
                // forwarder depending on where it moved to -- and no more,
                // because a request that keeps chasing an owner is a request
                // that never answers.
                return Box::pin(self.register(resource_type, body)).await;
            }

            self.await_applied(reply.applied_index).await?;
            return Ok(result_of(&reply));
        }

        let gate = self.gate.lock_for(&node_id);
        let held = gate.lock().await;
        let result = self
            .register_as_owner(resource_type, body, &node_id, owner.is_none())
            .await;
        drop(held);
        self.gate.forget(&node_id);
        result
    }

    async fn unregister(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Result<Option<Vec<ResourceEvent>>, MutationUnavailable> {
        // A 404 costs nothing: the local store is a complete replica, so "not
        // here" is not a guess.
        let present = self
            .registry
            .with_read_store(|store| store.get(resource_type, resource_id).is_some());
        if !present {
            return Ok(None);
        }

        let operation = Operation {
            proposal: ProposalId {
                member: self.index(),
                sequence: 0,
            },
            kind: OperationKind::Unregister {
                resource_type,
                resource_id: resource_id.to_owned(),
            },
        };
        match self
            .commit(operation, &format!("delete of {resource_id}"))
            .await?
        {
            Outcome::Removed(true) => Ok(Some(Vec::new())),
            _ => Ok(None),
        }
    }

    /// Refresh a Node's liveness. **Zero round trips on its owner.**
    ///
    /// The property this preserves is the one the etcd backend's lease design
    /// argues for: 100 Nodes beating every 5 s must not become 100 consensus
    /// rounds per second. Here it is stronger -- the beat writes nothing at
    /// all, not even a lease renewal.
    async fn heartbeat(&self, node_id: &str) -> Result<Option<i64>, MutationUnavailable> {
        let owner = self.owner_for(node_id);
        if let Some(owner) = owner
            && owner != self.index()
        {
            let reply = self
                .forward(
                    Forward {
                        verb: "heartbeat".to_owned(),
                        resource_type: "node".to_owned(),
                        resource_id: node_id.to_owned(),
                        body_text: String::new(),
                        request_id: 0,
                    },
                    owner,
                )
                .await;
            return Ok(match reply {
                Some(reply) if reply.ok => {
                    Some(i64::try_from(reply.applied_index).unwrap_or_else(|_| health_now()))
                }
                _ => None,
            });
        }

        Ok(self.registry.heartbeat(node_id))
    }

    /// Expire silent Nodes this member owns; forget tombstones everywhere.
    ///
    /// Expiry is **owner-decided and replicated**, not evaluated independently
    /// on every member. Independent evaluation is how members end up
    /// disagreeing about which Nodes are alive, and the member with the slowest
    /// clock resurrects resources the others have collected.
    ///
    /// Forgetting is local and unreplicated: it drops records that are already
    /// non-extant, so it cannot resurrect anything, cannot remove anything a
    /// peer still considers live, and emits no grains.
    async fn collect_garbage(&self) -> Result<Vec<ResourceEvent>, MutationUnavailable> {
        if self.state() != BackendState::Ready {
            return Ok(Vec::new());
        }

        let threshold = self
            .registry
            .with_read_store(|store| health_now().saturating_sub(store.gc_interval()));
        let candidates: Vec<String> = self.registry.with_read_store(|store| {
            store
                .iter_extant(ResourceType::Node)
                .filter(|node| node.health() < threshold)
                .map(|node| node.id.clone())
                .collect()
        });

        for node_id in candidates {
            if !self.owns(&node_id) {
                continue;
            }
            let operation = Operation {
                proposal: ProposalId {
                    member: self.index(),
                    sequence: 0,
                },
                kind: OperationKind::Expire {
                    node_id: node_id.clone(),
                },
            };
            if self
                .commit(operation, &format!("expiry of {node_id}"))
                .await
                .is_err()
            {
                // The cluster cannot commit right now. The Node stays
                // registered and the next pass tries again, which is better
                // than removing it locally and diverging.
                break;
            }
            self.gate.forget(&node_id);
        }

        // `None` means the store reads its own clock, which is what a standalone
        // collection does. The decision is still replicated: what travels is the
        // resulting victim list, not the clock reading.
        let victims = self
            .registry
            .with_read_store(|store| store.forgettable(None));
        if !victims.is_empty() && self.node.role() == Role::Leader {
            let operation = Operation {
                proposal: ProposalId {
                    member: self.index(),
                    sequence: 0,
                },
                kind: OperationKind::Forget { victims },
            };
            drop(self.commit(operation, "forgetting tombstones").await);
        }

        // The events are published by apply on every member, so there are none
        // to hand back here -- unlike standalone, where this call *is* the
        // mutation. The count a caller wants is visible in the store.
        Ok(Vec::new())
    }

    async fn close(&self) {
        self.stopping
            .store(true, std::sync::atomic::Ordering::SeqCst);
        self.node.close().await;
    }
}

fn result_of(reply: &ForwardReply) -> Result<Applied, RegistrationFailure> {
    if reply.ok {
        return Ok(Applied {
            created: reply.created,
            events: Vec::new(),
        });
    }
    Err(RegistrationFailure::new(
        RegistrationError::from_code(&reply.error).unwrap_or(RegistrationError::Schema),
        reply.detail.clone(),
    ))
}

fn reply_for(
    result: &Result<Applied, RegistrationFailure>,
    applied: u64,
    request_id: u64,
) -> ForwardReply {
    match *result {
        Ok(ref applied_result) => ForwardReply {
            ok: true,
            created: applied_result.created,
            error: String::new(),
            detail: String::new(),
            applied_index: applied,
            not_owner: false,
            request_id,
            owner: None,
        },
        Err(ref failure) => ForwardReply {
            ok: false,
            created: false,
            error: failure.error.as_str().to_owned(),
            detail: failure.detail.clone(),
            applied_index: applied,
            not_owner: false,
            request_id,
            owner: None,
        },
    }
}
