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
//! | rejection the body decides | 0 | 0 |
//! | rejection the store decides | 1 | 1 (2 off the leader) |
//!
//! # Where the difference comes from
//!
//! **Ownership removes the read -- from every answer but a refusal.** The etcd
//! backend validates against a local store that may be behind, so it fences
//! before it trusts a rejection. Here exactly one member is responsible for a
//! Node's subtree, so a registration that passes validation is simply
//! proposed: apply, not the proposer, decides whether it creates, updates or is
//! refused (`machine.rs`), and nothing stale can commit. A *refusal* is
//! different, because the refusal is the answer -- a 400 is terminal, something
//! a Node "MUST NOT" retry, and a 404 on heartbeat makes it re-register
//! everything -- and an owner's store is current only as of what it has
//! applied: one restarted with nothing validates against an empty store. So a
//! refusal the store decides, and a 404 from a delete or a heartbeat, is given
//! only after a read barrier (`read_barrier`): one quorum round, on those paths
//! alone.
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
    /// A Node owned by a member this one cannot reach reads as unowned at once,
    /// so whichever member it re-registers with takes it over. There is no grace
    /// period, and none is wanted:
    ///
    /// * a claim rides the registration's own proposal (`claim_owner`), so
    ///   taking a Node over adds no consensus round;
    /// * ownership moves only when the owner is unreachable from the member a
    ///   request reached -- a load balancer spreading a Node's traffic over
    ///   members that can reach its owner forwards, it does not claim;
    /// * for as long as a grace lasted, every request for the Node at another
    ///   member would go to an owner nobody can reach and be answered 503;
    /// * and a Node whose owner died must find a new one before the 12 s
    ///   collection (`Behaviour - Registration.md:47`) removes it, which
    ///   immediate takeover serves best.
    ///
    /// A move is safe whenever it happens: apply, not the proposer, decides
    /// whether a registration creates or updates (`machine.rs`).
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
    /// Device's -- which is looked up locally. A Device absent here is
    /// `PARENT_MISSING` only if this member is current, which is why every
    /// caller takes a read barrier before believing it (`read_barrier`).
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
    ///
    /// # Errors
    ///
    /// [`MutationUnavailable`] when the cursor's reservation could not be made
    /// durable (`RaftNode::allocate_cursor`). Nothing was proposed, so the
    /// Node's retry starts clean.
    fn cursors_for(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Result<(TaiCursor, TaiCursor), MutationUnavailable> {
        let updated = self
            .node
            .allocate_cursor(resource_type)
            .map_err(|failed| MutationUnavailable(failed.0))?;
        let created = self.registry.with_read_store(|store| {
            store
                .get_including_tombstoned(resource_type, resource_id)
                .filter(|existing| existing.extant)
                .map(|existing| existing.created)
        });
        Ok((created.unwrap_or(updated), updated))
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
        let prepare = || {
            self.registry
                .with_read_store(|store| store.prepare(resource_type, body.data()))
        };
        let mut prepared = prepare();
        if let Err(ref failure) = prepared
            && decided_by_state(failure)
        {
            // Validated against a store that may be behind. It was once
            // returned at once, as "authoritative, and free" because this
            // member owns the Node -- true only of an owner that has applied
            // everything committed, and an owner can lag like any member (one
            // restarted with nothing validates against an empty store). So this
            // member catches up to a read index first, exactly as the etcd
            // backend fences before it dares return a terminal 400, and decides
            // again.
            self.read_barrier().await?;
            prepared = prepare();
        }
        let prepared = match prepared {
            Ok(prepared) => prepared,
            Err(failure) => return Ok(Err(failure)),
        };

        let (created, updated) = self.cursors_for(resource_type, &prepared.resource_id)?;
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

    /// `register`, told what a moved Node may still do -- see [`WhenMoved`].
    async fn register_routed(
        &self,
        resource_type: ResourceType,
        body: Body,
        when_moved: WhenMoved,
    ) -> Result<Result<Applied, RegistrationFailure>, MutationUnavailable> {
        let node_id = match self.resolve_node(resource_type, body.data()) {
            Ok(node_id) => node_id,
            Err(failure) if decided_by_state(&failure) => {
                // A parent this member has not yet applied is not a missing
                // one: see `read_barrier`.
                self.read_barrier().await?;
                match self.resolve_node(resource_type, body.data()) {
                    Ok(node_id) => node_id,
                    Err(failure) => return Ok(Err(failure)),
                }
            }
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
                //
                // "No more" has to be enforced, not merely intended. Retrying
                // through `register` again, with nothing spent, recursed for
                // as long as this member's ownership table stayed behind --
                // and a table that is behind names the same former owner every
                // time, so the retry forwards to the member that has just said
                // no. The chaos soak measured it as a stack overflow that
                // aborted the process, the faulting thread 9,700 frames deep in
                // this function; the Python runs the same recursion into
                // `RecursionError`, 490 forwards in 0.4s. After the one retry
                // this is a 503: the tables converge, and a Node retries a 503.
                return match when_moved {
                    WhenMoved::RouteAgain => {
                        Box::pin(self.register_routed(resource_type, body, WhenMoved::Unavailable))
                            .await
                    }
                    WhenMoved::Unavailable => Err(MutationUnavailable(format!(
                        "member {owner} no longer owns node {node_id}, and this member has not \
                         yet learned which does",
                    ))),
                };
            }

            self.await_applied(reply.applied_index).await?;
            return result_of(&reply);
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

    /// A heartbeat for another member's Node, answered by that member.
    /// `heartbeat`, told what it may still do if its Node has moved.
    ///
    /// Routed exactly as `register_routed` is: forwarded to the Node's owner,
    /// and `when_moved` spent by the one retry `forward_heartbeat` makes when
    /// that member no longer owns it.
    async fn heartbeat_routed(
        &self,
        node_id: &str,
        when_moved: WhenMoved,
    ) -> Result<Option<i64>, MutationUnavailable> {
        if let Some(owner) = self.owner_for(node_id)
            && owner != self.index()
        {
            return self.forward_heartbeat(owner, node_id, when_moved).await;
        }
        match self.beat_here(node_id).await? {
            BeatHere::Refreshed(health) => Ok(health),
            BeatHere::OwnedBy(owner) => self.forward_heartbeat(owner, node_id, when_moved).await,
        }
    }

    /// Refresh a Node this member takes to be its own -- or find it is not.
    ///
    /// What both a Node's own heartbeat and a forwarded one do at the member
    /// they reach, so the two cannot drift.
    async fn beat_here(&self, node_id: &str) -> Result<BeatHere, MutationUnavailable> {
        let known = || {
            self.registry
                .with_read_store(|store| store.get(ResourceType::Node, node_id).is_some())
        };
        if !known() {
            // A 404 tells the Node to re-register everything (`Behaviour -
            // Registration.md:112-114`), so it has to be true: see
            // `read_barrier`. Once current, the Node may turn out to be another
            // member's.
            self.read_barrier().await?;
            if let Some(owner) = self.owner_for(node_id)
                && owner != self.index()
            {
                return Ok(BeatHere::OwnedBy(owner));
            }
            if !known() {
                return Ok(BeatHere::Refreshed(None));
            }
        }
        Ok(BeatHere::Refreshed(self.registry.heartbeat(node_id)))
    }

    /// Answer a forwarded heartbeat as its Node's owner, or say it is not.
    ///
    /// Never forwarded on, as a forwarded registration never is (`forward`): a
    /// request that hops between members has no bound on its latency, and two
    /// members whose tables disagree about the owner handed a heartbeat back and
    /// forth until an RPC deadline cut the chain. This was answered by the
    /// member's own `heartbeat`, which forwards.
    async fn heartbeat_forwarded(&self, node_id: &str) -> Result<BeatHere, MutationUnavailable> {
        match self.owner_for(node_id) {
            Some(owner) if owner != self.index() => Ok(BeatHere::OwnedBy(owner)),
            _ => self.beat_here(node_id).await,
        }
    }

    async fn forward_heartbeat(
        &self,
        owner: u64,
        node_id: &str,
        when_moved: WhenMoved,
    ) -> Result<Option<i64>, MutationUnavailable> {
        let Some(reply) = self
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
            .await
        else {
            // No answer is not "no such Node": the owner may well hold it. This
            // used to be a 404 -- a terminal instruction to re-register
            // everything, given because a link was slow.
            return Err(MutationUnavailable(format!(
                "the member owning node {node_id} did not answer",
            )));
        };
        if reply.not_owner {
            // Ownership moved underneath us, as it can for a registration
            // (`register_routed`): one retry, as owner or forwarder depending
            // on where it moved to, and no more -- a table that is behind names
            // the same former owner every time. Read as a plain refusal, as it
            // once was, this was a 404: the terminal "re-register every
            // resource", for a Node the cluster still held.
            return match when_moved {
                WhenMoved::RouteAgain => {
                    Box::pin(self.heartbeat_routed(node_id, WhenMoved::Unavailable)).await
                }
                WhenMoved::Unavailable => Err(MutationUnavailable(format!(
                    "member {owner} no longer owns node {node_id}, and this member has not yet \
                     learned which does",
                ))),
            };
        }
        if !reply.ok {
            if reply.error == "unavailable" {
                return Err(MutationUnavailable(if reply.detail.is_empty() {
                    format!("member {owner} could not answer")
                } else {
                    reply.detail
                }));
            }
            return Ok(None);
        }
        Ok(Some(
            i64::try_from(reply.applied_index).unwrap_or_else(|_| health_now()),
        ))
    }

    /// Bring this member's store up to everything committed when a read began.
    ///
    /// A member answers some requests from its own store -- a refusal from
    /// validation, a 404 for a delete or a heartbeat -- and a store can be
    /// behind what is committed: a follower that has not yet applied, an owner
    /// restarted with nothing. Those answers were given as if the store were
    /// current, and a client acts on them: a Node told 400 must not retry, one
    /// told 404 re-registers everything. The chaos soak counted them in
    /// hundreds per run set, each unjustifiable.
    ///
    /// So before such an answer this member learns a read index -- the commit
    /// index when the read began, confirmed by a quorum that its leader still
    /// leads (etcd's ReadIndex, `RaftNode::read_index`) -- and waits until it
    /// has applied it. The answer it then gives is the one the leader would
    /// have given. Only those answers pay: a registration that commits, and a
    /// heartbeat that finds its Node, pay nothing.
    ///
    /// # Errors
    ///
    /// [`MutationUnavailable`] when no read index can be had in time: a member
    /// that cannot show it is current answers 503, which a Node retries, rather
    /// than a terminal answer it cannot justify.
    async fn read_barrier(&self) -> Result<(), MutationUnavailable> {
        let timeout_ms = self
            .mutation_timeout
            .as_millis()
            .try_into()
            .unwrap_or(u64::MAX);
        let index = self.node.read_index(timeout_ms).await.map_err(|error| {
            MutationUnavailable(format!(
                "this member cannot confirm it is current: {}",
                error.0,
            ))
        })?;
        self.await_applied(index).await
    }

    /// Wait until this member has applied `index`.
    ///
    /// Two waits use it. On the forwarded path, the member that answered the
    /// client is not the member that applied the entry, and a client that
    /// immediately reads back from here would otherwise get a 404 for
    /// something it was just told was created. And a read barrier
    /// (`read_barrier`) waits here for its read index.
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
            return match self.heartbeat_forwarded(&message.resource_id).await {
                Ok(BeatHere::Refreshed(health)) => ForwardReply {
                    ok: health.is_some(),
                    created: false,
                    error: String::new(),
                    detail: String::new(),
                    applied_index: health.unwrap_or(0).max(0).unsigned_abs(),
                    not_owner: false,
                    request_id: message.request_id,
                    owner: None,
                },
                Ok(BeatHere::OwnedBy(owner)) => ForwardReply {
                    ok: false,
                    created: false,
                    error: String::new(),
                    detail: String::new(),
                    applied_index: 0,
                    not_owner: true,
                    request_id: message.request_id,
                    owner: Some(owner),
                },
                // Answered, not dropped into a "no such Node": see the refusal
                // at the end.
                Err(error) => refusal("unavailable", error.0),
            };
        }

        let body = Body::new(message.body_text.clone());
        let node_id = match self.resolve_node(resource_type, body.data()) {
            Ok(node_id) => node_id,
            Err(failure) if decided_by_state(&failure) => {
                // See `register_routed`: this member may be behind as well.
                if let Err(error) = self.read_barrier().await {
                    return refusal("unavailable", error.0);
                }
                match self.resolve_node(resource_type, body.data()) {
                    Ok(node_id) => node_id,
                    Err(failure) => return reply_for(&Err(failure), 0, message.request_id),
                }
            }
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
            // This member owns the Node but could not commit: say so at once,
            // with the reason, under the code every refusal of this kind uses
            // (`node.rs`'s `on_forward`, `transport.rs`'s `application_refusal`)
            // and which the forwarder answers as a 503 -- see `result_of`.
            Err(error) => refusal("unavailable", error.0),
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
        if self.stopping.load(std::sync::atomic::Ordering::SeqCst) || self.node.failure().is_some()
        {
            // A member that stopped itself on a broken invariant is going away
            // (`RaftNode::fail`): its process is about to exit, and until it
            // does, a mutation is answered 503 so the Node retries elsewhere.
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
        self.register_routed(resource_type, body, WhenMoved::RouteAgain)
            .await
    }

    async fn unregister(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Result<Option<Vec<ResourceEvent>>, MutationUnavailable> {
        let present = || {
            self.registry
                .with_read_store(|store| store.get(resource_type, resource_id).is_some())
        };
        if !present() {
            // "Not here" is a guess until this member is current: its store is
            // a complete replica only of what it has applied. See
            // `read_barrier`.
            self.read_barrier().await?;
            if !present() {
                return Ok(None);
            }
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
        self.heartbeat_routed(node_id, WhenMoved::RouteAgain).await
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

/// What a forwarded registration or heartbeat may still do when the member it
/// was forwarded to answers that it no longer owns the Node.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum WhenMoved {
    /// Route it once more, as owner or forwarder depending on where it moved.
    RouteAgain,
    /// It has been routed again already: answer 503, and let the Node retry.
    Unavailable,
}

/// What a heartbeat found at the member it reached (`beat_here`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum BeatHere {
    /// The Node's new health, or `None`: this member does not hold it.
    Refreshed(Option<i64>),
    /// Another member was found to own the Node once this one was current.
    OwnedBy(u64),
}

/// Does this refusal depend on what the store holds?
///
/// Every refusal but a malformed body (`SCHEMA`, `Behaviour -
/// Registration.md:100`), which is decided by the body alone: the others -- an
/// id of another type, an older version, a changed or missing parent
/// (`:101-104`) -- are only as true as the store they were read from.
fn decided_by_state(failure: &RegistrationFailure) -> bool {
    failure.error != RegistrationError::Schema
}

/// What a forwarded registration's reply means to this member's caller.
///
/// The Python `_result_of`, exactly. A reply that is not ok carries either a
/// registration error -- the owner's terminal 400, decided against its replica
/// -- or anything else, which means the owner could not decide at all:
/// `"unavailable"` from a member that could not commit or is at capacity
/// (`node.rs`'s `on_forward`, `transport.rs`'s `application_refusal`), or a
/// code this member does not know. Those are 503s, because a Node retries a 503
/// and MUST NOT retry a 400.
///
/// This once mapped every code it did not know to `Schema`, so an owner that
/// had lost its quorum answered the client **400**: the chaos soak's Status
/// Integrity oracle measured it as `400 ... schema: registration of ... could
/// not commit: lost contact with a quorum`, and a Node told that stops
/// re-registering.
fn result_of(
    reply: &ForwardReply,
) -> Result<Result<Applied, RegistrationFailure>, MutationUnavailable> {
    if reply.ok {
        return Ok(Ok(Applied {
            created: reply.created,
            events: Vec::new(),
        }));
    }
    let Some(error) = RegistrationError::from_code(&reply.error) else {
        return Err(MutationUnavailable(if reply.detail.is_empty() {
            "the owning member refused the registration".to_owned()
        } else {
            reply.detail.clone()
        }));
    };
    Ok(Err(RegistrationFailure::new(error, reply.detail.clone())))
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
