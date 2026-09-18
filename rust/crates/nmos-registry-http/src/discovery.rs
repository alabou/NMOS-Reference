// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The discovery ladders: `/` to `/x-nmos` to `/x-nmos/<api>` to the version.
//!
//! Port of `nmos/registry/handlers_root.py`. Each API exposes a ladder of index
//! resources so a client can walk down from the root without prior knowledge of
//! the layout.
//!
//! # Why there are two ladders and not one
//!
//! The Registration and Query APIs listen on **different ports with different
//! security policies**, so advertising `query/` from the registration port
//! would point a client at an endpoint that is not there. Each port lists only
//! what it actually serves.
//!
//! # The contents are fixed by schema, not by choice
//!
//! `registrationapi-base.json` requires exactly `["resource/", "health/"]`,
//! with `minItems` and `maxItems` both 2. `queryapi-base.json` requires exactly
//! seven entries: the six resource collections in IS-04 order, plus
//! `subscriptions/`. Adding an entry to either is a schema failure, not an
//! extension.
//!
//! # A note on the indented intermediate
//!
//! Python's `json_response` serialises with `indent=2` before handing the text
//! to the browsing view, and compactly otherwise. Here the browsing branch is
//! given the compact text, which produces **identical** HTML: the renderer
//! parses its input, so the indentation of what it parses cannot survive into
//! what it emits. The one path where it would matter is the `<pre>` fallback
//! for text that is not JSON, and this text was just serialised, so it always
//! parses.

use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;

use nmos_registry_core::resource_type::ResourceType;

use crate::response::{Caching, RequestView, json};

/// The only API version this registry serves.
pub const API_VERSION: &str = "v1.3";

/// An index response: a JSON array of strings, spelled the way `dump_any` does.
///
/// `", "` between entries, because `dump_any` inherits `json.dumps`'s default
/// separators. `PythonCompatFormatter` is what reproduces that, and it is the
/// difference between `["a","b"]` and `["a", "b"]` on every ladder response.
fn index(entries: &[&str], path: &str, headers: &HeaderMap) -> Response {
    let text = nmos_json::engine::dump_any(&entries).unwrap_or_else(|_| "[]".to_owned());
    let view = RequestView::new(path, headers);
    json(StatusCode::OK, text, Caching::Default, Some(&view))
}

/// `GET /` -- the only API family this process serves.
#[must_use]
pub fn root(path: &str, headers: &HeaderMap) -> Response {
    index(&["x-nmos/"], path, headers)
}

/// `GET /x-nmos` on the registration port.
#[must_use]
pub fn registration_root(path: &str, headers: &HeaderMap) -> Response {
    index(&["registration/"], path, headers)
}

/// `GET /x-nmos/registration` -- the supported versions.
#[must_use]
pub fn registration_versions(path: &str, headers: &HeaderMap) -> Response {
    index(&[&format!("{API_VERSION}/")], path, headers)
}

/// `GET /x-nmos/registration/v1.3` -- the two Registration API resources.
#[must_use]
pub fn registration_base(path: &str, headers: &HeaderMap) -> Response {
    index(&["resource/", "health/"], path, headers)
}

/// `GET /x-nmos` on the query port.
#[must_use]
pub fn query_root(path: &str, headers: &HeaderMap) -> Response {
    index(&["query/"], path, headers)
}

/// `GET /x-nmos/query` -- the supported versions.
#[must_use]
pub fn query_versions(path: &str, headers: &HeaderMap) -> Response {
    index(&[&format!("{API_VERSION}/")], path, headers)
}

/// `GET /x-nmos/query/v1.3` -- the seven Query API collections.
#[must_use]
pub fn query_base(path: &str, headers: &HeaderMap) -> Response {
    let mut entries: Vec<String> = ResourceType::ALL
        .iter()
        .map(|kind| format!("{}/", kind.plural()))
        .collect();
    entries.push("subscriptions/".to_owned());
    let borrowed: Vec<&str> = entries.iter().map(String::as_str).collect();
    index(&borrowed, path, headers)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn body_of(response: Response) -> String {
        let bytes = tokio::runtime::Builder::new_current_thread()
            .build()
            .expect("a current-thread runtime")
            .block_on(async {
                axum::body::to_bytes(response.into_body(), usize::MAX)
                    .await
                    .expect("a complete body")
                    .to_vec()
            });
        String::from_utf8(bytes).expect("UTF-8")
    }

    fn plain() -> HeaderMap {
        HeaderMap::new()
    }

    #[test]
    fn the_root_offers_only_x_nmos() {
        assert_eq!(body_of(root("/", &plain())), r#"["x-nmos/"]"#);
    }

    #[test]
    fn each_port_advertises_only_what_it_serves() {
        // Advertising the other API from this port points a client at an
        // endpoint that is not there.
        assert_eq!(
            body_of(registration_root("/x-nmos", &plain())),
            r#"["registration/"]"#,
        );
        assert_eq!(body_of(query_root("/x-nmos", &plain())), r#"["query/"]"#);
    }

    #[test]
    fn the_registration_base_is_exactly_the_two_entries_the_schema_pins() {
        // registrationapi-base.json: minItems 2, maxItems 2.
        assert_eq!(
            body_of(registration_base("/x-nmos/registration/v1.3", &plain())),
            r#"["resource/", "health/"]"#,
        );
    }

    #[test]
    fn the_query_base_is_exactly_the_seven_entries_the_schema_pins() {
        // queryapi-base.json: the six collections in IS-04 registration
        // dependency order, then subscriptions.
        assert_eq!(
            body_of(query_base("/x-nmos/query/v1.3", &plain())),
            r#"["nodes/", "devices/", "sources/", "flows/", "senders/", "receivers/", "subscriptions/"]"#,
        );
    }

    #[test]
    fn index_entries_are_separated_the_way_python_separates_them() {
        // `dump_any` inherits `json.dumps`'s `", "`, not serde_json's `","`.
        // Every ladder response on the wire depends on this.
        let body = body_of(query_base("/x-nmos/query/v1.3", &plain()));
        assert!(body.contains(r#"", ""#), "separators are compact: {body}");
        assert!(!body.contains(r#"",""#), "{body}");
    }

    #[test]
    fn both_version_ladders_report_v1_3_with_a_trailing_slash() {
        // The slash is what makes the entry a link in the browsing view and a
        // navigable index entry for a client walking down.
        assert_eq!(
            body_of(registration_versions("/x-nmos/registration", &plain())),
            r#"["v1.3/"]"#,
        );
        assert_eq!(
            body_of(query_versions("/x-nmos/query", &plain())),
            r#"["v1.3/"]"#,
        );
    }

    #[test]
    fn a_browser_gets_the_ladder_as_a_navigable_page() {
        let mut headers = HeaderMap::new();
        headers.insert(
            axum::http::header::ACCEPT,
            axum::http::HeaderValue::from_static("text/html"),
        );
        let body = body_of(query_base("/x-nmos/query/v1.3", &headers));
        assert!(body.starts_with("<!DOCTYPE html>"));
        assert!(
            body.contains(r#"<a href="/x-nmos/query/v1.3/nodes/">"#),
            "the ladder entries must be clickable: {body}",
        );
    }
}
