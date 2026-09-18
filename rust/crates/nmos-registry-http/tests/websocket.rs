// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The Query API WebSocket, over a real socket.
//!
//! Port of the parts of `nmos/registry/tests/test_subscriptions.py` that are
//! about the *listener* rather than the manager -- the sync burst arriving, the
//! four grain shapes on the wire, rate limiting, and a server-side close
//! tearing the socket down.
//!
//! These bind a real TCP listener and speak real WebSocket frames rather than
//! calling the handler, because the things that break here are the upgrade, the
//! `select!` arms and the teardown -- none of which a direct call exercises.

// Test code is exempt from the panic-free lints the workspace denies.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects
)]

use std::sync::Arc;
use std::time::Duration;

use futures_util::StreamExt as _;
use tokio::net::TcpListener;
use tokio_tungstenite::tungstenite::Message;

use nmos_registry::manager::{SubscriptionManager, SubscriptionRequest};
use nmos_registry::matcher::route_once;
use nmos_registry::registry::Registry;
use nmos_registry_core::body::Body as StoredBody;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_http::query::{DEFAULT_PAGING_LIMIT, MAX_PAGING_LIMIT, QueryState};
use nmos_registry_http::router;

const BASE: &str = "/x-nmos/query/v1.3";
const NODE_ID: &str = "3b8be755-08ff-452b-b217-c9151eb21193";

fn node(version: u32, label: &str) -> String {
    format!(
        r#"{{"id":"{NODE_ID}","version":"{version}:0","label":"{label}",
"description":"","tags":{{}},"href":"http://example.test/",
"hostname":"example","caps":{{}},
"api":{{"versions":["v1.3"],"endpoints":[]}},
"services":[],"clocks":[],"interfaces":[]}}"#
    )
}

struct Rig {
    registry: Arc<Registry>,
    subscriptions: Arc<SubscriptionManager>,
    addr: std::net::SocketAddr,
}

impl Rig {
    async fn start(max_update_rate_ms: u32, persist: bool) -> (Self, String) {
        let registry = Arc::new(Registry::new(RegistryStore::new()));
        let subscriptions = Arc::new(SubscriptionManager::new());
        let state = QueryState {
            registry: Arc::clone(&registry),
            subscriptions: Arc::clone(&subscriptions),
            query_id: "11111111-2222-4333-8444-555555555555".to_owned(),
            tls: false,
            ws_port: 0,
            paging_limit: DEFAULT_PAGING_LIMIT,
            paging_limit_max: MAX_PAGING_LIMIT,
        };

        let (subscription, _) = subscriptions
            .create_or_match(&SubscriptionRequest {
                resource_path: "/nodes".to_owned(),
                params: Vec::new(),
                max_update_rate_ms,
                persist,
                secure: false,
                authorization: false,
                host: "localhost".to_owned(),
                ws_scheme: "ws".to_owned(),
                ws_host: "localhost".to_owned(),
            })
            .expect("a subscribable resource_path");

        let listener = TcpListener::bind("127.0.0.1:0").await.expect("a free port");
        let addr = listener.local_addr().expect("a bound address");
        let app = router::query_websocket(
            state,
            // No OAuth 2.0 and no client auth: this file exercises the
            // socket itself, not the gate in front of it.
            nmos_registry_http::security::InterfaceSecurity::default(),
        );
        tokio::spawn(async move {
            let _ = axum::serve(listener, app).await;
        });

        (
            Self {
                registry,
                subscriptions,
                addr,
            },
            subscription.id,
        )
    }

    fn seed(&self, version: u32, label: &str) {
        self.registry
            .register(ResourceType::Node, StoredBody::new(node(version, label)))
            .expect("the fixture registers");
        route_once(&self.registry, &self.subscriptions);
    }

    fn url(&self, subscription_id: &str) -> String {
        format!("ws://{}{BASE}/subscriptions/{subscription_id}", self.addr)
    }
}

type Socket =
    tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;

async fn connect(url: &str) -> Socket {
    let (socket, _) = tokio_tungstenite::connect_async(url)
        .await
        .expect("the upgrade succeeds");
    socket
}

/// The next text frame, or `None` if the socket closed or nothing arrived.
async fn next_grain(socket: &mut Socket) -> Option<serde_json::Value> {
    let deadline = Duration::from_secs(2);
    loop {
        match tokio::time::timeout(deadline, socket.next()).await {
            Err(_) | Ok(None) => return None,
            Ok(Some(Ok(Message::Text(text)))) => {
                return Some(serde_json::from_str(&text).expect("a grain is JSON"));
            }
            // Pings and pongs are transport, not protocol.
            Ok(Some(Ok(_))) => {}
            Ok(Some(Err(_))) => return None,
        }
    }
}

fn entries(grain: &serde_json::Value) -> &Vec<serde_json::Value> {
    grain["grain"]["data"]
        .as_array()
        .expect("a grain carries a data array")
}

// -- the upgrade -----------------------------------------------------------

#[tokio::test]
async fn an_unknown_subscription_is_refused_before_the_upgrade() {
    // A plain 404, not a socket that opens and immediately dies -- which is
    // what a client that mistyped a ws_href should see.
    let (rig, _id) = Rig::start(0, true).await;
    let result =
        tokio_tungstenite::connect_async(rig.url("11111111-1111-4111-8111-111111111111")).await;
    assert!(result.is_err(), "an unknown subscription was upgraded");
}

#[tokio::test]
async fn connecting_delivers_the_sync_burst() {
    // `Behaviour - Querying.md:166` -- the sync events exist so "the client has
    // received all data for a given topic", and carry identical pre and post.
    let (rig, id) = Rig::start(0, true).await;
    rig.seed(1, "before");

    let mut socket = connect(&rig.url(&id)).await;
    let grain = next_grain(&mut socket).await.expect("a sync grain");

    let data = entries(&grain);
    assert_eq!(data.len(), 1);
    assert_eq!(data[0]["path"], NODE_ID);
    assert_eq!(
        data[0]["pre"], data[0]["post"],
        "a sync event carries the same body on both sides",
    );
}

#[tokio::test]
async fn an_empty_registry_sends_no_sync_grain() {
    // An empty grain would violate `queryapi-subscriptions-websocket.json`,
    // whose `data` array has `minItems: 1`. The AMWA mock sends it anyway.
    let (rig, id) = Rig::start(0, true).await;
    let mut socket = connect(&rig.url(&id)).await;

    let got = tokio::time::timeout(Duration::from_millis(300), socket.next()).await;
    assert!(got.is_err(), "an empty sync grain was sent");
}

#[tokio::test]
async fn the_grain_envelope_carries_the_documented_fields() {
    let (rig, id) = Rig::start(0, true).await;
    rig.seed(1, "x");
    let mut socket = connect(&rig.url(&id)).await;
    let grain = next_grain(&mut socket).await.expect("a sync grain");

    assert_eq!(
        grain["grain_type"], "event",
        "`Behaviour - Querying.md:70` fixes the grain type",
    );
    assert_eq!(
        grain["source_id"], "11111111-2222-4333-8444-555555555555",
        "`:37` -- source_id identifies the Query API instance",
    );
    assert_eq!(grain["grain"]["topic"], "/nodes/");
}

// -- the four event shapes -------------------------------------------------

#[tokio::test]
async fn an_added_resource_has_post_only() {
    let (rig, id) = Rig::start(0, true).await;
    let mut socket = connect(&rig.url(&id)).await;

    rig.seed(1, "new");
    let grain = next_grain(&mut socket).await.expect("a grain");
    let data = entries(&grain);

    assert!(
        data[0].get("pre").is_none(),
        "an add must carry no pre: {grain}"
    );
    assert_eq!(data[0]["post"]["label"], "new");
}

#[tokio::test]
async fn a_modified_resource_has_both_sides() {
    let (rig, id) = Rig::start(0, true).await;
    rig.seed(1, "before");
    let mut socket = connect(&rig.url(&id)).await;
    next_grain(&mut socket).await.expect("the sync grain");

    rig.seed(2, "after");
    let grain = next_grain(&mut socket).await.expect("a grain");
    let data = entries(&grain);

    assert_eq!(data[0]["pre"]["label"], "before");
    assert_eq!(data[0]["post"]["label"], "after");
}

#[tokio::test]
async fn a_removed_resource_has_pre_only() {
    let (rig, id) = Rig::start(0, true).await;
    rig.seed(1, "doomed");
    let mut socket = connect(&rig.url(&id)).await;
    next_grain(&mut socket).await.expect("the sync grain");

    rig.registry.delete(ResourceType::Node, NODE_ID);
    route_once(&rig.registry, &rig.subscriptions);
    let grain = next_grain(&mut socket).await.expect("a grain");
    let data = entries(&grain);

    assert_eq!(data[0]["pre"]["label"], "doomed");
    assert!(
        data[0].get("post").is_none(),
        "a removal must carry no post: {grain}",
    );
}

// -- byte fidelity ---------------------------------------------------------

#[tokio::test]
async fn a_grain_splices_the_stored_bytes() {
    // The guarantee end to end: the WebSocket and HTTP views must describe a
    // resource identically, escape for escape. A re-encode would normalise the
    // escape below and a Controller comparing the two views would see a
    // difference that is not there.
    let escape: String = ['\\', 'u', '0', '0', 'e', '9'].iter().collect();
    let (rig, id) = Rig::start(0, true).await;
    let mut socket = connect(&rig.url(&id)).await;

    let stored = node(1, "x").replace(r#""label":"x""#, &format!(r#""label":"caf{escape}""#));
    rig.registry
        .register(ResourceType::Node, StoredBody::new(stored))
        .expect("registers");
    route_once(&rig.registry, &rig.subscriptions);

    let raw = tokio::time::timeout(Duration::from_secs(2), socket.next())
        .await
        .expect("a grain arrives")
        .expect("a frame")
        .expect("no error");
    let text = raw.into_text().expect("a text frame");

    assert!(
        text.contains(&escape),
        "the grain re-encoded the body instead of splicing it:\n{text}",
    );
}

// -- rate limiting ---------------------------------------------------------

#[tokio::test]
async fn changes_within_one_window_coalesce_into_one_grain() {
    // `max_update_rate_ms` is a real bound, not a hint. The AMWA mock writes a
    // grain per event.
    let (rig, id) = Rig::start(400, true).await;
    rig.seed(1, "v0");
    let mut socket = connect(&rig.url(&id)).await;
    next_grain(&mut socket).await.expect("the sync grain");

    for version in 1..=3_u32 {
        rig.seed(version + 1, &format!("v{version}"));
    }

    let grain = next_grain(&mut socket).await.expect("one coalesced grain");
    let data = entries(&grain);
    assert_eq!(data.len(), 1, "three updates must coalesce into one entry");
    assert_eq!(
        data[0]["pre"]["label"], "v0",
        "pre is the state before the FIRST"
    );
    assert_eq!(data[0]["post"]["label"], "v3", "post is after the LAST");
}

// -- teardown --------------------------------------------------------------

#[tokio::test]
async fn deleting_the_subscription_closes_its_socket() {
    // `Behaviour - Querying.md:19` -- connected clients "SHOULD be forcibly
    // closed by the server". Without the shutdown signal the handler would sit
    // in its reader arm until the client happened to go away on its own.
    let (rig, id) = Rig::start(0, true).await;
    let mut socket = connect(&rig.url(&id)).await;

    rig.subscriptions.delete(&id);

    let closed = tokio::time::timeout(Duration::from_secs(2), async {
        while let Some(frame) = socket.next().await {
            match frame {
                Ok(Message::Close(_)) | Err(_) => return true,
                Ok(_) => {}
            }
        }
        true
    })
    .await;
    assert_eq!(closed, Ok(true), "a deleted subscription kept serving");
}

#[tokio::test]
async fn a_client_disconnect_detaches_the_connection() {
    let (rig, id) = Rig::start(0, true).await;
    {
        let mut socket = connect(&rig.url(&id)).await;
        socket.close(None).await.expect("a clean close");
    }

    // The handler notices through its reader arm and disconnects.
    for _ in 0..40 {
        if rig.subscriptions.grain_count() == 0 {
            return;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("the connection was never detached after the client left");
}

#[tokio::test]
async fn a_non_persistent_subscription_is_reaped_when_its_client_leaves() {
    // `:18` -- the Query API MAY remove a non-persistent subscription that no
    // longer has WebSocket connections.
    let (rig, id) = Rig::start(0, false).await;
    assert_eq!(rig.subscriptions.count(), 1);
    {
        let mut socket = connect(&rig.url(&id)).await;
        socket.close(None).await.expect("a clean close");
    }

    for _ in 0..40 {
        if rig.subscriptions.count() == 0 {
            return;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("a non-persistent subscription outlived its last client");
}

#[tokio::test]
async fn two_clients_on_one_subscription_both_receive() {
    let (rig, id) = Rig::start(0, true).await;
    let mut first = connect(&rig.url(&id)).await;
    let mut second = connect(&rig.url(&id)).await;
    assert_eq!(rig.subscriptions.grain_count(), 2);

    rig.seed(1, "fanned");

    for (name, socket) in [("first", &mut first), ("second", &mut second)] {
        let grain = next_grain(socket)
            .await
            .unwrap_or_else(|| panic!("{name} received nothing"));
        assert_eq!(entries(&grain)[0]["post"]["label"], "fanned", "{name}");
    }
}
