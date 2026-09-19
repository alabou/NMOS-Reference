// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Every route, in both spellings, against every verb.
//!
//! The handler tests each prove one route does the right thing. This proves the
//! *table* is right: that nothing is registered under only one spelling, that a
//! verb a path does not serve is refused rather than mishandled, and that the
//! routes which must not shadow each other do not.
//!
//! # What makes this worth its own file
//!
//! `matchit` has no regex route constraints, so several distinctions Python
//! draws in the pattern are drawn here by the table's shape instead:
//!
//! * `/subscriptions` must not be captured by `{collection}`. Python registers
//!   it first because aiohttp resolves in registration order; `matchit` prefers
//!   a static segment regardless, and this pins that so a future reordering
//!   cannot quietly turn it into an unknown collection.
//! * `{resourceId}` matches anything, so a malformed UUID reaches the handler
//!   and its defensive 404 is load-bearing rather than unreachable.
//!
//! # `APIs.md:85` and `:92`
//!
//! GET/HEAD/OPTIONS must work with or without a trailing slash, and the
//! state-changing verbs must work **without** one and must not be answered with
//! a redirect. Registering both spellings satisfies all of it without a single
//! 3xx, which is what the sweep below checks route by route.

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
use nmos_registry_core::store::RegistryStore;
use nmos_registry_http::query::{DEFAULT_PAGING_LIMIT, MAX_PAGING_LIMIT, QueryState};
use nmos_registry_http::registration::RegistrationState;
use nmos_registry_http::router;
use nmos_registry_http::security::InterfaceSecurity;

const QUERY_BASE: &str = "/x-nmos/query/v1.3";
const REG_BASE: &str = "/x-nmos/registration/v1.3";
const UUID: &str = "3b8be755-08ff-452b-b217-c9151eb21193";

const VERBS: &[&str] = &["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];

fn query_router() -> Router {
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
        InterfaceSecurity::default(),
    )
}

fn registration_router() -> Router {
    router::registration(
        RegistrationState {
            registry: Arc::new(Registry::new(RegistryStore::new())),
            backend: std::sync::Arc::new(nmos_registry_backend::StandaloneBackend::new(Arc::new(
                Registry::new(RegistryStore::new()),
            ))),
            subscriptions: std::sync::Arc::new(nmos_registry::manager::SubscriptionManager::new()),
        },
        InterfaceSecurity::registration(false),
    )
}

fn send(router: Router, verb: &str, path: &str) -> (StatusCode, HeaderMap) {
    let request = Request::builder()
        .method(verb)
        .uri(path)
        .header(header::HOST, "registry.test")
        .body(Body::empty())
        .expect("a test request");
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("a runtime")
        .block_on(async {
            let response = router.oneshot(request).await.expect("infallible");
            (response.status(), response.headers().clone())
        })
}

/// Every path the table registers, with the verbs it is expected to serve.
///
/// `OPTIONS` is listed only where a preflight route exists, because Python
/// registers it per path rather than blanket -- an unknown path must still 404
/// rather than be answered by a CORS layer.
fn query_routes() -> Vec<(String, Vec<&'static str>)> {
    let mut routes: Vec<(String, Vec<&'static str>)> = vec![
        ("/".to_owned(), vec!["GET"]),
        ("/x-nmos".to_owned(), vec!["GET"]),
        ("/x-nmos/query".to_owned(), vec!["GET"]),
        (QUERY_BASE.to_owned(), vec!["GET"]),
        (
            format!("{QUERY_BASE}/subscriptions"),
            vec!["GET", "POST", "OPTIONS"],
        ),
        (
            format!("{QUERY_BASE}/subscriptions/{UUID}"),
            vec!["GET", "DELETE", "OPTIONS"],
        ),
    ];
    for plural in [
        "nodes",
        "devices",
        "sources",
        "flows",
        "senders",
        "receivers",
    ] {
        routes.push((format!("{QUERY_BASE}/{plural}"), vec!["GET"]));
        routes.push((format!("{QUERY_BASE}/{plural}/{UUID}"), vec!["GET"]));
    }
    routes
}

fn registration_routes() -> Vec<(String, Vec<&'static str>)> {
    vec![
        ("/".to_owned(), vec!["GET"]),
        ("/x-nmos".to_owned(), vec!["GET"]),
        ("/x-nmos/registration".to_owned(), vec!["GET"]),
        (REG_BASE.to_owned(), vec!["GET"]),
        (format!("{REG_BASE}/resource"), vec!["POST", "OPTIONS"]),
        (
            format!("{REG_BASE}/resource/nodes/{UUID}"),
            vec!["GET", "DELETE", "OPTIONS"],
        ),
        (
            format!("{REG_BASE}/health/nodes/{UUID}"),
            vec!["GET", "POST", "OPTIONS"],
        ),
    ]
}

// -- both spellings --------------------------------------------------------

#[test]
fn every_route_is_registered_in_both_spellings() {
    // `APIs.md:85` and `:92`. A route present under only one spelling answers
    // 404 for the other, which is the failure this sweep exists to catch.
    for (build, routes, api) in [
        (query_router as fn() -> Router, query_routes(), "query"),
        (
            registration_router as fn() -> Router,
            registration_routes(),
            "registration",
        ),
    ] {
        for (path, verbs) in routes {
            if path == "/" {
                continue; // the root has no second spelling
            }
            for verb in verbs {
                // The two spellings must answer **identically**. Comparing them
                // to each other rather than to an expected status is what makes
                // this robust: `GET /subscriptions/{id}` legitimately 404s
                // because no such subscription exists, and an earlier version
                // of this test read that as "the route is missing".
                let (bare, _) = send(build(), verb, &path);
                let (slashed, _) = send(build(), verb, &format!("{path}/"));
                assert_eq!(
                    bare, slashed,
                    "{api}: {verb} {path} answers {bare} but {path}/ answers \
                     {slashed} -- registered under one spelling only",
                );
                assert_ne!(
                    bare,
                    StatusCode::METHOD_NOT_ALLOWED,
                    "{api}: {verb} {path} is not served at all",
                );
            }
        }
    }
}

#[test]
fn no_route_answers_a_trailing_slash_with_a_redirect() {
    // `:92` is explicit that the state-changing verbs must not be redirected,
    // and a 3xx would also break a client that does not follow them.
    for (build, routes) in [
        (query_router as fn() -> Router, query_routes()),
        (registration_router as fn() -> Router, registration_routes()),
    ] {
        for (path, verbs) in routes {
            for verb in verbs {
                for spelling in [path.clone(), format!("{path}/")] {
                    let (status, headers) = send(build(), verb, &spelling);
                    assert!(
                        !status.is_redirection(),
                        "{verb} {spelling} answered {status}",
                    );
                    assert!(
                        headers.get(header::LOCATION).is_none()
                            || status == StatusCode::CREATED
                            || status == StatusCode::OK,
                        "{verb} {spelling} sent a Location with {status}",
                    );
                }
            }
        }
    }
}

// -- verbs a path does not serve -------------------------------------------

#[test]
fn an_unserved_verb_is_405_not_404_or_500() {
    // The distinction matters to a client: 404 says "no such resource", 405
    // says "wrong verb", and only one of those tells it to stop retrying.
    /// A router, one of its paths, and verbs that path does not serve.
    type Unserved = (fn() -> Router, String, Vec<&'static str>);

    let cases: Vec<Unserved> = vec![
        (
            query_router,
            format!("{QUERY_BASE}/nodes"),
            vec!["POST", "PUT", "PATCH", "DELETE"],
        ),
        (
            registration_router,
            format!("{REG_BASE}/resource"),
            vec!["GET", "PUT", "PATCH", "DELETE"],
        ),
        (
            registration_router,
            format!("{REG_BASE}/health/nodes/{UUID}"),
            vec!["PUT", "PATCH", "DELETE"],
        ),
    ];
    for (build, path, verbs) in cases {
        for verb in verbs {
            let (status, _) = send(build(), verb, &path);
            assert_eq!(
                status,
                StatusCode::METHOD_NOT_ALLOWED,
                "{verb} {path} answered {status}",
            );
        }
    }
}

#[test]
fn no_verb_on_any_registered_path_produces_a_server_error() {
    // A sweep rather than a list: every path crossed with every verb, asserting
    // only that nothing 5xxes. A handler reached by a verb nobody considered is
    // exactly where an unwrap would surface.
    for (build, routes) in [
        (query_router as fn() -> Router, query_routes()),
        (registration_router as fn() -> Router, registration_routes()),
    ] {
        for (path, _) in routes {
            for verb in VERBS {
                for spelling in [path.clone(), format!("{path}/")] {
                    let (status, _) = send(build(), verb, &spelling);
                    assert!(
                        !status.is_server_error(),
                        "{verb} {spelling} answered {status}",
                    );
                }
            }
        }
    }
}

// -- shadowing -------------------------------------------------------------

#[test]
fn subscriptions_is_not_shadowed_by_the_collection_route() {
    // Python registers `/subscriptions` before `{collection}` because aiohttp
    // resolves in registration order. `matchit` prefers the static segment
    // regardless, and this pins it so a reordering cannot quietly turn
    // `/subscriptions` into an unknown collection.
    let (status, _) = send(
        query_router(),
        "GET",
        &format!("{QUERY_BASE}/subscriptions"),
    );
    assert_eq!(status, StatusCode::OK);

    // And POST reaches the subscription handler, not a collection that serves
    // no POST at all.
    let (status, _) = send(
        query_router(),
        "POST",
        &format!("{QUERY_BASE}/subscriptions"),
    );
    assert_eq!(
        status,
        StatusCode::BAD_REQUEST,
        "POST /subscriptions did not reach its handler",
    );
}

#[test]
fn an_unknown_collection_is_404_rather_than_a_routing_accident() {
    // `matchit` cannot constrain `{collection}` to the six plural names, so the
    // handler's own check is the only one.
    for path in [
        format!("{QUERY_BASE}/widgets"),
        format!("{QUERY_BASE}/widgets/{UUID}"),
    ] {
        let (status, _) = send(query_router(), "GET", &path);
        assert_eq!(status, StatusCode::NOT_FOUND, "{path}");
    }
}

#[test]
fn a_malformed_uuid_reaches_the_handler_and_is_refused_cleanly() {
    // Python constrains `{resourceId}` in the pattern, so its handler's 404 is
    // "not reachable through the registered routes". Here it is the only check.
    for (build, path) in [
        (
            query_router as fn() -> Router,
            format!("{QUERY_BASE}/nodes/not-a-uuid"),
        ),
        (
            registration_router as fn() -> Router,
            format!("{REG_BASE}/resource/nodes/not-a-uuid"),
        ),
        (
            registration_router as fn() -> Router,
            format!("{REG_BASE}/health/nodes/not-a-uuid"),
        ),
    ] {
        let (status, _) = send(build(), "GET", &path);
        assert_eq!(status, StatusCode::NOT_FOUND, "{path}");
    }
}

// -- CORS ------------------------------------------------------------------

#[test]
fn every_routed_response_carries_cors() {
    // Python decorates every response including 2xx, which is why CORS is
    // hand-rolled rather than a layer.
    for (build, routes) in [
        (query_router as fn() -> Router, query_routes()),
        (registration_router as fn() -> Router, registration_routes()),
    ] {
        for (path, verbs) in routes {
            for verb in verbs {
                let (status, headers) = send(build(), verb, &path);
                assert!(
                    headers.get("access-control-allow-origin").is_some(),
                    "{verb} {path} -> {status} went out without CORS",
                );
            }
        }
    }
}

#[test]
fn preflight_is_not_answered_on_paths_that_do_not_exist() {
    // The reason not to use a blanket CORS layer: it would answer OPTIONS
    // everywhere, and a path that does not exist would stop 404ing.
    for path in ["/nowhere", "/x-nmos/query/v9.9", "/x-nmos/registration"] {
        let (status, _) = send(query_router(), "OPTIONS", path);
        assert_eq!(status, StatusCode::NOT_FOUND, "{path}");
    }
}

#[test]
fn preflight_on_a_matched_route_without_one_is_405_not_404() {
    // A distinction the sweep above must not blur. `{collection}` matches
    // "widgets", so the path *is* routed -- it simply serves no OPTIONS. That
    // is 405, and answering 404 there would tell a client the path does not
    // exist when the table says it does.
    let (status, _) = send(query_router(), "OPTIONS", &format!("{QUERY_BASE}/widgets"));
    assert_eq!(status, StatusCode::METHOD_NOT_ALLOWED);
    let (status, _) = send(query_router(), "OPTIONS", &format!("{QUERY_BASE}/nodes"));
    assert_eq!(status, StatusCode::METHOD_NOT_ALLOWED);
}

// -- the two ports are separate --------------------------------------------

#[test]
fn neither_api_serves_the_others_routes() {
    for path in [
        QUERY_BASE.to_owned(),
        format!("{QUERY_BASE}/nodes"),
        format!("{QUERY_BASE}/subscriptions"),
    ] {
        let (status, _) = send(registration_router(), "GET", &path);
        assert_eq!(status, StatusCode::NOT_FOUND, "registration served {path}");
    }
    for path in [
        REG_BASE.to_owned(),
        format!("{REG_BASE}/resource"),
        format!("{REG_BASE}/health/nodes/{UUID}"),
    ] {
        let (status, _) = send(query_router(), "GET", &path);
        assert_eq!(status, StatusCode::NOT_FOUND, "query served {path}");
    }
}
