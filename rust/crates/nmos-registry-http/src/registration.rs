// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The IS-04 Registration API's handlers.
//!
//! Port of `nmos/registry/handlers_registration.py`.
//!
//! # Every refusal here is a 400
//!
//! `Behaviour - Registration.md:94-104` calls all five conditions -- schema,
//! id/type conflict, version regression, parent changed, parent missing -- a
//! client error the Node "MUST NOT retry without corrective action". They are
//! distinguished in the `debug` member, which `:106` points at as the
//! operator's debugging aid, not by different status codes.
//!
//! # What is deliberately not here yet
//!
//! **The 503 path.** Python asks `backend.state.accepts_mutations` before every
//! mutation and answers 503 with `Retry-After: 1` when a distributed backend
//! cannot commit. The standalone backend it defaults to never says no
//! (`backend.py:130-177`, 47 lines, never awaits), so at this milestone that
//! branch has no condition that can reach it -- writing it now would be a code
//! path with no caller. It lands with the `RegistryBackend` trait in M8, which
//! is also where `MutationUnavailable` acquires a meaning. When it lands, the
//! header is `Retry-After: 1`: the conditions that produce a 503 -- a lost
//! quorum, a resync after compaction, a member still preloading -- resolve on
//! the order of seconds, and a Node that backed off for minutes would stay
//! unregistered long after the registry recovered.
//!
//! **The status-line log.** Python logs `registry.status_line()` after every
//! successful POST, guarded by `log.isEnabledFor(logging.INFO)` -- and that
//! guard is load-bearing rather than stylistic: `status_line()` walks every
//! resource in every bucket, and as a bare argument it would be evaluated
//! eagerly on every registration even with INFO disabled. Logging arrives with
//! `tracing` in M5's launcher; when it does, this is the shape to copy, and
//! divergence D9's incremental counters are what make it cheap.

use std::sync::Arc;

use axum::extract::{Path, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, Uri};
use axum::response::Response;

use nmos_registry::decode::decode_post_envelope;
use nmos_registry::manager::SubscriptionManager;
use nmos_registry::registry::Registry;
use nmos_registry_core::links::LinkResolver;
use nmos_registry_core::resource_type::ResourceType;

use crate::response::{self, Caching, RequestView};

/// The Registration API's base path.
pub const BASE_PATH: &str = "/x-nmos/registration/v1.3";

/// The Query API's base path, for cross-references in the browsing view.
const QUERY_BASE_PATH: &str = "/x-nmos/query/v1.3";

/// What the handlers need.
#[derive(Clone)]
pub struct RegistrationState {
    /// The registry being written to.
    pub registry: Arc<Registry>,
    /// Needed only for the status line, which counts subscriptions and grains
    /// alongside resources -- nmos-cpp's format, and this handler has to match
    /// it for the two logs to be readable side by side.
    pub subscriptions: Arc<SubscriptionManager>,
}

/// The `Location` header for a registered resource.
///
/// `RegistrationAPI.raml:47` gives the form
/// `/x-nmos/registration/{version}/resource/nodes/{id}` -- a path, not an
/// absolute URL, so it stays correct behind a reverse proxy.
#[must_use]
pub fn resource_location(resource_type: ResourceType, resource_id: &str) -> String {
    format!(
        "{BASE_PATH}/resource/{}/{resource_id}",
        resource_type.plural(),
    )
}

/// `POST /resource` -- create or update a resource.
pub async fn post_resource(
    State(state): State<RegistrationState>,
    uri: Uri,
    headers: HeaderMap,
    source: String,
) -> Response {
    let path = uri.path().to_owned();
    let view = RequestView::new(&path, &headers);

    // The TEXT, not a parsed object: `decode_post_envelope` slices the resource
    // body out of it verbatim, so the bytes a Node registers are the bytes a
    // Controller reads back.
    let (resource_type, body) = match decode_post_envelope(&source) {
        Ok(decoded) => decoded,
        Err(failure) => {
            // `:100` -- "The request body does not meet the JSON schema for
            // that resource type". Decoding into the generated type IS that
            // check.
            return response::error(StatusCode::BAD_REQUEST, failure.message(), &[], Some(&view));
        }
    };

    let stored = body.text().to_owned();
    match state.registry.register(resource_type, body) {
        Err(failure) => response::error(StatusCode::BAD_REQUEST, &failure.detail, &[], Some(&view)),
        Ok(applied) => {
            // `:25` -- 201 for a create, 200 for an update, `Location` on both.
            // The body is the registered resource, in its stored raw form, so a
            // client sees exactly what it sent.
            let status = if applied.created {
                StatusCode::CREATED
            } else {
                StatusCode::OK
            };
            let id = resource_id_of(&stored);
            let location = resource_location(resource_type, &id);
            // `json`, not `json_body`: this is one resource, and the response
            // schema is the resource object, not a collection holding it.
            let mut response = response::json(status, stored, Caching::Default, Some(&view));
            if let Ok(value) = HeaderValue::from_str(&location) {
                response
                    .headers_mut()
                    .insert(axum::http::header::LOCATION, value);
            }

            // nmos-cpp emits its status line from both its expiry thread and
            // its `POST /resource` handler; Python matches that from
            // `run_status_reporting` and here, so a registration problem can be
            // diagnosed against either implementation's log.
            //
            // Python needs an explicit `log.isEnabledFor(logging.INFO)` guard
            // because a bare argument is evaluated eagerly, and `status_line`
            // is O(registry size) -- a 10,000-resource scan per registration,
            // on the latency path this project most wants to protect. `tracing`
            // evaluates its fields only inside the enabled branch, so the guard
            // is the macro's rather than one written here; the cost is the same
            // shape and the protection is not optional.
            tracing::info!(
                "registry: {}",
                state.registry.status_line(
                    state.subscriptions.count(),
                    state.subscriptions.grain_count(),
                ),
            );
            response
        }
    }
}

/// The resource's own id, read back out of the stored span.
///
/// The register call has already validated it -- a body without a well-formed
/// `id` cannot reach here -- so this re-reads rather than re-decides. Python
/// takes it from the parsed form it was keeping anyway; divergence D10 drops
/// that, so it comes from the span.
fn resource_id_of(stored: &str) -> String {
    serde_json::from_str::<serde_json::Value>(stored)
        .ok()
        .as_ref()
        .and_then(|value| value.get("id"))
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

/// Resolve the `{resourceType}` path segment, or produce the 404.
///
/// `RegistrationAPI.raml:75-82` fixes the permitted values as an explicit enum
/// of the six plural names. Matching them exactly is what keeps a bad path
/// segment from being coerced onto a real type -- the AMWA mock derives the
/// singular with `rstrip("s")`, which strips every trailing `s`.
///
/// In Python this is belt and braces: the route pattern already constrains the
/// segment. `matchit` has no regex constraints, so here it is the **only**
/// check, which is what the module-level note in [`crate::router`] is about.
fn parse_resource_type(name: &str) -> Option<ResourceType> {
    ResourceType::from_plural(name)
}

/// The 404 for a `{resourceType}` segment that is not one of the six.
///
/// Split from [`parse_resource_type`] rather than returned from it: a
/// `Result<_, Response>` puts a whole HTTP response in the error variant of
/// every call, which is 128 bytes moved on the success path too.
fn unknown_resource_type(name: &str, view: &RequestView<'_>) -> Response {
    let permitted = ResourceType::ALL
        .iter()
        .map(|kind| kind.plural())
        .collect::<Vec<_>>()
        .join(", ");
    response::error(
        StatusCode::NOT_FOUND,
        &format!(
            "unknown resource type {}; expected one of: {permitted}",
            nmos_json::error::python_repr(name),
        ),
        &[],
        Some(view),
    )
}

/// `DELETE /resource/{resourceType}/{resourceId}` -- unregister a resource.
///
/// Removes the resource and, cascading, every descendant
/// (`Behaviour - Registration.md:68`, `:74`).
pub async fn delete_resource(
    State(state): State<RegistrationState>,
    Path((resource_type, resource_id)): Path<(String, String)>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let path = uri.path().to_owned();
    let view = RequestView::new(&path, &headers);
    let Some(resolved) = parse_resource_type(&resource_type) else {
        return unknown_resource_type(&resource_type, &view);
    };

    // A 409 would belong here if the resource were held at another API version;
    // unreachable in a single-version registry.
    if state.registry.delete(resolved, &resource_id).is_none() {
        return response::error(
            StatusCode::NOT_FOUND,
            &format!("{} {resource_id} is not registered", resolved.singular()),
            &[],
            Some(&view),
        );
    }
    // `RegistrationAPI.raml:91-92` -- 204 No Content, no body.
    response::status_only(StatusCode::NO_CONTENT)
}

/// `GET /resource/{resourceType}/{resourceId}` -- read back a registration.
///
/// `RegistrationAPI.raml:105` marks this "for debug use only": the Registration
/// API is otherwise write-only and the Query API is the read interface. It is
/// implemented because the RAML defines it, and it is genuinely useful when
/// checking what a Node actually sent.
pub async fn get_resource(
    State(state): State<RegistrationState>,
    Path((resource_type, resource_id)): Path<(String, String)>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let path = uri.path().to_owned();
    let view = RequestView::new(&path, &headers);
    let Some(resolved) = parse_resource_type(&resource_type) else {
        return unknown_resource_type(&resource_type, &view);
    };

    let Some(snapshot) = state.registry.get(resolved, &resource_id) else {
        return response::error(
            StatusCode::NOT_FOUND,
            &format!("{} {resource_id} is not registered", resolved.singular()),
            &[],
            Some(&view),
        );
    };

    // Cross-references resolve into the **Query** API: the Registration API has
    // no collections to browse, so linking within it would only produce dead
    // ends.
    let resolver = LinkResolver::new(&path, QUERY_BASE_PATH);
    response::json_with_resolver(
        StatusCode::OK,
        snapshot.body.text().to_owned(),
        Caching::Default,
        Some(&view),
        Some(&resolver),
    )
}

/// The heartbeat response body.
///
/// `registrationapi-health-response.json` types `health` as
/// `{"type": "string", "pattern": "^[0-9]+$"}` -- a **string** holding the TAI
/// seconds. nmos-cpp agrees (`make_health_response_body` emits
/// `json::value::string`); the AMWA mock returns a JSON number, which does not
/// satisfy its own specification's schema.
#[must_use]
pub fn health_body(health: i64) -> String {
    format!(r#"{{"health": "{health}"}}"#)
}

/// `POST /health/nodes/{nodeId}` -- heartbeat.
///
/// `Behaviour - Registration.md:45` -- Nodes heartbeat every 5 s by default.
/// The heartbeat refreshes the Node and, recursively, all of its
/// sub-resources, so the whole subtree survives as one unit.
pub async fn post_health(
    State(state): State<RegistrationState>,
    Path(node_id): Path<String>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let path = uri.path().to_owned();
    let view = RequestView::new(&path, &headers);
    match state.registry.heartbeat(&node_id) {
        Some(health) => response::json(
            StatusCode::OK,
            health_body(health),
            Caching::Default,
            Some(&view),
        ),
        // `:112-114` -- 404 means "not known to the Registration API", most
        // likely because garbage collection removed it. The Node's documented
        // response is to re-register every resource in order.
        None => node_not_registered(&node_id, &view),
    }
}

/// `GET /health/nodes/{nodeId}` -- read health without heartbeating.
///
/// `RegistrationAPI.raml:152` -- "for debug use only". Deliberately does
/// **not** refresh health: a diagnostic read that silently kept a Node alive
/// would mask exactly the garbage-collection problem someone would be using it
/// to investigate.
pub async fn get_health(
    State(state): State<RegistrationState>,
    Path(node_id): Path<String>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let path = uri.path().to_owned();
    let view = RequestView::new(&path, &headers);
    match state.registry.node_health(&node_id) {
        Some(health) => response::json(
            StatusCode::OK,
            health_body(health),
            Caching::Default,
            Some(&view),
        ),
        None => node_not_registered(&node_id, &view),
    }
}

fn node_not_registered(node_id: &str, view: &RequestView<'_>) -> Response {
    response::error(
        StatusCode::NOT_FOUND,
        &format!("node {node_id} is not registered"),
        &[],
        Some(view),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_location_is_a_path_not_an_absolute_url() {
        // `RegistrationAPI.raml:47`. A path stays correct behind a reverse
        // proxy; an absolute URL built from the Host header does not.
        assert_eq!(
            resource_location(ResourceType::Node, "3b8be755-08ff-452b-b217-c9151eb21193"),
            "/x-nmos/registration/v1.3/resource/nodes/3b8be755-08ff-452b-b217-c9151eb21193",
        );
        assert!(!resource_location(ResourceType::Sender, "x").contains("://"));
    }

    #[test]
    fn the_location_uses_the_plural_segment() {
        for kind in ResourceType::ALL {
            let location = resource_location(kind, "x");
            assert!(
                location.contains(&format!("/resource/{}/", kind.plural())),
                "{kind:?}: {location}",
            );
        }
    }

    #[test]
    fn health_is_a_string_not_a_number() {
        // `registrationapi-health-response.json` pins `^[0-9]+$` on a STRING.
        // The AMWA mock emits a number, which fails its own schema.
        assert_eq!(health_body(1600000000), r#"{"health": "1600000000"}"#);
        let parsed: serde_json::Value = serde_json::from_str(&health_body(42)).expect("valid JSON");
        assert!(
            parsed["health"].is_string(),
            "health must be a string: {parsed}",
        );
    }

    #[test]
    fn the_health_body_uses_pythons_separators() {
        // `dump_any` inherits `json.dumps`'s `": "`, so `{"health": "1"}` and
        // not `{"health":"1"}`.
        assert_eq!(health_body(1), r#"{"health": "1"}"#);
    }

    #[test]
    fn the_id_is_read_back_out_of_the_stored_span() {
        // D10 drops the parsed form, so the Location header's id comes from
        // the bytes rather than from a retained `Value`.
        assert_eq!(
            resource_id_of(r#"{"id":"3b8be755-08ff-452b-b217-c9151eb21193","label":"n"}"#),
            "3b8be755-08ff-452b-b217-c9151eb21193",
        );
        assert_eq!(resource_id_of("not json"), "");
        assert_eq!(resource_id_of(r#"{"label":"n"}"#), "");
    }
}
