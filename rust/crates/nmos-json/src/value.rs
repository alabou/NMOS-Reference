// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The value types that are not plain Rust primitives.
//!
//! # The three-state model is not uniform, and that is the design
//!
//! Python gives every member a `_defined` flag on top of its value, so a member
//! can be undefined, defined-and-null, or defined-with-a-value. But *which* of
//! those three a member can reach is a property of its base type, not of the
//! member:
//!
//! | base type | a JSON `null` becomes |
//! |---|---|
//! | `NString` | **silently dropped** -- the member stays undefined |
//! | `NInt`, `NFloat`, `NBool`, `NEnum`, `NTime`, `NTags`, arrays | an error |
//! | `NUrl` | **defined, and empty** -- and it re-encodes as `null` |
//! | `NNullString`, `NNull`, `NGeneric` | defined, and null |
//!
//! So a blanket `Option<Option<T>>` would be a lie for eleven of those fourteen
//! rows: it would offer a null state that cannot be reached, on every one of the
//! 926 members on the wire, for the sake of the few that need it.
//!
//! Instead the defined/undefined axis is `Option<T>` -- absent means Python's
//! `_defined == False`, and `#[serde(skip_serializing_if = "Option::is_none")]`
//! reproduces "undefined members are omitted" exactly -- and only the types that
//! genuinely admit null carry [`Nullable`]. `NString` becomes a plain `String`,
//! which makes Python's null-dropping rule a fact the compiler enforces rather
//! than a convention someone has to remember.

use std::fmt;

use indexmap::IndexMap;
use serde::{Serialize, Serializer};

/// A value that may be JSON `null` while still being present.
///
/// Only for the types whose Python counterpart keeps null as a *defined* value:
/// `NNullString`, `NNull` and `NGeneric`. Wrapped in `Option` at the member
/// level, `Option<Nullable<T>>` expresses all three Python states -- absent,
/// present-and-null, present-with-a-value -- for exactly the members that have
/// them.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Default)]
pub enum Nullable<T> {
    /// JSON `null`, present in the document.
    #[default]
    Null,
    /// A value.
    Value(T),
}

impl<T> Nullable<T> {
    /// The value, or `None` when null.
    pub const fn as_option(&self) -> Option<&T> {
        match self {
            Self::Null => None,
            Self::Value(v) => Some(v),
        }
    }

    /// Whether this is JSON `null`.
    pub const fn is_null(&self) -> bool {
        matches!(self, Self::Null)
    }
}

/// `null` as a value, so [`Nullable::as_json`] can hand out a reference to it.
///
/// A `const` item rather than `&serde_json::Value::Null` written inline:
/// `Value` has drop glue, so the inline form is not promoted to `'static` and
/// would not outlive the expression.
const JSON_NULL: serde_json::Value = serde_json::Value::Null;

impl Nullable<serde_json::Value> {
    /// This member as JSON, with `null` spelled as `Value::Null`.
    ///
    /// The validators that accept a null -- `CheckNullPort`,
    /// `CheckNullAutoPort` and friends -- distinguish "null" from "absent" and
    /// from "the wrong type", which `as_option` flattens into one `None`.
    /// Python's `field.value` makes the same distinction by handing the check
    /// `None` and letting `isinstance` decide, so this is the shape that keeps
    /// the two agreeing.
    #[must_use]
    pub const fn as_json(&self) -> &serde_json::Value {
        match self {
            Self::Null => &JSON_NULL,
            Self::Value(v) => v,
        }
    }
}

impl<T> From<Option<T>> for Nullable<T> {
    fn from(value: Option<T>) -> Self {
        value.map_or(Self::Null, Self::Value)
    }
}

/// Seconds since the TAI epoch, as NMOS writes timestamps.
///
/// Stored the way Python stores it: **UTC-based**, with the 37-second offset
/// already subtracted on decode and added back on encode. Keeping the stored
/// form UTC rather than TAI is not cosmetic -- `nmos/registry/` compares these
/// against `health_now()`, which is UTC-based, so a TAI-stored value would be
/// 37 seconds in the future on every comparison.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub struct Tai {
    /// Seconds, UTC-based.
    pub sec: i64,
    /// Nanoseconds.
    ///
    /// `u64` rather than `u32` because Python stores whatever integer the
    /// document carried, with no range check -- see [`crate::decode::tai`].
    pub nsec: u64,
}

impl Tai {
    /// The TAI-UTC offset NMOS uses. `nmos/json/types.py:1121`.
    pub const UTC_OFFSET: i64 = 37;

    /// Render as the TAI `"sec:nsec"` string that goes on the wire.
    #[must_use]
    pub fn to_wire(self) -> String {
        format!(
            "{}:{}",
            self.sec.saturating_add(Self::UTC_OFFSET),
            self.nsec
        )
    }
}

impl fmt::Display for Tai {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.to_wire())
    }
}

/// A hyperlink: the text as written, and the target.
///
/// Python's `NHyperlink` stores both and sets them to the same string on
/// decode, which is why a decoded link always round-trips to what arrived.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Default)]
pub struct Hyperlink {
    /// The text as it appeared in the document.
    pub text: String,
    /// The link target.
    pub link: String,
}

impl Hyperlink {
    /// Build from one string, as decoding does.
    #[must_use]
    pub fn new(value: impl Into<String>) -> Self {
        let value = value.into();
        Self {
            text: value.clone(),
            link: value,
        }
    }
}

/// NMOS `tags`: a mapping from name to a list of string values.
///
/// Insertion-ordered, because Python dictionaries are and the encoder walks
/// them in that order -- so a re-encoded resource would otherwise list its tags
/// differently from the one that was registered.
pub type Tags = IndexMap<String, Vec<String>>;

// ---------------------------------------------------------------------------
// Serialization
// ---------------------------------------------------------------------------
//
// Hand-written rather than derived, because each of these encodes as something
// other than its Rust shape: a `Nullable` is null or the bare value with no
// enum tag, an `EnumId` and a `Tai` are strings, and a `Hyperlink` is just its
// text. A derive would emit the struct, which is not what the Python encoder
// writes.

impl<T: Serialize> Serialize for Nullable<T> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        match self {
            Self::Null => serializer.serialize_none(),
            Self::Value(value) => value.serialize(serializer),
        }
    }
}

impl Serialize for Tai {
    /// Encodes as the TAI `"sec:nsec"` string, with the 37-second offset added
    /// back -- the stored form is UTC-based. See [`Tai`].
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.to_wire())
    }
}

impl Serialize for Hyperlink {
    /// Encodes as its text alone. Python's `NHyperlink` stores text and link
    /// separately but writes one string, so a round trip returns what arrived.
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.text)
    }
}

// ---------------------------------------------------------------------------
// Raw JSON
// ---------------------------------------------------------------------------

/// Arbitrary JSON, preserved **byte for byte**.
///
/// The Rust counterpart of Python's `RawJson`, and it carries the same
/// guarantee: what goes in comes out unchanged.
///
/// # Why not `serde_json::Value`
///
/// Because `Value` is a parse, and a parse is lossy in three ways that all
/// reach the wire. Measured on one body:
///
/// ```text
/// in:            {"id": "x",  "n": 1e3, "e": "café", "r": "🎬"}
/// through Value: {"e":"café","id":"x","n":1000.0,"r":"🎬"}
/// ```
///
/// The keys were reordered, `1e3` became `1000.0`, and the whitespace went.
/// Any one of those makes a WebSocket grain describe a resource differently
/// from the HTTP response for the same resource -- which is exactly the
/// divergence the registry stores `Body::text` to avoid.
///
/// # What it cannot rescue
///
/// Decoding. A body arriving over the wire is parsed into a `serde_json::Value`
/// by the span slicer before any generated type sees it, so a `RawJson` member
/// *decoded* from that value carries the re-serialised form, not the original
/// bytes. That is not a gap this type can close; it is a property of decoding
/// through a DOM, which the error-message contract requires.
///
/// It does not matter for the case this exists for. A grain's `pre` and `post`
/// are never decoded -- the registry **constructs** them from
/// `Body::text` with [`RawJson::from_text`], and that path is exact.
#[derive(Debug, Clone)]
pub struct RawJson(Box<serde_json::value::RawValue>);

impl RawJson {
    /// Wrap JSON text, preserving it exactly.
    ///
    /// # Errors
    ///
    /// The text is not valid JSON. A grain would then carry a malformed body,
    /// which is worse than failing to build it.
    pub fn from_text(text: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(text).map(Self)
    }

    /// Wrap an already-parsed value, accepting the re-serialisation.
    ///
    /// For values the registry synthesises rather than relays, where there is
    /// no original to preserve.
    ///
    /// # Errors
    ///
    /// The value cannot be serialised.
    pub fn from_value(value: &serde_json::Value) -> Result<Self, serde_json::Error> {
        Self::from_text(&serde_json::to_string(value)?)
    }

    /// The JSON text, exactly as it was given.
    #[must_use]
    pub fn get(&self) -> &str {
        self.0.get()
    }

    /// Whether the stored JSON is an object.
    ///
    /// Reads the first non-whitespace byte rather than parsing. That is exact
    /// for valid JSON -- which construction guarantees -- and it keeps a
    /// validator that runs on every registration from parsing a document twice.
    #[must_use]
    pub fn is_object(&self) -> bool {
        self.get().bytes().find(|b| !b.is_ascii_whitespace()) == Some(b'{')
    }

    /// The parsed form, for a caller that needs field access.
    ///
    /// # Errors
    ///
    /// Only if the text stopped being valid JSON, which construction prevents.
    pub fn parse(&self) -> Result<serde_json::Value, serde_json::Error> {
        serde_json::from_str(self.0.get())
    }
}

impl PartialEq for RawJson {
    /// Equal when the bytes are equal.
    ///
    /// Deliberately not a parsed comparison: two documents differing only in
    /// whitespace are the same JSON and different bytes, and preserving that
    /// difference is what this type is for.
    fn eq(&self, other: &Self) -> bool {
        self.0.get() == other.0.get()
    }
}

impl Eq for RawJson {}

impl Default for RawJson {
    fn default() -> Self {
        Self(
            serde_json::value::RawValue::from_string("null".to_owned())
                .unwrap_or_else(|_| unreachable!("`null` is valid JSON")),
        )
    }
}

impl Serialize for RawJson {
    /// Writes the stored bytes, untouched.
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        self.0.serialize(serializer)
    }
}

impl fmt::Display for RawJson {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.get())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raw_json_preserves_the_bytes_a_parse_would_change() {
        // Each of these is a real loss through `serde_json::Value`, and each
        // would make a WebSocket grain describe a resource differently from the
        // HTTP response for the same resource.
        let original = "{\"id\": \"x\",  \"n\": 1e3, \"e\": \"caf\\u00e9\", \"z\": 1}";
        let raw = RawJson::from_text(original).expect("valid JSON");

        assert_eq!(raw.get(), original);
        assert_eq!(
            serde_json::to_string(&raw).expect("serialises"),
            original,
            "serialising re-encoded rather than writing the bytes",
        );

        // And the same value through `Value`, to show what is being avoided.
        let parsed: serde_json::Value = serde_json::from_str(original).expect("valid");
        let through_value = serde_json::to_string(&parsed).expect("serialises");
        assert_ne!(
            through_value, original,
            "if a parse round-trips exactly, this type has no purpose",
        );
        assert!(
            !through_value.contains("1e3"),
            "the number survived a parse"
        );
    }

    #[test]
    fn raw_json_equality_is_on_bytes() {
        // Two documents differing only in whitespace are the same JSON and
        // different bytes. Preserving that difference is the point.
        let a = RawJson::from_text(r#"{"a":1}"#).expect("valid");
        let b = RawJson::from_text(r#"{"a": 1}"#).expect("valid");
        assert_ne!(a, b);
        assert_eq!(a, RawJson::from_text(r#"{"a":1}"#).expect("valid"));
    }

    #[test]
    fn raw_json_rejects_text_that_is_not_json() {
        // Construction is what guarantees the stored bytes are spliceable, so
        // it has to be the thing that refuses.
        assert!(RawJson::from_text("{not json").is_err());
        assert!(RawJson::from_text("").is_err());
        assert!(RawJson::from_text("{\"a\":").is_err());
    }

    #[test]
    fn is_object_answers_without_parsing() {
        // Python asks `isinstance(v, dict)`; this reads the first
        // non-whitespace byte, which is exact for valid JSON.
        assert!(RawJson::from_text(r#"{"a":1}"#).expect("valid").is_object());
        assert!(RawJson::from_text("  \n\t {}").expect("valid").is_object());
        for not_an_object in ["[1,2]", "null", "7", r#""a string""#, "true"] {
            assert!(
                !RawJson::from_text(not_an_object)
                    .expect("valid")
                    .is_object(),
                "{not_an_object} was called an object",
            );
        }
    }

    #[test]
    fn the_default_is_valid_json() {
        // A default that could not be spliced would produce a malformed grain.
        let default = RawJson::default();
        assert_eq!(default.get(), "null");
        assert!(serde_json::from_str::<serde_json::Value>(default.get()).is_ok());
    }

    #[test]
    fn tai_round_trips_through_the_offset() {
        // Stored UTC, written TAI: the 37 seconds reappear on the wire.
        let t = Tai {
            sec: 1_600_000_000,
            nsec: 42,
        };
        assert_eq!(t.to_wire(), "1600000037:42");
    }

    #[test]
    fn nullable_distinguishes_null_from_a_value() {
        let null: Nullable<String> = Nullable::Null;
        let some = Nullable::Value("x".to_owned());
        assert!(null.is_null());
        assert!(!some.is_null());
        assert_eq!(some.as_option().map(String::as_str), Some("x"));
    }

    #[test]
    fn a_hyperlink_decodes_text_and_link_alike() {
        let h = Hyperlink::new("http://example.com/x");
        assert_eq!(h.text, h.link);
    }
}
