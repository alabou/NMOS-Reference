// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The route table. Every route this registry serves is registered here.
//!
//! Port of `nmos/registry/__init__.py`, which is deliberately *the* one place
//! routes appear -- and axum's `Router` preserves that structurally, which is
//! the main reason the plan chose it over raw hyper. The performance argument
//! for hyper does not survive the benchmark, whose costs are the store, the
//! lock and JSON.
//!
//! # Both spellings, no redirect (divergence D6)
//!
//! `APIs.md:85` requires GET/HEAD/OPTIONS to work with or without a trailing
//! slash, and `:92` requires the state-changing verbs to work without one and
//! **not** to be answered with a redirect. Python registers both spellings and
//! *also* runs a trailing-slash middleware as a second chance; the middleware
//! is dropped here and only the dual registration remains.
//!
//! Two reasons, and the second is the real one:
//!
//! * an axum layer cannot cleanly re-enter its own `Router` the way aiohttp
//!   re-resolves against `request.app.router`;
//! * normalising the path would be **silently wrong**. `paging_headers` builds
//!   `Link` from the path the client sent, and the browsing view echoes it in
//!   the heading -- so rewriting it corrupts headers and pages rather than
//!   tidying them.
//!
//! Dual registration already satisfies the requirement on its own, so nothing
//! is lost.
//!
//! # What `matchit` changes, for better and worse
//!
//! **Better:** Python registers `/subscriptions` *before* the generic
//! `{collection}` route because aiohttp resolves in registration order and
//! "subscriptions" would otherwise be captured by the pattern. `matchit`
//! prefers a static segment over a dynamic one regardless of registration
//! order, so the ordering trick is unnecessary here -- and cannot be broken by
//! someone reordering the table.
//!
//! **Worse:** `matchit` has no regex route constraints. Python writes
//! `{resourceId:UUID}` and `{collection:nodes|devices|...}`, so a malformed
//! UUID or an unknown collection is a *routing miss* and a clean 404 before any
//! handler runs. Here those segments match anything, which means each handler's
//! defensive branch -- the one Python's comments call "not reachable through
//! the registered routes" -- becomes load-bearing.

use axum::Router;
use axum::http::{HeaderMap, Uri};
use axum::response::Response;
use axum::routing::{delete, get, options, post};

use crate::discovery;
use crate::query as query_h;
use crate::query::QueryState;
use crate::registration as registration_h;
use crate::registration::RegistrationState;
use crate::response;
use crate::security::InterfaceSecurity;
use crate::websocket;

/// Register a route in both its bare and trailing-slash forms.
///
/// The whole of D6 in three lines. `path` must not already end in `/`.
fn both<S>(router: Router<S>, path: &str, method: axum::routing::MethodRouter<S>) -> Router<S>
where
    S: Clone + Send + Sync + 'static,
{
    debug_assert!(
        !path.ends_with('/') || path == "/",
        "both() adds the trailing slash; `{path}` already has one",
    );
    if path == "/" {
        return router.route("/", method);
    }
    router
        .route(path, method.clone())
        .route(&format!("{path}/"), method)
}

/// The path exactly as the client sent it.
///
/// Not a normalised form and not axum's matched-route pattern: it is echoed in
/// the browsing view's heading and is the base for `Link` headers, so anything
/// other than the raw path changes what clients are told.
fn sent_path(uri: &Uri) -> &str {
    uri.path()
}

/// The IS-04 Registration API's routes.
///
/// No route here requires OAuth 2.0, and that is normative rather than
/// incidental: `NMOS With Control Plane Security.md:105` requires that the
/// Registration API "MUST not require the NMOS Nodes to use OAuth 2.0
/// authorizations", and `:107` requires the registry's DNS-SD `api_auth` to be
/// false. Access control on this interface is TLS only.
pub fn registration(state: RegistrationState, security: InterfaceSecurity) -> Router {
    let prefix = registration_h::BASE_PATH;

    let router: Router<RegistrationState> = Router::new();
    let router = both(
        router,
        "/",
        get(
            |uri: Uri, headers: HeaderMap| async move { discovery::root(sent_path(&uri), &headers) },
        ),
    );
    let router = both(
        router,
        "/x-nmos",
        get(|uri: Uri, headers: HeaderMap| async move {
            discovery::registration_root(sent_path(&uri), &headers)
        }),
    );
    let router = both(
        router,
        "/x-nmos/registration",
        get(|uri: Uri, headers: HeaderMap| async move {
            discovery::registration_versions(sent_path(&uri), &headers)
        }),
    );
    let router = both(
        router,
        prefix,
        get(|uri: Uri, headers: HeaderMap| async move {
            discovery::registration_base(sent_path(&uri), &headers)
        }),
    );

    // `POST /resource` and, per path, its preflight.
    let resource = format!("{prefix}/resource");
    let router = both(router, &resource, post(registration_h::post_resource));
    let router = preflight(router, &resource);

    // `matchit` has no regex constraints, so `{resourceType}` and
    // `{resourceId}` match any segment. Python constrains both in the pattern;
    // here the handlers' own checks are what refuse a bad type or a missing
    // resource -- see the note at the top of this module.
    let one = format!("{resource}/{{resourceType}}/{{resourceId}}");
    let router = both(router, &one, get(registration_h::get_resource));
    let router = both(router, &one, delete(registration_h::delete_resource));
    let router = preflight(router, &one);

    let health = format!("{prefix}/health/nodes/{{nodeId}}");
    let router = both(router, &health, post(registration_h::post_health));
    let router = both(router, &health, get(registration_h::get_health));
    let router = preflight(router, &health);

    // The mTLS gate wraps the whole table, which is what makes it the single
    // enforcement point. `NMOS With OAuth2.0:110`: when mTLS is enabled every
    // state-changing request MUST present a verified client certificate.
    router
        .fallback(not_found)
        .with_state(state)
        .layer(axum::middleware::from_fn_with_state(
            security,
            crate::security::client_auth_layer,
        ))
}

/// The IS-04 Query API's routes.
pub fn query(state: QueryState, security: InterfaceSecurity) -> Router {
    let prefix = query_h::BASE_PATH;

    let router: Router<QueryState> = Router::new();
    let router = both(
        router,
        "/",
        get(
            |uri: Uri, headers: HeaderMap| async move { discovery::root(sent_path(&uri), &headers) },
        ),
    );
    let router = both(
        router,
        "/x-nmos",
        get(|uri: Uri, headers: HeaderMap| async move {
            discovery::query_root(sent_path(&uri), &headers)
        }),
    );
    let router = both(
        router,
        "/x-nmos/query",
        get(|uri: Uri, headers: HeaderMap| async move {
            discovery::query_versions(sent_path(&uri), &headers)
        }),
    );
    let router = both(
        router,
        prefix,
        get(|uri: Uri, headers: HeaderMap| async move {
            discovery::query_base(sent_path(&uri), &headers)
        }),
    );

    // Subscriptions. Python registers these BEFORE the generic `{collection}`
    // route, because aiohttp resolves in registration order and
    // "subscriptions" would otherwise be captured by the pattern. `matchit`
    // prefers a static segment regardless of order, so the ordering is not
    // load-bearing here -- they are simply written first because they read
    // better that way.
    let subscriptions = format!("{prefix}/subscriptions");
    let router = both(router, &subscriptions, post(query_h::post_subscriptions));
    let router = both(router, &subscriptions, get(query_h::get_subscriptions));
    let router = preflight(router, &subscriptions);

    let subscription = format!("{subscriptions}/{{subscriptionId}}");
    let router = both(router, &subscription, get(query_h::get_subscription));
    let router = both(router, &subscription, delete(query_h::delete_subscription));
    let router = preflight(router, &subscription);

    // One route for all six collections. Python constrains the segment to the
    // exact plural names so an unknown collection is a routing miss; `matchit`
    // cannot, so the handler's own check answers the 404.
    let collection = format!("{prefix}/{{collection}}");
    let router = both(router, &collection, get(query_h::get_collection));
    let one = format!("{collection}/{{resourceId}}");
    let router = both(router, &one, get(query_h::get_resource));

    router
        .fallback(not_found)
        .with_state(state)
        .layer(axum::middleware::from_fn_with_state(
            security,
            crate::security::client_auth_layer,
        ))
}

/// The Query API's WebSocket listener, which is its own router on its own port.
///
/// Separate from [`query`] for two reasons, and the first is load-bearing:
/// `ws_href` advertises a **distinct port** (the Node's `--rdsWebSocketPort`
/// defaults to 8448 against a query port of 8446), so the socket has to be
/// reachable there and the path it is registered at is the same one the HTTP
/// API uses. Second, a WebSocket upgrade and a REST request have different
/// lifetimes, and keeping them on one listener would mean one connection's
/// idle socket sharing a server task with every query.
///
/// The upgrade is a GET, so when OAuth 2.0 lands in M6 it is gated exactly as a
/// collection read is -- which is what makes a subscription's `authorization`
/// attribute mean something: if the Query API requires a token, so does the
/// socket it hands out.
pub fn query_websocket(state: QueryState, security: InterfaceSecurity) -> Router {
    let path = format!("{}/subscriptions/{{subscriptionId}}", query_h::BASE_PATH);
    both(Router::new(), &path, get(websocket::subscription_socket))
        .fallback(not_found)
        .with_state(state)
        // The same gate the Query API carries, and for the same reason: the
        // upgrade is a `GET`, so the mTLS half passes it through as a read and
        // the OAuth 2.0 half demands a token exactly as it would for a
        // collection read.
        //
        // `create_query_websocket_app` says what this is for: "when the Query
        // API requires a token, so does the socket it hands out". Without it a
        // registry under `--oauth2` refuses unauthenticated reads on one port
        // while streaming the same resources, unauthenticated, on the other.
        .layer(axum::middleware::from_fn_with_state(
            security,
            crate::security::client_auth_layer,
        ))
}

/// A CORS preflight route for one path, in both spellings.
///
/// Exposed because the route table is the only place that knows which paths
/// exist, and preflight must be answered on those and nowhere else -- answering
/// it everywhere is exactly what a blanket `CorsLayer` would do.
pub fn preflight<S>(router: Router<S>, path: &str) -> Router<S>
where
    S: Clone + Send + Sync + 'static,
{
    both(
        router,
        path,
        options(|headers: HeaderMap| async move {
            response::options(headers.get("access-control-request-headers"))
        }),
    )
}

/// The 404 every unrouted path gets, in NMOS error shape and with CORS.
///
/// axum's default fallback is a bare empty 404 with no body and no CORS
/// headers, which would be the one response in the whole surface that a browser
/// could not read cross-origin and a client could not parse.
async fn not_found(uri: Uri, headers: HeaderMap) -> Response {
    let path = uri.path().to_owned();
    let view = response::RequestView::new(&path, &headers);
    response::error(
        axum::http::StatusCode::NOT_FOUND,
        &format!("no such resource: {path}"),
        &[],
        Some(&view),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::{Request, StatusCode, header};
    use nmos_registry::registry::Registry;
    use nmos_registry_core::store::RegistryStore;
    use std::sync::Arc;
    use tower::ServiceExt as _;

    /// A Registration API router over an empty registry.
    fn registration_router() -> Router {
        registration(
            RegistrationState {
                registry: Arc::new(Registry::new(RegistryStore::new())),
                subscriptions: Arc::new(nmos_registry::manager::SubscriptionManager::new()),
            },
            InterfaceSecurity::registration(false),
        )
    }

    /// A Query API router over an empty registry.
    fn query_router() -> Router {
        query(
            QueryState {
                registry: Arc::new(Registry::new(RegistryStore::new())),
                subscriptions: Arc::new(nmos_registry::manager::SubscriptionManager::new()),
                query_id: "00000000-0000-4000-8000-000000000000".to_owned(),
                tls: false,
                ws_port: 0,
                paging_limit: crate::query::DEFAULT_PAGING_LIMIT,
                paging_limit_max: crate::query::MAX_PAGING_LIMIT,
            },
            InterfaceSecurity::default(),
        )
    }

    fn get_request(path: &str, accept: Option<&str>) -> Request<Body> {
        let mut builder = Request::builder().uri(path).method("GET");
        if let Some(accept) = accept {
            builder = builder.header(header::ACCEPT, accept);
        }
        builder.body(Body::empty()).expect("a test request")
    }

    fn run(router: Router, request: Request<Body>) -> (StatusCode, HeaderMap, String) {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("a current-thread runtime")
            .block_on(async {
                let response = router.oneshot(request).await.expect("infallible");
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

    // -- dual registration -------------------------------------------------

    #[test]
    fn every_route_answers_with_and_without_a_trailing_slash() {
        // `APIs.md:85` and `:92`. Both spellings, and never a redirect.
        for (router, paths) in [
            (
                registration_router as fn() -> Router,
                vec![
                    "/x-nmos",
                    "/x-nmos/registration",
                    "/x-nmos/registration/v1.3",
                ],
            ),
            (
                query_router as fn() -> Router,
                vec!["/x-nmos", "/x-nmos/query", "/x-nmos/query/v1.3"],
            ),
        ] {
            for path in paths {
                for spelling in [path.to_owned(), format!("{path}/")] {
                    let (status, _, body) = run(router(), get_request(&spelling, None));
                    assert_eq!(status, StatusCode::OK, "{spelling}");
                    assert!(body.starts_with('['), "{spelling}: {body}");
                }
            }
        }
    }

    #[test]
    fn a_trailing_slash_is_never_answered_with_a_redirect() {
        // `:92` is explicit that the state-changing verbs must not be
        // redirected, and a 3xx here would also break a client that does not
        // follow them.
        let (status, headers, _) = run(registration_router(), get_request("/x-nmos/", None));
        assert_eq!(status, StatusCode::OK);
        assert!(
            headers.get(header::LOCATION).is_none(),
            "the router redirected instead of serving",
        );
    }

    // -- the two ladders are separate --------------------------------------

    #[test]
    fn each_port_serves_only_its_own_ladder() {
        // Advertising the other API would point a client at an endpoint that
        // is not on this port -- and serving it would be worse.
        let (status, _, _) = run(registration_router(), get_request("/x-nmos/query", None));
        assert_eq!(status, StatusCode::NOT_FOUND);
        let (status, _, _) = run(query_router(), get_request("/x-nmos/registration", None));
        assert_eq!(status, StatusCode::NOT_FOUND);
    }

    #[test]
    fn the_root_is_shared_by_both_ports() {
        for router in [registration_router(), query_router()] {
            let (status, _, body) = run(router, get_request("/", None));
            assert_eq!(status, StatusCode::OK);
            assert_eq!(body, r#"["x-nmos/"]"#);
        }
    }

    // -- the fallback ------------------------------------------------------

    #[test]
    fn an_unrouted_path_gets_an_nmos_error_with_cors() {
        // axum's default fallback is a bare empty 404: no body a client can
        // parse, and no CORS headers a browser can read across origins.
        let (status, headers, body) = run(query_router(), get_request("/x-nmos/query/v9.9", None));
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert_eq!(
            headers
                .get("access-control-allow-origin")
                .and_then(|value| value.to_str().ok()),
            Some("*"),
        );
        assert!(body.contains(r#""code": 404"#), "{body}");
        assert!(body.contains(r#""error": "Not Found""#), "{body}");
        assert!(body.contains("/x-nmos/query/v9.9"), "{body}");
    }

    #[test]
    fn a_browser_gets_the_404_as_a_page() {
        let (status, headers, body) = run(
            query_router(),
            get_request("/x-nmos/query/v9.9", Some("text/html")),
        );
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert_eq!(
            headers
                .get(header::CONTENT_TYPE)
                .and_then(|value| value.to_str().ok()),
            Some("text/html; charset=utf-8"),
        );
        assert!(body.starts_with("<!DOCTYPE html>"));
    }

    // -- CORS on real responses -------------------------------------------

    #[test]
    fn a_served_route_carries_cors_too_not_only_preflight() {
        let (_, headers, _) = run(query_router(), get_request("/x-nmos/query/v1.3", None));
        for name in [
            "access-control-allow-origin",
            "access-control-allow-methods",
            "access-control-allow-headers",
            "access-control-max-age",
            "vary",
        ] {
            assert!(headers.get(name).is_some(), "missing {name}");
        }
    }

    // -- preflight is per path, not blanket --------------------------------

    fn options_request(path: &str) -> Request<Body> {
        Request::builder()
            .uri(path)
            .method("OPTIONS")
            .body(Body::empty())
            .expect("a test request")
    }

    #[test]
    fn preflight_is_answered_on_registered_paths_and_nowhere_else() {
        // The reason for hand-rolling CORS: a blanket layer answers OPTIONS
        // everywhere, so a path that does not exist stops 404ing.
        let (status, headers, _) = run(
            query_router(),
            options_request("/x-nmos/query/v1.3/subscriptions"),
        );
        assert_eq!(status, StatusCode::OK);
        assert_eq!(
            headers
                .get("access-control-allow-methods")
                .and_then(|value| value.to_str().ok()),
            Some("GET, PUT, POST, PATCH, HEAD, OPTIONS, DELETE"),
        );

        let (status, _, _) = run(query_router(), options_request("/nowhere"));
        assert_eq!(
            status,
            StatusCode::NOT_FOUND,
            "preflight was answered on a path that does not exist",
        );
    }

    #[test]
    fn preflight_registers_both_spellings_like_every_other_route() {
        for path in [
            "/x-nmos/query/v1.3/subscriptions",
            "/x-nmos/query/v1.3/subscriptions/",
        ] {
            let (status, _, _) = run(query_router(), options_request(path));
            assert_eq!(status, StatusCode::OK, "{path}");
        }
    }

    #[test]
    fn a_path_registered_twice_for_preflight_would_panic_at_build_time() {
        // axum refuses an overlapping method route, which is what caught a
        // duplicate `preflight` call while the query table was being written.
        // Recorded as a property rather than left as folklore: the route table
        // cannot silently register the same OPTIONS twice.
        let built = std::panic::catch_unwind(|| {
            preflight(query_router(), "/x-nmos/query/v1.3/subscriptions")
        });
        assert!(
            built.is_err(),
            "a duplicate preflight registration was accepted silently",
        );
    }

    // -- the path the client sent -----------------------------------------

    #[test]
    fn the_page_echoes_the_path_the_client_sent_not_a_normalised_one() {
        // Normalising would corrupt `Link` headers and page headings, which is
        // why the trailing-slash middleware was dropped rather than ported.
        let (_, _, body) = run(
            query_router(),
            get_request("/x-nmos/query/v1.3/", Some("text/html")),
        );
        assert!(
            body.contains("<h2>/x-nmos/query/v1.3/</h2>"),
            "the trailing slash was normalised away: {body}",
        );
    }
}
