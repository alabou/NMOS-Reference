// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Members built exactly as production builds them, on the chaos network.
//!
//! Construction follows `tests/consensus.rs` and `tests/backend.rs`, which
//! follow `nmos-registry-bin/src/distributed.rs`: a `RegistryStore` inside a
//! `Registry`, a `StateMachine` with the member's cursor lane, a `TermStore`
//! on disk, and -- when the run drives the registry rather than consensus
//! alone -- a `RaftRegistryBackend` in front, installed as its own node's
//! forward handler just as `start_all` does there.
//!
//! A restart is a real one: the old member is closed, and a new registry, a
//! new state machine and a new node are built over the **same term file**.
//! The log is gone, the term and vote survive, and the incarnation goes up --
//! which is the whole of what makes this backend's restart story different
//! from Raft's, and the thing the non-voting rejoin exists for.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use nmos_cluster::{Derivation, MemberSpec, derive_cluster};
use nmos_registry::registry::Registry;
use nmos_registry_backend::{BackendState, RegistryBackend};
use nmos_registry_core::store::RegistryStore;
use nmos_registry_raft::backend::RaftRegistryBackend;
use nmos_registry_raft::cluster::{RAFT_FLAVOUR, RaftLayout, derive_raft_layout};
use nmos_registry_raft::cursors::CursorAllocator;
use nmos_registry_raft::machine::StateMachine;
use nmos_registry_raft::node::{ForwardHandler, RaftNode, RaftTiming, Role};
use nmos_registry_raft::persist::TermStore;
use nmos_registry_raft::transport::Transport;

use super::net::ChaosNet;

/// How a cluster is built.
#[derive(Debug, Clone)]
pub struct ClusterConfig {
    /// Members.
    pub size: usize,
    /// Node timings.
    pub timing: RaftTiming,
    /// Front every node with a `RaftRegistryBackend`.
    pub backends: bool,
    /// The backend's commit deadline.
    pub mutation_timeout: Duration,
    /// Store garbage-collection interval, seconds.
    pub gc_interval: i64,
    /// Store tombstone-forget interval, seconds.
    pub forget_interval: i64,
    /// Prefixed to member names, so concurrent runs' log lines never collide.
    pub tag: String,
}

/// One member and everything it owns.
pub struct Member {
    /// Its index in the canonical order.
    pub index: u64,
    /// The consensus node.
    pub node: Arc<RaftNode>,
    /// Its replica of the registry.
    pub registry: Arc<Registry>,
    /// The backend in front, when the run has one.
    pub backend: Option<Arc<RaftRegistryBackend>>,
}

/// A cluster on the chaos network.
pub struct SoakCluster {
    /// How it was built.
    pub config: ClusterConfig,
    /// The members, by index.
    pub members: Vec<Member>,
    /// Member names, by index -- what the node puts in `member=` fields.
    pub names: Vec<String>,
    layouts: Vec<RaftLayout>,
    /// The network they share.
    pub net: Arc<ChaosNet>,
    dir: PathBuf,
}

impl SoakCluster {
    /// Build every member. Nothing starts until [`Self::start`].
    #[must_use]
    pub fn build(config: ClusterConfig, net: Arc<ChaosNet>, dir: PathBuf) -> Self {
        let names: Vec<String> = (0..config.size)
            .map(|index| format!("{}-m{index}", config.tag))
            .collect();
        let specs: Vec<MemberSpec> = (0..config.size)
            .map(|index| MemberSpec {
                host: "127.0.0.1".to_owned(),
                client_port: 2481 + (index as u16) * 2,
                peer_port: 2482 + (index as u16) * 2,
                name: Some(names[index].clone()),
                bind_address: None,
            })
            .collect();
        let layouts: Vec<RaftLayout> = (0..config.size)
            .map(|index| {
                let layout = derive_cluster(
                    &specs,
                    &Derivation {
                        local_host: "127.0.0.1",
                        local_peer_port: Some(2482 + (index as u16) * 2),
                        namespace: "/nmos-soak",
                        tls: false,
                        flavour: RAFT_FLAVOUR,
                    },
                )
                .expect("the soak's own member list is a valid cluster");
                let token = layout.token.clone();
                derive_raft_layout(&layout, token)
            })
            .collect();

        let mut cluster = Self {
            config,
            members: Vec::new(),
            names,
            layouts,
            net,
            dir,
        };
        cluster.members = (0..cluster.config.size)
            .map(|index| cluster.make_member(index))
            .collect();
        cluster
    }

    fn make_member(&self, index: usize) -> Member {
        let registry = Arc::new(Registry::new(RegistryStore::with_intervals(
            self.config.gc_interval,
            self.config.forget_interval,
        )));
        let index_u64 = index as u64;
        let node = RaftNode::new(
            self.layouts[index].clone(),
            self.net.transport(index_u64) as Arc<dyn Transport>,
            TermStore::new(self.dir.join(format!("member-{index}.json"))),
            StateMachine::new(
                index_u64,
                CursorAllocator::new(index_u64).expect("a lane for every soak member"),
            ),
            Arc::clone(&registry),
            self.config.timing,
        )
        // The file every incarnation of this member has written, and only by
        // an atomic replace: a refusal here is a defect in that save, not a
        // fault this soak injects.
        .expect("a term file only this member has written");
        // The network announces this incarnation to peers when the member
        // attaches, as the transport's `HelloAck` does.
        self.net.set_incarnation(index_u64, node.incarnation());
        // Before `start`, so the audit sees this node's first delivery.
        if let Some(audit) = self.net.audit() {
            audit.track(index_u64, &node);
        }
        let backend = self.config.backends.then(|| {
            let backend = RaftRegistryBackend::new(
                Arc::clone(&registry),
                Arc::clone(&node),
                self.config.mutation_timeout,
            );
            node.set_forward_handler(Arc::clone(&backend) as Arc<dyn ForwardHandler>);
            backend
        });
        Member {
            index: index_u64,
            node,
            registry,
            backend,
        }
    }

    /// Start every member, concurrently -- members really do start
    /// independently, and starting them in turn would make each backend wait
    /// out its leader timeout before the next exists.
    pub async fn start(&self) {
        let starts = self.members.iter().map(start_member);
        futures_util::future::join_all(starts).await;
    }

    /// Stop one member and bring it back with an empty log.
    pub async fn restart(&mut self, index: usize) {
        close_member(&self.members[index]).await;
        let replacement = self.make_member(index);
        start_member(&replacement).await;
        self.members[index] = replacement;
    }

    /// Close everything.
    pub async fn close(&self) {
        for member in &self.members {
            close_member(member).await;
        }
    }

    /// Members currently leading.
    #[must_use]
    pub fn leaders(&self) -> Vec<u64> {
        self.members
            .iter()
            .filter(|member| member.node.role() == Role::Leader)
            .map(|member| member.index)
            .collect()
    }

    /// The member a node's `member=` field names.
    #[must_use]
    pub fn index_of(&self, name: &str) -> Option<u64> {
        self.names
            .iter()
            .position(|candidate| candidate == name)
            .map(|at| at as u64)
    }
}

async fn start_member(member: &Member) {
    match member.backend {
        Some(ref backend) => {
            // Not awaited to completion: `start` waits up to the mutation
            // timeout for a leader, and a member restarted into a partition
            // would hold the driver for that long. What must be true before
            // the driver moves on is only that the node is running, which the
            // backend reports by leaving `Starting`.
            let starting = Arc::clone(backend);
            tokio::spawn(async move {
                drop(starting.start().await);
            });
            for _ in 0..10_000 {
                if backend.state() != BackendState::Starting {
                    return;
                }
                tokio::task::yield_now().await;
            }
            // A backend that never left `Starting` is reported by the run's
            // convergence check, which names the member; nothing to add here.
        }
        None => {
            member
                .node
                .start()
                .await
                .expect("the chaos transport starts unconditionally");
        }
    }
}

async fn close_member(member: &Member) {
    match member.backend {
        Some(ref backend) => backend.close().await,
        None => member.node.close().await,
    }
}
