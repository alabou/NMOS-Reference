// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Locating the exact source text of a member inside a JSON object.
//!
//! Parsing throws away spelling. `{"x": 1e3}` and `{"x": 1000.0}` parse to the
//! same float, `"caf\u00e9"` and `"café"` to the same string, and no encoder
//! tell afterwards which arrived. That is fine for meaning and wrong for
//! fidelity: a registry that promises to serve a Node's registration unchanged
//! has to keep the bytes rather than re-derive them.
//!
//! The registry needs it because what must be preserved is nested. A Node POSTs
//! `{"type": "node", "data": {...}}` and it is the `data` value alone that gets
//! stored and served back, so slicing it out of the request text is the only way
//! to keep it verbatim.
//!
//! # Why this is a hand-written scanner
//!
//! Python builds it on `json.decoder`'s own primitives, so whatever the standard
//! library accepts, it accepts identically -- and its error messages are the
//! standard library's. Those messages are not diagnostics: `decode.py:149` puts
//! them straight into the HTTP 400 body as `invalid JSON body: {exc}`, so a
//! client sees text like
//!
//! ```text
//! bad value for 'data' at offset 37: Expecting value: line 1 column 5 (char 4)
//! ```
//!
//! `serde_json`'s errors are worded completely differently, so reproducing that
//! means scanning here rather than delegating.
//!
//! # Two behaviours `serde_json` does not share
//!
//! * **`NaN`, `Infinity` and `-Infinity` are accepted.** Python's `json` allows
//!   them by default, and none is valid JSON. A body containing one is stored
//!   today, so it must still be.
//! * **Offsets are character indices, not byte indices.** Python subscripts
//!   `str`, so `{"café": 1}` reports positions that differ from byte positions.
//!   Errors here convert before reporting.
//!
//! On duplicate keys the **last** occurrence wins, as `json.loads` does.

// This module is index arithmetic from top to bottom -- that is what scanning
// is -- and `clippy::arithmetic_side_effects` fires on every `index + 1`.
//
// The lint is denied elsewhere for a specific hazard: `parking_lot` locks do
// not poison, so a panic mid-mutation would leave a half-applied store, and the
// write path is therefore held to being panic-free by construction. A scanner
// cursor is not that. Every value here is an offset into a `&str`, so it is
// bounded by `source.len()`, and overflowing `usize` would need a document of
// 2^64 bytes. Spelling each step as `saturating_add` would bury the algorithm
// in noise to guard against nothing.
//
// What the module IS careful about is going out of bounds, which is a real
// risk: every read goes through `get()` or a checked slice, never an index
// expression, so a truncated document ends the scan rather than panicking.
#![allow(clippy::arithmetic_side_effects)]

use indexmap::IndexMap;

use crate::error::{Error, ErrorKind, Result};

const WHITESPACE: &[u8] = b" \t\n\r";

fn span_error(message: impl Into<String>) -> Error {
    Error::new(ErrorKind::JsonSpan, message)
}

/// Every top-level member as `{name: span}`, in document order.
///
/// The span is a slice of `source`, so it costs nothing to produce and is
/// byte-for-byte what arrived.
///
/// # Errors
///
/// `JsonSpan` when `source` is not a well-formed JSON object, with the message
/// Python would have produced.
pub fn member_spans(source: &str) -> Result<IndexMap<String, &str>> {
    let mut out = IndexMap::new();
    Scanner::new(source).scan(|name, span| {
        // Last occurrence wins, matching `json.loads`. `insert` on an existing
        // key replaces the value and keeps the original position, which is what
        // Python's dict does too.
        out.insert(name, span);
    })?;
    Ok(out)
}

/// Whether `source` is one complete JSON document, by `json.loads`' rules.
///
/// Stands in for `JsonEngine.parse_any` where a caller needs the verdict and
/// not the value -- to tell "this is not JSON at all" from "this is JSON, but
/// not the shape I need", which are different things to say to an operator.
///
/// It has to be *these* rules rather than `serde_json::from_str(..).is_ok()`,
/// for two reasons that both reach real values:
///
/// * `NaN`, `Infinity` and `-Infinity` are accepted here and by Python, and
///   refused by `serde_json`. A stored body containing one exists today.
/// * Trailing content after the value is refused here and by Python
///   (`Extra data`), so `[1] x` is not a document either way.
///
/// Leading and trailing whitespace are allowed, exactly as `json.loads`
/// allows them.
#[must_use]
pub fn is_json_document(source: &str) -> bool {
    let mut scanner = Scanner::new(source);
    scanner.skip_whitespace();
    if scanner.scan_any().is_err() {
        return false;
    }
    scanner.skip_whitespace();
    scanner.index == source.len()
}

/// The exact source text of one member's value, or `None` when absent.
///
/// # Errors
///
/// `JsonSpan` when `source` is not a well-formed JSON object.
pub fn member_text<'a>(source: &'a str, key: &str) -> Result<Option<&'a str>> {
    let mut found = None;
    Scanner::new(source).scan(|name, span| {
        if name == key {
            found = Some(span);
        }
    })?;
    Ok(found)
}

struct Scanner<'a> {
    source: &'a str,
    bytes: &'a [u8],
    index: usize,
}

impl<'a> Scanner<'a> {
    fn new(source: &'a str) -> Self {
        Self {
            source,
            bytes: source.as_bytes(),
            index: 0,
        }
    }

    /// Walk the object, handing each `(name, span)` to `sink`.
    fn scan(&mut self, mut sink: impl FnMut(String, &'a str)) -> Result<()> {
        self.skip_whitespace();
        self.expect(b'{')?;
        self.index += 1;
        self.skip_whitespace();

        if self.peek() == Some(b'}') {
            return Ok(());
        }

        loop {
            self.expect(b'"')?;
            let name = self.scan_member_name()?;

            self.skip_whitespace();
            self.expect(b':')?;
            self.index += 1;
            self.skip_whitespace();

            let start = self.index;
            self.scan_value(&name, start)?;
            sink(name, &self.source[start..self.index]);

            self.skip_whitespace();
            match self.peek() {
                None => return Err(span_error("object ended without '}'")),
                Some(b',') => {
                    self.index += 1;
                    self.skip_whitespace();
                }
                Some(b'}') => return Ok(()),
                Some(found) => {
                    return Err(span_error(format!(
                        "expected ',' or '}}' at offset {}, found {}",
                        self.char_offset(self.index),
                        quoted(found),
                    )));
                }
            }
        }
    }

    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.index).copied()
    }

    fn skip_whitespace(&mut self) {
        while self.peek().is_some_and(|b| WHITESPACE.contains(&b)) {
            self.index += 1;
        }
    }

    fn expect(&self, expected: u8) -> Result<()> {
        match self.peek() {
            None => Err(span_error(format!(
                "expected {} but the text ended",
                quoted(expected),
            ))),
            Some(found) if found == expected => Ok(()),
            Some(found) => Err(span_error(format!(
                "expected {} at offset {}, found {}",
                quoted(expected),
                self.char_offset(self.index),
                quoted(found),
            ))),
        }
    }

    /// Consume a quoted member name, leaving `index` just past the closing quote.
    fn scan_member_name(&mut self) -> Result<String> {
        let open = self.index;
        match self.scan_string_body(self.index + 1) {
            Ok((name, end)) => {
                self.index = end;
                Ok(name)
            }
            Err(detail) => Err(span_error(format!(
                // The quote's own position: Python's `index` is unchanged when
                // `scanstring` raises, so it still points at the quote.
                "bad member name at offset {}: {}",
                self.char_offset(open),
                detail.render(self.source),
            ))),
        }
    }

    /// Consume one value, leaving `index` just past it.
    fn scan_value(&mut self, name: &str, start: usize) -> Result<()> {
        self.scan_any().map_err(|detail| {
            span_error(format!(
                "bad value for {} at offset {}: {}",
                python_repr_key(name),
                self.char_offset(start),
                detail.render(self.source),
            ))
        })
    }

    fn scan_any(&mut self) -> std::result::Result<(), Detail> {
        match self.peek() {
            Some(b'"') => {
                let (_, end) = self.scan_string_body(self.index + 1)?;
                self.index = end;
                Ok(())
            }
            Some(b'{') => self.scan_object(),
            Some(b'[') => self.scan_array(),
            Some(b't') => self.scan_literal("true"),
            Some(b'f') => self.scan_literal("false"),
            Some(b'n') => self.scan_literal("null"),
            // Python's json accepts these three; they are not valid JSON.
            Some(b'N') => self.scan_literal("NaN"),
            Some(b'I') => self.scan_literal("Infinity"),
            Some(b'-') if self.bytes.get(self.index + 1) == Some(&b'I') => {
                // Python matches `-Infinity` as one token, so a partial match
                // reports the `-` rather than wherever the comparison stopped.
                let start = self.index;
                self.index += 1;
                self.scan_literal("Infinity")
                    .map_err(|_| Detail::ExpectingValue(start))
            }
            Some(b) if b == b'-' || b.is_ascii_digit() => self.scan_number(),
            _ => Err(Detail::ExpectingValue(self.index)),
        }
    }

    fn scan_object(&mut self) -> std::result::Result<(), Detail> {
        self.index += 1;
        self.skip_whitespace();
        if self.peek() == Some(b'}') {
            self.index += 1;
            return Ok(());
        }
        loop {
            if self.peek() != Some(b'"') {
                return Err(Detail::ExpectingPropertyName(self.index));
            }
            let (_, end) = self.scan_string_body(self.index + 1)?;
            self.index = end;
            self.skip_whitespace();
            if self.peek() != Some(b':') {
                return Err(Detail::ExpectingColon(self.index));
            }
            self.index += 1;
            self.skip_whitespace();
            self.scan_any()?;
            self.skip_whitespace();
            match self.peek() {
                Some(b',') => {
                    self.index += 1;
                    self.skip_whitespace();
                }
                Some(b'}') => {
                    self.index += 1;
                    return Ok(());
                }
                _ => return Err(Detail::ExpectingComma(self.index)),
            }
        }
    }

    fn scan_array(&mut self) -> std::result::Result<(), Detail> {
        self.index += 1;
        self.skip_whitespace();
        if self.peek() == Some(b']') {
            self.index += 1;
            return Ok(());
        }
        loop {
            self.scan_any()?;
            self.skip_whitespace();
            match self.peek() {
                Some(b',') => {
                    self.index += 1;
                    self.skip_whitespace();
                }
                Some(b']') => {
                    self.index += 1;
                    return Ok(());
                }
                _ => return Err(Detail::ExpectingComma(self.index)),
            }
        }
    }

    fn scan_literal(&mut self, literal: &str) -> std::result::Result<(), Detail> {
        if self.source[self.index..].starts_with(literal) {
            self.index += literal.len();
            Ok(())
        } else {
            Err(Detail::ExpectingValue(self.index))
        }
    }

    /// A JSON number, stopping where Python's would.
    ///
    /// Python's scanner is a regex that matches the longest valid number and no
    /// further, so `01` yields `0` and leaves `1` for the caller to trip over.
    /// Reproduced, because that is how `{"a": 01}` becomes
    /// `expected ',' or '}' ... found '1'` rather than a number error.
    fn scan_number(&mut self) -> std::result::Result<(), Detail> {
        let start = self.index;
        if self.peek() == Some(b'-') {
            self.index += 1;
        }
        // Integer part: a lone 0, or a non-zero digit run.
        match self.peek() {
            Some(b'0') => self.index += 1,
            Some(b) if b.is_ascii_digit() => {
                while self.peek().is_some_and(|b| b.is_ascii_digit()) {
                    self.index += 1;
                }
            }
            _ => return Err(Detail::ExpectingValue(start)),
        }
        // Fraction.
        if self.peek() == Some(b'.')
            && self
                .bytes
                .get(self.index + 1)
                .is_some_and(u8::is_ascii_digit)
        {
            self.index += 1;
            while self.peek().is_some_and(|b| b.is_ascii_digit()) {
                self.index += 1;
            }
        }
        // Exponent.
        if matches!(self.peek(), Some(b'e' | b'E')) {
            let mark = self.index;
            self.index += 1;
            if matches!(self.peek(), Some(b'+' | b'-')) {
                self.index += 1;
            }
            if self.peek().is_some_and(|b| b.is_ascii_digit()) {
                while self.peek().is_some_and(|b| b.is_ascii_digit()) {
                    self.index += 1;
                }
            } else {
                self.index = mark;
            }
        }
        Ok(())
    }

    /// Scan a string body starting just past the opening quote.
    ///
    /// Returns the decoded name and the index just past the closing quote. Only
    /// the escapes Python's `scanstring` rejects are rejected here; the decoded
    /// value is needed because it is the member's name.
    fn scan_string_body(&self, mut index: usize) -> std::result::Result<(String, usize), Detail> {
        let open = index.saturating_sub(1);
        let mut out = String::new();
        loop {
            let Some(byte) = self.bytes.get(index).copied() else {
                return Err(Detail::UnterminatedString(open));
            };
            match byte {
                b'"' => return Ok((out, index + 1)),
                b'\\' => {
                    let Some(escape) = self.bytes.get(index + 1).copied() else {
                        return Err(Detail::UnterminatedString(open));
                    };
                    let decoded = match escape {
                        b'"' => '"',
                        b'\\' => '\\',
                        b'/' => '/',
                        b'b' => '\u{8}',
                        b'f' => '\u{c}',
                        b'n' => '\n',
                        b'r' => '\r',
                        b't' => '\t',
                        b'u' => {
                            // Python distinguishes this from a bad single-
                            // character escape, and points at the `u` rather
                            // than the backslash.
                            let hex = self
                                .source
                                .get(index + 2..index + 6)
                                .ok_or(Detail::InvalidUnicodeEscape(index + 1))?;
                            let code = u32::from_str_radix(hex, 16)
                                .map_err(|_| Detail::InvalidUnicodeEscape(index + 1))?;
                            index += 6;
                            out.push(char::from_u32(code).unwrap_or('\u{fffd}'));
                            continue;
                        }
                        _ => return Err(Detail::InvalidEscape(index)),
                    };
                    out.push(decoded);
                    index += 2;
                }
                // Python rejects a raw control character inside a string.
                b if b < 0x20 => return Err(Detail::InvalidControl(index)),
                _ => {
                    let ch = self.source[index..]
                        .chars()
                        .next()
                        .ok_or(Detail::UnterminatedString(open))?;
                    out.push(ch);
                    index += ch.len_utf8();
                }
            }
        }
    }

    /// Python indexes `str`, so every reported offset is a character index.
    fn char_offset(&self, byte_index: usize) -> usize {
        self.source
            .get(..byte_index)
            .map_or(byte_index, |head| head.chars().count())
    }
}

/// A failure from inside a value, carrying the position it happened at.
///
/// Rendered into the `line L column C (char N)` suffix Python's `json.decoder`
/// appends, which reaches the HTTP 400 body verbatim.
#[derive(Debug, Clone, Copy)]
enum Detail {
    ExpectingValue(usize),
    ExpectingColon(usize),
    ExpectingComma(usize),
    ExpectingPropertyName(usize),
    UnterminatedString(usize),
    InvalidEscape(usize),
    InvalidUnicodeEscape(usize),
    InvalidControl(usize),
}

impl Detail {
    fn render(self, source: &str) -> String {
        let (message, byte_index) = match self {
            Self::ExpectingValue(i) => ("Expecting value", i),
            Self::ExpectingColon(i) => ("Expecting ':' delimiter", i),
            Self::ExpectingComma(i) => ("Expecting ',' delimiter", i),
            Self::ExpectingPropertyName(i) => {
                ("Expecting property name enclosed in double quotes", i)
            }
            Self::UnterminatedString(i) => ("Unterminated string starting at", i),
            Self::InvalidEscape(i) => (r"Invalid \escape", i),
            Self::InvalidUnicodeEscape(i) => (r"Invalid \uXXXX escape", i),
            Self::InvalidControl(i) => ("Invalid control character at", i),
        };
        let (line, column, char_index) = position(source, byte_index);
        format!("{message}: line {line} column {column} (char {char_index})")
    }
}

/// Python's `(line, column, char)` triple: lines and columns are 1-based, the
/// character index is 0-based, and all three count characters rather than bytes.
fn position(source: &str, byte_index: usize) -> (usize, usize, usize) {
    let head = source.get(..byte_index).unwrap_or(source);
    let char_index = head.chars().count();
    let line = head.matches('\n').count() + 1;
    let column = head
        .rsplit_once('\n')
        .map_or(char_index, |(_, last)| last.chars().count())
        + 1;
    (line, column, char_index)
}

/// Render a byte the way Python's `repr` of a one-character string would.
///
/// Delegates to the shared `python_repr`, because the escaping is not just
/// quote selection: a backslash reports as `'\\'` and a tab as `'\t'`, and the
/// fuzz corpus reaches both.
fn quoted(byte: u8) -> String {
    crate::error::python_repr(&(byte as char).to_string())
}

/// Render a member name as Python's `{name!r}` would.
fn python_repr_key(name: &str) -> String {
    crate::error::python_repr(name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slices_a_member_verbatim() {
        // The whole point: spelling survives, where a parse would normalise it.
        let source = r#"{"type": "node", "data": {"x": 1e3, "s": "café"}}"#;
        let spans = member_spans(source).expect("scans");
        assert_eq!(spans["type"], r#""node""#);
        assert_eq!(spans["data"], r#"{"x": 1e3, "s": "café"}"#);
    }

    #[test]
    fn the_last_duplicate_wins_like_json_loads() {
        let spans = member_spans(r#"{"a": 1, "a": 2}"#).expect("scans");
        assert_eq!(spans["a"], "2");
    }

    #[test]
    fn python_only_literals_are_accepted() {
        // Not valid JSON, but Python's `json` takes them, so a body carrying
        // one is stored today and must still be.
        for source in [r#"{"a": NaN}"#, r#"{"a": Infinity}"#, r#"{"a": -Infinity}"#] {
            assert!(member_spans(source).is_ok(), "{source}");
        }
        assert_eq!(
            member_spans(r#"{"a": -Infinity}"#).unwrap()["a"],
            "-Infinity"
        );
    }

    #[test]
    fn a_leading_zero_stops_the_number_and_trips_the_outer_scan() {
        // Python's number scanner matches the longest valid number and no
        // further, so `01` yields `0` and the `1` becomes the outer error.
        let error = member_spans(r#"{"a": 01}"#).expect_err("rejects");
        assert_eq!(
            error.message(),
            "expected ',' or '}' at offset 7, found '1'"
        );
    }

    #[test]
    fn offsets_count_characters_not_bytes() {
        // `é` is two bytes and one character. Python subscripts `str`, so the
        // reported offset is 12, not 13.
        let source = "{\"café\": 1 2}";
        let error = member_spans(source).expect_err("rejects");
        assert_eq!(
            error.message(),
            "expected ',' or '}' at offset 11, found '2'"
        );
    }

    #[test]
    fn nested_failures_carry_pythons_decoder_message() {
        for (source, expected) in [
            (
                r#"{"a": xyz}"#,
                "bad value for 'a' at offset 6: Expecting value: line 1 column 7 (char 6)",
            ),
            (
                r#"{"a": {"b" 1}}"#,
                "bad value for 'a' at offset 6: Expecting ':' delimiter: line 1 column 12 (char 11)",
            ),
            (
                r#"{"a": [1 2]}"#,
                "bad value for 'a' at offset 6: Expecting ',' delimiter: line 1 column 10 (char 9)",
            ),
            (
                r#"{"a": {b: 1}}"#,
                "bad value for 'a' at offset 6: Expecting property name enclosed in double quotes: line 1 column 8 (char 7)",
            ),
        ] {
            let error = member_spans(source).expect_err("rejects");
            assert_eq!(error.message(), expected, "for {source}");
        }
    }

    #[test]
    fn structural_failures_match_python() {
        for (source, expected) in [
            ("[1,2]", "expected '{' at offset 0, found '['"),
            ("", "expected '{' but the text ended"),
            (r#"{"a": 1"#, "object ended without '}'"),
            (r#"{"a" 1}"#, "expected ':' at offset 5, found '1'"),
            ("{a: 1}", r#"expected '"' at offset 1, found 'a'"#),
            (r#"{"a": 1,}"#, r#"expected '"' at offset 8, found '}'"#),
            (
                r#"{"a": 1 2}"#,
                "expected ',' or '}' at offset 8, found '2'",
            ),
        ] {
            let error = member_spans(source).expect_err("rejects");
            assert_eq!(error.message(), expected, "for {source}");
        }
    }

    #[test]
    fn an_empty_object_has_no_members() {
        assert!(member_spans("{}").expect("scans").is_empty());
        assert!(member_spans("  {  }  ").expect("scans").is_empty());
    }

    #[test]
    fn member_text_finds_one_member() {
        let source = r#"{"type": "node", "data": {"x": 1}}"#;
        assert_eq!(member_text(source, "data").unwrap(), Some(r#"{"x": 1}"#));
        assert_eq!(member_text(source, "absent").unwrap(), None);
    }

    #[test]
    fn a_nested_data_key_is_not_mistaken_for_the_top_level_one() {
        // `raw_decode` consumes each value whole, which is what makes a "data"
        // inside another member invisible here.
        let source = r#"{"a": {"data": 1}, "data": 2}"#;
        assert_eq!(member_text(source, "data").unwrap(), Some("2"));
    }

    #[test]
    fn a_document_is_one_complete_value_by_json_loads_rules() {
        for source in [
            "{}",
            r#"{"a": 1}"#,
            "[1, 2]",
            "7",
            "-7.5e3",
            r#""text""#,
            "null",
            "true",
            "  [1]  ",
            "\n\t{}\r\n",
        ] {
            assert!(is_json_document(source), "{source:?} should be a document");
        }
    }

    #[test]
    fn pythons_three_non_json_literals_are_documents_here() {
        // The whole reason this exists rather than a `serde_json` round trip.
        // `json.loads` accepts all three, nested as well as bare, and a stored
        // body containing one exists today.
        for source in [
            "NaN",
            "Infinity",
            "-Infinity",
            "[NaN]",
            "[1, Infinity]",
            r#"[[{"a": NaN}]]"#,
            r#"{"n": -Infinity}"#,
        ] {
            assert!(is_json_document(source), "{source:?} should be a document");
            assert!(
                serde_json::from_str::<serde_json::Value>(source).is_err(),
                "{source:?} is the case serde_json disagrees about, and it did not",
            );
        }
    }

    #[test]
    fn trailing_content_is_extra_data_and_not_a_document() {
        // `json.loads` raises `Extra data`. Nothing may follow the value but
        // whitespace.
        for source in ["[1] x", "7 7", "{} {}", r#""a" "b""#, "nullx"] {
            assert!(
                !is_json_document(source),
                "{source:?} should not be a document",
            );
        }
    }

    #[test]
    fn nothing_at_all_is_not_a_document() {
        for source in ["", "   ", "\n", "{", "[1,", "tru"] {
            assert!(
                !is_json_document(source),
                "{source:?} should not be a document",
            );
        }
    }
}
