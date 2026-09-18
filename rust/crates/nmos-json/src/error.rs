// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The error kinds the type layer can produce, and their exact messages.
//!
//! # These strings are an API contract, not diagnostics
//!
//! `nmos/registry/handlers_registration.py:158` interpolates the decode
//! exception into the HTTP 400 body a Node receives. So the text below is not a
//! developer convenience that can be improved -- changing it changes what the
//! registry tells a client, and the Rust/Python parity harness compares these
//! byte for byte at its L3 level.
//!
//! # Only five kinds are reachable
//!
//! `nmos/errors/__init__.py` defines about thirty exception classes, but a
//! survey of every `raise` in the type layer (`nmos/json/`, `nmos/validators.py`
//! and all 269 generated modules) shows exactly five in use:
//!
//! | Python | count | raised when |
//! |---|---|---|
//! | `NotAvailable` | 1216 | reading a member that was never decoded |
//! | `InvalidObject` | 521 | a required member is absent, or an assertion failed |
//! | `InvalidData` | 308 | the JSON is the wrong shape for this type |
//! | `InvalidType` | 11 | a setter was handed the wrong Python type |
//! | `JsonSpanError` | 6 | the span slicer could not walk the source |
//!
//! `NotMatching` is *imported* by every generated polymorphic module and never
//! raised: a body matching no variant produces
//! `InvalidData("no matching type for polymorphic NSource")`. That import is
//! dead, and the Rust emitter does not reproduce it.
//!
//! `InvalidType` guards Python's setters against a wrong runtime type -- a whole
//! class of error Rust's type system removes before it can happen. It is kept
//! here only so the enum can round-trip a Python verdict in the parity harness;
//! nothing in this crate constructs one.

use std::fmt;

/// What kind of failure this is. Mirrors the Python exception class, because
/// the parity harness compares the class as well as the message (its L2 level):
/// two implementations that reject the same body for different stated reasons
/// have not really agreed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ErrorKind {
    /// Reading a member that was never decoded. Python: `NotAvailable`.
    NotAvailable,
    /// A required member is absent, or a `Check*` assertion failed.
    /// Python: `InvalidObject`.
    InvalidObject,
    /// The JSON is the wrong shape for this type. Python: `InvalidData`.
    InvalidData,
    /// A setter received the wrong type. Python: `InvalidType`. Unreachable
    /// from Rust; present so a Python verdict can be represented.
    InvalidType,
    /// The span slicer could not walk the source. Python: `JsonSpanError`.
    JsonSpan,
}

impl ErrorKind {
    /// The Python exception class name, as the parity harness reports it.
    #[must_use]
    pub const fn python_name(self) -> &'static str {
        match self {
            Self::NotAvailable => "NotAvailable",
            Self::InvalidObject => "InvalidObject",
            Self::InvalidData => "InvalidData",
            Self::InvalidType => "InvalidType",
            Self::JsonSpan => "JsonSpanError",
        }
    }
}

/// A decode or validation failure, carrying the message the client will see.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    kind: ErrorKind,
    message: String,
}

impl Error {
    /// Build an error of `kind` with `message` used verbatim.
    #[must_use]
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    /// `InvalidData` -- the JSON is the wrong shape for this type.
    #[must_use]
    pub fn invalid_data(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::InvalidData, message)
    }

    /// `InvalidObject` -- a required member is missing or an assertion failed.
    #[must_use]
    pub fn invalid_object(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::InvalidObject, message)
    }

    /// `NotAvailable` -- a member that was never decoded was read.
    #[must_use]
    pub fn not_available() -> Self {
        // Python raises this with a fixed string, everywhere.
        Self::new(ErrorKind::NotAvailable, "undefined value")
    }

    /// Which kind of failure this is.
    #[must_use]
    pub const fn kind(&self) -> ErrorKind {
        self.kind
    }

    /// The message, as it would reach an HTTP 400 body.
    #[must_use]
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for Error {}

/// The result of any decode or validation step in this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// The name Python's `type(value).__name__` would give this JSON value.
///
/// Every "expected X, got Y" message in the type layer is built from it, so a
/// Rust implementation that reported `Number` or `Null` where Python says `int`
/// or `NoneType` would differ from Python on the one part of the message a
/// client actually reads.
///
/// The int/float split matches because both languages decide it the same way:
/// `serde_json` makes `8080` an integer and `8080.0` an `f64`, and so does
/// `json.loads`. A bool is checked before the number arms for the same reason
/// Python writes `isinstance(data, int) and not isinstance(data, bool)` --
/// there, because `bool` subclasses `int`; here, because `Value::Bool` is a
/// separate variant and the ordering merely makes that explicit.
#[must_use]
pub fn python_type_name(value: &serde_json::Value) -> &'static str {
    match value {
        serde_json::Value::Null => "NoneType",
        serde_json::Value::Bool(_) => "bool",
        serde_json::Value::Number(n) => {
            if n.is_f64() {
                "float"
            } else {
                "int"
            }
        }
        serde_json::Value::String(_) => "str",
        serde_json::Value::Array(_) => "list",
        serde_json::Value::Object(_) => "dict",
    }
}

/// Render a string the way Python's `repr()` would.
///
/// Needed for one message -- `invalid TAI timestamp: {data!r}` -- and it is not
/// the same as Rust's `{:?}`. Python prefers single quotes, switches to double
/// quotes when the text contains a single quote but no double quote, and escapes
/// a different set of characters. Getting this wrong shows up as an L3 parity
/// failure on exactly the malformed inputs a client is most likely to send.
#[must_use]
pub fn python_repr(s: &str) -> String {
    let use_double = s.contains('\'') && !s.contains('"');
    let quote = if use_double { '"' } else { '\'' };

    let mut out = String::with_capacity(s.len().saturating_add(2));
    out.push(quote);
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            // Python repr escapes other C0 controls as \xNN.
            c if (c as u32) < 0x20 || (c as u32) == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn python_type_names_match_python() {
        assert_eq!(python_type_name(&json!(null)), "NoneType");
        assert_eq!(python_type_name(&json!(true)), "bool");
        assert_eq!(python_type_name(&json!(8080)), "int");
        assert_eq!(python_type_name(&json!(8080.0)), "float");
        assert_eq!(python_type_name(&json!(8080.5)), "float");
        assert_eq!(python_type_name(&json!("x")), "str");
        assert_eq!(python_type_name(&json!([])), "list");
        assert_eq!(python_type_name(&json!({})), "dict");
    }

    #[test]
    fn python_repr_quoting_follows_python() {
        assert_eq!(python_repr("y"), "'y'");
        assert_eq!(python_repr("it's"), "\"it's\"");
        // Both quote kinds present: Python stays with single and escapes.
        assert_eq!(python_repr("it's \"x\""), r#"'it\'s "x"'"#);
        assert_eq!(python_repr("a\nb"), "'a\\nb'");
        assert_eq!(python_repr("a\\b"), "'a\\\\b'");
    }

    #[test]
    fn kinds_carry_their_python_names() {
        assert_eq!(ErrorKind::InvalidData.python_name(), "InvalidData");
        assert_eq!(ErrorKind::JsonSpan.python_name(), "JsonSpanError");
    }
}
