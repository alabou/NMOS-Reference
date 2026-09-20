// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Python's `repr()`, for the values that reach an operator's log.
//!
//! # Why this exists at all
//!
//! The Python registry interpolates `{value!r}` into refusals that an operator
//! reads -- `envelope has unknown type 'nod'`, `key outside namespace
//! '/nmos'`. Those are not diagnostics for a developer; they are what tells
//! someone whether a key was written by the wrong tool or a namespace was
//! mistyped. Two implementations that describe the same corrupt key in
//! different words are a support problem that surfaces at three in the morning,
//! when somebody is comparing a Python member's log against a Rust member's.
//!
//! Rust's `{:?}` is close and not the same. It always double-quotes, where
//! Python prefers single quotes and switches only to avoid escaping; and it
//! escapes non-ASCII, where Python 3 prints it.
//!
//! This is the same family as [`crate::engine::format_repr`], which is
//! `repr()` for floats, and it delegates to it for exactly that case.
//!
//! # Where this stops being exact, and why that is the right place to stop
//!
//! `str.isprintable()` is false for the Unicode categories `Cc Cf Cs Co Cn Zl
//! Zp` and for `Zs` other than U+0020, and Python escapes every such character
//! as `\xXX`, `\uXXXX` or `\UXXXXXXXX`. Reproducing that needs Unicode
//! category tables -- and the ASCII half of it, which is the half that is
//! reachable here, needs none.
//!
//! Every key this registry writes is a configured prefix, fixed literals and
//! RFC-4122 UUIDs, so it is ASCII by construction. A key that is *not* is one
//! some other tool wrote into the namespace, and it arrives through
//! `String::from_utf8_lossy`, whose replacement character U+FFFD Python does
//! consider printable -- so the common non-ASCII case is exact too.
//!
//! What is left is a key containing a non-ASCII *non-printable* codepoint,
//! written by a third party: U+0085, U+200B and the like. Those print
//! literally here where Python would escape them. That is recorded rather than
//! hidden, and it is the boundary at which "match the message" stops being
//! worth a copy of Python's Unicode tables.

use serde_json::Value;

use crate::engine::format_repr;

/// `repr()` of a Python `str`.
///
/// Quote selection is Python's: single quotes, unless the text contains a
/// single quote and no double quote, in which case double quotes avoid an
/// escape.
#[must_use]
pub fn py_repr_str(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };

    let mut out = String::with_capacity(text.len().saturating_add(2));
    out.push(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str(r"\\"),
            '\n' => out.push_str(r"\n"),
            '\r' => out.push_str(r"\r"),
            '\t' => out.push_str(r"\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            // The ASCII non-printables, which is the whole of what is
            // reachable from a key this implementation writes. DEL (0x7f) is
            // included: Python escapes it, and it is not a control character
            // by Rust's `is_control`, so it is named explicitly.
            c if c.is_ascii_control() || c == '\u{7f}' => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// `repr()` of the Python value a JSON document decodes to.
///
/// The mapping is `json.loads`': `null` is `None`, `true`/`false` are
/// `True`/`False`, an object is a `dict` and an array is a `list`. A missing
/// member is `None`, because every call site here reaches this through
/// `document.get(name)`.
#[must_use]
pub fn py_repr(value: Option<&Value>) -> String {
    let Some(value) = value else {
        return "None".to_owned();
    };
    match value {
        Value::Null => "None".to_owned(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        Value::Number(number) => number.as_f64().map_or_else(
            // An integer: `repr` is the digits, which is what `Display` gives.
            || number.to_string(),
            |float| {
                if number.is_f64() {
                    format_repr(float)
                } else {
                    number.to_string()
                }
            },
        ),
        Value::String(text) => py_repr_str(text),
        Value::Array(items) => {
            let parts: Vec<String> = items.iter().map(|item| py_repr(Some(item))).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Object(members) => {
            if members.is_empty() {
                return "{}".to_owned();
            }
            let parts: Vec<String> = members
                .iter()
                .map(|(name, item)| format!("{}: {}", py_repr_str(name), py_repr(Some(item))))
                .collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Every expectation here was read off CPython, not derived from a rule.
    #[test]
    fn the_quote_is_chosen_the_way_python_chooses_it() {
        assert_eq!(py_repr_str("plain"), "'plain'");
        // A single quote inside and no double quote: switch, do not escape.
        assert_eq!(py_repr_str("it's"), "\"it's\"");
        // Both present: stay on single quotes and escape.
        assert_eq!(py_repr_str("it's \"x\""), r#"'it\'s "x"'"#);
        // A double quote alone needs nothing.
        assert_eq!(py_repr_str("say \"x\""), "'say \"x\"'");
    }

    #[test]
    fn the_ascii_escapes_are_pythons() {
        assert_eq!(py_repr_str("a\nb"), r"'a\nb'");
        assert_eq!(py_repr_str("a\tb"), r"'a\tb'");
        assert_eq!(py_repr_str("a\rb"), r"'a\rb'");
        assert_eq!(py_repr_str(r"a\b"), r"'a\\b'");
        assert_eq!(py_repr_str("a\u{0}b"), r"'a\x00b'");
        assert_eq!(py_repr_str("a\u{1b}b"), r"'a\x1bb'");
        assert_eq!(py_repr_str("a\u{7f}b"), r"'a\x7fb'");
    }

    #[test]
    fn printable_non_ascii_is_printed_not_escaped() {
        // Python 3's `repr` is unicode-aware; `{:?}` would give `caf\u{e9}`.
        assert_eq!(py_repr_str("café"), "'café'");
        // The one non-ASCII character actually reachable here, from
        // `from_utf8_lossy` on a key some other tool wrote.
        assert_eq!(py_repr_str("a\u{fffd}b"), "'a\u{fffd}b'");
    }

    #[test]
    fn the_json_scalars_map_to_python_names() {
        assert_eq!(py_repr(None), "None");
        assert_eq!(py_repr(Some(&Value::Null)), "None");
        assert_eq!(py_repr(Some(&json!(true))), "True");
        assert_eq!(py_repr(Some(&json!(false))), "False");
        assert_eq!(py_repr(Some(&json!(7))), "7");
        assert_eq!(py_repr(Some(&json!(-7))), "-7");
        assert_eq!(py_repr(Some(&json!("x"))), "'x'");
    }

    #[test]
    fn a_float_uses_the_float_repr_rules() {
        // Delegated, so the `1000000.0` / `1e-05` spellings hold here too.
        assert_eq!(py_repr(Some(&json!(1_000_000.0_f64))), "1000000.0");
        assert_eq!(py_repr(Some(&json!(1e-5_f64))), "1e-05");
    }

    #[test]
    fn containers_use_pythons_separators() {
        assert_eq!(py_repr(Some(&json!([1, 2]))), "[1, 2]");
        assert_eq!(py_repr(Some(&json!([]))), "[]");
        assert_eq!(py_repr(Some(&json!({}))), "{}");
        assert_eq!(py_repr(Some(&json!({"a": 1}))), "{'a': 1}");
        assert_eq!(
            py_repr(Some(&json!({"a": 1, "b": "x"}))),
            "{'a': 1, 'b': 'x'}",
        );
    }
}
