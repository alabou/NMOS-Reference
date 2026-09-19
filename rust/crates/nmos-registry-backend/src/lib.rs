// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! What the Registration API needs from the storage layer.
//!
//! Port of `nmos/registry/backend.py`. The seam exists so a distributed backend
//! can be substituted without the handlers learning anything about consensus,
//! and so the conformance suite can be written against a shape rather than
//! against an implementation.
//!
//! # Why every method is `async` even where nothing awaits
//!
//! [`StandaloneBackend`] completes every call without awaiting. The trait is
//! `async` anyway, because the handler code then reads identically in both
//! modes and cannot accidentally come to depend on standalone's synchrony. A
//! handler written against a synchronous seam would need rewriting the day a
//! backend had to talk to anything, and the rewrite would be the sort nobody
//! notices is incomplete.
//!
//! # Why `#[async_trait]` rather than native async fns in traits
//!
//! The binary chooses standalone or distributed at run time from a command
//! line, so it needs `dyn RegistryBackend`. Native AFIT is not dyn-compatible;
//! `async_trait`'s boxed futures are. The cost is one allocation per call on a
//! path already doing HTTP, which is not where the time goes -- see
//! `plans/20260918T200706Z-rust-port-m7-progress.md`.
//!
//! # The state machine, and the one distinction that matters
//!
//! [`BackendState::serves_queries`] is not the negation of
//! [`BackendState::accepts_mutations`]. A registry that cannot write but can
//! still serve a cached view is useful, and refusing reads because writes are
//! impossible turns a partial outage into a total one. Only `STARTING` --
//! where there is no trustworthy view yet -- withholds reads.

#![forbid(unsafe_code)]
#![cfg_attr(
    test,
    allow(
        clippy::unwrap_used,
        clippy::expect_used,
        clippy::indexing_slicing,
        clippy::panic
    )
)]

use std::sync::Arc;

use async_trait::async_trait;
use nmos_registry::registry::Registry;
use nmos_registry_core::body::Body;
use nmos_registry_core::event::ResourceEvent;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::{Applied, RegistrationFailure};

/// A mutation failed for a reason that is not the client's.
///
/// Distinct from a [`RegistrationFailure`], which is a 400 the Node must not
/// retry. This is the 503 case: the storage layer could not commit within its
/// deadline, or lost quorum part-way through. The body was fine; the registry
/// was not able to act on it right now.
///
/// Lives here rather than in a distributed backend so the handlers can catch it
/// without depending on one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MutationUnavailable(pub String);

impl std::fmt::Display for MutationUnavailable {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for MutationUnavailable {}

/// Lifecycle of the storage layer behind the Registration API.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BackendState {
    /// Not yet consistent.
    ///
    /// Mutations answer 503; Query is not serving a trustworthy view either, so
    /// the registry has not finished coming up.
    #[default]
    Starting,
    /// Fully consistent and accepting mutations.
    Ready,
    /// Cannot mutate -- the storage layer is unreachable or has no quorum --
    /// but the cached Query view remains valid and is still served.
    Degraded,
    /// Rebuilding the local view after a compaction.
    ///
    /// The previous snapshot is still served while the replacement is built off
    /// to the side, so Query never sees an empty or half-loaded store.
    Resyncing,
    /// Shutting down.
    Stopping,
}

impl BackendState {
    /// The wire spelling, matching the Python enum's values.
    #[must_use]
    pub const fn value(self) -> &'static str {
        match self {
            Self::Starting => "starting",
            Self::Ready => "ready",
            Self::Degraded => "degraded",
            Self::Resyncing => "resyncing",
            Self::Stopping => "stopping",
        }
    }

    /// Whether the Registration API may write.
    #[must_use]
    pub const fn accepts_mutations(self) -> bool {
        matches!(self, Self::Ready)
    }

    /// Whether the Query API has a view worth serving.
    ///
    /// Every state except `STARTING`. Deliberately **not** the negation of
    /// [`Self::accepts_mutations`] -- see the module docs.
    #[must_use]
    pub const fn serves_queries(self) -> bool {
        !matches!(self, Self::Starting)
    }
}

/// The storage layer behind the Registration API.
#[async_trait]
pub trait RegistryBackend: Send + Sync {
    /// Where this backend is in its lifecycle.
    fn state(&self) -> BackendState;

    /// The view the Query API reads.
    ///
    /// Shared rather than owned: Query serves from it concurrently with every
    /// mutation, which is the whole point of the store's lock living inside
    /// [`Registry`] rather than around this trait.
    fn registry(&self) -> &Arc<Registry>;

    /// Bring the backend to [`BackendState::Ready`], or fail.
    ///
    /// # Errors
    ///
    /// The storage layer could not be reached or could not be made consistent.
    async fn start(&self) -> Result<(), MutationUnavailable>;

    /// Register or update one resource.
    ///
    /// # Errors
    ///
    /// [`MutationUnavailable`] when the storage layer could not commit. A body
    /// the registry rejects is not an error here -- it comes back as the inner
    /// `Err(RegistrationFailure)`, which is a 400 rather than a 503.
    async fn register(
        &self,
        resource_type: ResourceType,
        body: Body,
    ) -> Result<Result<Applied, RegistrationFailure>, MutationUnavailable>;

    /// Remove one resource and its descendants.
    ///
    /// `None` means it was not there.
    ///
    /// # Errors
    ///
    /// The storage layer could not commit.
    async fn unregister(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Result<Option<Vec<ResourceEvent>>, MutationUnavailable>;

    /// Refresh a Node's liveness, returning the new health.
    ///
    /// `None` means the Node is not registered.
    ///
    /// # Errors
    ///
    /// The storage layer could not commit.
    async fn heartbeat(&self, node_id: &str) -> Result<Option<i64>, MutationUnavailable>;

    /// Run one collection pass, returning the events it produced.
    ///
    /// # Errors
    ///
    /// The storage layer could not commit.
    async fn collect_garbage(&self) -> Result<Vec<ResourceEvent>, MutationUnavailable>;

    /// Stop accepting work.
    async fn close(&self);
}

/// The in-memory registry, behind the async seam.
///
/// Every method completes without awaiting, so the behaviour is what it was
/// before this boundary existed -- which is the point. Standalone mode is not a
/// degraded distributed mode; it is the original registry, and this must not
/// add semantics to it.
#[derive(Debug)]
pub struct StandaloneBackend {
    registry: Arc<Registry>,
    state: std::sync::atomic::AtomicU8,
}

/// `BackendState` as a single atomic, so `state()` needs no lock.
///
/// The handler consults it on every request; a lock there would serialise reads
/// against a transition that happens perhaps twice in a process's life.
const fn encode(state: BackendState) -> u8 {
    match state {
        BackendState::Starting => 0,
        BackendState::Ready => 1,
        BackendState::Degraded => 2,
        BackendState::Resyncing => 3,
        BackendState::Stopping => 4,
    }
}

const fn decode(value: u8) -> BackendState {
    match value {
        1 => BackendState::Ready,
        2 => BackendState::Degraded,
        3 => BackendState::Resyncing,
        4 => BackendState::Stopping,
        // Anything else is the safe reading: not yet consistent.
        _ => BackendState::Starting,
    }
}

impl StandaloneBackend {
    /// Wrap a registry.
    ///
    /// **Ready from construction**, without an explicit `start`. There is
    /// nothing to load and nothing that can be unavailable; reporting
    /// `STARTING` until someone called `start` would make the Registration API
    /// answer 503 to anyone who forgot.
    #[must_use]
    pub fn new(registry: Arc<Registry>) -> Self {
        Self {
            registry,
            state: std::sync::atomic::AtomicU8::new(encode(BackendState::Ready)),
        }
    }
}

#[async_trait]
impl RegistryBackend for StandaloneBackend {
    fn state(&self) -> BackendState {
        decode(self.state.load(std::sync::atomic::Ordering::Relaxed))
    }

    fn registry(&self) -> &Arc<Registry> {
        &self.registry
    }

    async fn start(&self) -> Result<(), MutationUnavailable> {
        self.state.store(
            encode(BackendState::Ready),
            std::sync::atomic::Ordering::Relaxed,
        );
        Ok(())
    }

    async fn register(
        &self,
        resource_type: ResourceType,
        body: Body,
    ) -> Result<Result<Applied, RegistrationFailure>, MutationUnavailable> {
        Ok(self.registry.register(resource_type, body))
    }

    async fn unregister(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Result<Option<Vec<ResourceEvent>>, MutationUnavailable> {
        Ok(self.registry.delete(resource_type, resource_id))
    }

    async fn heartbeat(&self, node_id: &str) -> Result<Option<i64>, MutationUnavailable> {
        Ok(self.registry.heartbeat(node_id))
    }

    async fn collect_garbage(&self) -> Result<Vec<ResourceEvent>, MutationUnavailable> {
        Ok(self.registry.collect_garbage())
    }

    async fn close(&self) {
        self.state.store(
            encode(BackendState::Stopping),
            std::sync::atomic::Ordering::Relaxed,
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use nmos_registry_core::store::RegistryStore;
    use serde_json::json;

    const ALL: [BackendState; 5] = [
        BackendState::Starting,
        BackendState::Ready,
        BackendState::Degraded,
        BackendState::Resyncing,
        BackendState::Stopping,
    ];

    fn backend() -> StandaloneBackend {
        StandaloneBackend::new(Arc::new(Registry::new(RegistryStore::new())))
    }

    #[test]
    fn only_ready_accepts_mutations() {
        for state in ALL {
            assert_eq!(
                state.accepts_mutations(),
                state == BackendState::Ready,
                "{state:?}",
            );
        }
    }

    #[test]
    fn every_state_but_starting_still_serves_queries() {
        // The distinction the whole seam turns on: a registry that cannot write
        // is still worth reading. Making this the negation of
        // `accepts_mutations` would turn every write outage into a read outage.
        for state in ALL {
            assert_eq!(
                state.serves_queries(),
                state != BackendState::Starting,
                "{state:?}",
            );
        }
        assert!(BackendState::Degraded.serves_queries());
        assert!(!BackendState::Degraded.accepts_mutations());
    }

    #[test]
    fn the_state_names_match_the_python_enum() {
        // They reach the client in a 503 body: "registry storage is degraded".
        assert_eq!(
            ALL.map(BackendState::value),
            ["starting", "ready", "degraded", "resyncing", "stopping"],
        );
    }

    #[test]
    fn the_standalone_backend_is_ready_without_start() {
        // Anyone who forgot to call `start` would otherwise get 503 forever.
        assert_eq!(backend().state(), BackendState::Ready);
    }

    #[test]
    fn an_unknown_state_byte_decodes_to_starting() {
        // The safe reading. A corrupted value must not be taken for `Ready`.
        assert_eq!(decode(200), BackendState::Starting);
        for state in ALL {
            assert_eq!(decode(encode(state)), state, "{state:?} did not round-trip");
        }
    }

    #[tokio::test]
    async fn the_standalone_backend_delegates_to_the_registry() {
        let backend = backend();
        let id = "3b8be755-08ff-452b-b217-c9151eb21193";
        let body = Body::from_value(json!({
            "id": id, "version": "100:0", "label": "n",
        }));

        let applied = backend
            .register(ResourceType::Node, body)
            .await
            .expect("the standalone backend never reports unavailable")
            .expect("the body is valid");
        assert!(applied.created);

        // The registry it delegates to is the one it hands to Query.
        assert!(
            backend.registry().heartbeat(id).is_some(),
            "the registered Node is not visible through registry()",
        );

        let health = backend.heartbeat(id).await.expect("available");
        assert!(health.is_some());

        let removed = backend
            .unregister(ResourceType::Node, id)
            .await
            .expect("available");
        assert!(removed.is_some(), "the Node was not removed");
    }

    #[tokio::test]
    async fn a_heartbeat_for_an_unknown_node_is_none_not_an_error() {
        // 404, not 503. The distinction is the whole reason `heartbeat` returns
        // `Result<Option<_>>` rather than flattening the two.
        let health = backend()
            .heartbeat("3b8be755-08ff-452b-b217-c9151eb21193")
            .await
            .expect("an absent Node is not an availability failure");
        assert_eq!(health, None);
    }

    #[tokio::test]
    async fn closing_stops_accepting_mutations() {
        let backend = backend();
        assert!(backend.state().accepts_mutations());
        backend.close().await;
        assert_eq!(backend.state(), BackendState::Stopping);
        assert!(!backend.state().accepts_mutations());
        // And still serves reads while it drains.
        assert!(backend.state().serves_queries());
    }

    #[tokio::test]
    async fn it_is_usable_through_a_trait_object() {
        // The binary picks standalone or distributed at run time, so `dyn` has
        // to work -- which is what forces `#[async_trait]` over native AFIT.
        let backend: Arc<dyn RegistryBackend> = Arc::new(backend());
        assert_eq!(backend.state(), BackendState::Ready);
        assert!(
            backend
                .collect_garbage()
                .await
                .expect("available")
                .is_empty()
        );
    }
}
