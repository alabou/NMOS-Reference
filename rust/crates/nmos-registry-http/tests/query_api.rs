// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The Query API, driven through the real router.
//!
//! Port of the parts of `nmos/registry/tests/test_query_api.py` that do not
//! need TLS, OAuth 2.0 or a live WebSocket. Every request goes through
//! `Router`, so the route table and the handler are exercised together.

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

use nmos_registry::manager::SubscriptionManager;
use nmos_registry::registry::Registry;
use nmos_registry_core::body::Body as StoredBody;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_http::query::{DEFAULT_PAGING_LIMIT, MAX_PAGING_LIMIT, QueryState};
use nmos_registry_http::router;
use nmos_registry_http::security::InterfaceSecurity;

const BASE: &str = "/x-nmos/query/v1.3";

fn node(id: &str, version: u32, label: &str) -> String {
    format!(
        r#"{{"id":"{id}","version":"{version}:0","label":"{label}",
"description":"","tags":{{}},"href":"http://example.test/",
"hostname":"example","caps":{{}},
"api":{{"versions":["v1.3"],"endpoints":[]}},
"services":[],"clocks":[],"interfaces":[]}}"#
    )
}

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

    fn router(&self) -> Router {
        router::query(
            QueryState {
                registry: Arc::clone(&self.registry),
                subscriptions: Arc::clone(&self.subscriptions),
                query_id: "11111111-2222-4333-8444-555555555555".to_owned(),
                tls: false,
                ws_port: 8081,
                paging_limit: DEFAULT_PAGING_LIMIT,
                paging_limit_max: MAX_PAGING_LIMIT,
            },
            InterfaceSecurity::default(),
        )
    }

    fn seed(&self, id: &str, version: u32, label: &str) {
        self.registry
            .register(
                ResourceType::Node,
                StoredBody::new(node(id, version, label)),
            )
            .expect("the fixture registers");
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

    fn get(&self, path: &str) -> (StatusCode, HeaderMap, String) {
        self.send(
            Request::builder()
                .method("GET")
                .uri(path)
                .body(Body::empty())
                .expect("a test request"),
        )
    }

    fn post(&self, path: &str, body: &str) -> (StatusCode, HeaderMap, String) {
        self.send(
            Request::builder()
                .method("POST")
                .uri(path)
                .header(header::CONTENT_TYPE, "application/json")
                .header(header::HOST, "registry.test:8080")
                .body(Body::from(body.to_owned()))
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
}

fn header(headers: &HeaderMap, name: &str) -> Option<String> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(ToOwned::to_owned)
}

// -- collections -----------------------------------------------------------

#[test]
fn an_empty_collection_is_an_empty_array() {
    let rig = Rig::new();
    let (status, _, body) = rig.get(&format!("{BASE}/nodes"));
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, "[]");
}

#[test]
fn a_one_member_collection_is_still_an_array() {
    // The shape must not depend on how many resources happen to be registered.
    let rig = Rig::new();
    rig.seed("3b8be755-08ff-452b-b217-c9151eb21193", 1, "only");
    let (status, _, body) = rig.get(&format!("{BASE}/nodes"));

    assert_eq!(status, StatusCode::OK);
    assert!(
        body.starts_with('['),
        "a one-member collection was not an array: {body}"
    );
    assert!(body.ends_with(']'), "{body}");
    let parsed: serde_json::Value = serde_json::from_str(&body).expect("valid JSON");
    assert_eq!(parsed.as_array().map(Vec::len), Some(1));
}

#[test]
fn a_collection_serves_the_bytes_that_were_registered() {
    let rig = Rig::new();
    let stored = node("3b8be755-08ff-452b-b217-c9151eb21193", 1, "n");
    rig.registry
        .register(ResourceType::Node, StoredBody::new(stored.clone()))
        .expect("registers");

    let (_, _, body) = rig.get(&format!("{BASE}/nodes"));
    assert_eq!(
        body,
        format!("[{stored}]"),
        "the registry re-encoded the body"
    );
}

#[test]
fn one_resource_is_served_bare_not_as_a_one_member_array() {
    let rig = Rig::new();
    let id = "3b8be755-08ff-452b-b217-c9151eb21193";
    let stored = node(id, 1, "n");
    rig.registry
        .register(ResourceType::Node, StoredBody::new(stored.clone()))
        .expect("registers");

    let (status, _, body) = rig.get(&format!("{BASE}/nodes/{id}"));
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, stored);
}

#[test]
fn an_unregistered_resource_is_404() {
    let rig = Rig::new();
    let (status, _, body) = rig.get(&format!(
        "{BASE}/nodes/3b8be755-08ff-452b-b217-c9151eb21193"
    ));
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(body.contains("was not found"), "{body}");
}

#[test]
fn an_unknown_collection_is_404() {
    // Python constrains `{collection}` in the route pattern; `matchit` cannot,
    // so the handler's check is the only one.
    let rig = Rig::new();
    let (status, _, body) = rig.get(&format!("{BASE}/widgets"));
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(body.contains("unknown collection 'widgets'"), "{body}");
}

#[test]
fn every_collection_is_reachable() {
    let rig = Rig::new();
    for kind in ResourceType::ALL {
        let (status, _, body) = rig.get(&format!("{BASE}/{}", kind.plural()));
        assert_eq!(status, StatusCode::OK, "{kind:?}");
        assert_eq!(body, "[]", "{kind:?}");
    }
}

// -- paging ----------------------------------------------------------------

#[test]
fn a_collection_carries_the_paging_headers() {
    let rig = Rig::new();
    rig.seed("3b8be755-08ff-452b-b217-c9151eb21193", 1, "a");
    let (_, headers, _) = rig.get(&format!("{BASE}/nodes"));

    for name in ["x-paging-limit", "x-paging-since", "x-paging-until", "link"] {
        assert!(header(&headers, name).is_some(), "missing {name}");
    }
    assert_eq!(
        header(&headers, "x-paging-limit").as_deref(),
        Some("10"),
        "the default page size is 10",
    );
}

#[test]
fn the_link_header_names_all_four_relations() {
    let rig = Rig::new();
    rig.seed("3b8be755-08ff-452b-b217-c9151eb21193", 1, "a");
    let (_, headers, _) = rig.get(&format!("{BASE}/nodes"));
    let link = header(&headers, "link").expect("a Link header");

    for relation in ["next", "prev", "first", "last"] {
        assert!(
            link.contains(&format!(r#"rel="{relation}""#)),
            "no {relation} link: {link}",
        );
    }
}

#[test]
fn the_link_header_targets_an_absolute_url() {
    // AMWA IS-04-02 `test_21_*` refuse a `Link` whose target is not "http://"
    // or "https://" -- all nine paging tests failed against a bare path, which
    // is what this asserted before. Python builds
    // `str(request.url.with_query(None))`.
    //
    // Note the contrast with `Location` on the Registration API, which is
    // deliberately a path so it survives a reverse proxy. A `Link` has to be
    // dereferenceable on its own.
    let rig = Rig::new();
    rig.seed("3b8be755-08ff-452b-b217-c9151eb21193", 1, "a");
    let (_, headers, _) = rig.send(
        Request::builder()
            .method("GET")
            .uri(format!("{BASE}/nodes"))
            .header(header::HOST, "registry.test:8446")
            .body(Body::empty())
            .expect("a test request"),
    );
    let link = header(&headers, "link").expect("a Link header");

    assert!(
        link.contains("<http://registry.test:8446/x-nmos/query/v1.3/nodes?"),
        "the Link target is not an absolute URL: {link}",
    );
    assert_eq!(
        link.matches("<http://").count(),
        4,
        "every relation must be absolute, not just the first: {link}",
    );
}

#[test]
fn the_link_urls_are_built_from_the_path_without_the_clients_query() {
    // Carrying the client's own query through would duplicate every parameter
    // in every link.
    let rig = Rig::new();
    rig.seed("3b8be755-08ff-452b-b217-c9151eb21193", 1, "a");
    let (_, headers, _) = rig.get(&format!("{BASE}/nodes?paging.limit=5"));
    let link = header(&headers, "link").expect("a Link header");

    assert!(link.contains("/x-nmos/query/v1.3/nodes?"), "{link}");
    assert_eq!(
        link.matches("paging.limit").count(),
        4,
        "one limit per relation, not two: {link}",
    );
}

#[test]
fn a_requested_limit_above_the_ceiling_is_clamped_and_reported() {
    // `QueryAPI.raml:137` -- "the actual page size used would be returned in
    // X-Paging-Limit".
    let rig = Rig::new();
    let (status, headers, _) = rig.get(&format!("{BASE}/nodes?paging.limit=5000"));
    assert_eq!(status, StatusCode::OK);
    assert_eq!(header(&headers, "x-paging-limit").as_deref(), Some("100"));
}

#[test]
fn a_zero_limit_is_a_200_with_an_empty_body() {
    // AMWA IS-04-02 `test_21_4`.
    let rig = Rig::new();
    rig.seed("3b8be755-08ff-452b-b217-c9151eb21193", 1, "a");
    let (status, _, body) = rig.get(&format!("{BASE}/nodes?paging.limit=0"));
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, "[]");
}

#[test]
fn a_malformed_cursor_is_400() {
    let rig = Rig::new();
    let (status, _, body) = rig.get(&format!("{BASE}/nodes?paging.since=nonsense"));
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body.contains(r#""code": 400"#), "{body}");
}

#[test]
fn a_malformed_limit_is_400() {
    let rig = Rig::new();
    let (status, _, _) = rig.get(&format!("{BASE}/nodes?paging.limit=many"));
    assert_eq!(status, StatusCode::BAD_REQUEST);
}

#[test]
fn a_single_resource_ignores_paging_parameters() {
    // `QueryAPI.raml:157` -- this route carries `downgrade` but not `paged`.
    let rig = Rig::new();
    let id = "3b8be755-08ff-452b-b217-c9151eb21193";
    rig.seed(id, 1, "n");
    let (status, headers, _) = rig.get(&format!("{BASE}/nodes/{id}?paging.limit=nonsense"));

    assert_eq!(
        status,
        StatusCode::OK,
        "paging was validated where it does not apply"
    );
    assert!(header(&headers, "x-paging-limit").is_none());
}

// -- filters ---------------------------------------------------------------

#[test]
fn a_basic_query_filter_narrows_the_collection() {
    let rig = Rig::new();
    rig.seed("3b8be755-08ff-452b-b217-c9151eb21193", 1, "keep");
    rig.seed("58f6b536-ca4c-43fd-880a-9df2501fc125", 1, "drop");

    let (status, _, body) = rig.get(&format!("{BASE}/nodes?label=keep"));
    assert_eq!(status, StatusCode::OK);
    let parsed: serde_json::Value = serde_json::from_str(&body).expect("valid JSON");
    assert_eq!(parsed.as_array().map(Vec::len), Some(1), "{body}");
    assert_eq!(parsed[0]["label"], "keep");
}

#[test]
fn a_filter_is_echoed_into_the_paging_links() {
    // A client following `next` must keep its filter, or page two is a
    // different query than page one.
    let rig = Rig::new();
    rig.seed("3b8be755-08ff-452b-b217-c9151eb21193", 1, "keep");
    let (_, headers, _) = rig.get(&format!("{BASE}/nodes?label=keep"));
    let link = header(&headers, "link").expect("a Link header");
    assert!(link.contains("label=keep"), "{link}");
}

#[test]
fn an_unsupported_query_feature_is_501_not_400() {
    // The request is well formed; this registry simply does not implement it.
    let rig = Rig::new();
    let (status, _, body) = rig.get(&format!("{BASE}/nodes?query.rql=eq(label,x)"));
    assert_eq!(status, StatusCode::NOT_IMPLEMENTED);
    assert!(body.contains(r#""code": 501"#), "{body}");
}

// -- subscriptions ---------------------------------------------------------

const SUBSCRIPTION: &str = r#"{"max_update_rate_ms":100,"persist":true,
"resource_path":"/nodes","params":{}}"#;

#[test]
fn creating_a_subscription_is_201_with_a_location() {
    let rig = Rig::new();
    let (status, headers, body) = rig.post(&format!("{BASE}/subscriptions"), SUBSCRIPTION);

    assert_eq!(status, StatusCode::CREATED);
    let parsed: serde_json::Value = serde_json::from_str(&body).expect("valid JSON");
    let id = parsed["id"].as_str().expect("an id");
    assert_eq!(
        header(&headers, "location").as_deref(),
        Some(format!("{BASE}/subscriptions/{id}").as_str()),
    );
}

#[test]
fn an_identical_request_matches_the_existing_subscription_with_200() {
    // `Behaviour - Querying.md:25` -- what stops a reconnecting Controller
    // accumulating duplicates.
    let rig = Rig::new();
    let (_, _, first) = rig.post(&format!("{BASE}/subscriptions"), SUBSCRIPTION);
    let (status, _, second) = rig.post(&format!("{BASE}/subscriptions"), SUBSCRIPTION);

    assert_eq!(status, StatusCode::OK, "a match must be 200, not 201");
    let first: serde_json::Value = serde_json::from_str(&first).expect("valid JSON");
    let second: serde_json::Value = serde_json::from_str(&second).expect("valid JSON");
    assert_eq!(first["id"], second["id"]);
}

#[test]
fn the_ws_href_advertises_the_websocket_listeners_port() {
    // The host is the one the client reached us by; only the port is
    // substituted, because the WebSocket listener is a separate socket.
    let rig = Rig::new();
    let (_, _, body) = rig.post(&format!("{BASE}/subscriptions"), SUBSCRIPTION);
    let parsed: serde_json::Value = serde_json::from_str(&body).expect("valid JSON");
    let href = parsed["ws_href"].as_str().expect("a ws_href");

    assert!(href.starts_with("ws://registry.test:8081/"), "{href}");
    assert!(
        href.ends_with(parsed["id"].as_str().expect("an id")),
        "{href}"
    );
}

#[test]
fn a_missing_required_attribute_is_400_and_names_it() {
    let rig = Rig::new();
    let (status, _, body) = rig.post(
        &format!("{BASE}/subscriptions"),
        r#"{"persist":true,"resource_path":"/nodes","params":{}}"#,
    );
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(
        body.contains("missing required attributes: max_update_rate_ms"),
        "{body}",
    );
}

#[test]
fn each_attribute_is_type_checked() {
    let rig = Rig::new();
    for (body, expected) in [
        (
            r#"{"max_update_rate_ms":100,"persist":true,"resource_path":5,"params":{}}"#,
            "resource_path must be a string",
        ),
        (
            r#"{"max_update_rate_ms":"x","persist":true,"resource_path":"/nodes","params":{}}"#,
            "max_update_rate_ms must be an integer",
        ),
        (
            r#"{"max_update_rate_ms":-1,"persist":true,"resource_path":"/nodes","params":{}}"#,
            "max_update_rate_ms must not be negative",
        ),
        (
            r#"{"max_update_rate_ms":100,"persist":"yes","resource_path":"/nodes","params":{}}"#,
            "persist must be a boolean",
        ),
        (
            r#"{"max_update_rate_ms":100,"persist":true,"resource_path":"/nodes","params":[]}"#,
            "params must be an object",
        ),
    ] {
        let (status, _, response) = rig.post(&format!("{BASE}/subscriptions"), body);
        assert_eq!(status, StatusCode::BAD_REQUEST, "{body}");
        assert!(response.contains(expected), "{body}\n  got: {response}");
    }
}

#[test]
fn a_boolean_is_not_accepted_where_an_integer_is_required() {
    // Python has to say `isinstance(x, bool)` explicitly because a bool IS an
    // int there. This pins that the Rust side draws the same line.
    let rig = Rig::new();
    let (status, _, body) = rig.post(
        &format!("{BASE}/subscriptions"),
        r#"{"max_update_rate_ms":true,"persist":true,"resource_path":"/nodes","params":{}}"#,
    );
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(
        body.contains("max_update_rate_ms must be an integer"),
        "{body}"
    );
}

#[test]
fn an_unsubscribable_resource_path_is_400() {
    let rig = Rig::new();
    let (status, _, body) = rig.post(
        &format!("{BASE}/subscriptions"),
        r#"{"max_update_rate_ms":100,"persist":true,"resource_path":"/bogus","params":{}}"#,
    );
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body.contains("is not subscribable"), "{body}");
}

#[test]
fn requesting_secure_on_a_plaintext_listener_is_400() {
    // `Behaviour - Querying.md:13` -- a mismatch is not merely unsupported, it
    // is unimplementable: the WebSocket listener shares the HTTP listener's TLS.
    let rig = Rig::new();
    let (status, _, body) = rig.post(
        &format!("{BASE}/subscriptions"),
        r#"{"max_update_rate_ms":100,"persist":true,"resource_path":"/nodes",
"params":{},"secure":true}"#,
    );
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body.contains("secure=true was requested"), "{body}");
}

#[test]
fn requesting_authorization_where_there_is_none_is_400() {
    let rig = Rig::new();
    let (status, _, body) = rig.post(
        &format!("{BASE}/subscriptions"),
        r#"{"max_update_rate_ms":100,"persist":true,"resource_path":"/nodes",
"params":{},"authorization":true}"#,
    );
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body.contains("authorization=true was requested"), "{body}");
}

#[test]
fn filter_values_are_normalised_to_query_string_text() {
    // A client may send `true` or `5` where the query string would carry
    // `"true"` or `"5"`.
    let rig = Rig::new();
    let (status, _, body) = rig.post(
        &format!("{BASE}/subscriptions"),
        r#"{"max_update_rate_ms":100,"persist":true,"resource_path":"/nodes",
"params":{"a":true,"b":5,"c":null}}"#,
    );
    assert_eq!(status, StatusCode::CREATED);
    let parsed: serde_json::Value = serde_json::from_str(&body).expect("valid JSON");
    assert_eq!(parsed["params"]["a"], "true");
    assert_eq!(parsed["params"]["b"], "5");
    assert_eq!(parsed["params"]["c"], "null");
}

#[test]
fn a_malformed_subscription_body_is_400() {
    let rig = Rig::new();
    assert_eq!(
        rig.post(&format!("{BASE}/subscriptions"), "not json").0,
        StatusCode::BAD_REQUEST,
    );
    assert_eq!(
        rig.post(&format!("{BASE}/subscriptions"), "[]").0,
        StatusCode::BAD_REQUEST,
    );
}

#[test]
fn listing_subscriptions_is_always_an_array() {
    let rig = Rig::new();
    assert_eq!(rig.get(&format!("{BASE}/subscriptions")).2, "[]");

    rig.post(&format!("{BASE}/subscriptions"), SUBSCRIPTION);
    let (status, _, body) = rig.get(&format!("{BASE}/subscriptions"));
    assert_eq!(status, StatusCode::OK);
    let parsed: serde_json::Value = serde_json::from_str(&body).expect("valid JSON");
    assert_eq!(parsed.as_array().map(Vec::len), Some(1), "{body}");
}

#[test]
fn listing_subscriptions_validates_paging_even_though_it_does_not_page() {
    // `QueryAPI.raml:441` gives it the `paged` trait, and rejecting a
    // malformed cursor here keeps the behaviour uniform across paged resources.
    let rig = Rig::new();
    let (status, _, _) = rig.get(&format!("{BASE}/subscriptions?paging.since=nonsense"));
    assert_eq!(status, StatusCode::BAD_REQUEST);
}

#[test]
fn reading_one_subscription_returns_it() {
    let rig = Rig::new();
    let (_, _, created) = rig.post(&format!("{BASE}/subscriptions"), SUBSCRIPTION);
    let created: serde_json::Value = serde_json::from_str(&created).expect("valid JSON");
    let id = created["id"].as_str().expect("an id");

    let (status, _, body) = rig.get(&format!("{BASE}/subscriptions/{id}"));
    assert_eq!(status, StatusCode::OK);
    let parsed: serde_json::Value = serde_json::from_str(&body).expect("valid JSON");
    assert_eq!(parsed["id"], created["id"]);
    assert_eq!(parsed["resource_path"], "/nodes");
}

#[test]
fn reading_an_unknown_subscription_is_404() {
    let rig = Rig::new();
    let (status, _, body) = rig.get(&format!("{BASE}/subscriptions/nope"));
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(body.contains("was not found"), "{body}");
}

#[test]
fn deleting_a_persistent_subscription_is_204() {
    let rig = Rig::new();
    let (_, _, created) = rig.post(&format!("{BASE}/subscriptions"), SUBSCRIPTION);
    let created: serde_json::Value = serde_json::from_str(&created).expect("valid JSON");
    let id = created["id"].as_str().expect("an id");

    let (status, _, body) = rig.delete(&format!("{BASE}/subscriptions/{id}"));
    assert_eq!(status, StatusCode::NO_CONTENT);
    assert!(body.is_empty());
    assert_eq!(
        rig.get(&format!("{BASE}/subscriptions/{id}")).0,
        StatusCode::NOT_FOUND,
    );
}

#[test]
fn deleting_a_non_persistent_subscription_is_403() {
    // `Behaviour - Querying.md:18` -- it belongs to the API, which reaps it
    // when its last WebSocket closes. Letting a client delete one would let it
    // destroy a subscription another client is still using.
    let rig = Rig::new();
    let (_, _, created) = rig.post(
        &format!("{BASE}/subscriptions"),
        r#"{"max_update_rate_ms":100,"persist":false,"resource_path":"/nodes","params":{}}"#,
    );
    let created: serde_json::Value = serde_json::from_str(&created).expect("valid JSON");
    let id = created["id"].as_str().expect("an id");

    let (status, _, body) = rig.delete(&format!("{BASE}/subscriptions/{id}"));
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert!(body.contains(r#""code": 403"#), "{body}");
}

#[test]
fn deleting_an_unknown_subscription_is_404() {
    let rig = Rig::new();
    assert_eq!(
        rig.delete(&format!("{BASE}/subscriptions/nope")).0,
        StatusCode::NOT_FOUND,
    );
}

#[test]
fn subscriptions_is_not_captured_by_the_collection_route() {
    // Python has to register it first because aiohttp resolves in registration
    // order; `matchit` prefers the static segment regardless. Pinned so a
    // future router change cannot silently turn `/subscriptions` into an
    // unknown collection.
    let rig = Rig::new();
    let (status, _, body) = rig.get(&format!("{BASE}/subscriptions"));
    assert_eq!(status, StatusCode::OK);
    assert!(
        !body.contains("unknown collection"),
        "the generic collection route captured /subscriptions: {body}",
    );
}
