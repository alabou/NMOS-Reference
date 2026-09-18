// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The browsing view over HTTP: are the links right?
//!
//! Port of `nmos/registry/tests/test_html_links.py`.
//!
//! # Why this exists beside the 63-case browse corpus
//!
//! `browse_parity.rs` compares whole rendered pages against what Python
//! produces, byte for byte, which settles the *renderer*. It says nothing about
//! the wiring: which resolver a handler hands it, what base path that resolver
//! was built with, and whether the page a real GET returns is the one the
//! renderer was asked for.
//!
//! That wiring is where the interesting failures live. A `flow_id` on a Sender
//! must link into `/flows/`, not into `/senders/` -- the generic rule can only
//! point a UUID at the collection being browsed, so every cross-reference would
//! be a 404. And a debug read on the **Registration** API has to link into the
//! **Query** API, because the Registration API has no collections to browse and
//! linking within it would offer nothing but dead ends.

// Test code is exempt from the panic-free lints the workspace denies.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects
)]

use std::collections::HashMap;
use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::http::{Request, StatusCode, header};
use regex::Regex;
use tower::ServiceExt as _;

use nmos_registry::manager::SubscriptionManager;
use nmos_registry::registry::Registry;
use nmos_registry_core::body::Body as StoredBody;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_http::query::{DEFAULT_PAGING_LIMIT, MAX_PAGING_LIMIT, QueryState};
use nmos_registry_http::registration::RegistrationState;
use nmos_registry_http::router;
use nmos_registry_http::security::InterfaceSecurity;

const QUERY_BASE: &str = "/x-nmos/query/v1.3";
const REG_BASE: &str = "/x-nmos/registration/v1.3";

const NODE_ID: &str = "3b8be755-08ff-452b-b217-c9151eb21193";
const DEVICE_ID: &str = "58f6b536-ca4c-43fd-880a-9df2501fc125";
const SOURCE_ID: &str = "9c1e2f3a-4b5c-4d6e-8f70-112233445566";
const FLOW_ID: &str = "a1b2c3d4-e5f6-4708-890a-bcdef0123456";
const SENDER_ID: &str = "0fedcba9-8765-4321-bfed-cba987654321";
const RECEIVER_ID: &str = "11112222-3333-4444-8555-666677778888";

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

    fn put(&self, kind: ResourceType, json: String) {
        self.registry
            .register(kind, StoredBody::new(json))
            .unwrap_or_else(|failure| panic!("fixture rejected: {}", failure.detail));
    }

    /// Node -> Device -> {Source, Flow, Sender, Receiver}, all cross-referenced.
    fn seed_tree(&self) {
        self.put(
            ResourceType::Node,
            format!(
                r#"{{"id":"{NODE_ID}","version":"1:0","label":"n","description":"","tags":{{}},
"href":"http://example.test/","hostname":"example","caps":{{}},
"api":{{"versions":["v1.3"],"endpoints":[]}},"services":[],"clocks":[],"interfaces":[]}}"#
            ),
        );
        self.put(
            ResourceType::Device,
            format!(
                r#"{{"id":"{DEVICE_ID}","version":"1:0","label":"d","description":"","tags":{{}},
"type":"urn:x-nmos:device:generic","node_id":"{NODE_ID}",
"senders":["{SENDER_ID}"],"receivers":["{RECEIVER_ID}"],"controls":[]}}"#
            ),
        );
        self.put(
            ResourceType::Source,
            format!(
                r#"{{"id":"{SOURCE_ID}","version":"1:0","label":"s","description":"","tags":{{}},
"device_id":"{DEVICE_ID}","parents":[],"caps":{{}},
"format":"urn:x-nmos:format:video","clock_name":null}}"#
            ),
        );
        self.put(
            ResourceType::Flow,
            format!(
                r#"{{"id":"{FLOW_ID}","version":"1:0","label":"f","description":"","tags":{{}},
"device_id":"{DEVICE_ID}","source_id":"{SOURCE_ID}","parents":[],
"format":"urn:x-nmos:format:video","media_type":"video/raw",
"frame_width":1920,"frame_height":1080,"interlace_mode":"progressive",
"colorspace":"BT709","components":[]}}"#
            ),
        );
        self.put(
            ResourceType::Sender,
            format!(
                r#"{{"id":"{SENDER_ID}","version":"1:0","label":"snd","description":"","tags":{{}},
"device_id":"{DEVICE_ID}","flow_id":"{FLOW_ID}",
"transport":"urn:x-nmos:transport:rtp","interface_bindings":[],
"subscription":{{"receiver_id":null,"active":false}},"manifest_href":null,"caps":{{}}}}"#
            ),
        );
        self.put(ResourceType::Receiver, format!(
            r#"{{"id":"{RECEIVER_ID}","version":"1:0","label":"rcv","description":"","tags":{{}},
"device_id":"{DEVICE_ID}","transport":"urn:x-nmos:transport:rtp",
"interface_bindings":[],"format":"urn:x-nmos:format:video",
"caps":{{"media_types":["video/raw"]}},
"subscription":{{"sender_id":"{SENDER_ID}","active":true}}}}"#
        ));
    }

    fn browse(&self, router: Router, path: &str) -> (StatusCode, String) {
        let request = Request::builder()
            .method("GET")
            .uri(path)
            .header(header::ACCEPT, "text/html")
            .header(header::HOST, "registry.test:8446")
            .body(Body::empty())
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
                (status, String::from_utf8_lossy(&bytes).into_owned())
            })
    }

    fn query(&self, path: &str) -> (StatusCode, String) {
        self.browse(self.query_router(), path)
    }
}

/// `field name -> href`, for every linked object member on the page.
///
/// The Python port of this is a regex over the rendered markup, and so is this:
/// the markup *is* the contract, and parsing it back is what proves the page a
/// browser receives carries the links rather than that some intermediate
/// structure did.
fn named_links(body: &str) -> HashMap<String, String> {
    let pattern = Regex::new(
        r#"<span class="name">&quot;(\w+)&quot;</span>: <span class="value"><a href="([^"]*)""#,
    )
    .expect("a constant pattern compiles");
    pattern
        .captures_iter(body)
        .map(|c| (c[1].to_owned(), c[2].to_owned()))
        .collect()
}

/// Every `href` on the page, in order.
fn all_links(body: &str) -> Vec<String> {
    let pattern = Regex::new(r#"<a href="([^"]*)""#).expect("a constant pattern compiles");
    pattern
        .captures_iter(body)
        .map(|c| c[1].to_owned())
        .collect()
}

// -- index pages -----------------------------------------------------------

#[test]
fn the_query_base_links_every_collection() {
    let rig = Rig::new();
    let (status, body) = rig.query(QUERY_BASE);
    assert_eq!(status, StatusCode::OK);

    let links = all_links(&body);
    for kind in ResourceType::ALL {
        let expected = format!("{QUERY_BASE}/{}/", kind.plural());
        assert!(
            links.contains(&expected),
            "no link to {expected}: {links:?}"
        );
    }
    assert!(
        links.contains(&format!("{QUERY_BASE}/subscriptions/")),
        "{links:?}",
    );
}

#[test]
fn the_query_discovery_ladder_links_downward() {
    let rig = Rig::new();
    for (path, expected) in [
        ("/", "/x-nmos/"),
        ("/x-nmos", "/x-nmos/query/"),
        ("/x-nmos/query", "/x-nmos/query/v1.3/"),
    ] {
        let (_, body) = rig.query(path);
        assert!(
            all_links(&body).contains(&expected.to_owned()),
            "{path}: no link to {expected} in {:?}",
            all_links(&body),
        );
    }
}

#[test]
fn the_registration_base_entries_are_not_linked() {
    // `resource/` answers 405 (POST and OPTIONS only) and `health/` 404 -- the
    // resource is `/health/nodes/{id}`. Both appear in the index because
    // `registrationapi-base.json` mandates it, but rendering them as links
    // would offer the reader two clicks that cannot work.
    let rig = Rig::new();
    let (status, body) = rig.browse(rig.registration_router(), REG_BASE);

    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("resource/"), "the entries are still listed");
    assert!(body.contains("health/"), "the entries are still listed");
    assert!(
        all_links(&body).is_empty(),
        "a Registration API base entry was linked: {:?}",
        all_links(&body),
    );
}

#[test]
fn the_registration_version_ladder_links() {
    let rig = Rig::new();
    for (path, expected) in [
        ("/x-nmos", "/x-nmos/registration/"),
        ("/x-nmos/registration", "/x-nmos/registration/v1.3/"),
    ] {
        let (_, body) = rig.browse(rig.registration_router(), path);
        assert!(
            all_links(&body).contains(&expected.to_owned()),
            "{path}: {:?}",
            all_links(&body),
        );
    }
}

// -- cross references ------------------------------------------------------

#[test]
fn sender_references_resolve_to_their_own_collections() {
    // The whole reason the per-field resolver exists. The generic rule can only
    // link a UUID into the collection being browsed, which would make
    // `flow_id` point at `/senders/<flow id>` and 404.
    let rig = Rig::new();
    rig.seed_tree();
    let (_, body) = rig.query(&format!("{QUERY_BASE}/senders/{SENDER_ID}"));
    let links = named_links(&body);

    assert_eq!(links["id"], format!("{QUERY_BASE}/senders/{SENDER_ID}"));
    assert_eq!(links["flow_id"], format!("{QUERY_BASE}/flows/{FLOW_ID}"));
    assert_eq!(
        links["device_id"],
        format!("{QUERY_BASE}/devices/{DEVICE_ID}")
    );
}

#[test]
fn a_device_links_up_to_its_node() {
    let rig = Rig::new();
    rig.seed_tree();
    let (_, body) = rig.query(&format!("{QUERY_BASE}/devices/{DEVICE_ID}"));
    assert_eq!(
        named_links(&body)["node_id"],
        format!("{QUERY_BASE}/nodes/{NODE_ID}"),
    );
}

#[test]
fn a_flow_links_to_its_source_and_device() {
    let rig = Rig::new();
    rig.seed_tree();
    let (_, body) = rig.query(&format!("{QUERY_BASE}/flows/{FLOW_ID}"));
    let links = named_links(&body);
    assert_eq!(
        links["source_id"],
        format!("{QUERY_BASE}/sources/{SOURCE_ID}")
    );
    assert_eq!(
        links["device_id"],
        format!("{QUERY_BASE}/devices/{DEVICE_ID}")
    );
}

#[test]
fn a_nested_reference_resolves_by_its_own_key() {
    // `subscription.sender_id` is nested inside an object, and must resolve by
    // its own name rather than by the key of the object holding it.
    let rig = Rig::new();
    rig.seed_tree();
    let (_, body) = rig.query(&format!("{QUERY_BASE}/receivers"));
    assert_eq!(
        named_links(&body)["sender_id"],
        format!("{QUERY_BASE}/senders/{SENDER_ID}"),
    );
}

#[test]
fn a_devices_sender_array_links_elementwise() {
    // Array elements inherit the array's own key, so a `senders` array of
    // UUIDs resolves like the named reference it is.
    let rig = Rig::new();
    rig.seed_tree();
    let (_, body) = rig.query(&format!("{QUERY_BASE}/devices/{DEVICE_ID}"));
    let links = all_links(&body);

    assert!(
        links.contains(&format!("{QUERY_BASE}/senders/{SENDER_ID}")),
        "the senders array was not linked elementwise: {links:?}",
    );
    assert!(
        links.contains(&format!("{QUERY_BASE}/receivers/{RECEIVER_ID}")),
        "{links:?}",
    );
}

#[test]
fn a_collection_listing_links_each_resource() {
    let rig = Rig::new();
    rig.seed_tree();
    let (_, body) = rig.query(&format!("{QUERY_BASE}/senders"));
    assert!(
        all_links(&body).contains(&format!("{QUERY_BASE}/senders/{SENDER_ID}")),
        "{:?}",
        all_links(&body),
    );
}

#[test]
fn every_internal_link_on_a_browsed_tree_actually_resolves() {
    // The property the individual assertions above are examples of: a link the
    // page offers must not 404. This is what catches a resolver pointed at the
    // wrong collection generically rather than one field at a time.
    let rig = Rig::new();
    rig.seed_tree();

    let mut checked = 0;
    for kind in ResourceType::ALL {
        let (_, body) = rig.query(&format!("{QUERY_BASE}/{}", kind.plural()));
        for href in all_links(&body) {
            if !href.starts_with(QUERY_BASE) || href.ends_with('/') {
                // Index entries point at collections, which are separately
                // covered; this is about resource references.
                continue;
            }
            let (status, _) = rig.query(&href);
            assert_eq!(
                status,
                StatusCode::OK,
                "{} offered {href}, which does not resolve",
                kind.plural(),
            );
            checked += 1;
        }
    }
    assert!(
        checked >= 6,
        "only {checked} links were checked -- too few to mean anything"
    );
}

#[test]
fn a_registration_debug_read_links_into_the_query_api() {
    // The Registration API is write-only apart from these debug reads and has
    // no collections to browse, so linking within it would only produce dead
    // ends. Cross-references resolve into the Query API instead.
    let rig = Rig::new();
    rig.seed_tree();
    let (status, body) = rig.browse(
        rig.registration_router(),
        &format!("{REG_BASE}/resource/senders/{SENDER_ID}"),
    );

    assert_eq!(status, StatusCode::OK);
    let links = named_links(&body);
    assert_eq!(links["flow_id"], format!("{QUERY_BASE}/flows/{FLOW_ID}"));
    assert_eq!(
        links["device_id"],
        format!("{QUERY_BASE}/devices/{DEVICE_ID}")
    );

    // `id` is the exception, and deliberately so: it names *this* resource, so
    // it points at the page the reader is already on rather than at the Query
    // API's copy. Measured against Python, which produces the same three links
    // for the same request -- an earlier version of this test asserted that no
    // link may point into the Registration API at all, which would have made a
    // correct implementation look broken.
    assert_eq!(
        links["id"],
        format!("{REG_BASE}/resource/senders/{SENDER_ID}"),
    );
}

// -- the JSON view is unaffected -------------------------------------------

#[test]
fn no_html_without_the_accept_header() {
    let rig = Rig::new();
    rig.seed_tree();
    let request = Request::builder()
        .method("GET")
        .uri(format!("{QUERY_BASE}/senders/{SENDER_ID}"))
        .body(Body::empty())
        .expect("a test request");
    let (status, headers, body) = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("a runtime")
        .block_on(async {
            let response = rig
                .query_router()
                .oneshot(request)
                .await
                .expect("infallible");
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
        });

    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        headers
            .get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok()),
        Some("application/json"),
    );
    assert!(!body.contains("<a href"), "a JSON client was served markup");
    assert!(body.starts_with('{'), "{body}");
}

#[test]
fn the_html_page_still_describes_the_same_resource() {
    // Rendering must not change the data, only its presentation.
    let rig = Rig::new();
    rig.seed_tree();
    let (_, html) = rig.query(&format!("{QUERY_BASE}/senders/{SENDER_ID}"));
    for expected in [SENDER_ID, FLOW_ID, DEVICE_ID, "urn:x-nmos:transport:rtp"] {
        assert!(html.contains(expected), "the page lost {expected}");
    }
}
