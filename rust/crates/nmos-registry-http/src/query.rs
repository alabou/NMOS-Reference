// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The IS-04 Query API's handlers.
//!
//! Port of `nmos/registry/handlers_query.py`.
//!
//! # The order of operations is fixed by the specification
//!
//! Reject unsupported query features, validate downgrade, parse paging, filter,
//! then page. Each step has its own status code -- 501 for a feature this
//! registry does not implement, 400 for a malformed request -- and swapping two
//! of them changes which one a request with two problems is told about.
//!
//! # What is deliberately not here yet
//!
//! **OAuth 2.0.** Python wraps every route in `check_oauth2` with the `query`
//! scope, a pass-through whenever `security.oauth2` is false. It lands with the
//! rest of the security surface in M6; until then the `authorization`
//! negotiation below always compares against "disabled", which is what an
//! unsecured Query API must answer.
//!
//! **The query metric.** Python records collection size, matched count, page
//! size and filter count per query, because "query took 8 ms" is not
//! diagnosable while "8 ms over 20,000 resources, 3 matched" is. `metrics.rs`
//! is scheduled with the performance gate in M7; the inputs it needs are all
//! available at the one call site below, which is the point of recording them
//! there.

use std::sync::Arc;

use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, HeaderName, HeaderValue, StatusCode, Uri};
use axum::response::Response;

use nmos_registry::manager::{SubscriptionManager, SubscriptionRequest};
use nmos_registry::registry::Registry;
use nmos_registry_core::links::LinkResolver;
use nmos_registry_core::paging::{paging_headers, parse_paging};
use nmos_registry_core::query_filter;
use nmos_registry_core::resource_type::ResourceType;

use crate::response::{self, Caching, RequestView};

/// The Query API's base path.
pub const BASE_PATH: &str = "/x-nmos/query/v1.3";

/// The server's default page size.
pub const DEFAULT_PAGING_LIMIT: usize = 10;

/// The largest page size the server will honour.
pub const MAX_PAGING_LIMIT: usize = 100;

/// What the handlers need.
#[derive(Clone)]
pub struct QueryState {
    /// The registry being read.
    pub registry: Arc<Registry>,
    /// Every subscription and connection.
    pub subscriptions: Arc<SubscriptionManager>,
    /// A UUID identifying this Query API instance.
    ///
    /// Becomes the `source_id` of every grain (`Behaviour - Querying.md:37`),
    /// which is how a client tells two registries apart when it is subscribed
    /// to both.
    pub query_id: String,
    /// Whether this listener runs TLS.
    ///
    /// Drives the `ws`/`wss` scheme of `ws_href` and the `secure` negotiation
    /// of `Behaviour - Querying.md:13`.
    pub tls: bool,
    /// Port of the companion WebSocket listener, used to build `ws_href`.
    pub ws_port: u16,
    /// Server default page size.
    pub paging_limit: usize,
    /// Largest page size the server will honour.
    pub paging_limit_max: usize,
}

/// The query string as ordered pairs.
///
/// Ordered, and duplicates kept, because `filter_params` passes everything that
/// is not a paging parameter through to the matcher -- and a client that sends
/// one name twice is asking for two filters, not for the last one.
type Params = Vec<(String, String)>;

fn lookup(params: &Params) -> impl Fn(&str) -> Option<String> + use<'_> {
    move |name: &str| {
        params
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.clone())
    }
}

/// `GET /{collection}` -- list a resource type.
pub async fn get_collection(
    State(state): State<QueryState>,
    Path(collection): Path<String>,
    Query(params): Query<Params>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let path = uri.path().to_owned();
    let view = RequestView::new(&path, &headers);

    // Python constrains `{collection}` in the route pattern to the six plural
    // names, so this is unreachable there. `matchit` has no such constraint, so
    // here it is the check.
    let Some(resource_type) = ResourceType::from_plural(&collection) else {
        return response::error(
            StatusCode::NOT_FOUND,
            &format!(
                "unknown collection {}",
                nmos_json::error::python_repr(&collection),
            ),
            &[],
            Some(&view),
        );
    };

    let get = lookup(&params);
    if let Err(unsupported) = query_filter::check_unsupported(&get) {
        // 501, not 400: the request is well formed and this registry simply
        // does not implement the feature.
        return response::error(
            StatusCode::NOT_IMPLEMENTED,
            &unsupported.to_string(),
            &[],
            Some(&view),
        );
    }
    if let Err(bad) = query_filter::check_downgrade(&get) {
        return response::error(StatusCode::BAD_REQUEST, &bad.to_string(), &[], Some(&view));
    }

    let paging = match parse_paging(&get, state.paging_limit, state.paging_limit_max) {
        Ok(paging) => paging,
        Err(bad) => {
            return response::error(StatusCode::BAD_REQUEST, &bad.to_string(), &[], Some(&view));
        }
    };

    let filters = query_filter::filter_params(&params);
    let page = state.registry.page(resource_type, &paging, &filters);

    let fragments: Vec<&str> = page
        .resources
        .iter()
        .map(|resource| resource.body.text())
        .collect();
    let resolver = LinkResolver::new(&path, BASE_PATH);
    let mut out = response::json_body_with_resolver(
        StatusCode::OK,
        &fragments,
        Caching::Default,
        Some(&view),
        Some(&resolver),
    );

    // An **absolute** URL, without its query string. Two separate requirements,
    // and the first was learned the hard way: AMWA IS-04-02 `test_21_*` refuse
    // a `Link` whose target is not "http://" or "https://", so the nine paging
    // tests failed against a path. Python builds the same thing as
    // `str(request.url.with_query(None))`.
    //
    // Note this is the opposite of `Location` on the Registration API, which is
    // deliberately a path (`RegistrationAPI.raml:47`) so it survives a reverse
    // proxy. `Link` has to be dereferenceable on its own.
    //
    // The query string is dropped because the paging links rebuild it from the
    // filters and the cursors; carrying the client's own through would
    // duplicate every parameter.
    let base_url = format!(
        "{}://{}{path}",
        if state.tls { "https" } else { "http" },
        headers
            .get(axum::http::header::HOST)
            .and_then(|value| value.to_str().ok())
            .unwrap_or("localhost"),
    );
    for (name, value) in paging_headers(page.window(), &base_url, &filters, paging.order) {
        if let (Ok(name), Ok(value)) = (
            HeaderName::from_bytes(name.as_bytes()),
            HeaderValue::from_str(&value),
        ) {
            out.headers_mut().insert(name, value);
        }
    }
    out
}

/// `GET /{collection}/{resourceId}` -- one resource.
///
/// Carries the `downgrade` trait but not `paged` (`QueryAPI.raml:157`), so
/// paging parameters are simply not consulted here.
///
/// A 409 would be returned if the resource existed at a lower API version and
/// no adequate downgrade was requested (`:168-174`). This registry holds v1.3
/// only, so a resource either matches the requested version or does not exist.
pub async fn get_resource(
    State(state): State<QueryState>,
    Path((collection, resource_id)): Path<(String, String)>,
    Query(params): Query<Params>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let path = uri.path().to_owned();
    let view = RequestView::new(&path, &headers);

    let Some(resource_type) = ResourceType::from_plural(&collection) else {
        return response::error(
            StatusCode::NOT_FOUND,
            &format!(
                "unknown collection {}",
                nmos_json::error::python_repr(&collection),
            ),
            &[],
            Some(&view),
        );
    };

    if let Err(bad) = query_filter::check_downgrade(lookup(&params)) {
        return response::error(StatusCode::BAD_REQUEST, &bad.to_string(), &[], Some(&view));
    }

    let Some(snapshot) = state.registry.get(resource_type, &resource_id) else {
        return response::error(
            StatusCode::NOT_FOUND,
            &format!("{} {resource_id} was not found", resource_type.singular(),),
            &[],
            Some(&view),
        );
    };

    let resolver = LinkResolver::new(&path, BASE_PATH);
    response::json_with_resolver(
        StatusCode::OK,
        snapshot.body.text().to_owned(),
        Caching::Default,
        Some(&view),
        Some(&resolver),
    )
}

/// The `Location` header for a subscription.
#[must_use]
pub fn subscription_location(subscription_id: &str) -> String {
    format!("{BASE_PATH}/subscriptions/{subscription_id}")
}

/// The scheme and authority to advertise in `ws_href`.
///
/// `secure` selects `ws://` or `wss://` (`Behaviour - Querying.md:13`). The
/// host is the one the client used to reach us, so the URL it is handed stays
/// reachable by the same route; only the port is substituted, because the
/// WebSocket listener is a separate socket from the HTTP one.
fn ws_target(state: &QueryState, headers: &HeaderMap) -> (String, String) {
    let host = headers
        .get(axum::http::header::HOST)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(':').next())
        .filter(|value| !value.is_empty())
        .unwrap_or("localhost");
    let scheme = if state.tls { "wss" } else { "ws" };
    let authority = if state.ws_port == 0 {
        host.to_owned()
    } else {
        format!("{host}:{}", state.ws_port)
    };
    (scheme.to_owned(), authority)
}

/// Render a JSON filter value as the text a query string would carry.
///
/// `params` is a free-form object, so a client may send `true` or `5` where the
/// query-string form would have `"true"` or `"5"`. Normalising here means the
/// matcher has one representation to compare against.
fn param_text(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::Bool(true) => "true".to_owned(),
        serde_json::Value::Bool(false) => "false".to_owned(),
        serde_json::Value::Null => "null".to_owned(),
        serde_json::Value::String(text) => text.clone(),
        // `str()` of a Python int/float/list/dict. Numbers are the reachable
        // case; the others round-trip through their JSON spelling, which is what
        // `str()` of a parsed value produces closely enough for a filter that
        // will not match anything either way.
        other => other.to_string(),
    }
}

/// `POST /subscriptions` -- create or match a subscription.
pub async fn post_subscriptions(
    State(state): State<QueryState>,
    uri: Uri,
    headers: HeaderMap,
    source: String,
) -> Response {
    let path = uri.path().to_owned();
    let view = RequestView::new(&path, &headers);
    let refuse =
        |message: &str| response::error(StatusCode::BAD_REQUEST, message, &[], Some(&view));

    let body: serde_json::Value = match serde_json::from_str(&source) {
        Ok(value) => value,
        Err(error) => return refuse(&format!("invalid JSON body: {error}")),
    };
    let Some(body) = body.as_object() else {
        return refuse("expected a JSON object");
    };

    // `queryapi-subscriptions-post-request.json`:
    // required = [max_update_rate_ms, persist, resource_path, params]
    let missing: Vec<&str> = ["max_update_rate_ms", "persist", "resource_path", "params"]
        .into_iter()
        .filter(|name| !body.contains_key(*name))
        .collect();
    if !missing.is_empty() {
        return refuse(&format!(
            "missing required attributes: {}",
            missing.join(", "),
        ));
    }

    let Some(resource_path) = body["resource_path"].as_str() else {
        return refuse("resource_path must be a string");
    };

    // `as_i64` is false for a bool in `serde_json`, which is the distinction
    // Python has to make explicitly (`isinstance(x, bool)` is true for an int).
    let Some(max_update_rate_ms) = body["max_update_rate_ms"].as_i64() else {
        return refuse("max_update_rate_ms must be an integer");
    };
    if max_update_rate_ms < 0 {
        return refuse("max_update_rate_ms must not be negative");
    }

    let Some(persist) = body["persist"].as_bool() else {
        return refuse("persist must be a boolean");
    };

    let Some(raw_params) = body["params"].as_object() else {
        return refuse("params must be an object");
    };
    // Filter values arrive as JSON but are compared as query-string text.
    let params: Vec<(String, String)> = raw_params
        .iter()
        .map(|(key, value)| (key.clone(), param_text(value)))
        .collect();

    // `Behaviour - Querying.md:13` -- if the client does not specify, the
    // server assigns false for HTTP and true for HTTPS. A client MAY request
    // the opposite "however they will receive a 400 response code unless the
    // Query API explicitly supports a mismatch". This one does not: the
    // WebSocket listener shares the HTTP listener's TLS configuration, so a
    // mismatch is not merely unsupported, it is unimplementable.
    let secure = match body.get("secure") {
        None => state.tls,
        Some(value) => match value.as_bool() {
            Some(flag) => flag,
            None => return refuse("secure must be a boolean"),
        },
    };
    if secure != state.tls {
        return refuse(&format!(
            "secure={secure} was requested but this Query API serves {}; \
             a mismatch between encrypted and insecure HTTP and WebSocket \
             connections is not supported",
            if state.tls { "HTTPS" } else { "HTTP" },
        ));
    }

    // `:15` -- the same rule for `authorization`. Always false until M6.
    let oauth2 = false;
    let authorization = match body.get("authorization") {
        None => oauth2,
        Some(value) => match value.as_bool() {
            Some(flag) => flag,
            None => return refuse("authorization must be a boolean"),
        },
    };
    if authorization != oauth2 {
        return refuse(&format!(
            "authorization={authorization} was requested but this Query API \
             is operating with authorization {}",
            if oauth2 { "enabled" } else { "disabled" },
        ));
    }

    let (ws_scheme, ws_host) = ws_target(&state, &headers);
    let request = SubscriptionRequest {
        resource_path: resource_path.to_owned(),
        params,
        // Clamped rather than cast: the value is already known non-negative,
        // and a rate above `u32::MAX` milliseconds is 49 days, which is the
        // same as "never" for every purpose the field has.
        max_update_rate_ms: u32::try_from(max_update_rate_ms).unwrap_or(u32::MAX),
        persist,
        secure,
        authorization,
        host: headers
            .get(axum::http::header::HOST)
            .and_then(|value| value.to_str().ok())
            .unwrap_or_default()
            .to_owned(),
        ws_scheme,
        ws_host,
    };

    let (subscription, created) = match state.subscriptions.create_or_match(&request) {
        Ok(result) => result,
        Err(failure) => return refuse(failure.message()),
    };

    let status = if created {
        StatusCode::CREATED
    } else {
        StatusCode::OK
    };
    let mut out = response::json(
        status,
        subscription.to_json(),
        Caching::Default,
        Some(&view),
    );
    if let Ok(value) = HeaderValue::from_str(&subscription_location(&subscription.id)) {
        out.headers_mut()
            .insert(axum::http::header::LOCATION, value);
    }
    out
}

/// `GET /subscriptions` -- list subscriptions.
///
/// `QueryAPI.raml:442` marks this "for debug use only" and
/// `Behaviour - Querying.md:23` actively discourages using it to find a
/// subscription to reuse. It carries the `paged` trait (`:441`), so paging
/// parameters are validated even though the collection is normally tiny --
/// rejecting a malformed cursor here rather than ignoring it keeps the
/// behaviour uniform across every paged resource.
pub async fn get_subscriptions(
    State(state): State<QueryState>,
    Query(params): Query<Params>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let path = uri.path().to_owned();
    let view = RequestView::new(&path, &headers);

    if let Err(bad) = parse_paging(lookup(&params), state.paging_limit, state.paging_limit_max) {
        return response::error(StatusCode::BAD_REQUEST, &bad.to_string(), &[], Some(&view));
    }

    let rendered: Vec<String> = state
        .subscriptions
        .all()
        .iter()
        .map(nmos_registry::subscription::Subscription::to_json)
        .collect();
    let fragments: Vec<&str> = rendered.iter().map(String::as_str).collect();
    // Always an array, even with one entry: this is a collection, and
    // `json_body`'s single-fragment shortcut is for a single **resource**.
    let text = format!("[{}]", fragments.join(", "));
    response::json(StatusCode::OK, text, Caching::Default, Some(&view))
}

/// `GET /subscriptions/{subscriptionId}` -- one subscription.
pub async fn get_subscription(
    State(state): State<QueryState>,
    Path(subscription_id): Path<String>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let path = uri.path().to_owned();
    let view = RequestView::new(&path, &headers);
    match state.subscriptions.get(&subscription_id) {
        Some(subscription) => response::json(
            StatusCode::OK,
            subscription.to_json(),
            Caching::Default,
            Some(&view),
        ),
        None => subscription_not_found(&subscription_id, &view),
    }
}

/// `DELETE /subscriptions/{subscriptionId}`.
///
/// `Behaviour - Querying.md:18` -- "The Query API MUST NOT acknowledge HTTP
/// DELETE requests for Subscriptions running in this non-persistent mode,
/// instead issuing an HTTP 403 (Forbidden) response." A non-persistent
/// subscription belongs to the API, which reaps it when its last WebSocket
/// closes; letting a client delete one would let it destroy a subscription
/// another client is still using.
pub async fn delete_subscription(
    State(state): State<QueryState>,
    Path(subscription_id): Path<String>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let path = uri.path().to_owned();
    let view = RequestView::new(&path, &headers);

    let Some(subscription) = state.subscriptions.get(&subscription_id) else {
        return subscription_not_found(&subscription_id, &view);
    };
    if !subscription.persist {
        return response::error(
            StatusCode::FORBIDDEN,
            "a non-persistent subscription is managed by the Query API and \
             cannot be deleted",
            &[],
            Some(&view),
        );
    }

    state.subscriptions.delete(&subscription_id);
    response::status_only(StatusCode::NO_CONTENT)
}

fn subscription_not_found(subscription_id: &str, view: &RequestView<'_>) -> Response {
    response::error(
        StatusCode::NOT_FOUND,
        &format!("subscription {subscription_id} was not found"),
        &[],
        Some(view),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state(tls: bool, ws_port: u16) -> QueryState {
        QueryState {
            registry: Arc::new(Registry::new(
                nmos_registry_core::store::RegistryStore::new(),
            )),
            subscriptions: Arc::new(SubscriptionManager::new()),
            query_id: "00000000-0000-4000-8000-000000000000".to_owned(),
            tls,
            ws_port,
            paging_limit: DEFAULT_PAGING_LIMIT,
            paging_limit_max: MAX_PAGING_LIMIT,
        }
    }

    fn host(value: &str) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert(
            axum::http::header::HOST,
            HeaderValue::from_str(value).expect("a test header"),
        );
        headers
    }

    #[test]
    fn the_subscription_location_is_a_path() {
        assert_eq!(
            subscription_location("s-1"),
            "/x-nmos/query/v1.3/subscriptions/s-1",
        );
    }

    #[test]
    fn the_ws_scheme_follows_the_listeners_tls() {
        // `Behaviour - Querying.md:13`.
        assert_eq!(ws_target(&state(false, 0), &host("h")).0, "ws");
        assert_eq!(ws_target(&state(true, 0), &host("h")).0, "wss");
    }

    #[test]
    fn the_ws_host_is_the_one_the_client_reached_us_by() {
        // Only the port is substituted, because the WebSocket listener is a
        // separate socket -- the host must stay reachable by the same route.
        assert_eq!(
            ws_target(&state(false, 8080), &host("registry.test")).1,
            "registry.test:8080"
        );
        assert_eq!(
            ws_target(&state(false, 8080), &host("registry.test:1234")).1,
            "registry.test:8080",
            "the client's port must be replaced, not kept",
        );
    }

    #[test]
    fn a_zero_ws_port_advertises_the_bare_host() {
        assert_eq!(
            ws_target(&state(false, 0), &host("registry.test")).1,
            "registry.test"
        );
    }

    #[test]
    fn a_missing_host_header_falls_back_to_localhost() {
        assert_eq!(
            ws_target(&state(false, 0), &HeaderMap::new()).1,
            "localhost"
        );
    }

    #[test]
    fn filter_values_are_rendered_as_query_string_text() {
        // A client may send `true` or `5` where the query-string form carries
        // `"true"` or `"5"`, and the matcher needs one representation.
        use serde_json::json;
        assert_eq!(param_text(&json!(true)), "true");
        assert_eq!(param_text(&json!(false)), "false");
        assert_eq!(param_text(&json!(null)), "null");
        assert_eq!(param_text(&json!(5)), "5");
        assert_eq!(param_text(&json!("already text")), "already text");
        assert_eq!(
            param_text(&json!("quoted \"inside\"")),
            r#"quoted "inside""#,
            "a string value must not acquire JSON quoting",
        );
    }
}
