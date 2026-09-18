// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! A resource body: the bytes that arrived, and their parsed form on demand.
//!
//! # `text` is authoritative
//!
//! It is the JSON exactly as the Node wrote it, sliced out of the registration
//! request by the span scanner and carried unchanged through storage -- so what
//! a Controller reads back is byte-for-byte what was registered. `1e3` stays
//! `1e3`, `"café"` stays escaped. A parse would normalise all of that
//! irreversibly, and the HTTP and WebSocket views would then describe the same
//! resource with different bytes.
//!
//! # Divergence: `data` is genuinely lazy here
//!
//! Python constructs `Body(data_text, data)` with the parse already in hand,
//! because the same scan that produced the span also produced the dict. Every
//! resource registered over HTTP therefore holds **both** representations for
//! its lifetime -- roughly 50 MB of parsed values beside the text at 15,000
//! resources with ~1 KB bodies. `Body`'s "parsed once on first use" docstring
//! is true of its other construction paths and not of that one.
//!
//! This port drops it. The store needs exactly three scalars from a parsed
//! body -- `id`, the parent id, and `version` -- and `RegisteredResource`
//! already carries all three as its own fields. They are extracted during
//! validation, which walks the document anyway, and the parsed value is then
//! released.
//!
//! What that costs: a resource that *is* filtered pays one re-parse, cached
//! thereafter, where Python pays zero. What it buys: a resource never filtered
//! holds text only. Most Query traffic is unfiltered collection GETs served
//! straight from `text`, so this should be strongly favourable -- **to be
//! confirmed at M7 against the filter-heavy phases**, not assumed.
//!
//! # Why `Arc`
//!
//! One change fans out to every matching subscription. Cloning an `Arc` there
//! copies a pointer; cloning the text would copy the whole body once per
//! subscriber, which at fifty subscribers is fifty copies of every
//! registration.

use std::fmt;
use std::sync::{Arc, OnceLock};

use serde_json::Value;

/// The shared interior of a [`Body`].
#[derive(Debug)]
struct BodyInner {
    /// The bytes as they arrived. `Box<str>` rather than `String`: a body is
    /// never appended to, and the capacity field would be 8 bytes per resource
    /// describing a growth that cannot happen.
    text: Box<str>,
    /// Parsed on first use and kept thereafter. `OnceLock` rather than a lock
    /// plus an `Option`, so a read that finds it populated is a single atomic
    /// load and readers never block each other.
    data: OnceLock<Value>,
}

/// A resource body.
///
/// Cheap to clone: it is one `Arc`.
#[derive(Debug, Clone)]
pub struct Body {
    inner: Arc<BodyInner>,
}

impl Body {
    /// Wrap the exact bytes that arrived.
    #[must_use]
    pub fn new(text: impl Into<Box<str>>) -> Self {
        Self {
            inner: Arc::new(BodyInner {
                text: text.into(),
                data: OnceLock::new(),
            }),
        }
    }

    /// Build from an already-parsed value, when there is no original text.
    ///
    /// Only for callers that genuinely have none -- tests, and resources the
    /// registry itself synthesises. Anything arriving over the wire must come
    /// from the span slicer instead, or the fidelity guarantee is lost at that
    /// point in the chain.
    ///
    /// The parse is kept rather than thrown away and re-derived: it is already
    /// in hand and is exactly what a later read would recompute.
    #[must_use]
    pub fn from_value(value: Value) -> Self {
        let text = serde_json::to_string(&value).unwrap_or_else(|_| String::from("null"));
        let data = OnceLock::new();
        let _ = data.set(value);
        Self {
            inner: Arc::new(BodyInner {
                text: text.into_boxed_str(),
                data,
            }),
        }
    }

    /// The bytes as they arrived. Anything that serialises should use this and
    /// skip the parse entirely.
    #[must_use]
    pub fn text(&self) -> &str {
        &self.inner.text
    }

    /// The parsed form, parsed on first use and cached.
    ///
    /// A body that fails to parse yields `Value::Null`, which reads as "no
    /// fields" to every filter and structural check. That cannot happen for a
    /// body that reached the store -- decoding is what let it in -- and the
    /// alternative here is a `Result` on a hot read path that no caller could
    /// act on differently.
    #[must_use]
    pub fn data(&self) -> &Value {
        self.inner
            .data
            .get_or_init(|| serde_json::from_str(&self.inner.text).unwrap_or(Value::Null))
    }

    /// A top-level string member, without forcing a parse of anything else.
    ///
    /// Still parses the whole document on first use -- `serde_json` has no
    /// partial parse -- but it is the shape every store-side read takes, so it
    /// keeps those call sites from reaching into `Value` themselves.
    #[must_use]
    pub fn string_member(&self, key: &str) -> Option<&str> {
        self.data().get(key)?.as_str()
    }

    /// Whether the parsed form has been materialised yet.
    ///
    /// For tests and metrics. The laziness above is a claim about memory, and
    /// a claim about memory that nothing can observe is a claim nothing checks.
    #[must_use]
    pub fn is_parsed(&self) -> bool {
        self.inner.data.get().is_some()
    }
}

impl PartialEq for Body {
    /// Equal when the bytes are equal.
    ///
    /// Deliberately not a parsed comparison: two bodies that differ only in
    /// whitespace are the same resource to a client and different bytes on the
    /// wire, and this type exists to preserve that difference.
    fn eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.inner, &other.inner) || self.inner.text == other.inner.text
    }
}

impl Eq for Body {}

impl fmt::Display for Body {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.text())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_text_survives_exactly() {
        // Spelling a parse would normalise away. This is the guarantee.
        let original = r#"{"a": 1e3, "b": "café", "c":  1.50 }"#;
        let body = Body::new(original);
        assert_eq!(body.text(), original);
        // And reading fields does not rewrite it.
        assert_eq!(body.string_member("b"), Some("café"));
        assert_eq!(body.text(), original);
    }

    #[test]
    fn parsing_is_deferred_until_something_asks() {
        let body = Body::new(r#"{"id": "x"}"#);
        assert!(!body.is_parsed(), "constructing a body must not parse it");

        assert_eq!(body.text(), r#"{"id": "x"}"#);
        assert!(!body.is_parsed(), "reading the text must not parse it");

        assert_eq!(body.string_member("id"), Some("x"));
        assert!(body.is_parsed(), "reading a field parses");
    }

    #[test]
    fn the_parse_happens_once() {
        let body = Body::new(r#"{"id": "x"}"#);
        let first = body.data() as *const Value;
        let second = body.data() as *const Value;
        assert_eq!(first, second, "the cached value was rebuilt");
    }

    #[test]
    fn from_value_arrives_already_parsed() {
        let body = Body::from_value(json!({"id": "x"}));
        assert!(body.is_parsed(), "the parse was in hand and was discarded");
        assert_eq!(body.string_member("id"), Some("x"));
    }

    #[test]
    fn cloning_shares_rather_than_copies() {
        let body = Body::new(r#"{"id": "x"}"#);
        let clone = body.clone();
        // Parsing through one is visible through the other, which is the
        // observable form of "fifty subscribers share one body".
        assert!(!clone.is_parsed());
        let _ = body.data();
        assert!(clone.is_parsed(), "the clone did not share the cache");
    }

    #[test]
    fn equality_is_on_bytes_not_on_meaning() {
        assert_eq!(Body::new(r#"{"a":1}"#), Body::new(r#"{"a":1}"#));
        // Same meaning, different bytes: different bodies, because the wire
        // difference is the thing this type exists to keep.
        assert_ne!(Body::new(r#"{"a":1}"#), Body::new(r#"{"a": 1}"#));
        assert_ne!(Body::new(r#"{"a":1e3}"#), Body::new(r#"{"a":1000.0}"#));
    }

    #[test]
    fn an_unparseable_body_reads_as_having_no_fields() {
        // Unreachable through the registry -- decode is what lets a body in --
        // but a panic here would take down a read path, so it is pinned.
        let body = Body::new("{not json");
        assert_eq!(body.data(), &Value::Null);
        assert_eq!(body.string_member("anything"), None);
        assert_eq!(body.text(), "{not json", "the bytes are still served");
    }

    #[test]
    fn a_non_object_body_reads_as_having_no_fields() {
        for text in ["[1,2]", "\"a string\"", "null", "7"] {
            let body = Body::new(text);
            assert_eq!(body.string_member("id"), None, "for {text}");
        }
    }
}
