// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! A cluster shape the conformance suite can be written against.
//!
//! Port of `nmos/registry/tests/rigs/protocol.py`. The suite that uses it
//! describes *conditions* -- "quorum has been lost", "the local member is
//! gone" -- rather than actions on a particular backend, so the same tests
//! apply to a distributed backend the day one exists.
//!
//! # Why this lands before the distributed backend does
//!
//! So that the rig and the suite are known good **before** there is a second
//! backend to blame. A conformance failure against a brand-new backend is
//! ambiguous: the backend may be wrong, or the test may be. Running the suite
//! first against [`StandaloneRig`] -- where the answers are not in doubt --
//! removes one of those possibilities in advance. That is the whole reason M8
//! precedes M9 in the plan.
//!
//! At size 1 most of the suite is not meaningful, and those cases must
//! [`Skipped`] **with a reason** rather than pass vacuously. A suite that
//! reported success while exercising nothing would be worse than no suite,
//! because it would also report coverage it does not have.

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
use nmos_registry_backend::{BackendState, RegistryBackend, StandaloneBackend};
use nmos_registry_core::store::RegistryStore;

/// This backend cannot be exercised here.
///
/// A missing binary, an absent certificate set -- conditions where the honest
/// answer is "not installed", not "failed". Callers turn it into a skip.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RigUnavailable(pub String);

impl std::fmt::Display for RigUnavailable {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RigUnavailable {}

/// A conformance case that does not apply to this rig.
///
/// Carries the reason, because "skipped" without one is indistinguishable from
/// "quietly not run".
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Skipped(pub String);

impl std::fmt::Display for Skipped {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// A cluster of `size` members, with a registry in front of each.
#[async_trait]
pub trait ClusterRig: Send + Sync {
    /// How many members: 1, 3 or 5.
    fn size(&self) -> usize;

    /// How many members a write needs.
    ///
    /// Topology, not backend: a majority of `size`, whatever is storing the
    /// data underneath.
    fn quorum(&self) -> usize {
        self.size().div_ceil(2)
    }

    /// How many members may fail while writes still commit.
    fn failures_tolerated(&self) -> usize {
        self.size().saturating_sub(self.quorum())
    }

    /// Bring the storage layer up, if it is separate from the registries.
    ///
    /// A no-op for a backend whose members *are* the registries -- there the
    /// cluster forms when the backends start.
    ///
    /// # Errors
    ///
    /// The rig cannot run here.
    async fn start_all(&self) -> Result<(), RigUnavailable>;

    /// Tear everything down. Safe to call twice.
    async fn stop_all(&self);

    /// A started backend for member `index`.
    ///
    /// # Errors
    ///
    /// The member could not be started.
    async fn backend_for(&self, index: usize) -> Result<Arc<dyn RegistryBackend>, RigUnavailable>;

    /// Member `index` is gone, abruptly.
    ///
    /// Must not return until it is genuinely unreachable, or a test expecting
    /// the next operation to fail may still reach a dying member.
    async fn kill(&self, index: usize);

    /// Take away enough members that no write can commit.
    ///
    /// An intent rather than "kill members 1 and 2", so a test reads as the
    /// condition it is about and a 5-member rig does the right thing without
    /// the test knowing how many that is. **Member 0 is always left standing**,
    /// because that is the one tests attach to.
    async fn lose_quorum(&self);
}

/// One member, one in-memory registry.
///
/// Size 1, so quorum is 1 and nothing is tolerated. That is not a degenerate
/// cluster pretending to be one: it is the honest topology of standalone mode,
/// and stating it lets the suite skip what does not apply rather than invent a
/// result.
#[derive(Debug)]
pub struct StandaloneRig {
    backend: Arc<StandaloneBackend>,
    /// Set by [`ClusterRig::lose_quorum`] and [`ClusterRig::kill`].
    ///
    /// A one-member cluster loses quorum the moment its only member goes, so
    /// the two are the same event here. What must still hold afterwards is that
    /// reads keep working -- which is the half of the conformance case that is
    /// meaningful at this size.
    down: Arc<std::sync::atomic::AtomicBool>,
}

impl Default for StandaloneRig {
    fn default() -> Self {
        Self::new()
    }
}

impl StandaloneRig {
    /// A fresh one-member rig.
    #[must_use]
    pub fn new() -> Self {
        Self {
            backend: Arc::new(StandaloneBackend::new(Arc::new(Registry::new(
                RegistryStore::new(),
            )))),
            down: Arc::new(std::sync::atomic::AtomicBool::new(false)),
        }
    }

    /// The view behind the single member, for assertions about reads.
    #[must_use]
    pub fn registry(&self) -> &Arc<Registry> {
        self.backend.registry()
    }
}

/// The single member, wrapped so losing quorum can be expressed.
///
/// `StandaloneBackend` has no way to fail -- which is correct for it, and
/// exactly why the rig needs this: the conformance suite has to be able to
/// create the condition it is about.
#[derive(Debug)]
struct Interruptible {
    inner: Arc<StandaloneBackend>,
    down: Arc<std::sync::atomic::AtomicBool>,
}

impl Interruptible {
    fn is_down(&self) -> bool {
        self.down.load(std::sync::atomic::Ordering::Relaxed)
    }
}

#[async_trait]
impl RegistryBackend for Interruptible {
    fn state(&self) -> BackendState {
        if self.is_down() {
            // Degraded, not Stopping: the member is unreachable but the view it
            // already had is still valid and still served.
            BackendState::Degraded
        } else {
            self.inner.state()
        }
    }

    fn registry(&self) -> &Arc<Registry> {
        self.inner.registry()
    }

    async fn start(&self) -> Result<(), nmos_registry_backend::MutationUnavailable> {
        self.inner.start().await
    }

    async fn register(
        &self,
        resource_type: nmos_registry_core::resource_type::ResourceType,
        body: nmos_registry_core::body::Body,
    ) -> Result<
        Result<nmos_registry_core::store::Applied, nmos_registry_core::store::RegistrationFailure>,
        nmos_registry_backend::MutationUnavailable,
    > {
        if self.is_down() {
            return Err(nmos_registry_backend::MutationUnavailable(
                "no quorum".to_owned(),
            ));
        }
        self.inner.register(resource_type, body).await
    }

    async fn unregister(
        &self,
        resource_type: nmos_registry_core::resource_type::ResourceType,
        resource_id: &str,
    ) -> Result<
        Option<Vec<nmos_registry_core::event::ResourceEvent>>,
        nmos_registry_backend::MutationUnavailable,
    > {
        if self.is_down() {
            return Err(nmos_registry_backend::MutationUnavailable(
                "no quorum".to_owned(),
            ));
        }
        self.inner.unregister(resource_type, resource_id).await
    }

    async fn heartbeat(
        &self,
        node_id: &str,
    ) -> Result<Option<i64>, nmos_registry_backend::MutationUnavailable> {
        if self.is_down() {
            return Err(nmos_registry_backend::MutationUnavailable(
                "no quorum".to_owned(),
            ));
        }
        self.inner.heartbeat(node_id).await
    }

    async fn collect_garbage(
        &self,
    ) -> Result<
        Vec<nmos_registry_core::event::ResourceEvent>,
        nmos_registry_backend::MutationUnavailable,
    > {
        if self.is_down() {
            return Err(nmos_registry_backend::MutationUnavailable(
                "no quorum".to_owned(),
            ));
        }
        self.inner.collect_garbage().await
    }

    async fn close(&self) {
        self.inner.close().await;
    }
}

#[async_trait]
impl ClusterRig for StandaloneRig {
    fn size(&self) -> usize {
        1
    }

    async fn start_all(&self) -> Result<(), RigUnavailable> {
        // Nothing separate to start: the member *is* the registry.
        Ok(())
    }

    async fn stop_all(&self) {
        self.backend.close().await;
    }

    async fn backend_for(&self, index: usize) -> Result<Arc<dyn RegistryBackend>, RigUnavailable> {
        if index != 0 {
            return Err(RigUnavailable(format!(
                "a standalone rig has one member; asked for index {index}",
            )));
        }
        Ok(Arc::new(Interruptible {
            inner: Arc::clone(&self.backend),
            down: Arc::clone(&self.down),
        }))
    }

    async fn kill(&self, _index: usize) {
        self.down.store(true, std::sync::atomic::Ordering::Relaxed);
    }

    async fn lose_quorum(&self) {
        // One member is its own quorum, so losing it is the same event as
        // killing it. The rig says so rather than pretending otherwise.
        self.down.store(true, std::sync::atomic::Ordering::Relaxed);
    }
}

/// Why a conformance case does not apply to a rig of this size.
///
/// Returned rather than asserted so the caller decides between skipping and
/// failing, and so the reason travels with the decision.
///
/// # Errors
///
/// The rig is smaller than the case needs.
pub fn require_members(rig: &dyn ClusterRig, needed: usize) -> Result<(), Skipped> {
    if rig.size() >= needed {
        return Ok(());
    }
    Err(Skipped(format!(
        "needs {needed} members, this rig has {}",
        rig.size(),
    )))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_standalone_rig_is_one_member_tolerating_nothing() {
        let rig = StandaloneRig::new();
        assert_eq!(rig.size(), 1);
        assert_eq!(rig.quorum(), 1);
        assert_eq!(
            rig.failures_tolerated(),
            0,
            "a one-member cluster tolerates no failures, and saying otherwise \
             would make the conformance suite assert something false",
        );
    }

    #[test]
    fn quorum_is_a_majority_at_every_size() {
        // Topology, not backend. Checked here because the suite asserts against
        // these numbers and a wrong one would make it agree with a broken
        // cluster.
        struct Sized(usize);
        #[async_trait]
        impl ClusterRig for Sized {
            fn size(&self) -> usize {
                self.0
            }
            async fn start_all(&self) -> Result<(), RigUnavailable> {
                Ok(())
            }
            async fn stop_all(&self) {}
            async fn backend_for(
                &self,
                _index: usize,
            ) -> Result<Arc<dyn RegistryBackend>, RigUnavailable> {
                Err(RigUnavailable("shape only".to_owned()))
            }
            async fn kill(&self, _index: usize) {}
            async fn lose_quorum(&self) {}
        }

        for (size, quorum, tolerated) in [(1, 1, 0), (3, 2, 1), (5, 3, 2), (7, 4, 3)] {
            let rig = Sized(size);
            assert_eq!(rig.quorum(), quorum, "quorum at size {size}");
            assert_eq!(
                rig.failures_tolerated(),
                tolerated,
                "tolerance at size {size}",
            );
        }
    }

    #[tokio::test]
    async fn a_member_is_reachable_and_index_one_is_not() {
        let rig = StandaloneRig::new();
        rig.start_all().await.expect("nothing to start");
        assert!(rig.backend_for(0).await.is_ok());
        // `expect_err` would need `dyn RegistryBackend: Debug`, which the
        // trait deliberately does not require of its implementors.
        let Err(missing) = rig.backend_for(1).await else {
            panic!("a standalone rig handed out a second member");
        };
        assert!(missing.to_string().contains("one member"), "{missing}");
    }

    #[tokio::test]
    async fn losing_quorum_stops_writes_but_not_reads() {
        // The one multi-member conformance case that *is* meaningful at size 1,
        // and the reason the rig bothers to model a down member at all.
        let rig = StandaloneRig::new();
        rig.start_all().await.expect("started");
        let backend = rig.backend_for(0).await.expect("member 0");

        let id = "3b8be755-08ff-452b-b217-c9151eb21193";
        backend
            .register(
                nmos_registry_core::resource_type::ResourceType::Node,
                nmos_registry_core::body::Body::from_value(serde_json::json!({
                    "id": id, "version": "100:0", "label": "n",
                })),
            )
            .await
            .expect("available")
            .expect("valid");

        rig.lose_quorum().await;

        let refused = backend
            .register(
                nmos_registry_core::resource_type::ResourceType::Node,
                nmos_registry_core::body::Body::from_value(serde_json::json!({
                    "id": "aad9ed36-bfb9-400a-9890-a85da2e5842b",
                    "version": "101:0", "label": "n2",
                })),
            )
            .await;
        assert!(
            refused.is_err(),
            "a write committed with no quorum, which is the one thing \
             consensus exists to prevent",
        );

        // And the cached view keeps serving.
        assert!(
            backend.state().serves_queries(),
            "reads were refused because writes were impossible",
        );
        assert!(
            rig.registry().heartbeat(id).is_some(),
            "the view lost the resource it had already accepted",
        );
    }

    #[tokio::test]
    async fn stopping_twice_is_safe() {
        let rig = StandaloneRig::new();
        rig.stop_all().await;
        rig.stop_all().await;
    }

    #[test]
    fn a_case_that_needs_more_members_skips_with_its_reason() {
        let rig = StandaloneRig::new();
        assert!(require_members(&rig, 1).is_ok());
        let skipped = require_members(&rig, 3).expect_err("size 1 cannot do this");
        assert!(
            skipped.to_string().contains("needs 3 members"),
            "a skip without a reason is indistinguishable from a case that \
             quietly did not run: {skipped}",
        );
    }
}
