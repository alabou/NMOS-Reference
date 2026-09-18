// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Live API responses, validated against the published IS-04 JSON schemas.
//!
//! Port of `nmos/registry/tests/test_schema_conformance.py`.
//!
//! # Why an independent authority
//!
//! Every other test here asserts behaviour this implementation was written to
//! produce. This one checks the bytes against the schemas shipped in
//! `nmos/registry/specs/schemas/`, copied verbatim from AMWA IS-04 `v1.3.x` at
//! tag `v1.3.3`.
//!
//! The independence is the point. The registry validates *incoming* resources
//! by decoding them into the generated types, so a mistake in those definitions
//! would be invisible to a test that used the same types to check the answer.
//! Validating wire bytes against the published schema catches the class of
//! error where implementation and test share a wrong assumption.
//!
//! # What this covers that AMWA IS-04-02 does not
//!
//! The external suite has a RAML-driven sweep -- 41 of its 96 results are
//! `auto_*` tests -- that validates every *documented* path's response against
//! its declared schema, and we hold result-set parity with Python on all of it.
//! So this file is deliberately **not** a re-run of that. It exists for three
//! things the sweep does not reach:
//!
//! * **error responses.** The sweep walks success paths; nothing on either side
//!   validated a 400 or a 404 body against `error.json`, and that body is an
//!   observable contract -- `handlers_registration.py:158` puts a decode
//!   message straight into it.
//! * **all four grain shapes, deterministically.** IS-04-02 validates whichever
//!   messages happen to arrive; added, removed, modified and sync are distinct
//!   shapes and each is asserted here.
//! * **the inner loop.** The sweep needs the external tool and two running
//!   registries. This runs in `cargo test`.

// Test code is exempt from the panic-free lints the workspace denies.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects
)]

use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::http::{Request, StatusCode, header};
use serde_json::Value;
use tower::ServiceExt as _;

use nmos_registry::grain::build_grain;
use nmos_registry::manager::{SubscriptionManager, SubscriptionRequest};
use nmos_registry::registry::Registry;
use nmos_registry::subscription::PendingEvent;
use nmos_registry_core::body::Body as StoredBody;
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_http::query::{DEFAULT_PAGING_LIMIT, MAX_PAGING_LIMIT, QueryState};
use nmos_registry_http::registration::RegistrationState;
use nmos_registry_http::router;
use nmos_registry_http::security::InterfaceSecurity;

const QUERY_BASE: &str = "/x-nmos/query/v1.3";
const REG_BASE: &str = "/x-nmos/registration/v1.3";
const NODE_ID: &str = "3b8be755-08ff-452b-b217-c9151eb21193";

/// Where the vendored schemas live, relative to the workspace.
fn schema_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../nmos/registry/specs/schemas")
        .canonicalize()
        .expect("the vendored IS-04 schemas are part of the repository")
}

/// Compile one schema, resolving its `$ref`s out of the vendored directory.
///
/// The schemas reference each other by bare filename (`"$ref": "device.json"`),
/// which is how AMWA ships them. `jsonschema` needs a retriever to turn those
/// into documents, and this is it -- 37 of the 47 files carry at least one.
fn compile(name: &str) -> jsonschema::Validator {
    struct Vendored(PathBuf);

    impl jsonschema::Retrieve for Vendored {
        fn retrieve(
            &self,
            uri: &jsonschema::Uri<String>,
        ) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
            // Bare filenames arrive as the path component of a relative URI.
            let target = uri.path().as_str().rsplit('/').next().unwrap_or_default();
            let text = std::fs::read_to_string(self.0.join(target))
                .map_err(|error| format!("{target}: {error}"))?;
            Ok(serde_json::from_str(&text)?)
        }
    }

    let dir = schema_dir();
    let text =
        std::fs::read_to_string(dir.join(name)).unwrap_or_else(|error| panic!("{name}: {error}"));
    let schema: Value = serde_json::from_str(&text).expect("a vendored schema is JSON");
    jsonschema::options()
        .with_retriever(Vendored(dir))
        .build(&schema)
        .unwrap_or_else(|error| panic!("{name} does not compile: {error}"))
}

/// Assert a document satisfies a schema, reporting every violation.
fn check(schema_name: &str, document: &Value, what: &str) {
    let validator = compile(schema_name);
    let errors: Vec<String> = validator
        .iter_errors(document)
        .map(|error| format!("  at {}: {error}", error.instance_path()))
        .collect();
    assert!(
        errors.is_empty(),
        "{what} does not satisfy {schema_name}:\n{}\n\ndocument: {}",
        errors.join("\n"),
        serde_json::to_string_pretty(document).unwrap_or_default(),
    );
}

// -- the rig ---------------------------------------------------------------

struct Rig {
    registry: Arc<Registry>,
    subscriptions: Arc<SubscriptionManager>,
}

impl Rig {
    fn new() -> Self {
        Self {
            registry: Arc::new(Registry::new(RegistryStore::new())),
            subscriptions: Arc::new(SubscriptionManager::new()),
        }
    }

    fn query_router(&self) -> Router {
        router::query(
            QueryState {
                registry: Arc::clone(&self.registry),
                subscriptions: Arc::clone(&self.subscriptions),
                query_id: "11111111-2222-4333-8444-555555555555".to_owned(),
                tls: false,
                ws_port: 8448,
                paging_limit: DEFAULT_PAGING_LIMIT,
                paging_limit_max: MAX_PAGING_LIMIT,
            },
            InterfaceSecurity::default(),
        )
    }

    fn registration_router(&self) -> Router {
        router::registration(
            RegistrationState {
                registry: Arc::clone(&self.registry),
                subscriptions: std::sync::Arc::new(
                    nmos_registry::manager::SubscriptionManager::new(),
                ),
            },
            InterfaceSecurity::registration(false),
        )
    }

    fn node_json(&self) -> String {
        format!(
            r#"{{"id":"{NODE_ID}","version":"1600000000:0","label":"n","description":"",
"tags":{{}},"href":"http://example.test/","hostname":"example","caps":{{}},
"api":{{"versions":["v1.3"],"endpoints":[]}},"services":[],"clocks":[],"interfaces":[]}}"#
        )
    }

    fn seed_node(&self) {
        self.registry
            .register(ResourceType::Node, StoredBody::new(self.node_json()))
            .expect("the fixture registers");
    }

    fn send(&self, router: Router, verb: &str, path: &str, body: String) -> (StatusCode, Value) {
        let request = Request::builder()
            .method(verb)
            .uri(path)
            .header(header::HOST, "registry.test:8446")
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(body))
            .expect("a test request");
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
                let text = String::from_utf8_lossy(&bytes).into_owned();
                let parsed = if text.is_empty() {
                    Value::Null
                } else {
                    serde_json::from_str(&text).unwrap_or_else(|error| {
                        panic!("{verb} {path} is not JSON: {error}\n{text}")
                    })
                };
                (status, parsed)
            })
    }

    fn get(&self, router: Router, path: &str) -> (StatusCode, Value) {
        self.send(router, "GET", path, String::new())
    }
}

// -- Registration API ------------------------------------------------------

#[test]
fn the_registration_base_matches_its_schema() {
    let rig = Rig::new();
    let (status, body) = rig.get(rig.registration_router(), REG_BASE);
    assert_eq!(status, StatusCode::OK);
    check("registrationapi-base.json", &body, "the Registration base");
}

#[test]
fn a_post_response_matches_the_resource_schema() {
    // `:25` -- the body is the registered resource.
    let rig = Rig::new();
    let (status, body) = rig.send(
        rig.registration_router(),
        "POST",
        &format!("{REG_BASE}/resource"),
        format!(r#"{{"type":"node","data":{}}}"#, rig.node_json()),
    );
    assert_eq!(status, StatusCode::CREATED);
    check("node.json", &body, "a POST /resource response");
}

#[test]
fn a_health_response_matches_its_schema() {
    // The one that catches a JSON number where the schema says a string
    // matching `^[0-9]+$`. The AMWA mock fails its own specification here.
    let rig = Rig::new();
    rig.seed_node();
    let (status, body) = rig.send(
        rig.registration_router(),
        "POST",
        &format!("{REG_BASE}/health/nodes/{NODE_ID}"),
        String::new(),
    );
    assert_eq!(status, StatusCode::OK);
    check("registrationapi-health-response.json", &body, "a heartbeat");
}

// -- Query API -------------------------------------------------------------

#[test]
fn the_query_base_matches_its_schema() {
    let rig = Rig::new();
    let (status, body) = rig.get(rig.query_router(), QUERY_BASE);
    assert_eq!(status, StatusCode::OK);
    check("queryapi-base.json", &body, "the Query base");
}

#[test]
fn every_collection_matches_its_schema() {
    let rig = Rig::new();
    rig.seed_node();
    for (plural, schema) in [
        ("nodes", "nodes.json"),
        ("devices", "devices.json"),
        ("sources", "sources.json"),
        ("flows", "flows.json"),
        ("senders", "senders.json"),
        ("receivers", "receivers.json"),
    ] {
        let (status, body) = rig.get(rig.query_router(), &format!("{QUERY_BASE}/{plural}"));
        assert_eq!(status, StatusCode::OK, "{plural}");
        check(schema, &body, &format!("GET /{plural}"));
    }
}

#[test]
fn a_single_resource_matches_its_schema() {
    let rig = Rig::new();
    rig.seed_node();
    let (status, body) = rig.get(rig.query_router(), &format!("{QUERY_BASE}/nodes/{NODE_ID}"));
    assert_eq!(status, StatusCode::OK);
    check("node.json", &body, "GET /nodes/{id}");
}

#[test]
fn a_subscription_response_matches_its_schema() {
    let rig = Rig::new();
    let (status, body) = rig.send(
        rig.query_router(),
        "POST",
        &format!("{QUERY_BASE}/subscriptions"),
        r#"{"max_update_rate_ms":100,"persist":true,"resource_path":"/nodes","params":{}}"#
            .to_owned(),
    );
    assert_eq!(status, StatusCode::CREATED);
    check(
        "queryapi-subscription-response.json",
        &body,
        "POST /subscriptions",
    );
}

#[test]
fn the_subscriptions_listing_matches_its_schema() {
    let rig = Rig::new();
    rig.send(
        rig.query_router(),
        "POST",
        &format!("{QUERY_BASE}/subscriptions"),
        r#"{"max_update_rate_ms":100,"persist":true,"resource_path":"/nodes","params":{}}"#
            .to_owned(),
    );
    let (status, body) = rig.get(rig.query_router(), &format!("{QUERY_BASE}/subscriptions"));
    assert_eq!(status, StatusCode::OK);
    check(
        "queryapi-subscriptions-response.json",
        &body,
        "GET /subscriptions",
    );
}

// -- error responses -------------------------------------------------------
//
// The gap the AMWA sweep leaves: it walks success paths, so nothing validated
// an error body against `error.json` on either side.

#[test]
fn a_registration_400_matches_the_error_schema() {
    let rig = Rig::new();
    let (status, body) = rig.send(
        rig.registration_router(),
        "POST",
        &format!("{REG_BASE}/resource"),
        "not json".to_owned(),
    );
    assert_eq!(status, StatusCode::BAD_REQUEST);
    check("error.json", &body, "a Registration 400");
}

#[test]
fn a_registration_404_matches_the_error_schema() {
    let rig = Rig::new();
    let (status, body) = rig.get(
        rig.registration_router(),
        &format!("{REG_BASE}/resource/nodes/{NODE_ID}"),
    );
    assert_eq!(status, StatusCode::NOT_FOUND);
    check("error.json", &body, "a Registration 404");
}

#[test]
fn a_query_404_matches_the_error_schema() {
    let rig = Rig::new();
    let (status, body) = rig.get(rig.query_router(), &format!("{QUERY_BASE}/nodes/{NODE_ID}"));
    assert_eq!(status, StatusCode::NOT_FOUND);
    check("error.json", &body, "a Query 404");
}

#[test]
fn a_query_400_matches_the_error_schema() {
    let rig = Rig::new();
    let (status, body) = rig.get(
        rig.query_router(),
        &format!("{QUERY_BASE}/nodes?paging.limit=many"),
    );
    assert_eq!(status, StatusCode::BAD_REQUEST);
    check("error.json", &body, "a Query 400");
}

#[test]
fn a_query_501_matches_the_error_schema() {
    let rig = Rig::new();
    let (status, body) = rig.get(
        rig.query_router(),
        &format!("{QUERY_BASE}/nodes?query.rql=eq(label,x)"),
    );
    assert_eq!(status, StatusCode::NOT_IMPLEMENTED);
    check("error.json", &body, "a Query 501");
}

// -- grains ----------------------------------------------------------------
//
// The other gap: IS-04-02 validates whichever messages happen to arrive, and
// the four shapes are distinct.

fn grain_for(pre: Option<&str>, post: Option<&str>) -> Value {
    let subscription = SubscriptionRequest {
        resource_path: "/nodes".to_owned(),
        params: Vec::new(),
        max_update_rate_ms: 0,
        persist: true,
        secure: false,
        authorization: false,
        host: "localhost".to_owned(),
        ws_scheme: "ws".to_owned(),
        ws_host: "localhost:8448".to_owned(),
    };
    let manager = SubscriptionManager::new();
    let (subscription, _) = manager
        .create_or_match(&subscription)
        .expect("subscribable");
    let events = vec![PendingEvent {
        path: NODE_ID.to_owned(),
        pre: pre.map(|text| StoredBody::new(text.to_owned())),
        post: post.map(|text| StoredBody::new(text.to_owned())),
    }];
    let text = build_grain(
        &subscription,
        &events,
        "11111111-2222-4333-8444-555555555555",
        TaiCursor::new(1_600_000_000, 0),
    )
    .expect("the stored bodies are JSON");
    serde_json::from_str(&text).expect("a grain is JSON")
}

fn a_node() -> String {
    format!(
        r#"{{"id":"{NODE_ID}","version":"1600000000:0","label":"n","description":"",
"tags":{{}},"href":"http://example.test/","hostname":"example","caps":{{}},
"api":{{"versions":["v1.3"],"endpoints":[]}},"services":[],"clocks":[],"interfaces":[]}}"#
    )
}

#[test]
fn each_grain_shape_matches_the_websocket_schema() {
    let node = a_node();
    for (what, pre, post) in [
        ("an added event", None, Some(node.as_str())),
        ("a removed event", Some(node.as_str()), None),
        ("a modified event", Some(node.as_str()), Some(node.as_str())),
        // `:166` -- a sync event carries the same body on both sides, which is
        // the same shape as a modification and is listed separately because it
        // is a different thing to a client.
        ("a sync event", Some(node.as_str()), Some(node.as_str())),
    ] {
        let grain = grain_for(pre, post);
        check("queryapi-subscriptions-websocket.json", &grain, what);
    }
}

#[test]
fn a_multi_entry_grain_matches_the_websocket_schema() {
    // `data` has `minItems: 1`, and a grain carrying several resources is the
    // ordinary case for a coalesced window.
    let subscription = SubscriptionRequest {
        resource_path: "/nodes".to_owned(),
        params: Vec::new(),
        max_update_rate_ms: 0,
        persist: true,
        secure: false,
        authorization: false,
        host: "localhost".to_owned(),
        ws_scheme: "ws".to_owned(),
        ws_host: "localhost:8448".to_owned(),
    };
    let manager = SubscriptionManager::new();
    let (subscription, _) = manager
        .create_or_match(&subscription)
        .expect("subscribable");

    let events: Vec<PendingEvent> = (0..3)
        .map(|index| PendingEvent {
            path: format!("{index:08}-0000-4000-8000-000000000000"),
            pre: None,
            post: Some(StoredBody::new(a_node())),
        })
        .collect();
    let text = build_grain(
        &subscription,
        &events,
        "11111111-2222-4333-8444-555555555555",
        TaiCursor::new(1_600_000_000, 0),
    )
    .expect("the stored bodies are JSON");
    let grain: Value = serde_json::from_str(&text).expect("a grain is JSON");

    assert_eq!(grain["grain"]["data"].as_array().map(Vec::len), Some(3));
    check(
        "queryapi-subscriptions-websocket.json",
        &grain,
        "a three-entry grain",
    );
}

// -- the harness itself ----------------------------------------------------

#[test]
fn the_vendored_schemas_are_present_and_compile() {
    // A silently absent schema directory would make every check above vacuous.
    let dir = schema_dir();
    let count = std::fs::read_dir(&dir)
        .expect("the schema directory is readable")
        .filter_map(Result::ok)
        .filter(|entry| entry.path().extension().is_some_and(|ext| ext == "json"))
        .count();
    assert!(
        count >= 40,
        "only {count} schemas found in {}",
        dir.display()
    );

    // And the ones used above resolve their `$ref`s, which is what the
    // retriever exists for -- 37 of the 47 files carry at least one.
    for name in [
        "node.json",
        "nodes.json",
        "error.json",
        "queryapi-base.json",
        "queryapi-subscriptions-websocket.json",
        "registrationapi-health-response.json",
    ] {
        let _ = compile(name);
    }
}
