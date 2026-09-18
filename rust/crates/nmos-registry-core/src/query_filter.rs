// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Basic queries and downgrade queries.
//!
//! # Basic queries
//!
//! `APIs - Query Parameters.md:436-524`. Any attribute a resource could carry
//! may be used as a query parameter, matched by exact string equality, with `.`
//! descending into nested objects **and into objects held in arrays**.
//!
//! Two rules in that section are easy to miss, and are why this is a module
//! rather than a closure at the call site:
//!
//! * `:498` -- for an attribute whose value is an *array*, the query matches
//!   when the array **contains** the value. The worked example is
//!   `?tags.studio=HQ1` against `"tags": {"studio": ["HQ1"]}`.
//! * `:444` -- "If a query parameter is requested which does not match an
//!   attribute found in any resource, an empty result set MUST be returned." A
//!   path that does not resolve makes that resource not match; if it resolves
//!   nowhere the result is empty. It is specifically **not** a 400 and never a
//!   500. The AMWA mock ignores every filter but `id`, and raises a `KeyError`
//!   into a 500 for an unknown one.
//!
//! Matching runs against the resource's raw JSON rather than a typed view, for
//! the same reason the raw form is what gets served: a client filtering on a
//! vendor extension the generated types do not model should still get an
//! answer.
//!
//! # Downgrade queries
//!
//! `APIs - Query Parameters.md:371-434`. A downgrade query asks the registry to
//! *also* return resources registered under older minor versions -- it does
//! **not** strip attributes from the response. Stripping is a separate,
//! unconditional rule (`:392`).
//!
//! This registry stores and serves v1.3 exclusively, so both halves are no-ops:
//! there are no older-versioned resources to add, and every stored resource
//! already matches. The parameter is still parsed and validated, because the
//! one case that *is* observable is the error -- `:434`: a downgrade across
//! major versions MUST be refused with a 400.

use std::fmt;

use serde_json::Value;

use crate::paging::is_paging_param;

/// The API version this registry serves. Everything about downgrade is
/// relative to it.
pub const API_VERSION: &str = "v1.3";

/// The downgrade parameter.
pub const PARAM_DOWNGRADE: &str = "query.downgrade";
/// The RQL parameter, which this implementation does not support.
pub const PARAM_RQL: &str = "query.rql";
/// The ancestry-id parameter, which this implementation does not support.
pub const PARAM_ANCESTRY_ID: &str = "query.ancestry_id";
/// The ancestry-type parameter, which this implementation does not support.
pub const PARAM_ANCESTRY_TYPE: &str = "query.ancestry_type";
/// The ancestry-generations parameter, which this implementation does not
/// support.
pub const PARAM_ANCESTRY_GENERATIONS: &str = "query.ancestry_generations";

/// A query parameter was invalid. The caller answers 400.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QueryError {
    /// The message for the response body.
    pub detail: String,
}

impl fmt::Display for QueryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.detail)
    }
}

impl std::error::Error for QueryError {}

/// A query parameter names an unimplemented optional feature. Answers **501**.
///
/// Each of these is a MAY in the specification, and each MUST answer 501 when
/// unsupported rather than being silently ignored -- `:528` for RQL, `:578` for
/// ancestry. Silently ignoring one would be the dangerous failure: a client
/// would receive an unfiltered set and treat it as a filtered one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnsupportedQuery {
    /// The message for the response body.
    pub detail: String,
}

impl fmt::Display for UnsupportedQuery {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.detail)
    }
}

impl std::error::Error for UnsupportedQuery {}

/// A parsed `vMAJOR.MINOR` API version.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ParsedVersion {
    /// The major version.
    pub major: u32,
    /// The minor version.
    pub minor: u32,
}

impl ParsedVersion {
    /// Parse `^v[0-9]+\.[0-9]+\z` (`QueryAPI.raml:70`).
    ///
    /// Hand-matched rather than compiled: the shape is two digit runs around a
    /// dot, the check runs once per request, and a regex here would be a
    /// dependency plus a lazy static for something `split_once` expresses
    /// directly. The digit checks are what keep `v+1.0` and `v 1.0` out, which
    /// a bare integer parse would admit.
    #[must_use]
    pub fn parse(text: &str) -> Option<Self> {
        let rest = text.strip_prefix('v')?;
        let (major, minor) = rest.split_once('.')?;
        if major.is_empty() || minor.is_empty() {
            return None;
        }
        if !major.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        if !minor.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        Some(Self {
            major: major.parse().ok()?,
            minor: minor.parse().ok()?,
        })
    }
}

/// Reject a request that uses an unimplemented optional query feature.
///
/// # Errors
///
/// Names the feature, which the caller turns into a 501.
pub fn check_unsupported(lookup: impl Fn(&str) -> Option<String>) -> Result<(), UnsupportedQuery> {
    for (name, feature) in [
        (PARAM_RQL, "RQL queries"),
        (PARAM_ANCESTRY_ID, "ancestry queries"),
        (PARAM_ANCESTRY_TYPE, "ancestry queries"),
        (PARAM_ANCESTRY_GENERATIONS, "ancestry queries"),
    ] {
        if lookup(name).is_some() {
            return Err(UnsupportedQuery {
                detail: format!("{feature} are not supported by this Query API ({name})"),
            });
        }
    }
    Ok(())
}

/// Validate `query.downgrade`. Returns the requested version, if any.
///
/// # Errors
///
/// A malformed value, or one naming a different major version -- `:377`:
/// "Downgrades MUST only be performed between minor API versions as major
/// versions might remove or re-purpose attributes", which `:434` makes a 400.
pub fn check_downgrade(
    lookup: impl Fn(&str) -> Option<String>,
) -> Result<Option<String>, QueryError> {
    let Some(requested) = lookup(PARAM_DOWNGRADE) else {
        return Ok(None);
    };

    let Some(target) = ParsedVersion::parse(&requested) else {
        return Err(QueryError {
            detail: format!("{PARAM_DOWNGRADE} must match '^v[0-9]+\\.[0-9]+$', got '{requested}'"),
        });
    };

    // `API_VERSION` is a constant of this module, so a failure to parse it
    // would be a defect here rather than in the request. Treated as "no
    // constraint" instead of panicking, because this crate's write path is
    // panic-free and a malformed constant is caught by the test below.
    if let Some(current) = ParsedVersion::parse(API_VERSION)
        && target.major != current.major
    {
        return Err(QueryError {
            detail: format!(
                "cannot downgrade from {API_VERSION} to {requested}: downgrade \
                 queries must not cross major API versions"
            ),
        });
    }
    Ok(Some(requested))
}

/// Split the basic-query filters out of a request's query string.
///
/// Everything that is not a reserved `paging.*` or `query.*` parameter is a
/// filter on a resource attribute (`:440`).
#[must_use]
pub fn filter_params(params: &[(String, String)]) -> Vec<(String, String)> {
    params
        .iter()
        .filter(|(name, _)| !is_paging_param(name) && !name.starts_with("query."))
        .cloned()
        .collect()
}

/// Does one resource satisfy every filter?
///
/// The filters are `(dotted_path, expected_string)` pairs, ANDed together --
/// Example 2 at `:476` combines two parameters.
#[must_use]
pub fn matches(raw: &Value, filters: &[(String, String)]) -> bool {
    filters.iter().all(|(path, expected)| {
        let segments: Vec<&str> = path.split('.').collect();
        path_matches(raw, &segments, expected)
    })
}

/// Walk a dotted path and test the value at the end of it.
///
/// Arrays are **traversed, not indexed**: `services.type=X` matches when *any*
/// element of `services` has `type == X` (Example 4, `:500`). That "any" is
/// also what makes the containment rule work for `tags.studio=HQ1`, where the
/// path ends on an array of strings.
fn path_matches(value: &Value, segments: &[&str], expected: &str) -> bool {
    let Some((head, rest)) = segments.split_first() else {
        return scalar_matches(value, expected);
    };

    match value {
        Value::Object(map) => map
            .get(*head)
            .is_some_and(|inner| path_matches(inner, rest, expected)),
        // The path continues into the elements of an array of objects.
        Value::Array(items) => items
            .iter()
            .any(|item| path_matches(item, segments, expected)),
        // A scalar with path segments left over: the attribute does not exist
        // at this depth, so the resource does not match (`:444`).
        _ => false,
    }
}

/// Compare a resolved value against the query string's text.
///
/// Query-string values are always strings, so the resource's value is rendered
/// to its JSON scalar text before comparison. The renderings that matter:
///
/// * booleans are `true`/`false`, not Python's `True`/`False` -- otherwise
///   `?persist=true` would never match anything;
/// * null is `null`;
/// * a float uses the same spelling the encoder writes, so a value that went
///   out as `1000.0` is matched by `?x=1000.0` and not by `?x=1000`;
/// * an object never compares equal to a query-string scalar.
fn scalar_matches(value: &Value, expected: &str) -> bool {
    match value {
        // Array containment (`:498`).
        Value::Array(items) => items.iter().any(|item| scalar_matches(item, expected)),
        Value::Bool(flag) => (if *flag { "true" } else { "false" }) == expected,
        Value::Null => expected == "null",
        Value::String(text) => text == expected,
        Value::Number(number) => number_matches(number, expected),
        Value::Object(_) => false,
    }
}

/// Render a JSON number the way Python's `str()` would, then compare.
fn number_matches(number: &serde_json::Number, expected: &str) -> bool {
    if let Some(integer) = number.as_i64() {
        return integer.to_string() == expected;
    }
    if let Some(unsigned) = number.as_u64() {
        return unsigned.to_string() == expected;
    }
    number
        .as_f64()
        .is_some_and(|float| nmos_json::engine::format_repr(float) == expected)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn filters(pairs: &[(&str, &str)]) -> Vec<(String, String)> {
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
            .collect()
    }

    #[test]
    fn a_top_level_attribute_matches_by_exact_string() {
        let resource = json!({"label": "My Node", "id": "abc"});
        assert!(matches(&resource, &filters(&[("label", "My Node")])));
        assert!(!matches(&resource, &filters(&[("label", "my node")])));
        assert!(!matches(&resource, &filters(&[("label", "My")])));
    }

    #[test]
    fn filters_are_anded_together() {
        // Example 2 at `:476`.
        let resource = json!({"label": "A", "description": "B"});
        assert!(matches(
            &resource,
            &filters(&[("label", "A"), ("description", "B")])
        ));
        assert!(!matches(
            &resource,
            &filters(&[("label", "A"), ("description", "wrong")])
        ));
    }

    #[test]
    fn a_dot_descends_into_a_nested_object() {
        let resource = json!({"caps": {"media_types": "video/raw"}});
        assert!(matches(
            &resource,
            &filters(&[("caps.media_types", "video/raw")])
        ));
    }

    #[test]
    fn an_array_matches_by_containment() {
        // `:498` -- the worked example is `?tags.studio=HQ1` against
        // `"tags": {"studio": ["HQ1"]}`.
        let resource = json!({"tags": {"studio": ["HQ1", "HQ2"]}});
        assert!(matches(&resource, &filters(&[("tags.studio", "HQ1")])));
        assert!(matches(&resource, &filters(&[("tags.studio", "HQ2")])));
        assert!(!matches(&resource, &filters(&[("tags.studio", "HQ3")])));
    }

    #[test]
    fn a_path_descends_into_objects_held_in_an_array() {
        // Example 4, `:500` -- `services.type=X` matches when *any* element
        // has `type == X`. Indexing rather than traversing would need
        // `services.0.type`, which the specification does not define.
        let resource = json!({
            "services": [
                {"type": "urn:x-nmos:service:a", "href": "http://a/"},
                {"type": "urn:x-nmos:service:b", "href": "http://b/"},
            ],
        });
        assert!(matches(
            &resource,
            &filters(&[("services.type", "urn:x-nmos:service:b")])
        ));
        assert!(!matches(
            &resource,
            &filters(&[("services.type", "urn:x-nmos:service:c")])
        ));
    }

    #[test]
    fn an_unresolvable_path_makes_the_resource_not_match() {
        // `:444` -- an empty result set, not a 400 and never a 500.
        let resource = json!({"label": "A"});
        assert!(!matches(&resource, &filters(&[("nonexistent", "x")])));
        assert!(!matches(&resource, &filters(&[("label.deeper", "x")])));
        assert!(!matches(&resource, &filters(&[("a.b.c.d", "x")])));
    }

    #[test]
    fn booleans_render_as_json_not_as_python() {
        // `?persist=true` would never match anything if this said `True`.
        let resource = json!({"persist": true, "active": false});
        assert!(matches(&resource, &filters(&[("persist", "true")])));
        assert!(matches(&resource, &filters(&[("active", "false")])));
        assert!(!matches(&resource, &filters(&[("persist", "True")])));
        assert!(!matches(&resource, &filters(&[("persist", "1")])));
    }

    #[test]
    fn null_renders_as_null() {
        let resource = json!({"receiver_id": null});
        assert!(matches(&resource, &filters(&[("receiver_id", "null")])));
        assert!(!matches(&resource, &filters(&[("receiver_id", "")])));
    }

    #[test]
    fn numbers_render_the_way_the_encoder_writes_them() {
        // A float that went out as `1000.0` is matched by `?x=1000.0`, not by
        // `?x=1000` -- the same spelling on both sides of the wire.
        let resource = json!({"port": 8080, "rate": 1000.0, "ratio": 1.5});
        assert!(matches(&resource, &filters(&[("port", "8080")])));
        assert!(!matches(&resource, &filters(&[("port", "8080.0")])));
        assert!(matches(&resource, &filters(&[("rate", "1000.0")])));
        assert!(matches(&resource, &filters(&[("ratio", "1.5")])));
    }

    #[test]
    fn an_object_never_matches_a_query_string_scalar() {
        let resource = json!({"caps": {"a": 1}});
        assert!(!matches(&resource, &filters(&[("caps", "{}")])));
        assert!(!matches(&resource, &filters(&[("caps", "")])));
    }

    #[test]
    fn no_filters_matches_everything() {
        assert!(matches(&json!({}), &[]));
        assert!(matches(&json!({"a": 1}), &[]));
    }

    #[test]
    fn the_reserved_parameters_are_not_filters() {
        let params = vec![
            ("paging.limit".to_owned(), "10".to_owned()),
            ("paging.since".to_owned(), "0:0".to_owned()),
            ("query.downgrade".to_owned(), "v1.2".to_owned()),
            ("query.rql".to_owned(), "eq(a,b)".to_owned()),
            ("label".to_owned(), "My Node".to_owned()),
            ("tags.studio".to_owned(), "HQ1".to_owned()),
        ];
        let kept = filter_params(&params);
        assert_eq!(
            kept,
            vec![
                ("label".to_owned(), "My Node".to_owned()),
                ("tags.studio".to_owned(), "HQ1".to_owned()),
            ],
        );
    }

    #[test]
    fn an_unsupported_optional_feature_is_refused_rather_than_ignored() {
        // Silently ignoring one is the dangerous failure: the client would get
        // an unfiltered set and treat it as filtered.
        for name in [
            PARAM_RQL,
            PARAM_ANCESTRY_ID,
            PARAM_ANCESTRY_TYPE,
            PARAM_ANCESTRY_GENERATIONS,
        ] {
            let owned = name.to_owned();
            let result = check_unsupported(move |asked| (asked == owned).then(|| "x".to_owned()));
            assert!(result.is_err(), "{name} was silently ignored");
        }
        assert!(check_unsupported(|_| None).is_ok());
    }

    #[test]
    fn a_downgrade_within_the_major_version_is_allowed() {
        let requested =
            check_downgrade(|name| (name == PARAM_DOWNGRADE).then(|| "v1.0".to_owned()));
        assert_eq!(requested.unwrap(), Some("v1.0".to_owned()));
    }

    #[test]
    fn a_downgrade_across_major_versions_is_refused() {
        // `:377` and `:434` -- a 400, because a major version might remove or
        // re-purpose attributes.
        let refused = check_downgrade(|name| (name == PARAM_DOWNGRADE).then(|| "v2.0".to_owned()));
        assert!(refused.is_err());
        let refused = check_downgrade(|name| (name == PARAM_DOWNGRADE).then(|| "v0.9".to_owned()));
        assert!(refused.is_err());
    }

    #[test]
    fn a_malformed_downgrade_version_is_refused() {
        for bad in [
            "1.3", "v1", "v1.", "v.3", "vx.y", "", "v1.3.1", "v 1.3", "v+1.3",
        ] {
            let owned = bad.to_owned();
            let result =
                check_downgrade(move |name| (name == PARAM_DOWNGRADE).then(|| owned.clone()));
            assert!(result.is_err(), "accepted version {bad:?}");
        }
    }

    #[test]
    fn the_api_version_constant_parses() {
        // The downgrade check silently permits everything if this stops being
        // a well-formed version, so it is asserted rather than assumed.
        assert_eq!(
            ParsedVersion::parse(API_VERSION),
            Some(ParsedVersion { major: 1, minor: 3 }),
        );
    }
}
