// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! What the Registration API does when the storage layer cannot write.
//!
//! Port of `test_backend.py`'s HTTP half. The unit half -- the state machine
//! and the standalone delegation -- lives in `nmos-registry-backend`'s own
//! tests; this is the part that needs a router in front of it.
//!
//! # The distinction being proven
//!
//! A backend that cannot accept mutations must answer **503 with
//! `Retry-After`**, and the Query API must **keep serving** from the same view.
//! Those two facts together are the reason the seam exists: a storage outage
//! should cost a registry its writes, not its reads. Gating Query on the same
//! flag would turn a partial outage into a total one, and would do it in a way
//! that looks tidy in the code.
//!
//! The other half is that a **bad body is still 400**. Once a 503 path exists it
//! is easy to route every failure through it, and a Node that retries a
//! malformed registration for ever is worse off than one told to stop.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::sync::Arc;

use axum::body::Body as AxumBody;
use axum::http::{Request, StatusCode};
use nmos_registry::manager::SubscriptionManager;
use nmos_registry::registry::Registry;
use nmos_registry_backend::{
    BackendState, MutationUnavailable, RegistryBackend, StandaloneBackend,
};
use nmos_registry_core::body::Body;
use nmos_registry_core::event::ResourceEvent;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::{Applied, RegistrationFailure, RegistryStore};
use nmos_registry_http::registration::RegistrationState;
use nmos_registry_http::router;
use nmos_registry_http::security::InterfaceSecurity;
use tower::ServiceExt as _;

const BASE: &str = "/x-nmos/registration/v1.3";
const NODE_ID: &str = "3b8be755-08ff-452b-b217-c9151eb21193";

/// A backend frozen in one state, delegating everything else.
///
/// The only way to exercise `DEGRADED` without a distributed backend to break.
#[derive(Debug)]
struct Frozen {
    inner: StandaloneBackend,
    state: BackendState,
}

#[async_trait::async_trait]
impl RegistryBackend for Frozen {
    fn state(&self) -> BackendState {
        self.state
    }

    fn registry(&self) -> &Arc<Registry> {
        self.inner.registry()
    }

    async fn start(&self) -> Result<(), MutationUnavailable> {
        Ok(())
    }

    async fn register(
        &self,
        resource_type: ResourceType,
        body: Body,
    ) -> Result<Result<Applied, RegistrationFailure>, MutationUnavailable> {
        self.inner.register(resource_type, body).await
    }

    async fn unregister(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Result<Option<Vec<ResourceEvent>>, MutationUnavailable> {
        self.inner.unregister(resource_type, resource_id).await
    }

    async fn heartbeat(&self, node_id: &str) -> Result<Option<i64>, MutationUnavailable> {
        self.inner.heartbeat(node_id).await
    }

    async fn collect_garbage(&self) -> Result<Vec<ResourceEvent>, MutationUnavailable> {
        self.inner.collect_garbage().await
    }

    async fn close(&self) {}
}

fn node_body() -> String {
    format!(
        r#"{{"type":"node","data":{{"id":"{NODE_ID}","version":"100:0","label":"n",
           "description":"d","tags":{{}},"href":"http://192.0.2.1:8080/","caps":{{}},
           "api":{{"versions":["v1.3"],"endpoints":[
               {{"host":"192.0.2.1","port":8080,"protocol":"http"}}]}},
           "services":[],"clocks":[],"interfaces":[]}}}}"#
    )
}

/// A Registration router whose backend is frozen in `state`.
fn routers(state: BackendState) -> (axum::Router, axum::Router, Arc<Registry>) {
    let registry = Arc::new(Registry::new(RegistryStore::new()));
    let backend: Arc<dyn RegistryBackend> = Arc::new(Frozen {
        inner: StandaloneBackend::new(Arc::clone(&registry)),
        state,
    });
    let subscriptions = Arc::new(SubscriptionManager::new());

    let registration = router::registration(
        RegistrationState {
            registry: Arc::clone(&registry),
            backend,
            subscriptions: Arc::clone(&subscriptions),
        },
        InterfaceSecurity::registration(false),
    );
    let query = router::query(
        nmos_registry_http::query::QueryState {
            registry: Arc::clone(&registry),
            subscriptions,
            query_id: "q".to_owned(),
            tls: false,
            ws_port: 8448,
            paging_limit: nmos_registry_http::query::DEFAULT_PAGING_LIMIT,
            paging_limit_max: nmos_registry_http::query::MAX_PAGING_LIMIT,
        },
        InterfaceSecurity::default(),
    );
    (registration, query, registry)
}

async fn send(router: &axum::Router, request: Request<AxumBody>) -> (StatusCode, String, String) {
    let response = router.clone().oneshot(request).await.expect("served");
    let status = response.status();
    let retry_after = response
        .headers()
        .get("retry-after")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .to_owned();
    let bytes = axum::body::to_bytes(response.into_body(), 1 << 20)
        .await
        .expect("body");
    (
        status,
        String::from_utf8_lossy(&bytes).into_owned(),
        retry_after,
    )
}

fn post(path: &str, body: String) -> Request<AxumBody> {
    Request::builder()
        .method("POST")
        .uri(path)
        .header("content-type", "application/json")
        .body(AxumBody::from(body))
        .expect("request")
}

fn get(path: &str) -> Request<AxumBody> {
    Request::builder()
        .uri(path)
        .body(AxumBody::empty())
        .expect("request")
}

/// The states in which a mutation must be refused.
const NOT_WRITABLE: [BackendState; 4] = [
    BackendState::Starting,
    BackendState::Degraded,
    BackendState::Resyncing,
    BackendState::Stopping,
];

#[tokio::test]
async fn every_mutation_answers_503_when_the_backend_cannot_write() {
    for state in NOT_WRITABLE {
        let (registration, _, _) = routers(state);

        for (label, request) in [
            (
                "POST /resource",
                post(&format!("{BASE}/resource"), node_body()),
            ),
            (
                "POST /health",
                post(&format!("{BASE}/health/nodes/{NODE_ID}"), String::new()),
            ),
            (
                "DELETE /resource",
                Request::builder()
                    .method("DELETE")
                    .uri(format!("{BASE}/resource/nodes/{NODE_ID}"))
                    .body(AxumBody::empty())
                    .expect("request"),
            ),
        ] {
            let (status, body, retry_after) = send(&registration, request).await;
            assert_eq!(
                status,
                StatusCode::SERVICE_UNAVAILABLE,
                "{label} in {state:?} answered {status}, body {body}",
            );
            assert_eq!(
                retry_after, "1",
                "{label} in {state:?} answered 503 without Retry-After -- a \
                 Node with no retry hint backs off on its own schedule, which \
                 for these outages is far too long",
            );
            assert!(
                body.contains(state.value()),
                "the 503 body does not name the state ({}): {body}",
                state.value(),
            );
        }
    }
}

#[tokio::test]
async fn a_ready_backend_registers_normally() {
    // Guard the guard: a router that refused everything would pass the test
    // above while being useless.
    let (registration, _, _) = routers(BackendState::Ready);
    let (status, body, _) = send(
        &registration,
        post(&format!("{BASE}/resource"), node_body()),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED, "{body}");
}

#[tokio::test]
async fn query_keeps_serving_while_writes_are_refused() {
    // The reason `serves_queries` is not the negation of `accepts_mutations`.
    // A storage outage should cost the registry its writes, not its reads.
    let (registration, query, registry) = routers(BackendState::Degraded);

    // Put a resource in by the back door -- the front door is refused, which is
    // the point of the test.
    registry
        .register(
            ResourceType::Node,
            Body::from_value(serde_json::json!({
                "id": NODE_ID, "version": "100:0", "label": "n",
            })),
        )
        .expect("the registry itself still works");

    let (status, _, _) = send(
        &registration,
        post(&format!("{BASE}/resource"), node_body()),
    )
    .await;
    assert_eq!(
        status,
        StatusCode::SERVICE_UNAVAILABLE,
        "writes should be refused"
    );

    let (status, body, _) = send(&query, get("/x-nmos/query/v1.3/nodes")).await;
    assert_eq!(
        status,
        StatusCode::OK,
        "Query stopped serving because writes were impossible",
    );
    assert!(
        body.contains(NODE_ID),
        "Query served an empty view during a write outage: {body}",
    );
}

#[tokio::test]
async fn a_malformed_body_is_still_400_not_503() {
    // Once a 503 path exists it is easy to route every failure through it. A
    // Node that retries a malformed registration for ever is worse off than one
    // told to stop.
    let (registration, _, _) = routers(BackendState::Ready);
    let (status, body, _) = send(
        &registration,
        post(
            &format!("{BASE}/resource"),
            r#"{"type":"node","data":{}}"#.to_owned(),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "{body}");
}

#[tokio::test]
async fn reads_on_the_registration_api_are_not_gated_either() {
    // `GET /health/nodes/{id}` and the discovery ladders are reads. Refusing
    // them would make a degraded registry undiagnosable from outside.
    let (registration, _, _) = routers(BackendState::Degraded);
    let (status, _, _) = send(&registration, get(&format!("{BASE}/"))).await;
    assert_eq!(status, StatusCode::OK, "a discovery read was refused");
}
