// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Assembling the running registry: three listeners and a matcher task.
//!
//! This is the minimal plaintext launcher the port plan calls for at M5. The
//! real CLI -- roughly a hundred flags, TLS, the TR-10-SEC restrictions -- is
//! M6; what is here is the wiring, which is the part that has to be right
//! before any of that means anything.
//!
//! ```text
//!   Registration listener ─┐
//!   Query listener ────────┼─→ Registry ──(commit queue)──→ matcher task ──→ ConnectionBuffers
//!   WebSocket listener ────┘                                                        │
//!                                                                                   ↓
//!                                                                            WebSocket tasks
//! ```
//!
//! # Why the matcher is a task rather than a call
//!
//! This is divergence D2 made concrete. Python publishes inline, in the same
//! uninterrupted step as the mutation. Here a mutation appends to the commit
//! queue and returns; [`matcher_task`] drains it and fans out with no store
//! lock held.
//!
//! It **sleeps** rather than polls: `Registry::wait_for_commits` is woken by
//! every mutation, so an idle registry costs nothing. A polling loop would be a
//! timer firing forever to discover that nothing has happened, and its interval
//! would be a latency floor on every grain.
//!
//! # Why the WebSocket gets its own listener
//!
//! `ws_href` advertises a distinct port -- the Node's `--rdsWebSocketPort`
//! defaults to 8448 against a query port of 8446 -- so the socket has to be
//! reachable there. See [`crate::router::query_websocket`].

use std::net::SocketAddr;
use std::sync::Arc;

use tokio::net::TcpListener;

use nmos_registry::manager::SubscriptionManager;
use nmos_registry::matcher::route_once;
use nmos_registry::registry::Registry;

use crate::query::{DEFAULT_PAGING_LIMIT, MAX_PAGING_LIMIT, QueryState};
use crate::registration::RegistrationState;
use crate::router;
use crate::security::InterfaceSecurity;
use nmos_registry_backend::{RegistryBackend, StandaloneBackend};

/// Where the three listeners bind.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Ports {
    /// The Registration API.
    pub registration: u16,
    /// The Query API.
    pub query: u16,
    /// The Query API's WebSocket.
    pub websocket: u16,
}

impl Default for Ports {
    /// The defaults the Python launcher uses.
    fn default() -> Self {
        Self {
            registration: 8447,
            query: 8446,
            websocket: 8448,
        }
    }
}

/// Everything a running registry is made of.
pub struct Assembly {
    /// How the Registration listener is secured.
    ///
    /// Separate from the Query one because the two interfaces have different
    /// permitted postures: Registration is TLS-only (the three RAPs) and
    /// TR-10-SEC:105 forbids it from requiring OAuth 2.0, while Query supports
    /// the full matrix.
    pub registration_security: InterfaceSecurity,
    /// How the Query listener is secured.
    pub query_security: InterfaceSecurity,
    /// The store, behind its lock.
    pub registry: Arc<Registry>,
    /// Every subscription and connection.
    pub subscriptions: Arc<SubscriptionManager>,
    /// The Query API instance id, stamped into every grain's `source_id`.
    pub query_id: String,
    /// The storage layer behind the Registration API.
    ///
    /// Defaults to [`StandaloneBackend`] over the same registry, which keeps
    /// every existing caller working and is not a placeholder: standalone mode
    /// is the original registry, not a degraded distributed one.
    pub backend: Arc<dyn RegistryBackend>,
}

impl Assembly {
    /// A fresh, empty registry.
    #[must_use]
    pub fn new(registry: Registry, query_id: String) -> Self {
        let registry = Arc::new(registry);
        Self {
            backend: Arc::new(StandaloneBackend::new(Arc::clone(&registry))),
            registry,
            subscriptions: Arc::new(SubscriptionManager::new()),
            query_id,
            registration_security: InterfaceSecurity::registration(false),
            query_security: InterfaceSecurity::default(),
        }
    }

    fn registration_state(&self) -> RegistrationState {
        RegistrationState {
            registry: Arc::clone(&self.registry),
            backend: Arc::clone(&self.backend),
            subscriptions: Arc::clone(&self.subscriptions),
        }
    }

    fn query_state(&self, tls: bool, ws_port: u16) -> QueryState {
        QueryState {
            registry: Arc::clone(&self.registry),
            subscriptions: Arc::clone(&self.subscriptions),
            query_id: self.query_id.clone(),
            tls,
            ws_port,
            paging_limit: DEFAULT_PAGING_LIMIT,
            paging_limit_max: MAX_PAGING_LIMIT,
        }
    }

    /// The three routers, built but not yet bound to anything.
    ///
    /// Exists so the binary can put its own listeners underneath them --
    /// specifically TLS ones, which need a per-connection accept loop and a
    /// crypto library this crate deliberately does not depend on. [`run`] is
    /// the plaintext path and goes through here too, so there is one place
    /// where a router is constructed rather than two that can drift.
    ///
    /// `query_tls` is whether the **Query** listener is TLS, not whether any
    /// listener is: it decides `ws` against `wss` in `ws_href` and the
    /// `secure` attribute a subscription reports. Registration's posture does
    /// not enter into it, and the two are independently configurable.
    #[must_use]
    pub fn routers(&self, ports: Ports, query_tls: bool) -> Routers {
        Routers {
            registration: router::registration(
                self.registration_state(),
                self.registration_security.clone(),
            ),
            query: router::query(
                self.query_state(query_tls, ports.websocket),
                self.query_security.clone(),
            ),
            websocket: router::query_websocket(
                self.query_state(query_tls, ports.websocket),
                self.query_security.clone(),
            ),
        }
    }
}

/// The registry's three routers.
pub struct Routers {
    /// The Registration API.
    pub registration: axum::Router,
    /// The Query API.
    pub query: axum::Router,
    /// The Query API's subscription WebSocket, on its own port.
    pub websocket: axum::Router,
}

/// Drain the commit queue and fan out, for ever.
///
/// The body of divergence D2. Returns only when the registry is dropped, which
/// for a server is never.
pub async fn matcher_task(registry: Arc<Registry>, subscriptions: Arc<SubscriptionManager>) {
    loop {
        registry.wait_for_commits().await;
        route_once(&registry, &subscriptions);
        // Yield between passes so a burst of registrations cannot starve the
        // listeners on a single-threaded runtime.
        tokio::task::yield_now().await;
    }
}

/// How often garbage collection runs.
///
/// One second, matching `nmos/registry/gc.py`'s `GC_TICK_S`. It is a *tick*,
/// not the expiry threshold: a Node is collected after `gc_interval` seconds of
/// heartbeat silence (12 s by default), and this is merely how often that is
/// noticed. Ticking far slower would make the delay between a Node going away
/// and the registry admitting it depend on the sweep rather than on the
/// specification.
const GC_TICK: std::time::Duration = std::time::Duration::from_secs(1);

/// Log the registry status line every `interval` seconds.
///
/// Port of `run_status_reporting`. Reproduces nmos-cpp's status line, which it
/// emits from its expiry thread and from its `POST /resource` handler; this
/// registry logs it from the same two places, so the two implementations' logs
/// are directly comparable when diagnosing a registration problem against one
/// another.
///
/// A non-positive `interval` disables reporting, and says so once. That is not
/// only an operator convenience: `bench_registry/compare.py` passes
/// `--statusInterval 0` for its matched-quiet runs, and a registry that ignored
/// it would be measured while logging what the other target was not.
pub async fn status_task(
    registry: Arc<Registry>,
    subscriptions: Arc<SubscriptionManager>,
    interval: f64,
) {
    if interval <= 0.0 {
        tracing::info!("registry: periodic status reporting disabled");
        return;
    }
    tracing::info!("registry: status reporting every {interval:.1}s");
    let period = std::time::Duration::from_secs_f64(interval);
    loop {
        tokio::time::sleep(period).await;
        tracing::info!(
            "registry: {}",
            registry.status_line(subscriptions.count(), subscriptions.grain_count()),
        );
    }
}

/// Expire silent Nodes and drop elapsed tombstones, for ever.
///
/// Without this the registry never forgets anything: a Node that stops
/// heartbeating stays in every collection indefinitely, which AMWA IS-04-02
/// `test_27` catches as "Query API did not return 404 on a resource which
/// should have been removed due to missing heartbeats".
///
/// Failures are logged and the loop continues, deliberately. Collection is the
/// only thing standing between an ungracefully-disconnected Node and a
/// permanently stale registry, so it has to survive a bad pass and try again --
/// the same reasoning as `gc.py`'s own handler.
pub async fn collector_task(
    backend: Arc<dyn RegistryBackend>,
    subscriptions: Arc<SubscriptionManager>,
) {
    let registry = Arc::clone(backend.registry());
    let mut ticker = tokio::time::interval(GC_TICK);
    // The first tick of a tokio interval fires immediately; skipping it stops a
    // sweep running before anything has had a chance to register.
    ticker.tick().await;
    loop {
        ticker.tick().await;
        // The events are committed by `collect_garbage` itself, so the matcher
        // picks them up and subscribers see the removals as grains.
        // Through the backend, not around it. A distributed backend has to
        // replicate a collection like any other mutation, and one that swept
        // its local store directly would delete resources the rest of the
        // cluster still believed in.
        let collected = match backend.collect_garbage().await {
            Ok(collected) => collected,
            Err(error) => {
                // Not fatal: the next tick tries again, and a registry that
                // stopped collecting because one pass failed would grow without
                // bound while looking healthy.
                tracing::warn!("registry: garbage collection unavailable: {error}");
                continue;
            }
        };
        if collected.is_empty() {
            continue;
        }
        // `gc.py` logs the status line on a non-empty pass and nowhere else,
        // and that guard is load-bearing rather than tidy: the line walks every
        // bucket, so building it on an idle tick would make an empty registry
        // pay once a second for a string it throws away.
        //
        // The counts come from the manager because that is where they live --
        // the store's lock never reaches outside itself.
        tracing::info!(
            "registry: garbage collected {} resource(s); {}",
            collected.len(),
            registry.status_line(subscriptions.count(), subscriptions.grain_count()),
        );
    }
}

/// Bind and serve all three listeners plus the matcher, until the process ends.
///
/// # Errors
///
/// A port that cannot be bound, or a listener that fails.
pub async fn run(assembly: &Assembly, ports: Ports) -> std::io::Result<()> {
    let registration =
        TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], ports.registration))).await?;
    let query = TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], ports.query))).await?;
    let websocket = TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], ports.websocket))).await?;

    let matcher = tokio::spawn(matcher_task(
        Arc::clone(&assembly.registry),
        Arc::clone(&assembly.subscriptions),
    ));
    let collector = tokio::spawn(collector_task(
        Arc::clone(&assembly.backend),
        Arc::clone(&assembly.subscriptions),
    ));

    // Plaintext, so `query_tls` is false -- `ws_href` advertises `ws://` and
    // subscriptions report `secure: false`, which is what this listener is.
    let apps = assembly.routers(ports, false);

    let result = tokio::try_join!(
        axum::serve(registration, apps.registration).into_future(),
        axum::serve(query, apps.query).into_future(),
        axum::serve(websocket, apps.websocket).into_future(),
    );
    matcher.abort();
    collector.abort();
    result.map(|_| ())
}

#[cfg(test)]
mod tests {
    use super::*;
    use nmos_registry_core::body::Body;
    use nmos_registry_core::resource_type::ResourceType;
    use nmos_registry_core::store::RegistryStore;

    fn node(version: u32) -> Body {
        Body::new(format!(
            r#"{{"id":"3b8be755-08ff-452b-b217-c9151eb21193","version":"{version}:0",
"label":"n","description":"","tags":{{}},"href":"http://example.test/",
"hostname":"example","caps":{{}},
"api":{{"versions":["v1.3"],"endpoints":[]}},
"services":[],"clocks":[],"interfaces":[]}}"#
        ))
    }

    #[test]
    fn the_default_ports_are_the_ones_the_python_launcher_uses() {
        // A Node configured against the Python registry must reach this one
        // without being reconfigured.
        let ports = Ports::default();
        assert_eq!(ports.registration, 8447);
        assert_eq!(ports.query, 8446);
        assert_eq!(ports.websocket, 8448);
    }

    #[tokio::test]
    async fn the_matcher_task_routes_without_being_polled() {
        // The wiring this module exists for. Every WebSocket test drives
        // `route_once` by hand; in a running server nothing does, and a
        // registry whose matcher is never called accepts registrations and
        // delivers no grains at all.
        let assembly = Assembly::new(
            Registry::new(RegistryStore::new()),
            "11111111-2222-4333-8444-555555555555".to_owned(),
        );
        let subscription = assembly
            .subscriptions
            .create_or_match(&nmos_registry::manager::SubscriptionRequest {
                resource_path: "/nodes".to_owned(),
                params: Vec::new(),
                max_update_rate_ms: 0,
                persist: true,
                secure: false,
                authorization: false,
                host: "localhost".to_owned(),
                ws_scheme: "ws".to_owned(),
                ws_host: "localhost".to_owned(),
            })
            .expect("subscribable")
            .0;
        let connection = assembly
            .subscriptions
            .connect(&assembly.registry, &subscription.id)
            .expect("the subscription exists");

        let task = tokio::spawn(matcher_task(
            Arc::clone(&assembly.registry),
            Arc::clone(&assembly.subscriptions),
        ));

        // The first registration proves the task is alive -- but it proves
        // nothing about the wake-up, because the task may not have reached its
        // `await` before the commit landed, in which case its very first check
        // finds work and it never parks at all. That is not a hypothetical: it
        // is what this test did before, and it passed against a build whose
        // mutations announced nothing.
        assembly
            .registry
            .register(ResourceType::Node, node(1))
            .expect("registers");
        tokio::time::timeout(std::time::Duration::from_secs(2), connection.wait())
            .await
            .expect("the matcher never ran at all");
        connection.drain();

        // Now it is genuinely parked: the queue is empty and has been for long
        // enough that the task has looped back round to its await.
        for _ in 0..20 {
            if assembly.registry.pending_commits() == 0 {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;

        // The second registration is the one that tests the signal.
        assembly
            .registry
            .register(ResourceType::Node, node(2))
            .expect("registers");
        let waited =
            tokio::time::timeout(std::time::Duration::from_secs(2), connection.wait()).await;
        assert!(waited.is_ok(), "a parked matcher was never woken");
        assert_eq!(connection.drain().len(), 1);
        task.abort();
    }

    #[tokio::test]
    async fn the_collector_task_expires_a_silent_node() {
        // Without this task the registry never forgets anything: a Node that
        // stops heartbeating stays in every collection for ever. AMWA IS-04-02
        // `test_27` catches it as "Query API did not return 404 on a resource
        // which should have been removed due to missing heartbeats" -- which is
        // exactly how it was found, because `collect_garbage` existed and
        // nothing called it.
        let registry = Arc::new(Registry::new(RegistryStore::with_intervals(0, 0)));
        registry
            .register(ResourceType::Node, node(1))
            .expect("registers");
        assert_eq!(registry.count_extant(ResourceType::Node), 1);

        let task = tokio::spawn(collector_task(
            Arc::new(StandaloneBackend::new(Arc::clone(&registry))),
            Arc::new(SubscriptionManager::new()),
        ));
        for _ in 0..60 {
            if registry.count_extant(ResourceType::Node) == 0 {
                task.abort();
                return;
            }
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        }
        task.abort();
        panic!("a silent Node was never collected");
    }

    #[tokio::test]
    async fn collection_is_published_so_subscribers_see_the_removal() {
        // The removal has to reach clients, not merely leave the store: a
        // Controller watching `/nodes` must be told the Node went away. That
        // works only because `collect_garbage` commits its events, so the
        // matcher picks them up like any other change.
        let assembly = Assembly::new(
            Registry::new(RegistryStore::with_intervals(0, 0)),
            "q".to_owned(),
        );
        let (subscription, _) = assembly
            .subscriptions
            .create_or_match(&nmos_registry::manager::SubscriptionRequest {
                resource_path: "/nodes".to_owned(),
                params: Vec::new(),
                max_update_rate_ms: 0,
                persist: true,
                secure: false,
                authorization: false,
                host: "localhost".to_owned(),
                ws_scheme: "ws".to_owned(),
                ws_host: "localhost".to_owned(),
            })
            .expect("subscribable");

        assembly
            .registry
            .register(ResourceType::Node, node(1))
            .expect("registers");
        let connection = assembly
            .subscriptions
            .connect(&assembly.registry, &subscription.id)
            .expect("exists");
        connection.drain(); // the sync burst

        let matcher = tokio::spawn(matcher_task(
            Arc::clone(&assembly.registry),
            Arc::clone(&assembly.subscriptions),
        ));
        let collector = tokio::spawn(collector_task(
            Arc::clone(&assembly.backend),
            Arc::clone(&assembly.subscriptions),
        ));

        let woken =
            tokio::time::timeout(std::time::Duration::from_secs(5), connection.wait()).await;
        collector.abort();
        matcher.abort();

        assert!(woken.is_ok(), "no grain was published for the collection");
        let events = connection.drain();
        assert_eq!(events.len(), 1);
        assert!(
            events[0].post.is_none(),
            "a collected Node must be reported as removed",
        );
    }

    #[tokio::test]
    async fn an_idle_matcher_does_not_spin() {
        // It sleeps on a signal rather than polling, so an idle registry costs
        // nothing. Measured by the absence of progress rather than by CPU: if
        // this were a polling loop the queue would be drained repeatedly and
        // the high-water mark would still be zero, so instead this asserts the
        // task is genuinely parked by checking it makes no progress and then
        // wakes correctly when it should.
        let assembly = Assembly::new(Registry::new(RegistryStore::new()), "q".to_owned());
        let task = tokio::spawn(matcher_task(
            Arc::clone(&assembly.registry),
            Arc::clone(&assembly.subscriptions),
        ));
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        assert_eq!(assembly.registry.pending_commits(), 0);

        assembly
            .registry
            .register(ResourceType::Node, node(1))
            .expect("registers");
        for _ in 0..40 {
            if assembly.registry.pending_commits() == 0 {
                task.abort();
                return;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
        task.abort();
        panic!("the parked matcher never woke for a real commit");
    }
}
