// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The mTLS gate, through the real routers.
//!
//! `security.rs` unit-tests the decision; this tests the **wiring**, which is
//! the half that can be silently absent. A layer applied to one router and not
//! the other, or applied inside `with_state` where it never sees the request,
//! leaves every unit test passing and the registry wide open.
//!
//! # What is being enforced
//!
//! `NMOS With OAuth2.0:110` and `NMOS With Node Reservation:57`: when mTLS is
//! enabled, every **state-changing** request must present a verified client
//! certificate. Read-only verbs pass without one, which is Node Reservation's
//! "read-only granted without client certificate" rule
//! (`Node Reservation.md:41-45`).
//!
//! The identity travels as a request extension, never a header, so these tests
//! insert it the way the TLS acceptor will rather than by setting something a
//! client could also set.

// Test code is exempt from the panic-free lints the workspace denies.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects
)]

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::http::{Request, StatusCode, header};
use tower::ServiceExt as _;

use nmos_registry::manager::SubscriptionManager;
use nmos_registry::registry::Registry;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_http::query::{DEFAULT_PAGING_LIMIT, MAX_PAGING_LIMIT, QueryState};
use nmos_registry_http::registration::RegistrationState;
use nmos_registry_http::router;
use nmos_registry_http::security::{InterfaceSecurity, PeerIdentity};

const QUERY_BASE: &str = "/x-nmos/query/v1.3";
const REG_BASE: &str = "/x-nmos/registration/v1.3";
const NODE_ID: &str = "3b8be755-08ff-452b-b217-c9151eb21193";

fn registration_router(client_auth_required: bool) -> Router {
    router::registration(
        RegistrationState {
            registry: Arc::new(Registry::new(RegistryStore::new())),
            subscriptions: std::sync::Arc::new(nmos_registry::manager::SubscriptionManager::new()),
        },
        InterfaceSecurity::registration(client_auth_required),
    )
}

fn query_router(client_auth_required: bool) -> Router {
    router::query(
        QueryState {
            registry: Arc::new(Registry::new(RegistryStore::new())),
            subscriptions: Arc::new(SubscriptionManager::new()),
            query_id: "q".to_owned(),
            tls: false,
            ws_port: 8448,
            paging_limit: DEFAULT_PAGING_LIMIT,
            paging_limit_max: MAX_PAGING_LIMIT,
        },
        InterfaceSecurity {
            client_auth_required,
            ..InterfaceSecurity::default()
        },
    )
}

/// Send a request, optionally as a peer the TLS layer verified.
fn send(router: Router, verb: &str, path: &str, authenticated: bool) -> (StatusCode, String) {
    let mut request = Request::builder()
        .method(verb)
        .uri(path)
        .header(header::HOST, "registry.test")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(r#"{"type":"node","data":{}}"#))
        .expect("a test request");
    if authenticated {
        // Exactly what the TLS acceptor will do: an extension, which has no
        // wire representation and so cannot be supplied by the peer.
        request.extensions_mut().insert(PeerIdentity::Verified {
            names: vec!["node1.example.test".to_owned()],
        });
    }
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("a runtime")
        .block_on(async {
            let response = router.oneshot(request).await.expect("infallible");
            let status = response.status();
            let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
                .await
                .expect("a complete body");
            (status, String::from_utf8_lossy(&bytes).into_owned())
        })
}

// -- the gate is actually applied ------------------------------------------

#[test]
fn a_state_changing_request_without_a_certificate_is_refused() {
    let (status, body) = send(
        registration_router(true),
        "POST",
        &format!("{REG_BASE}/resource"),
        false,
    );
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert!(
        body.contains("TLS client authentication required"),
        "{body}"
    );
}

#[test]
fn the_refusal_carries_www_authenticate() {
    let mut request = Request::builder()
        .method("POST")
        .uri(format!("{REG_BASE}/resource"))
        .body(Body::empty())
        .expect("a test request");
    request
        .headers_mut()
        .insert(header::HOST, "r.test".parse().expect("host"));
    let headers = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("a runtime")
        .block_on(async {
            registration_router(true)
                .oneshot(request)
                .await
                .expect("infallible")
                .headers()
                .clone()
        });
    assert_eq!(
        headers
            .get("www-authenticate")
            .and_then(|v| v.to_str().ok()),
        Some(r#"Bearer realm="nmos-mtls""#),
    );
}

#[test]
fn a_state_changing_request_with_a_certificate_reaches_the_handler() {
    // 400 rather than 401: the body is deliberately invalid, so reaching the
    // decode error proves the gate let it through.
    let (status, _) = send(
        registration_router(true),
        "POST",
        &format!("{REG_BASE}/resource"),
        true,
    );
    assert_eq!(
        status,
        StatusCode::BAD_REQUEST,
        "an authenticated request did not reach the handler",
    );
}

#[test]
fn read_only_verbs_pass_without_a_certificate() {
    // `Node Reservation.md:41-45`.
    for (verb, path) in [
        ("GET", REG_BASE.to_owned()),
        ("GET", format!("{REG_BASE}/resource/nodes/{NODE_ID}")),
        ("OPTIONS", format!("{REG_BASE}/resource")),
    ] {
        let (status, _) = send(registration_router(true), verb, &path, false);
        assert_ne!(
            status,
            StatusCode::UNAUTHORIZED,
            "{verb} {path} was refused",
        );
    }
}

#[test]
fn delete_is_gated_too() {
    // Every state-changing verb, not only the one the tests happen to use.
    let (status, _) = send(
        registration_router(true),
        "DELETE",
        &format!("{REG_BASE}/resource/nodes/{NODE_ID}"),
        false,
    );
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[test]
fn the_query_api_is_gated_as_well_as_registration() {
    // The failure this file exists for: a layer applied to one router and not
    // the other leaves every unit test passing.
    let (status, _) = send(
        query_router(true),
        "POST",
        &format!("{QUERY_BASE}/subscriptions"),
        false,
    );
    assert_eq!(status, StatusCode::UNAUTHORIZED);

    let (status, _) = send(
        query_router(true),
        "DELETE",
        &format!("{QUERY_BASE}/subscriptions/{NODE_ID}"),
        false,
    );
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[test]
fn query_reads_pass_without_a_certificate() {
    for path in [
        QUERY_BASE.to_owned(),
        format!("{QUERY_BASE}/nodes"),
        format!("{QUERY_BASE}/subscriptions"),
    ] {
        let (status, _) = send(query_router(true), "GET", &path, false);
        assert_ne!(status, StatusCode::UNAUTHORIZED, "{path}");
    }
}

// -- the gate is off when it should be -------------------------------------

#[test]
fn nothing_is_gated_when_client_auth_is_not_required() {
    // The plaintext and server-TLS deployments must not start demanding
    // certificates nobody configured.
    let (status, _) = send(
        registration_router(false),
        "POST",
        &format!("{REG_BASE}/resource"),
        false,
    );
    assert_eq!(
        status,
        StatusCode::BAD_REQUEST,
        "a request was gated on a listener with no client auth configured",
    );
}

// -- forgery ---------------------------------------------------------------

#[test]
fn a_client_cannot_authenticate_itself_with_a_header() {
    // The reason the identity is an extension. Every header a client might try
    // must be inert.
    for name in [
        "x-nmos-verified-peer",
        "x-forwarded-client-cert",
        "ssl-client-verify",
        "x-client-cert-cn",
    ] {
        let request = Request::builder()
            .method("POST")
            .uri(format!("{REG_BASE}/resource"))
            .header(header::HOST, "registry.test")
            .header(name, "node1.example.test")
            .body(Body::from(r#"{"type":"node","data":{}}"#))
            .expect("a test request");
        let status = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("a runtime")
            .block_on(async {
                registration_router(true)
                    .oneshot(request)
                    .await
                    .expect("infallible")
                    .status()
            });
        assert_eq!(
            status,
            StatusCode::UNAUTHORIZED,
            "the header `{name}` was accepted as a verified identity",
        );
    }
}
