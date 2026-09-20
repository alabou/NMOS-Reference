// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Registry storage backed by an etcd cluster.
//!
//! The port of `nmos/registry/etcd_backend.py`.
//!
//! # The three ideas the whole design rests on
//!
//! **The watch is the only thing that changes the local store -- including for
//! this member's own writes.** A mutation commits through a transaction and is
//! then applied when the watch delivers it. That is what makes a locally
//! originated write need no special casing and no duplicate suppression, which
//! is the failure the legacy dRDS spent an origin-index byte on and still got
//! wrong for deletes.
//!
//! **A Node's whole subtree is one prefix on one lease.** Deleting a Node is
//! one ranged delete; expiry collects the subtree atomically on every member
//! at once; and a heartbeat writes *nothing* to the keyspace, where the legacy
//! design wrote a health key per beat and woke every watcher.
//!
//! **Fast path on success, full fence before reporting any rejection.** An
//! optimistic validation runs against a store that may be behind, so a
//! *failure* it produces may be a lie -- a parent registered a moment ago on
//! another member is not here yet. A 400 is terminal, something the Node "MUST
//! NOT" retry without corrective action (`Behaviour - Registration.md:94`), so
//! a rejection is never returned without first fencing and re-validating.

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicI64, AtomicU64, Ordering};
use std::time::Duration;

use async_trait::async_trait;
use nmos_etcd::channel::{Credentials, EtcdChannelPool, SharedPool, UnaryMethod, parse_endpoints};
use nmos_etcd::generated::etcdserverpb as pb;
use nmos_etcd::kv::{
    EtcdKv, compare_absent, compare_exists, compare_mod, delete_op, delete_prefix_op, first_kv,
    put_op,
};
use nmos_etcd::lease::EtcdLease;
use nmos_etcd::watch::{EtcdWatch, RevisionBatch};
use nmos_etcd::{EtcdError, RangeResult};
use nmos_registry::Registry;
use nmos_registry::fence::RevisionFence;
use nmos_registry_backend::{BackendState, MutationUnavailable, RegistryBackend};
use nmos_registry_core::{
    Applied, RegisteredResource, RegistrationFailure, RegistryStore, ResourceEvent, ResourceType,
    TaiCursor,
};
use parking_lot::Mutex;

use crate::config::EtcdConfig;
use crate::keys::{ENVELOPE_VERSION, Envelope, Namespace, ParsedKey};
use crate::placement::{ParentLookup, Placement, placement_for};

/// Page size for the preload scan.
///
/// Small enough that one response stays well inside etcd's message limits even
/// when every resource is large, big enough that a few thousand resources load
/// in a handful of round trips.
const PRELOAD_PAGE: i64 = 100;

/// How long to wait before retrying a watch that dropped.
///
/// Short, because until it reconnects this member's view is frozen and
/// Registration is DEGRADED.
const WATCH_RETRY_INITIAL: Duration = Duration::from_millis(250);
/// The ceiling that backoff climbs to.
const WATCH_RETRY_MAX: Duration = Duration::from_secs(5);

const STATUS: UnaryMethod = UnaryMethod::new("/etcdserverpb.Maintenance/Status");
const MEMBER_LIST: UnaryMethod = UnaryMethod::new("/etcdserverpb.Cluster/MemberList");

/// The oldest etcd whose behaviour this client depends on.
///
/// Checked as a client RPC rather than in the supervisor so it applies in
/// every mode -- managed, adopted and `--etcdExternal` -- rather than only
/// where this process happened to spawn the binary.
const MINIMUM_ETCD: (u32, u32) = (3, 5);

/// The value stored under an id claim: the key it points at.
///
/// A claim is only meaningful with its target, so the target is what it holds.
/// That is also what lets a stale claim be reclaimed transactionally: the
/// reclaiming write can compare against the very key the claim names.
fn claim_value(key: &[u8]) -> Vec<u8> {
    key.to_vec()
}

/// Network waits on the critical path of one mutation.
///
/// Threaded through the call chain rather than kept on the backend, because
/// several mutations are in flight at once and an instance-level counter would
/// attribute one registration's round trips to whichever finished next.
#[derive(Debug, Default)]
struct Trips(u32);

impl Trips {
    fn add(&mut self) {
        self.0 = self.0.saturating_add(1);
    }
}

/// A cluster that is not the one this registry was configured for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClusterMismatch(pub String);

impl std::fmt::Display for ClusterMismatch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ClusterMismatch {}

/// The local store, as a parent lookup for placement.
struct StoreParents<'a>(&'a RegistryStore);

impl ParentLookup for StoreParents<'_> {
    fn node_of_device(&self, device_id: &str) -> Option<String> {
        self.0
            .get(ResourceType::Device, device_id)
            .and_then(|device| device.parent_id.clone())
    }
}

/// Registry storage backed by an etcd cluster.
pub struct EtcdRegistryBackend {
    registry: Arc<Registry>,
    config: EtcdConfig,
    namespace: Namespace,

    state: Mutex<BackendState>,
    fence: Arc<RevisionFence>,

    pool: Mutex<Option<SharedPool>>,
    stopping: Arc<AtomicBool>,
    watch_task: Mutex<Option<tokio::task::JoinHandle<()>>>,
    /// Which member the watch is pinned to, as an index into the pool's
    /// endpoints. Index 0 is the local member, so the first attempt always
    /// prefers it; this only advances when a connection fails.
    watch_endpoint: Arc<AtomicU64>,

    /// What the watch last told us each key's `mod_revision` is.
    ///
    /// The "believed" state the fast path builds its comparisons from. A stale
    /// entry cannot commit anything wrong -- it simply fails the compare.
    revisions: Arc<Mutex<HashMap<Vec<u8>, i64>>>,
    /// Node id -> lease id.
    ///
    /// Learned from etcd, because every key carries its lease, so a member can
    /// renew and attach to a lease another member granted.
    leases: Arc<Mutex<HashMap<String, i64>>>,
    fast_path: bool,

    /// The revision the last preload or resnapshot installed.
    preload_revision: AtomicI64,

    /// A handle on this backend, for the watch task.
    ///
    /// The trait's `start` takes `&self`, and the watch task must outlive
    /// that call while holding the backend. `Arc::new_cyclic` is what lets
    /// `start` upgrade to an owned handle without the caller having to
    /// remember a second `launch` step -- a step that, forgotten, would
    /// leave a registry serving a snapshot that never moves again.
    me: std::sync::Weak<Self>,
}

impl std::fmt::Debug for EtcdRegistryBackend {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("EtcdRegistryBackend")
            .field("state", &*self.state.lock())
            .field("namespace", &self.namespace.prefix())
            .field("applied", &self.fence.applied())
            .field("fast_path", &self.fast_path)
            .finish_non_exhaustive()
    }
}

impl EtcdRegistryBackend {
    /// Build a backend over `config`, without connecting.
    ///
    /// # Errors
    ///
    /// `ClusterMismatch` when the configured namespace is not a usable key
    /// prefix -- the one thing that can be decided before any I/O.
    pub fn new(registry: Arc<Registry>, config: EtcdConfig) -> Result<Arc<Self>, ClusterMismatch> {
        let namespace = Namespace::new(config.namespace.clone())
            .map_err(|fault| ClusterMismatch(fault.message().to_owned()))?;
        Ok(Arc::new_cyclic(|me| Self {
            me: me.clone(),
            registry,
            config,
            namespace,
            state: Mutex::new(BackendState::Starting),
            fence: Arc::new(RevisionFence::new(0)),
            pool: Mutex::new(None),
            stopping: Arc::new(AtomicBool::new(false)),
            watch_task: Mutex::new(None),
            watch_endpoint: Arc::new(AtomicU64::new(0)),
            revisions: Arc::new(Mutex::new(HashMap::new())),
            leases: Arc::new(Mutex::new(HashMap::new())),
            fast_path: fast_path_enabled(),
            preload_revision: AtomicI64::new(0),
        }))
    }

    /// The key layout this backend writes.
    #[must_use]
    pub const fn namespace(&self) -> &Namespace {
        &self.namespace
    }

    /// How far the local view has been applied.
    #[must_use]
    pub fn applied_revision(&self) -> u64 {
        self.fence.applied()
    }

    fn pool(&self) -> Result<SharedPool, EtcdError> {
        self.pool
            .lock()
            .clone()
            .ok_or_else(|| EtcdError::Other("the etcd backend is not started".to_owned()))
    }

    fn kv(&self) -> Result<EtcdKv, EtcdError> {
        Ok(EtcdKv::new(self.pool()?))
    }

    fn lease(&self) -> Result<EtcdLease, EtcdError> {
        Ok(EtcdLease::new(self.pool()?))
    }

    fn set_state(&self, state: BackendState) {
        let mut current = self.state.lock();
        if *current != state {
            tracing::info!(from = ?*current, to = ?state, "registry: backend state");
            *current = state;
        }
    }

    /// Stop accepting mutations, keep serving the cached Query view.
    fn degrade(&self, reason: &str) {
        let mut current = self.state.lock();
        if matches!(*current, BackendState::Stopping | BackendState::Resyncing) {
            return;
        }
        tracing::error!("registry: {reason}");
        *current = BackendState::Degraded;
    }

    // -----------------------------------------------------------------
    // Start
    // -----------------------------------------------------------------

    async fn connect(&self) -> Result<(), EtcdError> {
        let config = &self.config;
        let connector = if config.tls {
            Some(
                Credentials {
                    trusted_root_ca: config.trusted_root_ca.clone(),
                    certificate: config.certificate.clone(),
                    key: config.key.clone(),
                }
                .connector()?,
            )
        } else {
            None
        };

        let local = format!(
            "{}:{}",
            config.layout.local.host, config.layout.local.client_port,
        );
        let endpoints = parse_endpoints(&config.endpoints, Some(&local))?;
        let pool = Arc::new(EtcdChannelPool::new(
            endpoints,
            connector,
            config.tls.then(|| config.certificate_name.clone()),
            config.rpc_timeout,
        )?);
        *self.pool.lock() = Some(pool);
        Ok(())
    }

    /// Version gate and membership reconciliation, both as client RPCs.
    async fn verify_cluster(&self) -> Result<(), MutationUnavailable> {
        let pool = self.pool().map_err(unavailable)?;

        let status: pb::StatusResponse = pool
            .call(STATUS, pb::StatusRequest {}, None)
            .await
            .map_err(unavailable)?;
        require_supported_version(&status.version).map_err(|exc| MutationUnavailable(exc.0))?;

        let members: pb::MemberListResponse = pool
            .call(
                MEMBER_LIST,
                pb::MemberListRequest { linearizable: true },
                None,
            )
            .await
            .map_err(unavailable)?;
        let names: Vec<String> = members.members.into_iter().map(|m| m.name).collect();
        self.reconcile_members(&names)
            .map_err(|exc| MutationUnavailable(exc.0))
    }

    /// Check the cluster we reached is the cluster we were configured for.
    ///
    /// A client-side check against etcd, not a call to a peer registry --
    /// there is no registry-to-registry channel anywhere in this design.
    ///
    /// What can be checked depends on who named the members, and conflating
    /// the two modes gets this wrong:
    ///
    /// **Managed.** This registry launched its member with a name derived from
    /// the canonical member list, and every peer derived the same list, so the
    /// names must match exactly. A mismatch means this member was launched
    /// against a different cluster than it was configured for, and serving
    /// from it would mean serving another deployment's data.
    ///
    /// **External.** The operator named the members; the names are not ours to
    /// have an opinion about. What *is* still ours is the failure tolerance the
    /// configuration promises: a registry told it is one of three, talking to a
    /// single-member cluster, would advertise resilience it does not have. So
    /// only a cluster *smaller* than configured is refused. A larger one is
    /// merely noted -- more members than expected is more resilient, not less.
    ///
    /// # Errors
    ///
    /// `ClusterMismatch` when this member must not serve from what it reached.
    pub fn reconcile_members(&self, actual: &[String]) -> Result<(), ClusterMismatch> {
        let mut found: Vec<String> = actual.iter().filter(|n| !n.is_empty()).cloned().collect();
        found.sort();
        found.dedup();
        let mut expected: Vec<String> = self
            .config
            .layout
            .members
            .iter()
            .map(|m| m.name.clone())
            .collect();
        expected.sort();
        expected.dedup();

        if !self.config.external {
            if !found.is_empty() && found != expected {
                return Err(ClusterMismatch(format!(
                    "etcd reports members {found:?} but this registry is \
                     configured for {expected:?}. Refusing to serve: the member \
                     set must be identical on every registry.",
                )));
            }
            return Ok(());
        }

        let configured = expected.len();
        if found.len() < configured {
            return Err(ClusterMismatch(format!(
                "--etcdExternal cluster has {} member(s) ({found:?}) but this \
                 registry is configured as one of {configured}, which promises \
                 {} tolerated failure(s). Refusing to advertise resilience the \
                 cluster does not have.",
                found.len(),
                self.config.layout.failures_tolerated(),
            )));
        }
        if found.len() > configured {
            tracing::info!(
                "registry: external etcd cluster has {} member(s), more than \
                 the {configured} configured; tolerating more failures than \
                 promised",
                found.len(),
            );
        }
        Ok(())
    }

    // -----------------------------------------------------------------
    // Preload
    // -----------------------------------------------------------------

    /// Build a complete snapshot at one fixed revision and install it.
    ///
    /// Returns the snapshot revision, which is also where the watch starts (at
    /// `+ 1`) and what the fence is seeded to.
    async fn preload(&self) -> Result<i64, EtcdError> {
        let (revision, candidate, count) = self.read_snapshot().await?;
        self.registry.swap_store(candidate);
        self.preload_revision.store(revision, Ordering::Relaxed);
        tracing::info!("registry: preloaded {count} resource(s) at revision {revision}");
        Ok(revision)
    }

    /// Page the whole namespace at one revision into an off-side store.
    async fn read_snapshot(&self) -> Result<(i64, RegistryStore, usize), EtcdError> {
        let kv = self.kv()?;
        let (gc_interval, forget_interval) = self
            .registry
            .with_read_store(|store| (store.gc_interval(), store.forget_interval()));
        let mut candidate = RegistryStore::with_intervals(gc_interval, forget_interval);

        // (depth, key) so parents are applied before children -- the store
        // enforces referential integrity, so a Sender applied before its
        // Device would be rejected.
        let mut collected: Vec<(u8, ParsedKey, Envelope)> = Vec::new();

        let mut revision = 0_i64;
        let mut start_after: Option<Vec<u8>> = None;
        loop {
            let page: RangeResult = kv
                .range_prefix_at(
                    &self.namespace.root(),
                    revision,
                    PRELOAD_PAGE,
                    start_after.as_deref(),
                    None,
                )
                .await?;
            if revision == 0 {
                // The first response fixes the snapshot revision; every later
                // page reads at exactly this, so a concurrent write cannot
                // make pages overlap or skip.
                revision = page.revision;
            }

            for pair in &page.kvs {
                // Seed the believed revisions from the snapshot -- for every
                // key, claims included -- so the fast path is usable from the
                // first registration rather than only after the first watch
                // event.
                self.revisions
                    .lock()
                    .insert(pair.key.to_vec(), pair.mod_revision);

                let Some(parsed) = self
                    .namespace
                    .parse(&pair.key)
                    .map_err(|fault| EtcdError::Other(fault.message().to_owned()))?
                else {
                    continue; // meta/config and id claims are not materialised
                };
                let envelope = Envelope::decode(&pair.value)
                    .map_err(|fault| EtcdError::Other(fault.message().to_owned()))?;
                check_envelope(&parsed, &envelope).map_err(EtcdError::Other)?;
                if parsed.is_node() && pair.lease != 0 {
                    self.leases
                        .lock()
                        .insert(parsed.node_id.clone(), pair.lease);
                }
                collected.push((parsed.depth(), parsed, envelope));
            }

            if !page.more || page.kvs.is_empty() {
                break;
            }
            start_after = page.kvs.last().map(|pair| pair.key.to_vec());
        }

        collected.sort_by(|a, b| {
            a.0.cmp(&b.0)
                .then_with(|| a.1.resource_id.cmp(&b.1.resource_id))
        });
        let count = collected.len();
        for (_depth, parsed, envelope) in collected {
            apply_envelope(&mut candidate, &parsed, &envelope).map_err(EtcdError::Other)?;
        }

        check_no_orphans(&candidate).map_err(EtcdError::Other)?;
        Ok((revision, candidate, count))
    }

    // -----------------------------------------------------------------
    // Watch
    // -----------------------------------------------------------------

    /// Seed the fence and begin applying revisions.
    ///
    /// The fence is seeded to the *preload* revision, not to zero, and that is
    /// load-bearing: etcd answers a progress request only once the store
    /// revision has reached the watch's start revision, so on a cluster with
    /// no writes since the preload no progress reply ever arrives. A fence at
    /// zero would block the recovery fence for its whole deadline on every
    /// startup.
    fn start_watch(self: &Arc<Self>, revision: i64) {
        self.fence.reset(revision.max(0).unsigned_abs());
        let backend = Arc::clone(self);
        let handle = tokio::spawn(async move {
            backend.watch_loop(revision.saturating_add(1)).await;
        });
        *self.watch_task.lock() = Some(handle);
    }

    /// Apply revisions forever, reconnecting and resnapshotting as needed.
    async fn watch_loop(self: Arc<Self>, start_revision: i64) {
        let mut backoff = WATCH_RETRY_INITIAL;
        let mut next_revision = start_revision;

        while !self.stopping.load(Ordering::Relaxed) {
            match self.watch_once(next_revision).await {
                Ok(()) => {
                    if self.stopping.load(Ordering::Relaxed) {
                        return;
                    }
                    // A clean end of stream is still a disconnection; resume
                    // from the next unapplied revision.
                    next_revision = self.next_unapplied();
                }
                Err(EtcdError::Compacted { message, .. }) => {
                    // The only failure that cannot be fixed by reconnecting:
                    // the history this watch needs is gone, so the view must
                    // be rebuilt.
                    tracing::warn!("registry: {message}; resnapshotting");
                    match self.resnapshot().await {
                        Ok(revision) => next_revision = revision.saturating_add(1),
                        Err(exc) => {
                            self.degrade(&format!("resnapshot failed: {exc}"));
                            next_revision = self.next_unapplied();
                        }
                    }
                    backoff = WATCH_RETRY_INITIAL;
                    continue;
                }
                Err(exc) => {
                    self.degrade(&format!("watch failed: {exc}"));
                    next_revision = self.next_unapplied();
                    self.watch_endpoint.fetch_add(1, Ordering::Relaxed);
                }
            }

            if self.stopping.load(Ordering::Relaxed) {
                return;
            }
            tokio::time::sleep(backoff).await;
            backoff = (backoff.saturating_mul(2)).min(WATCH_RETRY_MAX);
        }
    }

    fn next_unapplied(&self) -> i64 {
        i64::try_from(self.fence.applied())
            .unwrap_or(i64::MAX)
            .saturating_add(1)
    }

    /// One watch connection, applying batches until it ends.
    ///
    /// Rotates to the next member on each attempt. A watch is a long-lived
    /// stream, so it is pinned to one member for its lifetime -- but if that
    /// member is the one that died, retrying it forever would leave this
    /// registry frozen while a perfectly healthy quorum sat next to it. The
    /// local member is index 0 and so is always tried first; rotation only
    /// matters once it has failed.
    async fn watch_once(&self, start_revision: i64) -> Result<(), EtcdError> {
        let pool = self.pool()?;
        let endpoints = pool.endpoints();
        if endpoints.is_empty() {
            return Err(EtcdError::Other("no etcd endpoints".to_owned()));
        }
        let index = usize::try_from(self.watch_endpoint.load(Ordering::Relaxed))
            .unwrap_or(0)
            .checked_rem(endpoints.len())
            .unwrap_or(0);
        let endpoint = endpoints.get(index).cloned().ok_or_else(|| {
            EtcdError::Other("the endpoint rotation went out of range".to_owned())
        })?;

        let watch = EtcdWatch::new(Arc::clone(&pool));
        let mut stream = watch
            .open(
                &self.namespace.root(),
                start_revision,
                Some(&endpoint),
                false,
            )
            .await?;

        let degraded = *self.state.lock() == BackendState::Degraded;
        if degraded {
            // Reconnected: catch the view up before accepting mutations
            // again, so a write is never validated against a stale store.
            self.recovery_fence(self.fence.applied()).await;
            self.set_state(BackendState::Ready);
        }
        // The stream is established, so this endpoint works. Reset the
        // rotation to prefer the local member again on the next reconnect;
        // otherwise one transient failure would permanently exile the
        // co-located member and add a network hop to every change.
        self.watch_endpoint.store(0, Ordering::Relaxed);

        loop {
            if self.stopping.load(Ordering::Relaxed) {
                stream.close();
                return Ok(());
            }
            match stream.next_batch().await? {
                Some(batch) => self.apply_batch(&batch),
                None => return Ok(()),
            }
        }
    }

    /// Apply one complete revision, then advance the fence.
    ///
    /// Synchronous from the first mutation to the last grain queued: the store
    /// documents that no coroutine may observe a half-applied mutation, and a
    /// revision that deletes a Node and its whole subtree has to become
    /// visible as one step. The fence is advanced only afterwards, which is
    /// what makes a waiter that returns able to rely on the change being
    /// visible.
    fn apply_batch(&self, batch: &RevisionBatch) {
        if batch.progress_only() {
            // No events, but the revision is authoritative -- this is what
            // lets a fence advance on a quiet cluster.
            self.advance(batch.revision);
            return;
        }

        let mut additions: Vec<(u8, ParsedKey, Envelope)> = Vec::new();
        let mut removals: Vec<(u8, ParsedKey)> = Vec::new();

        for event in &batch.events {
            let Some(kv) = event.kv.as_ref() else {
                continue;
            };
            let deleted =
                event.r#type == nmos_etcd::generated::mvccpb::event::EventType::Delete as i32;

            // Recorded for EVERY key, including the ones the Query view never
            // materialises: id claims are part of the write set every CAS
            // compares against. Recording them only for materialised
            // resources left the claim permanently believed-absent, so the
            // second write of any resource always failed its compare and fell
            // to the fenced path.
            self.revisions
                .lock()
                .insert(kv.key.to_vec(), if deleted { 0 } else { kv.mod_revision });

            let parsed = match self.namespace.parse(&kv.key) {
                Ok(Some(parsed)) => parsed,
                Ok(None) => continue, // meta/config and id claims
                Err(fault) => {
                    tracing::error!("registry: unparseable key in watch: {fault}");
                    continue;
                }
            };

            if deleted {
                if parsed.is_node() {
                    self.leases.lock().remove(&parsed.node_id);
                }
                removals.push((parsed.depth(), parsed));
            } else {
                if parsed.is_node() && kv.lease != 0 {
                    self.leases.lock().insert(parsed.node_id.clone(), kv.lease);
                }
                match Envelope::decode(&kv.value) {
                    Ok(envelope) => {
                        if let Err(detail) = check_envelope(&parsed, &envelope) {
                            tracing::error!("registry: corrupt envelope in watch: {detail}");
                            continue;
                        }
                        additions.push((parsed.depth(), parsed, envelope));
                    }
                    Err(fault) => {
                        tracing::error!("registry: corrupt envelope in watch: {fault}");
                        continue;
                    }
                }
            }
        }

        // Parents before children: the store enforces referential integrity,
        // and a revision can create a Device and its Senders together.
        additions.sort_by(|a, b| {
            a.0.cmp(&b.0)
                .then_with(|| a.1.resource_id.cmp(&b.1.resource_id))
        });
        // Descendants before ancestors, so a subscriber never sees a parent
        // vanish while its children are still present.
        removals.sort_by_key(|(depth, _)| std::cmp::Reverse(*depth));

        // One critical section for the whole revision: the events are queued
        // in the same acquisition as the mutations that produced them, which
        // is what makes a Node delete and its cascade one visible step.
        self.registry.with_mutation(|store| {
            let mut events: Vec<ResourceEvent> = Vec::new();
            for (_depth, parsed, envelope) in &additions {
                match apply_envelope(store, parsed, envelope) {
                    Ok(mut produced) => events.append(&mut produced),
                    Err(detail) => {
                        tracing::error!("registry: cannot apply {}: {detail}", parsed.resource_id,);
                    }
                }
            }
            for (_depth, parsed) in &removals {
                if let Some(removed) = store.remove_one(parsed.resource_type, &parsed.resource_id) {
                    events.push(removed);
                }
            }
            ((), events)
        });

        self.advance(batch.revision);
    }

    /// Advance the fence, which is what a mutation waits on.
    fn advance(&self, revision: i64) {
        self.fence.advance(revision.max(0).unsigned_abs());
    }

    /// Wait until everything through `revision` is applied locally.
    async fn recovery_fence(&self, revision: u64) {
        if let Err(exc) = self
            .fence
            .wait(revision, self.config.mutation_timeout)
            .await
        {
            tracing::warn!("registry: recovery fence: {exc}");
        }
    }

    /// Rebuild the view after compaction, without ever serving an empty one.
    ///
    /// The replacement is built off to the side and installed in one
    /// assignment, so Query keeps answering from the previous snapshot right
    /// up to the swap. Subscribers are then sent the difference, because they
    /// have been told about the old state and are entitled to a consistent
    /// story about how it became the new one.
    async fn resnapshot(&self) -> Result<i64, EtcdError> {
        self.set_state(BackendState::Resyncing);
        let (revision, candidate, count) = self.read_snapshot().await?;

        let previous = self.registry.swap_store(candidate);
        let diff = self.registry.with_mutation(|store| {
            let events = diff_stores(&previous, store);
            let produced = events.len();
            (produced, events)
        });

        self.fence.reset(revision.max(0).unsigned_abs());
        self.preload_revision.store(revision, Ordering::Relaxed);
        tracing::info!(
            "registry: resnapshotted {count} resource(s) at revision {revision}, \
             {diff} subscription event(s)",
        );
        self.set_state(BackendState::Ready);
        Ok(revision)
    }

    // -----------------------------------------------------------------
    // Mutations
    // -----------------------------------------------------------------

    /// Turn an etcd failure into DEGRADED plus a retryable answer.
    ///
    /// Every mutation funnels through here so the two things that must happen
    /// together always do: the backend stops claiming it can accept writes,
    /// and the caller gets a retryable answer rather than a 500. Doing it per
    /// call site is how one path ends up reporting healthy while another has
    /// already given up.
    ///
    /// `Compacted` is deliberately NOT swallowed: it is the watch loop's to
    /// handle, and absorbing it here would let a mutation proceed against a
    /// view that is about to be rebuilt.
    fn guarded<T>(
        &self,
        what: &str,
        outcome: Result<T, EtcdError>,
    ) -> Result<T, MutationUnavailable> {
        match outcome {
            Ok(value) => Ok(value),
            Err(EtcdError::Compacted { message, .. }) => {
                // Still a 503 to the caller, but without degrading: the watch
                // loop is already rebuilding, and marking DEGRADED here would
                // race its own recovery.
                Err(MutationUnavailable(format!("{what}: {message}")))
            }
            Err(exc) => {
                self.degrade(&format!("{what} failed: {exc}"));
                Err(MutationUnavailable(format!("{what}: {exc}")))
            }
        }
    }

    /// The lease every key in a Node's subtree hangs off.
    ///
    /// TTL is `ceil(--garbageCollectionInterval)` -- 12 s by default, the
    /// interval of `Behaviour - Registration.md:47`. Deliberately not the 15 s
    /// the legacy dRDS used against the same 12 s registry interval, which
    /// left a Node the registry had already collected alive in etcd for
    /// several more seconds.
    ///
    /// Memoised, so only a Node's *first* registration pays the grant.
    async fn ensure_node_lease(&self, node_id: &str, trips: &mut Trips) -> Result<i64, EtcdError> {
        let existing = self.leases.lock().get(node_id).copied().unwrap_or(0);
        if existing != 0 {
            return Ok(existing);
        }
        let ttl = self
            .registry
            .with_read_store(RegistryStore::gc_interval)
            .max(1);
        trips.add();
        let lease = self.lease()?.grant(ttl, None).await?;
        self.leases.lock().insert(node_id.to_owned(), lease.id);
        Ok(lease.id)
    }

    /// Comparisons that make this write safe to commit.
    ///
    /// Identical in both paths -- only where the revisions came from differs.
    /// On the fast path they are what the watch last told us; on the fenced
    /// path they are what a linearizable read just returned.
    fn compare_set(&self, placement: &Placement) -> Vec<pb::Compare> {
        // Both revisions are read out under one acquisition and the guard is
        // dropped before anything is built: the comparison builders allocate,
        // and holding a lock across allocation is a habit worth not forming.
        let (believed, claim_revision) = {
            let revisions = self.revisions.lock();
            (
                revisions.get(&placement.key).copied().unwrap_or(0),
                revisions.get(&placement.claim).copied().unwrap_or(0),
            )
        };

        let mut compares = Vec::with_capacity(3);
        compares.push(if believed != 0 {
            compare_mod(&placement.key, believed)
        } else {
            compare_absent(&placement.key)
        });

        compares.push(if claim_revision != 0 {
            compare_mod(&placement.claim, claim_revision)
        } else {
            compare_absent(&placement.claim)
        });

        if let Some(parent) = placement.parent.as_ref() {
            compares.push(compare_exists(parent));
        }
        compares
    }

    /// The resource and its id claim, both on the Node's lease.
    ///
    /// Attaching to the lease is the whole of distributed garbage collection:
    /// when the Node stops heartbeating, etcd removes every key on it, on
    /// every member, at once.
    fn write_ops(
        &self,
        placement: &Placement,
        body: &nmos_registry_core::Body,
        resource_type: ResourceType,
    ) -> Vec<pb::RequestOp> {
        let (created, updated) = self.cursors_for(placement, resource_type);
        let envelope = Envelope {
            version: ENVELOPE_VERSION,
            resource_type,
            body: body.clone(),
            created,
            updated,
            health: nmos_registry_core::store::health_now(),
        };
        vec![
            put_op(&placement.key, &envelope.encode(), placement.lease),
            put_op(
                &placement.claim,
                &claim_value(&placement.key),
                placement.lease,
            ),
        ]
    }

    /// Authoritative paging cursors for this write.
    ///
    /// `created` is preserved across updates so a resource does not jump to
    /// the top of a creation-ordered page every time it is re-registered.
    /// Uniqueness within a type is what stops paging skipping a record, so a
    /// fresh cursor is taken from the store's allocator rather than from the
    /// bare clock.
    fn cursors_for(
        &self,
        placement: &Placement,
        resource_type: ResourceType,
    ) -> (TaiCursor, TaiCursor) {
        self.registry.with_mutation(|store| {
            let existing = store
                .get_including_tombstoned(resource_type, &placement.resource_id)
                .filter(|resource| resource.extant)
                .map(|resource| resource.created);
            let updated = store.next_cursor(resource_type);
            ((existing.unwrap_or(updated), updated), Vec::new())
        })
    }

    /// Linearizable read of the write set, then wait for the view to match.
    ///
    /// The read gives the revisions the CAS must compare against; the wait is
    /// what makes the subsequent local validation trustworthy.
    async fn read_fence(&self, placement: &Placement, trips: &mut Trips) -> Result<i64, EtcdError> {
        let mut keys = vec![placement.key.clone(), placement.claim.clone()];
        if let Some(parent) = placement.parent.as_ref() {
            keys.push(parent.clone());
        }

        trips.add();
        let read = self.kv()?.read_set(&keys, None).await?;

        {
            let mut revisions = self.revisions.lock();
            for (key, response) in keys.iter().zip(read.responses.iter()) {
                let found = first_kv(response).map_or(0, |kv| kv.mod_revision);
                revisions.insert(key.clone(), found);
            }
        }

        self.fence
            .wait(
                read.revision.max(0).unsigned_abs(),
                self.config.mutation_timeout,
            )
            .await
            .map_err(|exc| EtcdError::Unavailable(exc.to_string()))?;
        Ok(read.revision)
    }

    /// Wait until our own commit has come back through the watch.
    ///
    /// This is what gives read-your-write on the member that answered, and it
    /// is why a locally originated write needs no special handling anywhere
    /// else: it becomes visible by exactly the same path as a remote one.
    ///
    /// Counted as a round trip even though no request is sent: the mutation
    /// cannot answer until the commit has travelled to etcd, been replicated,
    /// and come back down the watch stream.
    async fn await_commit(&self, revision: i64, trips: &mut Trips) -> Result<(), EtcdError> {
        trips.add();
        self.fence
            .wait(revision.max(0).unsigned_abs(), self.config.mutation_timeout)
            .await
            .map_err(|exc| EtcdError::Unavailable(exc.to_string()))
    }

    /// One speculative CAS from believed revisions. `None` means "fall back".
    ///
    /// The compare set is what enforces correctness, so submitting from a
    /// stale belief cannot commit anything wrong -- it simply fails the
    /// compare and returns here as `None`.
    async fn try_fast_path(
        &self,
        resource_type: ResourceType,
        body: &nmos_registry_core::Body,
        placement: &Placement,
        trips: &mut Trips,
    ) -> Result<Option<Applied>, EtcdError> {
        let prepared = self
            .registry
            .with_read_store(|store| store.prepare(resource_type, body.data()));
        let Ok(prepared) = prepared else {
            // Might be a genuine 400, might be staleness. Not ours to answer.
            return Ok(None);
        };

        trips.add();
        let result = self
            .kv()?
            .txn(
                &self.compare_set(placement),
                &self.write_ops(placement, body, resource_type),
                &[],
                None,
            )
            .await?;

        if !result.succeeded {
            return Ok(None);
        }

        self.await_commit(result.revision, trips).await?;
        Ok(Some(Applied {
            created: prepared.creates,
            events: Vec::new(),
        }))
    }

    /// Read, fence, validate, commit -- retrying until the deadline.
    async fn fenced_register(
        &self,
        resource_type: ResourceType,
        body: &nmos_registry_core::Body,
        placement: &Placement,
        deadline: tokio::time::Instant,
        trips: &mut Trips,
    ) -> Result<Result<Applied, RegistrationFailure>, EtcdError> {
        let mut attempt = 0_u32;
        loop {
            attempt = attempt.saturating_add(1);
            self.read_fence(placement, trips).await?;

            // Now validating against a store known to include everything up
            // to that revision, so a rejection here is authoritative and
            // returnable.
            let prepared = self
                .registry
                .with_read_store(|store| store.prepare(resource_type, body.data()));
            let prepared = match prepared {
                Ok(prepared) => prepared,
                Err(failure) => return Ok(Err(failure)),
            };

            trips.add();
            let result = self
                .kv()?
                .txn(
                    &self.compare_set(placement),
                    &self.write_ops(placement, body, resource_type),
                    &[],
                    None,
                )
                .await?;

            if result.succeeded {
                self.await_commit(result.revision, trips).await?;
                return Ok(Ok(Applied {
                    created: prepared.creates,
                    events: Vec::new(),
                }));
            }

            if tokio::time::Instant::now() >= deadline {
                return Err(EtcdError::Unavailable(format!(
                    "registration of {} did not commit within {:.1}s ({attempt} attempt(s))",
                    placement.resource_id,
                    self.config.mutation_timeout.as_secs_f64(),
                )));
            }
            // Someone else committed first; re-read and re-validate rather
            // than re-submitting the same comparisons, which would fail
            // identically.
            tokio::task::yield_now().await;
        }
    }

    /// Delete a resource and, for a Node or Device, its whole subtree.
    ///
    /// The cascade is a single ranged delete rather than a walk, which is the
    /// payoff for keeping a Node's subtree under one prefix. Every key that
    /// goes produces a watch event, so the local store learns about each one
    /// individually and emits removals descendants-first.
    async fn unregister_inner(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
        trips: &mut Trips,
    ) -> Result<Option<Vec<ResourceEvent>>, EtcdError> {
        let leases = Arc::clone(&self.leases);
        let placement = self.registry.with_read_store(|store| {
            let resource = store.get(resource_type, resource_id)?;
            placement_for(
                &self.namespace,
                resource_type,
                resource.body.data(),
                |node| leases.lock().get(node).copied().unwrap_or(0),
                &StoreParents(store),
            )
            .ok()
        });
        let Some(placement) = placement else {
            return Ok(None);
        };

        let mut ops = Vec::with_capacity(2);
        match resource_type {
            ResourceType::Node => {
                ops.push(delete_prefix_op(&self.namespace.node_subtree(resource_id)));
            }
            ResourceType::Device => {
                ops.push(delete_prefix_op(
                    &self
                        .namespace
                        .device_subtree(&placement.node_id, resource_id),
                ));
            }
            _ => ops.push(delete_op(&placement.key, None)),
        }
        // This resource's OWN claim goes with it. A Node's descendants keep
        // theirs until the lease expires: the ranged delete above covers the
        // subtree, and the claims are flat -- deliberately, because the tree
        // is keyed by *where* a resource is and the claim answers whether an
        // id exists *anywhere*.
        //
        // Not unbounded, and not worth a revoke to avoid: the claims hang off
        // the Node's lease, so etcd collects them within one
        // `--garbageCollectionInterval`, and the alternative is the hazard
        // described below. Gathering the descendants from the local store and
        // deleting their claims in this same transaction would remove the
        // litter without touching the lease, if the diagnostic noise ever
        // matters more than the extra operations per delete.
        ops.push(delete_op(&placement.claim, None));

        trips.add();
        let result = self.kv()?.txn(&[], &ops, &[], None).await?;
        self.await_commit(result.revision, trips).await?;

        // The Node's lease is deliberately NOT revoked here. It is left to
        // expire, and the local table drops it when the watch delivers the
        // deletion -- which has already happened by the time `await_commit`
        // returns, since that is exactly what it waited for.
        //
        // This looks like an omission and is not. A lease revoke is
        // **unconditional**: it removes every key attached to the lease,
        // whenever that key was written. Another member can still be writing
        // to this lease after the transaction above commits, because
        // `ensure_node_lease` reuses the cached id without revalidating it and
        // a member only drops that entry when ITS OWN watch applies the
        // deletion -- strictly later than this one's commit.
        //
        // `Behaviour - Registration.md:112-114` actively drives that sequence:
        // a Node whose heartbeat answers 404 re-registers everything, and that
        // registration lands on whichever member it is talking to. If that
        // member is still behind, it writes onto this lease. Revoking here
        // would then delete a registration that had just been accepted.
        //
        // Expiry has no such hazard, and the difference was measured rather
        // than reasoned about:
        //
        // * a `put` with a new lease re-attaches the key, so this lease
        //   expiring cannot take a later re-registration with it;
        // * a re-registration that reuses this lease renews it, so the keys
        //   survive for as long as something is heartbeating them.
        //
        // What deferring costs is litter: any descendant's id claim outlives
        // the resource it named until the lease expires -- one
        // `--garbageCollectionInterval`, 12 s by default. It blocks nothing.
        // An id cannot be re-registered under a different type during that
        // window either way, and that refusal comes from the local store's
        // tombstone, not from the claim: standalone mode, with no etcd at all,
        // refuses it identically until `--forgetInterval` elapses.
        //
        // The events come from the watch, not from here: every member learns
        // about the delete by the same path, including this one.
        Ok(Some(Vec::new()))
    }

    /// Renew a Node's lease. `None` means it is gone.
    ///
    /// No full-database fence and, deliberately, **no write**. The lease is
    /// the liveness record. The legacy dRDS wrote a health key on every beat
    /// and every member watched it -- 100 Nodes at the 5 s default is 100 Raft
    /// writes per second fanning out to 500 watch events per second across
    /// five members, to record something the lease already records more
    /// reliably, since a lease cannot be renewed by a member that has lost
    /// quorum.
    async fn heartbeat_inner(
        &self,
        node_id: &str,
        trips: &mut Trips,
    ) -> Result<Option<i64>, EtcdError> {
        let lease_id = self.leases.lock().get(node_id).copied().unwrap_or(0);
        if lease_id == 0 {
            // Not ours to renew, and answered without touching the network --
            // the 404 that makes the Node re-register.
            return Ok(None);
        }

        trips.add();
        match self.lease()?.keepalive_once(lease_id, None).await {
            Ok(_ttl) => {}
            Err(EtcdError::LeaseNotFound(_)) => {
                // Authoritative: the cluster has collected this Node.
                // Answering 404 is what makes the Node re-register everything
                // in order, per `Behaviour - Registration.md:112-114`.
                self.leases.lock().remove(node_id);
                return Ok(None);
            }
            Err(other) => return Err(other),
        }

        let health = nmos_registry_core::store::health_now();
        // Local diagnostic health only. It must never drive collection: that
        // is the lease's job, and a member with a slow clock reviving
        // resources its peers had collected is exactly the divergence this
        // design removes.
        self.registry.heartbeat(node_id);
        Ok(Some(health))
    }
}

/// Whether the speculative CAS is attempted.
///
/// Off via `NMOS_ETCD_FAST_PATH=0`. An environment variable rather than a flag
/// because it is a measurement and diagnosis switch, not a deployment choice:
/// the benchmark runs the same workload with it forced on and forced off, and
/// if the gap is not roughly the one round trip it is supposed to save, the
/// optimisation is not earning its complexity.
///
/// Turning it off is always *safe* -- it only means every mutation takes the
/// fenced path, which is the path a fast-path miss falls back to anyway.
fn fast_path_enabled() -> bool {
    !matches!(
        std::env::var("NMOS_ETCD_FAST_PATH")
            .unwrap_or_else(|_| "1".to_owned())
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "0" | "false" | "no" | "off",
    )
}

fn unavailable(exc: EtcdError) -> MutationUnavailable {
    MutationUnavailable(exc.message().to_owned())
}

/// The oldest etcd whose behaviour this client depends on.
///
/// # Errors
///
/// `ClusterMismatch` naming what was found and what is needed.
pub fn require_supported_version(version: &str) -> Result<(), ClusterMismatch> {
    let mut parts = version.split('.');
    let major: u32 = parts.next().and_then(|p| p.parse().ok()).unwrap_or(0);
    let minor: u32 = parts.next().and_then(|p| p.parse().ok()).unwrap_or(0);
    if (major, minor) < MINIMUM_ETCD {
        return Err(ClusterMismatch(format!(
            "etcd {version} is older than the {}.{} this registry requires",
            MINIMUM_ETCD.0, MINIMUM_ETCD.1,
        )));
    }
    Ok(())
}

/// The key and the value must agree about what this resource is.
///
/// They are written in one transaction, so disagreement means corruption or a
/// bug, never a race -- and serving either half of a contradiction would put
/// this member permanently out of step with its peers.
fn check_envelope(parsed: &ParsedKey, envelope: &Envelope) -> Result<(), String> {
    if envelope.resource_type != parsed.resource_type {
        return Err(format!(
            "{}: key says {}, envelope says {}",
            parsed.resource_id, parsed.resource_type, envelope.resource_type,
        ));
    }
    let body_id = envelope.raw().get("id").and_then(serde_json::Value::as_str);
    if body_id != Some(parsed.resource_id.as_str()) {
        return Err(format!(
            "key names {} but the resource body has id {}",
            parsed.resource_id,
            nmos_json::py_repr(envelope.raw().get("id")),
        ));
    }
    Ok(())
}

/// Insert one resource, carrying etcd's authoritative cursors.
///
/// Deliberately does **not** re-decode the body against its generated type.
/// Every resource in the namespace was schema-validated at the Registration
/// API of whichever member accepted it, so a decode here would be
/// re-validating our own storage -- work that produces an object nothing reads
/// and could only ever report a failure the registry has no way to repair.
/// What keeps the namespace trustworthy is the restriction on who may write to
/// it at all (mTLS plus etcd's allowed-hostname check), not a parse after the
/// fact.
///
/// The structural checks that *are* state-dependent -- parent exists, id not
/// claimed by another type, version monotonic -- still run, via `prepare`.
fn apply_envelope(
    store: &mut RegistryStore,
    parsed: &ParsedKey,
    envelope: &Envelope,
) -> Result<Vec<ResourceEvent>, String> {
    let prepared = store
        .prepare(envelope.resource_type, envelope.raw())
        .map_err(|failure| {
            format!(
                "{} ({}) is not valid against the snapshot: {}",
                parsed.resource_id, envelope.resource_type, failure.detail,
            )
        })?;
    let applied = store.apply_committed(
        &prepared,
        envelope.body.clone(),
        Some(envelope.created),
        Some(envelope.updated),
        Some(envelope.health),
    );
    Ok(applied.events)
}

/// Every non-Node resource must have found its parent.
///
/// `prepare` already refuses a resource whose parent is absent, so this is
/// belt and braces -- but a snapshot that quietly dropped a subtree is the
/// kind of failure that shows up days later as "the Controller cannot see that
/// sender", and it costs one pass to rule out.
fn check_no_orphans(store: &RegistryStore) -> Result<(), String> {
    for resource_type in ResourceType::ALL {
        if resource_type == ResourceType::Node {
            continue;
        }
        for resource in store.iter_extant(resource_type) {
            if resource.parent_id.is_none() {
                return Err(format!(
                    "{resource_type} {} has no parent after preload",
                    resource.id,
                ));
            }
        }
    }
    Ok(())
}

/// The events that take a subscriber from `previous` to `current`.
///
/// Subscribers were told about the old state and are entitled to a consistent
/// story about how it became the new one, so a resnapshot publishes the
/// difference rather than silently swapping the world underneath them.
fn diff_stores(previous: &RegistryStore, current: &RegistryStore) -> Vec<ResourceEvent> {
    let mut events = Vec::new();
    for resource_type in ResourceType::ALL {
        let before: HashMap<&str, &RegisteredResource> = previous
            .iter_extant(resource_type)
            .map(|resource| (resource.id.as_str(), resource))
            .collect();
        let after: HashMap<&str, &RegisteredResource> = current
            .iter_extant(resource_type)
            .map(|resource| (resource.id.as_str(), resource))
            .collect();

        for (id, resource) in &after {
            match before.get(id) {
                None => events.push(ResourceEvent::added(resource)),
                Some(old) if old.body != resource.body => {
                    events.push(ResourceEvent::modified(old.body.clone(), resource));
                }
                Some(_) => {}
            }
        }
        for (id, resource) in &before {
            if !after.contains_key(id) {
                events.push(ResourceEvent::removed(resource));
            }
        }
    }
    events
}

#[async_trait]
impl RegistryBackend for EtcdRegistryBackend {
    fn state(&self) -> BackendState {
        *self.state.lock()
    }

    fn registry(&self) -> &Arc<Registry> {
        &self.registry
    }

    async fn start(&self) -> Result<(), MutationUnavailable> {
        self.connect().await.map_err(unavailable)?;
        self.verify_cluster().await?;
        let revision = self.preload().await.map_err(unavailable)?;

        let me = self.me.upgrade().ok_or_else(|| {
            MutationUnavailable("the etcd backend was dropped while starting".to_owned())
        })?;
        me.start_watch(revision);
        // Wait for the view to catch up before declaring READY, so the first
        // mutation is never validated against a store the watch has not yet
        // reached.
        self.recovery_fence(revision.max(0).unsigned_abs()).await;
        self.set_state(BackendState::Ready);
        Ok(())
    }

    async fn register(
        &self,
        resource_type: ResourceType,
        body: nmos_registry_core::Body,
    ) -> Result<Result<Applied, RegistrationFailure>, MutationUnavailable> {
        let deadline = tokio::time::Instant::now()
            .checked_add(self.config.mutation_timeout)
            .unwrap_or_else(tokio::time::Instant::now);

        let placement = {
            let leases = Arc::clone(&self.leases);
            self.registry.with_read_store(|store| {
                placement_for(
                    &self.namespace,
                    resource_type,
                    body.data(),
                    |node| leases.lock().get(node).copied().unwrap_or(0),
                    &StoreParents(store),
                )
            })
        };
        let mut placement = match placement {
            Ok(placement) => placement,
            // Decided locally, so it cost nothing on the wire.
            Err(failure) => return Ok(Err(failure)),
        };

        let mut trips = Trips::default();

        if resource_type == ResourceType::Node {
            // Every key in this Node's subtree will hang off this lease, so it
            // has to exist before the first write. Children reuse whatever the
            // Node's lease already is; if it is not known here yet, the parent
            // check has already failed and the fenced path re-decides.
            let lease = self.ensure_node_lease(&placement.node_id, &mut trips).await;
            let lease = self.guarded("lease grant", lease)?;
            placement = placement.with_lease(lease);
        }

        if self.fast_path {
            let attempt = self
                .try_fast_path(resource_type, &body, &placement, &mut trips)
                .await;
            if let Some(applied) = self.guarded(
                &format!("registration of {}", placement.resource_id),
                attempt,
            )? {
                return Ok(Ok(applied));
            }
        }

        let outcome = self
            .fenced_register(resource_type, &body, &placement, deadline, &mut trips)
            .await;
        self.guarded(
            &format!("registration of {}", placement.resource_id),
            outcome,
        )
    }

    async fn unregister(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Result<Option<Vec<ResourceEvent>>, MutationUnavailable> {
        let mut trips = Trips::default();
        let outcome = self
            .unregister_inner(resource_type, resource_id, &mut trips)
            .await;
        self.guarded(&format!("delete of {resource_id}"), outcome)
    }

    async fn heartbeat(&self, node_id: &str) -> Result<Option<i64>, MutationUnavailable> {
        let mut trips = Trips::default();
        let outcome = self.heartbeat_inner(node_id, &mut trips).await;
        self.guarded(&format!("heartbeat of {node_id}"), outcome)
    }

    /// Stage two only: forget tombstones, never expire live resources.
    ///
    /// **Expiry stays disabled.** A Node's liveness is an etcd lease. If every
    /// member also ran health-based expiry they could disagree about which
    /// Nodes are alive, and the member with the slowest clock would resurrect
    /// resources the others had collected.
    ///
    /// **Forgetting must not be disabled with it.** `remove_one` only marks a
    /// resource non-extant, so without stage two every deleted resource leaves
    /// a permanent record -- memory grows without bound, the status line's
    /// non-extant count never falls, and an id that was deleted can never be
    /// registered again under a different type.
    ///
    /// Stage two is safe to run locally and unilaterally: it drops records
    /// that are *already* non-extant, so it cannot resurrect anything, cannot
    /// remove anything a peer still considers live, and emits no grains.
    ///
    /// Returns no events, and that is not a leftover: the number this reports
    /// is the *expiry* count, and stage two produces none.
    async fn collect_garbage(&self) -> Result<Vec<ResourceEvent>, MutationUnavailable> {
        self.registry.with_mutation(|store| {
            for (resource_type, resource_id) in store.forgettable(None) {
                store.forget(resource_type, &resource_id);
            }
            ((), Vec::new())
        });
        Ok(Vec::new())
    }

    async fn close(&self) {
        self.stopping.store(true, Ordering::Relaxed);
        self.set_state(BackendState::Stopping);

        let task = self.watch_task.lock().take();
        if let Some(handle) = task {
            handle.abort();
            drop(handle.await);
        }
        *self.pool.lock() = None;
    }
}
