// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Decoding a JSON value into one of the base types.
//!
//! A direct transcription of every `decode_value` in `nmos/json/types.py`,
//! **quirks included**. Several of these look like bugs and are not being
//! fixed here, because the registry's accept/reject behaviour is the thing the
//! Rust port has to reproduce exactly; a body Python stores must be a body Rust
//! stores. Where a quirk is genuinely a defect it is raised with the user
//! separately rather than quietly corrected on one side only.
//!
//! The ones worth knowing before reading:
//!
//! * a JSON `null` for a string is **dropped**, leaving the member undefined --
//!   not an error, and not a null value ([`string`]);
//! * an integer member accepts an integral float, and **truncates** it, so
//!   `8080.7` is stored as `8080` ([`int`]);
//! * a URL member turns `null` into a **defined empty string**, which then
//!   re-encodes as `null` ([`url`]);
//! * a tags member turns a non-list value into an **empty list** rather than
//!   erroring ([`tags`]);
//! * an array-of-hyperlink member **stringifies** whatever it is given, so
//!   `[5]` becomes `["5"]` ([`array_of_hyperlink`]).
//!
//! # Undefined versus null
//!
//! Two different `None`s appear in these signatures and they mean opposite
//! things, so the return types keep them apart:
//!
//! * `Result<Option<T>>` -- `Ok(None)` means *leave the member undefined*. Only
//!   [`string`] can produce it.
//! * `Result<Nullable<T>>` -- `Ok(Nullable::Null)` means *present, and null*.

use serde_json::Value;

use crate::enums::EnumId;
use crate::error::{Error, Result, python_repr, python_type_name};
use crate::value::{Hyperlink, Nullable, Tags, Tai};

/// Decode a string member.
///
/// Returns `Ok(None)` for JSON `null`: Python's `NString.decode_value` has a
/// bare `pass` on that branch (`types.py:157-158`), so the member is left
/// undefined rather than erroring or being set to null. If the member is
/// required, the caller's required-presence check is what then reports
/// `missing required member X` -- which is the verdict Python produces for
/// `{"label": null}`.
pub fn string(value: &Value) -> Result<Option<String>> {
    match value {
        Value::String(s) => Ok(Some(s.clone())),
        Value::Null => Ok(None),
        other => Err(Error::invalid_data(format!(
            "expected string, got {}",
            python_type_name(other)
        ))),
    }
}

/// Decode an integer member.
///
/// Accepts an integral *or* fractional float and truncates toward zero, exactly
/// as Python's `int(data)` does, so `8080.0` and `8080.7` both become `8080`.
/// A bool is refused: Python spells that `isinstance(data, int) and not
/// isinstance(data, bool)` because `bool` subclasses `int` there.
///
/// **Known bounded divergence.** Python integers are arbitrary precision, so
/// `int(1e308)` yields an exact 309-digit value where this saturates at
/// `i64::MAX`. No NMOS field -- port, dimension, bit rate -- comes close, so
/// this is reachable only from deliberately absurd input, and the parity fuzz
/// corpus generates exactly that. It is recorded rather than hidden.
pub fn int(value: &Value) -> Result<i64> {
    match value {
        Value::Number(n) if !n.is_f64() => n.as_i64().ok_or_else(|| {
            Error::invalid_data(format!("expected int, got {}", python_type_name(value)))
        }),
        Value::Number(n) => {
            let f = n.as_f64().unwrap_or(0.0);
            Ok(f.trunc() as i64)
        }
        other => Err(Error::invalid_data(format!(
            "expected int, got {}",
            python_type_name(other)
        ))),
    }
}

/// Decode a float member. Accepts integers too, refuses bools.
pub fn float(value: &Value) -> Result<f64> {
    match value {
        Value::Number(n) => n.as_f64().ok_or_else(|| {
            Error::invalid_data(format!("expected float, got {}", python_type_name(value)))
        }),
        other => Err(Error::invalid_data(format!(
            "expected float, got {}",
            python_type_name(other)
        ))),
    }
}

/// Decode a boolean member. Nothing else converts.
pub fn bool(value: &Value) -> Result<bool> {
    match value {
        Value::Bool(b) => Ok(*b),
        other => Err(Error::invalid_data(format!(
            "expected bool, got {}",
            python_type_name(other)
        ))),
    }
}

/// Decode an enum member.
///
/// Any string is accepted -- see [`crate::enums`] for why an unknown value is
/// stored rather than rejected. Only a non-string fails.
pub fn enum_id(value: &Value) -> Result<EnumId> {
    match value {
        Value::String(s) => Ok(EnumId::new(s.as_str())),
        other => Err(Error::invalid_data(format!(
            "expected string for enum, got {}",
            python_type_name(other)
        ))),
    }
}

/// Decode a nullable-string member, where `null` is a value rather than an
/// absence.
pub fn nullable_string(value: &Value) -> Result<Nullable<String>> {
    match value {
        Value::Null => Ok(Nullable::Null),
        Value::String(s) => Ok(Nullable::Value(s.clone())),
        other => Err(Error::invalid_data(format!(
            "expected string or null, got {}",
            python_type_name(other)
        ))),
    }
}

/// Decode an `NNull` member, which holds any JSON value including null.
///
/// Infallible. Python's `_cast` refuses anything that is not JSON-representable,
/// but its input always comes from `json.loads`, so that branch cannot be
/// reached -- and in Rust a `serde_json::Value` is JSON-representable by
/// construction.
pub fn null_value(value: &Value) -> Nullable<Value> {
    match value {
        Value::Null => Nullable::Null,
        other => Nullable::Value(other.clone()),
    }
}

/// Decode a generic member: anything at all, always defined.
pub fn generic(value: &Value) -> Value {
    value.clone()
}

/// Decode a hyperlink member.
pub fn hyperlink(value: &Value) -> Result<Hyperlink> {
    match value {
        Value::String(s) => Ok(Hyperlink::new(s.as_str())),
        other => Err(Error::invalid_data(format!(
            "expected string for hyperlink, got {}",
            python_type_name(other)
        ))),
    }
}

/// Decode a URL member.
///
/// A JSON `null` becomes a **defined empty string**, not an undefined member --
/// the opposite of [`string`]. The asymmetry is Python's and it is load-bearing
/// on the way out: an empty URL re-encodes as `null`, so a resource registered
/// with `"href": null` is served back with `"href": null`.
///
/// Python's version has an unreachable second `elif data is None: pass` after
/// this branch. Dead code, reported separately; only the live branch is ported.
pub fn url(value: &Value) -> Result<String> {
    match value {
        Value::String(s) => Ok(s.clone()),
        Value::Null => Ok(String::new()),
        other => Err(Error::invalid_data(format!(
            "expected string for URL, got {}",
            python_type_name(other)
        ))),
    }
}

/// Decode a TAI timestamp of the form `"seconds:nanoseconds"`.
///
/// The stored value is UTC-based: the 37-second offset is subtracted here and
/// added back when encoding. See [`Tai`].
pub fn tai(value: &Value) -> Result<Tai> {
    let Value::String(s) = value else {
        return Err(Error::invalid_data(format!(
            "expected string for time, got {}",
            python_type_name(value)
        )));
    };

    // Equivalent to Python's ``^([0-9]+):([0-9]+)\Z`` without paying for a
    // regex: both parts must be non-empty runs of ASCII digits, and there must
    // be exactly one colon with nothing after the second run.
    //
    // (That pattern used ``$`` until this port. Python's ``$`` also matches
    // before a trailing newline, so ``"1600000000:0\n"`` was a valid timestamp;
    // it is now ``\Z`` on both sides.)
    let mut parts = s.split(':');
    let invalid = || Error::invalid_data(format!("invalid TAI timestamp: {}", python_repr(s)));

    let (Some(sec_text), Some(nsec_text), None) = (parts.next(), parts.next(), parts.next()) else {
        return Err(invalid());
    };
    let digits = |t: &str| !t.is_empty() && t.bytes().all(|b| b.is_ascii_digit());
    if !digits(sec_text) || !digits(nsec_text) {
        return Err(invalid());
    }

    // Saturating, not fallible. Python integers have no range, so
    // "99999999999999999999:0" is a timestamp it *accepts* -- and accept/reject
    // is the contract the two implementations have to share. Rejecting on
    // overflow would diverge on exactly the input a fuzz corpus reaches for.
    //
    // The magnitude saturates where Python's would not. That is recorded rather
    // than hidden: on the registry path the decoded value is discarded after
    // validating (`nmos/registry/decode.py:167-171`), and resources are stored
    // and served as their original bytes, so no client can observe the clamp.
    let tai_sec: i64 = sec_text.parse().unwrap_or(i64::MAX);
    let nsec: u64 = nsec_text.parse().unwrap_or(u64::MAX);
    Ok(Tai {
        sec: tai_sec.saturating_sub(Tai::UTC_OFFSET),
        nsec,
    })
}

/// Decode a `tags` member.
///
/// A value that is not a list becomes an **empty list** rather than an error --
/// Python coerces here, and a body relying on that is a body the registry
/// currently accepts.
///
/// # Key order is the parser's, not the document's
///
/// Python dictionaries preserve insertion order, so its tags come out in
/// document order. `serde_json` is built here WITHOUT `preserve_order`, because
/// that feature costs 25% on every parse and nothing in the registry needs it
/// -- so the map arrives already sorted and these tags are sorted too.
///
/// That is unobservable in the registry. The only typed encode it performs is
/// `build_grain`, which splices the resource's ORIGINAL bytes via `RawJson`
/// precisely so "the WebSocket view would not normalise spelling the HTTP view
/// preserves" (`subscriptions.py:491`). A decoded resource is never re-encoded,
/// so decoded tag order never reaches a client.
///
/// An `IndexMap` is still the right type: it preserves whatever order it is
/// given, so the day a caller does need document order, only the parser has to
/// change.
pub fn tags(value: &Value) -> Result<Tags> {
    let Value::Object(map) = value else {
        return Err(Error::invalid_data(format!(
            "expected dict for tags, got {}",
            python_type_name(value)
        )));
    };

    let mut out = Tags::with_capacity(map.len());
    for (key, item) in map {
        let values = match item {
            Value::Array(items) => items.iter().map(json_str).collect(),
            _ => Vec::new(),
        };
        out.insert(key.clone(), values);
    }
    Ok(out)
}

/// Decode an array of strings. Every element must be a string.
pub fn array_of_string(value: &Value) -> Result<Vec<String>> {
    let items = expect_array(value)?;
    items
        .iter()
        .map(|item| match item {
            Value::String(s) => Ok(s.clone()),
            other => Err(Error::invalid_data(format!(
                "expected string in array, got {}",
                python_type_name(other)
            ))),
        })
        .collect()
}

/// Decode an array of integers, with the same float truncation as [`int`].
pub fn array_of_int(value: &Value) -> Result<Vec<i64>> {
    let items = expect_array(value)?;
    items
        .iter()
        .map(|item| match item {
            Value::Number(_) => int(item).map_err(|_| element_error("int", item)),
            other => Err(element_error("int", other)),
        })
        .collect()
}

/// Decode an array of floats. Integers convert; bools do not.
pub fn array_of_float(value: &Value) -> Result<Vec<f64>> {
    let items = expect_array(value)?;
    items
        .iter()
        .map(|item| match item {
            Value::Number(n) => n.as_f64().ok_or_else(|| element_error("float", item)),
            other => Err(element_error("float", other)),
        })
        .collect()
}

/// Decode an array of booleans.
pub fn array_of_bool(value: &Value) -> Result<Vec<bool>> {
    let items = expect_array(value)?;
    items
        .iter()
        .map(|item| match item {
            Value::Bool(b) => Ok(*b),
            other => Err(element_error("bool", other)),
        })
        .collect()
}

/// Decode an array of enum values.
///
/// The element message differs from the other arrays -- Python says
/// `expected string in enum array`, not `expected string in array`.
pub fn array_of_enum(value: &Value) -> Result<Vec<EnumId>> {
    let items = expect_array(value)?;
    items
        .iter()
        .map(|item| match item {
            Value::String(s) => Ok(EnumId::new(s.as_str())),
            other => Err(Error::invalid_data(format!(
                "expected string in enum array, got {}",
                python_type_name(other)
            ))),
        })
        .collect()
}

/// Decode an array of hyperlinks.
///
/// **Stringifies every element** rather than requiring strings, so `[5]`
/// decodes to `["5"]` and only a non-array fails. Python reaches this through
/// `str(item)`, which is why it never rejects an element.
pub fn array_of_hyperlink(value: &Value) -> Result<Vec<Hyperlink>> {
    let items = expect_array(value)?;
    Ok(items
        .iter()
        .map(|item| Hyperlink::new(json_str(item)))
        .collect())
}

/// Decode an array of arbitrary JSON values.
pub fn array_of_generic(value: &Value) -> Result<Vec<Value>> {
    Ok(expect_array(value)?.clone())
}

fn expect_array(value: &Value) -> Result<&Vec<Value>> {
    match value {
        Value::Array(items) => Ok(items),
        other => Err(Error::invalid_data(format!(
            "expected array, got {}",
            python_type_name(other)
        ))),
    }
}

fn element_error(what: &str, item: &Value) -> Error {
    Error::invalid_data(format!(
        "expected {what} in array, got {}",
        python_type_name(item)
    ))
}

/// Render a JSON value the way Python's `str()` would.
///
/// Used where Python calls `str(item)` on something that need not be a string.
/// The spellings differ from JSON's in three places and all three are reachable
/// from a registration body: Python writes `True`/`False` rather than
/// `true`/`false`, and `None` rather than `null`.
fn json_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        Value::Null => "None".to_owned(),
        other => other.to_string(),
    }
}

/// A generic JSON member, preserved byte for byte where it can be.
///
/// Infallible, like Python's `NGeneric`: any JSON is acceptable, including
/// `null`.
///
/// # The fidelity this can and cannot give
///
/// The value handed here has already been parsed by the span slicer, so the
/// original bytes are gone before this runs -- what it preserves is *this*
/// rendering, stably. The path that matters for the byte-fidelity guarantee is
/// the other one: a grain's `pre`/`post` are **constructed** from `Body::text`
/// with `RawJson::from_text`, never decoded, and that is exact.
#[must_use]
pub fn raw_json(value: &serde_json::Value) -> crate::value::RawJson {
    crate::value::RawJson::from_value(value).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_null_string_is_dropped_not_nulled() {
        // The member is left undefined; it is not an error and not a null.
        assert_eq!(string(&json!(null)).unwrap(), None);
        assert_eq!(string(&json!("x")).unwrap(), Some("x".to_owned()));
        assert_eq!(
            string(&json!(5)).unwrap_err().message(),
            "expected string, got int"
        );
    }

    #[test]
    fn an_integer_member_truncates_a_float() {
        assert_eq!(int(&json!(8080)).unwrap(), 8080);
        assert_eq!(int(&json!(8080.0)).unwrap(), 8080);
        assert_eq!(int(&json!(8080.7)).unwrap(), 8080);
        assert_eq!(int(&json!(-8080.7)).unwrap(), -8080);
        assert_eq!(
            int(&json!(true)).unwrap_err().message(),
            "expected int, got bool"
        );
    }

    #[test]
    fn a_url_turns_null_into_an_empty_string() {
        assert_eq!(url(&json!(null)).unwrap(), "");
        assert_eq!(url(&json!("http://x/")).unwrap(), "http://x/");
    }

    #[test]
    fn tags_coerce_a_non_list_value_to_empty() {
        let t = tags(&json!({"a": ["1", "2"], "b": "not a list"})).unwrap();
        assert_eq!(t["a"], vec!["1".to_owned(), "2".to_owned()]);
        assert!(t["b"].is_empty());
    }

    #[test]
    fn tags_keep_whatever_order_the_parser_gave_them() {
        // Sorted, because serde_json is built without `preserve_order`. The
        // map type still preserves order, so this follows the parser rather
        // than imposing a sort of its own -- see the note on `tags`.
        let t = tags(&json!({"z": [], "a": [], "m": []})).unwrap();
        assert_eq!(t.keys().collect::<Vec<_>>(), vec!["a", "m", "z"]);

        // The property that actually matters: nothing is lost or invented.
        assert_eq!(t.len(), 3);
    }

    #[test]
    fn tai_subtracts_the_offset_and_rejects_a_trailing_newline() {
        let t = tai(&json!("1600000037:42")).unwrap();
        assert_eq!(t.sec, 1_600_000_000);
        assert_eq!(t.nsec, 42);
        assert_eq!(
            tai(&json!("y")).unwrap_err().message(),
            "invalid TAI timestamp: 'y'"
        );
        // The anchor fix, from both sides.
        assert!(tai(&json!("1600000037:42\n")).is_err());
        // Python has no integer range, so it accepts these; rejecting on
        // overflow would be an accept/reject divergence. The magnitude clamps,
        // which no client can observe -- see the note in `tai`.
        assert!(tai(&json!("99999999999999999999:0")).is_ok());
        assert!(tai(&json!("1600000037:99999999999999999999")).is_ok());
        assert_eq!(tai(&json!("0:0")).unwrap().sec, -37);
        assert!(tai(&json!("1600000037:42:9")).is_err());
        assert!(tai(&json!("1600000037:")).is_err());
    }

    #[test]
    fn array_element_messages_name_the_element_type() {
        assert_eq!(
            array_of_string(&json!(["a", 5])).unwrap_err().message(),
            "expected string in array, got int"
        );
        assert_eq!(
            array_of_enum(&json!([5])).unwrap_err().message(),
            "expected string in enum array, got int"
        );
        assert_eq!(
            array_of_string(&json!("nope")).unwrap_err().message(),
            "expected array, got str"
        );
    }

    #[test]
    fn hyperlink_arrays_stringify_their_elements() {
        let links = array_of_hyperlink(&json!([5, "x", true])).unwrap();
        let text: Vec<_> = links.iter().map(|h| h.text.as_str()).collect();
        assert_eq!(text, vec!["5", "x", "True"]);
    }
}
