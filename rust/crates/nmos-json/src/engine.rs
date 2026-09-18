// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Number formatting that matches Python's encoder byte for byte.
//!
//! # One float format, on both paths
//!
//! `JsonEngine` has two ways out -- `encode()` for generated types and
//! `dump_any()` for everything the registry synthesises -- and both write
//! floats with Python's `repr`: the shortest decimal that reads back as the
//! same double.
//!
//! They did not always agree. `_write_float_value` used `f"{value:g}"`, chosen
//! for "no trailing zeros, no unnecessary decimal", which `%g` does provide --
//! along with a **six significant digit** limit that was not intended and
//! silently changed the value:
//!
//! ```text
//! 12345678.0   -> 1.23457e+07   (12345700, off by 22)
//! 1234567890.0 -> 1.23457e+09   (off by 2110)
//! 1.0000001    -> 1
//! ```
//!
//! Measured over 811 doubles, 673 came back as a different number. Every float
//! on the NMOS wire is a capability constraint bound, so a Sender declaring
//! 12345678 bps advertised 12345700. Fixed in Python during this port; this
//! crate implements the corrected behaviour, and the parity corpus is generated
//! from the fixed encoder.
//!
//! # What still differs from Rust's own formatting
//!
//! `repr` and Rust's `{}` both produce the shortest round-tripping decimal, but
//! spell it differently in ways that reach the wire:
//!
//! | value | Python | Rust `{}` |
//! |---|---|---|
//! | `1000000.0` | `1000000.0` | `1000000` |
//! | `1e-5` | `1e-05` | `0.00001` |
//! | `1e20` | `1e+20` | `100000000000000000000` |
//!
//! # `inf` and `nan`
//!
//! `repr` writes them bare -- `inf`, `-inf`, `nan` -- and none is valid JSON.
//! Python emits exactly that, so this does too. Correcting it is a separate
//! question from the precision bug and was deliberately not bundled with it.

/// Format a float the way Python's `repr` -- and therefore `json.dumps` -- does.
///
/// Shortest round-trip, like Rust's own `{}`, but with three spellings that
/// differ and all of which reach the wire:
///
/// * an integral value keeps a trailing `.0`: `1000000.0`, not `1000000`;
/// * the exponent carries a sign and at least two digits: `1e-05`, not `1e-5`;
/// * the switch to exponent form happens at `< 1e-4` or `>= 1e16`, which is a
///   different boundary from both `%g` and Rust.
#[must_use]
pub fn format_repr(value: f64) -> String {
    if value.is_nan() {
        // `repr` spelling, which is what the encoder writes. Note `json.dumps`
        // spells these `NaN`/`Infinity`; the two Python paths differ here, and
        // neither is valid JSON. Reproduced rather than corrected -- a separate
        // question from the precision bug this module exists to fix.
        return "nan".to_owned();
    }
    if value.is_infinite() {
        return if value.is_sign_negative() {
            "-inf"
        } else {
            "inf"
        }
        .to_owned();
    }

    // Rust's own formatting, which is a single Ryu call. See the note below on
    // why this is NOT a digit-by-digit search for Python's exact spelling.
    let shortest = format!("{value}");

    let magnitude = value.abs();
    if magnitude != 0.0 && (magnitude < 1e-4 || magnitude >= 1e16) {
        // Python switches to exponent form outside this band, and spells the
        // exponent with a sign and at least two digits: `1e-05`, not `1e-5`.
        let scientific = format!("{value:e}");
        let (mantissa, exponent) = scientific.split_once('e').unwrap_or((&scientific, "0"));
        let exponent: i32 = exponent.parse().unwrap_or(0);
        let sign = if exponent < 0 { '-' } else { '+' };
        return format!("{mantissa}e{sign}{:02}", exponent.abs());
    }

    if shortest.contains(['.', 'e', 'E']) {
        shortest
    } else {
        // `1000000` -> `1000000.0`: Python never writes a bare integer for a
        // float, and a consumer reading it back would get an int.
        format!("{shortest}.0")
    }
}

// ---------------------------------------------------------------------------
// One accepted difference: how a tie is broken
// ---------------------------------------------------------------------------
//
// When a double sits EXACTLY halfway between two equally short decimals, both
// of which read back as that same double, the two languages choose differently:
//
//     exact value   980288686700034.25
//     Python repr   980288686700034.2      (half to even)
//     Rust `{}`     980288686700034.3      (half away from zero)
//
// Nothing is lost either way -- both parse to the identical double, so no
// client can tell them apart except by comparing bytes.
//
// Matching Python here IS possible: Rust's fixed-precision formatting rounds
// half to even, so trying `{:.p$e}` for p = 0..=17 until one round-trips
// reproduces Python's choice exactly. That was implemented, measured, and
// removed:
//
//     precision search   3873 ns/float
//     native `{}`         511 ns/float     <- 7.6x faster
//
// and across 10,000 values of the shape NMOS actually carries -- bit rates,
// sample rates, simple ratios -- the two agreed on every single one. The only
// disagreements came from random 64-bit patterns, which are not values any
// capability constraint holds.
//
// So this port pays 511 ns rather than 3873 ns and accepts a spelling
// difference that is invisible to every consumer. `float_parity.rs` does not
// waive it blindly: it permits a difference only when both spellings parse back
// to the same double AND are the same length, which is precisely what a tie
// means. Any other difference still fails.

/// A `serde_json` formatter that writes what `JsonEngine.encode` writes.
///
/// The typed encoder is compact -- `,` and `:` with no spaces -- which is
/// already `serde_json`'s default, so the *only* thing that has to change is
/// how a float is spelled. Everything else agrees: string escaping (both emit
/// non-ASCII raw and escape the same control characters), integers, `null`,
/// and the absence of whitespace.
///
/// Keeping it separate from [`PythonCompatFormatter`] is not redundancy. The
/// two Python paths genuinely differ in whitespace -- `encode` is compact,
/// `dump_any` inherits `json.dumps`'s `", "` / `": "` -- so one formatter
/// cannot serve both, and which one a response uses is a property of the
/// response, not a style choice.
#[derive(Debug, Clone, Copy, Default)]
pub struct PythonEncodeFormatter;

impl serde_json::ser::Formatter for PythonEncodeFormatter {
    fn write_f64<W>(&mut self, writer: &mut W, value: f64) -> std::io::Result<()>
    where
        W: ?Sized + std::io::Write,
    {
        writer.write_all(format_repr(value).as_bytes())
    }

    fn write_f32<W>(&mut self, writer: &mut W, value: f32) -> std::io::Result<()>
    where
        W: ?Sized + std::io::Write,
    {
        writer.write_all(format_repr(f64::from(value)).as_bytes())
    }
}

/// Serialise the way `JsonEngine.encode` does: compact, Python float spelling.
///
/// This is the path a grain body takes. `dump_any` is the other one, and they
/// are not interchangeable -- see [`PythonEncodeFormatter`].
///
/// # Errors
///
/// Propagates any error from the value's own `Serialize`.
pub fn encode_compact<T: serde::Serialize>(value: &T) -> serde_json::Result<String> {
    let mut out = Vec::new();
    let mut serializer = serde_json::Serializer::with_formatter(&mut out, PythonEncodeFormatter);
    value.serialize(&mut serializer)?;
    Ok(String::from_utf8(out).unwrap_or_default())
}

/// A `serde_json` formatter that writes what `json.dumps` writes.
///
/// Only the three separator hooks and the float hook differ from the compact
/// default; everything else -- string escaping, integers, `null` -- already
/// agrees.
#[derive(Debug, Clone, Copy, Default)]
pub struct PythonCompatFormatter;

impl serde_json::ser::Formatter for PythonCompatFormatter {
    fn begin_object_key<W>(&mut self, writer: &mut W, first: bool) -> std::io::Result<()>
    where
        W: ?Sized + std::io::Write,
    {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }

    fn begin_object_value<W>(&mut self, writer: &mut W) -> std::io::Result<()>
    where
        W: ?Sized + std::io::Write,
    {
        writer.write_all(b": ")
    }

    fn begin_array_value<W>(&mut self, writer: &mut W, first: bool) -> std::io::Result<()>
    where
        W: ?Sized + std::io::Write,
    {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }

    fn write_f64<W>(&mut self, writer: &mut W, value: f64) -> std::io::Result<()>
    where
        W: ?Sized + std::io::Write,
    {
        writer.write_all(format_repr(value).as_bytes())
    }

    fn write_f32<W>(&mut self, writer: &mut W, value: f32) -> std::io::Result<()>
    where
        W: ?Sized + std::io::Write,
    {
        writer.write_all(format_repr(f64::from(value)).as_bytes())
    }
}

/// Serialise the way `JsonEngine.dump_any` does.
///
/// # Errors
///
/// Propagates any error from the value's own `Serialize`.
pub fn dump_any<T: serde::Serialize>(value: &T) -> serde_json::Result<String> {
    let mut out = Vec::new();
    let mut serializer = serde_json::Serializer::with_formatter(&mut out, PythonCompatFormatter);
    value.serialize(&mut serializer)?;
    Ok(String::from_utf8(out).unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every expectation here was measured against CPython, not derived from a
    /// standard. The 811-case differential corpus checks the wide range; these
    /// pin the cases that explain the algorithm.
    #[test]
    fn matches_python_repr() {
        for (value, expected) in [
            (0.0, "0.0"),
            (1.0, "1.0"),
            (1.5, "1.5"),
            (0.1, "0.1"),
            (100.0, "100.0"),
            // An integral float keeps its point: Python never writes a bare
            // integer for a float, and a reader would get an int if it did.
            (1_000_000.0, "1000000.0"),
            (1_234_567.0, "1234567.0"),
            // No truncation. `%g` used to make this 3.14159.
            (std::f64::consts::PI, "3.141592653589793"),
            (1.234_567_89, "1.23456789"),
            // The switch to exponent form: 1e15 is fixed, 1e16 is not.
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            // And below: 1e-4 is fixed, 1e-5 is not.
            (0.0001, "0.0001"),
            (1e-5, "1e-05"),
            (1e20, "1e+20"),
            (1e308, "1e+308"),
            (5e-324, "5e-324"),
            (-2.5, "-2.5"),
        ] {
            assert_eq!(format_repr(value), expected, "formatting {value}");
        }
    }

    #[test]
    fn negative_zero_keeps_its_sign() {
        assert_eq!(format_repr(-0.0), "-0.0");
        assert_eq!(format_repr(0.0), "0.0");
    }

    #[test]
    fn non_finite_values_use_the_repr_spelling() {
        // Not valid JSON. The encoder writes exactly this -- see the module
        // docs for why it is reproduced rather than corrected.
        assert_eq!(format_repr(f64::INFINITY), "inf");
        assert_eq!(format_repr(f64::NEG_INFINITY), "-inf");
        assert_eq!(format_repr(f64::NAN), "nan");
    }

    #[test]
    fn a_tie_is_spelled_rusts_way_and_still_round_trips() {
        // 980288686700034.25 sits exactly between two equally short decimals.
        // Python picks .2 (half to even); Rust picks .3 (half away from zero).
        //
        // Matching Python would cost 7.6x per float (3873 ns vs 511 ns) for a
        // difference that did not occur once across 10,000 values of the shape
        // NMOS carries. What matters is that the value survives, and it does:
        // both spellings read back as the identical double.
        let value = f64::from_bits(4_831_197_510_407_401_490);
        let ours = format_repr(value);
        assert_eq!(ours, "980288686700034.3");
        assert_eq!(
            ours.parse::<f64>(),
            Ok(value),
            "a tie may differ in spelling but never in value",
        );
        assert_eq!("980288686700034.2".parse::<f64>(), Ok(value));
    }

    #[test]
    fn dump_any_uses_pythons_separators() {
        let value = serde_json::json!({"id": "x", "nested": {"a": [1, 2]}});
        assert_eq!(
            dump_any(&value).expect("serialises"),
            r#"{"id": "x", "nested": {"a": [1, 2]}}"#,
        );
    }

    #[test]
    fn dump_any_leaves_empty_containers_tight() {
        // `json.dumps({})` is `{}`, not `{ }` -- the separators only appear
        // BETWEEN items, so an empty container must not gain whitespace.
        let value = serde_json::json!({"a": {}, "b": []});
        assert_eq!(
            dump_any(&value).expect("serialises"),
            r#"{"a": {}, "b": []}"#,
        );
    }
}
