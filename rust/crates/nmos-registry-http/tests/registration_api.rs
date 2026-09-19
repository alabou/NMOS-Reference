// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The Registration API, driven through the real router.
//!
//! Port of the parts of `nmos/registry/tests/test_registration_api.py` that do
//! not need a backend seam or TLS. Every request goes through `Router` rather
//! than calling a handler directly, so the route table, the dual
//! trailing-slash registration and the handler are all exercised together --
//! which is the point, because most of what can go wrong here is a route that
//! matches the wrong thing.

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
use axum::http::{HeaderMap, Request, StatusCode, header};
use tower::ServiceExt as _;

use nmos_registry::registry::Registry;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_http::registration::RegistrationState;
use nmos_registry_http::router;
use nmos_registry_http::security::InterfaceSecurity;

const BASE: &str = "/x-nmos/registration/v1.3";
const NODE_ID: &str = "3b8be755-08ff-452b-b217-c9151eb21193";
const DEVICE_ID: &str = "58f6b536-ca4c-43fd-880a-9df2501fc125";

fn node_body(version: &str, label: &str) -> String {
    format!(
        r#"{{"id":"{NODE_ID}","version":"{version}","label":"{label}",
"description":"","tags":{{}},"href":"http://example.test/",
"hostname":"example","caps":{{}},
"api":{{"versions":["v1.3"],"endpoints":[]}},
"services":[],"clocks":[],"interfaces":[]}}"#
    )
}

fn device_body(version: &str) -> String {
    format!(
        r#"{{"id":"{DEVICE_ID}","version":"{version}","label":"d",
"description":"","tags":{{}},"type":"urn:x-nmos:device:generic",
"node_id":"{NODE_ID}","senders":[],"receivers":[],"controls":[]}}"#
    )
}

fn envelope(kind: &str, data: &str) -> String {
    format!(r#"{{"type":"{kind}","data":{data}}}"#)
}

struct Rig {
    registry: Arc<Registry>,
}

impl Rig {
    fn new() -> Self {
        Self {
            registry: Arc::new(Registry::new(RegistryStore::new())),
        }
    }

    fn router(&self) -> Router {
        router::registration(
            RegistrationState {
                registry: Arc::clone(&self.registry),
                backend: std::sync::Arc::new(nmos_registry_backend::StandaloneBackend::new(
                    Arc::clone(&self.registry),
                )),
                subscriptions: std::sync::Arc::new(
                    nmos_registry::manager::SubscriptionManager::new(),
                ),
            },
            InterfaceSecurity::registration(false),
        )
    }

    fn send(&self, request: Request<Body>) -> (StatusCode, HeaderMap, String) {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("a current-thread runtime")
            .block_on(async {
                let response = self.router().oneshot(request).await.expect("infallible");
                let status = response.status();
                let headers = response.headers().clone();
                let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
                    .await
                    .expect("a complete body");
                (
                    status,
                    headers,
                    String::from_utf8_lossy(&bytes).into_owned(),
                )
            })
    }

    fn post(&self, path: &str, body: String) -> (StatusCode, HeaderMap, String) {
        self.send(
            Request::builder()
                .method("POST")
                .uri(path)
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(body))
                .expect("a test request"),
        )
    }

    fn get(&self, path: &str) -> (StatusCode, HeaderMap, String) {
        self.send(
            Request::builder()
                .method("GET")
                .uri(path)
                .body(Body::empty())
                .expect("a test request"),
        )
    }

    fn delete(&self, path: &str) -> (StatusCode, HeaderMap, String) {
        self.send(
            Request::builder()
                .method("DELETE")
                .uri(path)
                .body(Body::empty())
                .expect("a test request"),
        )
    }

    fn register_node(&self) -> (StatusCode, HeaderMap, String) {
        self.post(
            &format!("{BASE}/resource"),
            envelope("node", &node_body("1600000000:0", "n")),
        )
    }
}

fn location(headers: &HeaderMap) -> Option<&str> {
    headers
        .get(header::LOCATION)
        .and_then(|value| value.to_str().ok())
}

// -- POST /resource --------------------------------------------------------

#[test]
fn a_first_registration_is_201_with_a_location() {
    // `Behaviour - Registration.md:25` -- 201 for a create, Location on both.
    let rig = Rig::new();
    let (status, headers, body) = rig.register_node();

    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(
        location(&headers),
        Some(format!("{BASE}/resource/nodes/{NODE_ID}").as_str()),
    );
    assert!(body.contains(NODE_ID), "{body}");
}

#[test]
fn a_re_registration_is_200_and_still_carries_a_location() {
    let rig = Rig::new();
    rig.register_node();
    let (status, headers, _) = rig.post(
        &format!("{BASE}/resource"),
        envelope("node", &node_body("1600000001:0", "renamed")),
    );

    assert_eq!(status, StatusCode::OK, "an update must be 200, not 201");
    assert_eq!(
        location(&headers),
        Some(format!("{BASE}/resource/nodes/{NODE_ID}").as_str()),
    );
}

#[test]
fn the_response_body_is_the_bytes_that_were_registered() {
    // The byte-fidelity guarantee, end to end over HTTP: a spelling a parse
    // would normalise must come back as it went in.
    let rig = Rig::new();
    // Built rather than written literally: the point is a JSON `\uXXXX`
    // escape, which a parse-and-re-encode would silently fold into the
    // character it denotes. A source file holding the character itself would
    // pass against a registry that does exactly that.
    let escape = ['\\', 'u', '0', '0', 'e', '9'].iter().collect::<String>();
    let data = node_body("1600000000:0", "n")
        .replace(r#""label":"n""#, &format!(r#""label":"caf{escape}""#));
    let (status, _, body) = rig.post(&format!("{BASE}/resource"), envelope("node", &data));

    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(body, data, "the registry re-encoded the body");
    assert!(
        body.contains(&escape),
        "the escape was folded into its character: {body}",
    );
    assert!(
        !body.contains('\u{e9}'),
        "the escape was normalised: {body}",
    );
}

#[test]
fn a_malformed_envelope_is_400_with_the_reason_in_debug() {
    // `handlers_registration.py:158` -- the decode message IS the 400 body.
    let rig = Rig::new();
    let (status, _, body) = rig.post(&format!("{BASE}/resource"), "not json".to_owned());

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body.contains(r#""code": 400"#), "{body}");
    assert!(body.contains("invalid JSON body"), "{body}");
}

#[test]
fn a_child_whose_parent_is_missing_is_400() {
    // `Behaviour - Registration.md:104` -- a client error the Node must not
    // retry without corrective action.
    let rig = Rig::new();
    let (status, _, body) = rig.post(
        &format!("{BASE}/resource"),
        envelope("device", &device_body("1600000000:0")),
    );

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body.contains(r#""code": 400"#), "{body}");
}

#[test]
fn a_version_regression_is_400() {
    let rig = Rig::new();
    rig.post(
        &format!("{BASE}/resource"),
        envelope("node", &node_body("1600000002:0", "n")),
    );
    let (status, _, _) = rig.post(
        &format!("{BASE}/resource"),
        envelope("node", &node_body("1600000001:0", "older")),
    );
    assert_eq!(status, StatusCode::BAD_REQUEST);
}

#[test]
fn the_post_route_answers_with_and_without_a_trailing_slash() {
    // `APIs.md:92` -- the state-changing verbs must work without a trailing
    // slash and must not be answered with a redirect.
    for path in [format!("{BASE}/resource"), format!("{BASE}/resource/")] {
        let rig = Rig::new();
        let (status, headers, _) =
            rig.post(&path, envelope("node", &node_body("1600000000:0", "n")));
        assert_eq!(status, StatusCode::CREATED, "{path}");
        assert!(
            headers.get(header::LOCATION).is_some(),
            "{path}: no Location",
        );
    }
}

// -- GET /resource/{type}/{id} ---------------------------------------------

#[test]
fn a_registered_resource_reads_back_verbatim() {
    // `RegistrationAPI.raml:105` -- "for debug use only", and genuinely useful
    // for checking what a Node actually sent.
    let rig = Rig::new();
    rig.register_node();
    let (status, _, body) = rig.get(&format!("{BASE}/resource/nodes/{NODE_ID}"));

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, node_body("1600000000:0", "n"));
}

#[test]
fn an_unregistered_resource_is_404() {
    let rig = Rig::new();
    let (status, _, body) = rig.get(&format!("{BASE}/resource/nodes/{NODE_ID}"));
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(body.contains("is not registered"), "{body}");
}

#[test]
fn an_unknown_resource_type_segment_is_404_and_lists_what_is_permitted() {
    // Python constrains this in the route pattern; `matchit` cannot, so the
    // handler's check is the only one. The AMWA mock coerces with
    // `rstrip("s")`, which is exactly what this refuses to do.
    let rig = Rig::new();
    let (status, _, body) = rig.get(&format!("{BASE}/resource/widgets/{NODE_ID}"));

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(body.contains("unknown resource type 'widgets'"), "{body}");
    assert!(
        body.contains("nodes, devices, sources, flows, senders, receivers"),
        "{body}",
    );
}

#[test]
fn a_malformed_resource_id_is_404_not_a_500() {
    // Without regex route constraints a bad id reaches the handler, so the
    // defensive branch Python calls "not reachable" is load-bearing here.
    let rig = Rig::new();
    let (status, _, _) = rig.get(&format!("{BASE}/resource/nodes/not-a-uuid"));
    assert_eq!(status, StatusCode::NOT_FOUND);
}

// -- DELETE /resource/{type}/{id} ------------------------------------------

#[test]
fn deleting_a_registered_resource_is_204_with_no_body() {
    // `RegistrationAPI.raml:91-92`.
    let rig = Rig::new();
    rig.register_node();
    let (status, _, body) = rig.delete(&format!("{BASE}/resource/nodes/{NODE_ID}"));

    assert_eq!(status, StatusCode::NO_CONTENT);
    assert!(body.is_empty(), "204 must carry no body: {body}");
    assert_eq!(
        rig.get(&format!("{BASE}/resource/nodes/{NODE_ID}")).0,
        StatusCode::NOT_FOUND,
        "the resource survived its deletion",
    );
}

#[test]
fn deleting_an_unregistered_resource_is_404() {
    let rig = Rig::new();
    let (status, _, _) = rig.delete(&format!("{BASE}/resource/nodes/{NODE_ID}"));
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[test]
fn deleting_a_node_cascades_to_its_children() {
    // `Behaviour - Registration.md:68`, `:74`.
    let rig = Rig::new();
    rig.register_node();
    let (status, _, _) = rig.post(
        &format!("{BASE}/resource"),
        envelope("device", &device_body("1600000000:0")),
    );
    assert_eq!(
        status,
        StatusCode::CREATED,
        "the Device must register first"
    );

    rig.delete(&format!("{BASE}/resource/nodes/{NODE_ID}"));

    assert_eq!(
        rig.get(&format!("{BASE}/resource/devices/{DEVICE_ID}")).0,
        StatusCode::NOT_FOUND,
        "the Device outlived its Node",
    );
}

// -- health ----------------------------------------------------------------

#[test]
fn a_heartbeat_returns_health_as_a_string() {
    // `registrationapi-health-response.json` types it as a STRING matching
    // `^[0-9]+$`. The AMWA mock returns a number, failing its own schema.
    let rig = Rig::new();
    rig.register_node();
    let (status, _, body) = rig.post(&format!("{BASE}/health/nodes/{NODE_ID}"), String::new());

    assert_eq!(status, StatusCode::OK);
    let parsed: serde_json::Value = serde_json::from_str(&body).expect("valid JSON");
    assert!(
        parsed["health"].is_string(),
        "health must be a string: {body}"
    );
    assert!(
        parsed["health"]
            .as_str()
            .is_some_and(|text| text.chars().all(|c| c.is_ascii_digit())),
        "health must match ^[0-9]+$: {body}",
    );
}

#[test]
fn a_heartbeat_for_an_unregistered_node_is_404() {
    // `:112-114` -- most likely garbage collection removed it, and the Node's
    // documented response is to re-register every resource in order.
    let rig = Rig::new();
    let (status, _, body) = rig.post(&format!("{BASE}/health/nodes/{NODE_ID}"), String::new());
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(body.contains("is not registered"), "{body}");
}

#[test]
fn reading_health_does_not_refresh_it() {
    // `RegistrationAPI.raml:152` -- a diagnostic read that silently kept a
    // Node alive would mask exactly the garbage-collection problem someone
    // would be using it to investigate.
    let rig = Rig::new();
    rig.register_node();
    let before = rig.registry.node_health(NODE_ID).expect("registered");

    let (status, _, _) = rig.get(&format!("{BASE}/health/nodes/{NODE_ID}"));
    assert_eq!(status, StatusCode::OK);

    assert_eq!(
        rig.registry.node_health(NODE_ID),
        Some(before),
        "a debug read refreshed the Node's health",
    );
}

#[test]
fn reading_health_for_an_unregistered_node_is_404() {
    let rig = Rig::new();
    let (status, _, _) = rig.get(&format!("{BASE}/health/nodes/{NODE_ID}"));
    assert_eq!(status, StatusCode::NOT_FOUND);
}

// -- the shape of the surface ---------------------------------------------

#[test]
fn every_response_carries_cors() {
    let rig = Rig::new();
    rig.register_node();
    for (status, headers, _) in [
        rig.register_node(),
        rig.get(&format!("{BASE}/resource/nodes/{NODE_ID}")),
        rig.get(&format!("{BASE}/resource/nodes/missing")),
        rig.post(&format!("{BASE}/health/nodes/{NODE_ID}"), String::new()),
        rig.delete(&format!("{BASE}/resource/nodes/{NODE_ID}")),
    ] {
        assert!(
            headers.get("access-control-allow-origin").is_some(),
            "{status} went out without CORS",
        );
    }
}

#[test]
fn preflight_is_answered_on_every_registration_path() {
    let rig = Rig::new();
    for path in [
        format!("{BASE}/resource"),
        format!("{BASE}/resource/nodes/{NODE_ID}"),
        format!("{BASE}/health/nodes/{NODE_ID}"),
    ] {
        let (status, headers, _) = rig.send(
            Request::builder()
                .method("OPTIONS")
                .uri(&path)
                .body(Body::empty())
                .expect("a test request"),
        );
        assert_eq!(status, StatusCode::OK, "{path}");
        assert!(
            headers.get("access-control-allow-methods").is_some(),
            "{path}",
        );
    }
}

#[test]
fn the_registration_port_does_not_serve_the_query_ladder() {
    // Advertising or serving the other API from this port would send a client
    // to an endpoint that is not here.
    let rig = Rig::new();
    assert_eq!(rig.get("/x-nmos/query").0, StatusCode::NOT_FOUND);
    assert_eq!(rig.get("/x-nmos/query/v1.3").0, StatusCode::NOT_FOUND);
    assert_eq!(rig.get("/x-nmos").2, r#"["registration/"]"#);
}
