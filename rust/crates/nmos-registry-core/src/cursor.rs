// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! TAI instants, and the paging cursors built from them.
//!
//! # Why cursors are registry-owned
//!
//! `APIs - Query Parameters.md:15-17` says the registry SHOULD maintain
//! `creation` and `update` timestamps alongside each resource, that they SHOULD
//! NOT appear in the response body, and that there SHOULD NOT be duplicates
//! within a type -- so that paging cannot skip a record.
//!
//! They are therefore allocated by the store and are deliberately **not** the
//! resource's own `version` attribute, which is Node-controlled, may repeat and
//! may even go backwards. Paging on `version` is one of the reasons the AMWA
//! mock registry's paging does not match the specification.

use std::cmp::Ordering;
use std::fmt;
use std::time::{SystemTime, UNIX_EPOCH};

/// TAI minus UTC, in seconds, as of 1 January 2017.
///
/// Hard-coded, with no leap-second table, exactly as `nmos/json/types.py`
/// records it. A table would be more correct in principle and would need
/// maintaining in two languages to stay identical in practice, which is a
/// worse trade for a registry whose cursors only ever have to agree with
/// themselves and with the Python implementation.
pub const TAI_UTC_OFFSET: i64 = 37;

/// A `<seconds>:<nanoseconds>` TAI instant, used as a paging cursor.
///
/// Ordering is lexicographic on `(seconds, nanoseconds)`, which is what makes a
/// `BTreeSet<(TaiCursor, ResourceId)>` a correct paging index.
///
/// `Copy` and 16 bytes, because this is compared on every insert and on every
/// page query. The Python type is frozen for the same reason expressed
/// differently: a cursor is a recorded instant, and re-stamping a resource
/// allocates a new one rather than mutating the old.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub struct TaiCursor {
    /// Whole TAI seconds.
    pub seconds: u64,
    /// Nanoseconds within the second, always `< 1_000_000_000`.
    pub nanoseconds: u32,
}

impl TaiCursor {
    /// The `0:0` cursor.
    ///
    /// A real protocol value rather than a sentinel:
    /// `APIs - Query Parameters.md:100` defines the `first` paging link as the
    /// query with `paging.since=0:0`.
    pub const MIN: Self = Self {
        seconds: 0,
        nanoseconds: 0,
    };

    /// The largest representable cursor.
    ///
    /// Not a protocol value. It exists so a range query over the paging index
    /// can be written as `(since, ID_MIN) ..= (until, ID_MAX)` without a
    /// special case for the open end.
    pub const MAX: Self = Self {
        seconds: u64::MAX,
        nanoseconds: 999_999_999,
    };

    /// Build from parts, normalising a nanosecond overflow into the seconds.
    #[must_use]
    pub const fn new(seconds: u64, nanoseconds: u32) -> Self {
        if nanoseconds >= 1_000_000_000 {
            Self {
                seconds: seconds.saturating_add((nanoseconds / 1_000_000_000) as u64),
                nanoseconds: nanoseconds % 1_000_000_000,
            }
        } else {
            Self {
                seconds,
                nanoseconds,
            }
        }
    }

    /// The current wall clock, converted to TAI.
    ///
    /// # Panics
    ///
    /// Never: a clock before the Unix epoch yields [`Self::MIN`] rather than
    /// unwrapping. This crate denies `unwrap_used` and a registry that refused
    /// to start because of a misconfigured clock would be a worse failure than
    /// one whose first cursor is zero.
    #[must_use]
    pub fn now() -> Self {
        let since_epoch = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default();
        let seconds = since_epoch
            .as_secs()
            .saturating_add(TAI_UTC_OFFSET.unsigned_abs());
        Self::new(seconds, since_epoch.subsec_nanos())
    }

    /// Parse `"<seconds>:<nanoseconds>"`.
    ///
    /// Returns `None` on anything that does not match the RAML pattern
    /// `^[0-9]+:[0-9]+$` (`QueryAPI.raml:31,35`), so a caller can answer 400
    /// rather than propagate an error.
    ///
    /// The digit check is not redundant with parsing. Rust's `str::parse`
    /// accepts a leading `+`, and Python's `int()` additionally accepts
    /// surrounding whitespace and underscores -- none of which the pattern
    /// permits. Checking the characters first is what makes the two
    /// implementations agree on the rejections as well as the acceptances.
    #[must_use]
    pub fn parse(text: &str) -> Option<Self> {
        let (head, tail) = text.split_once(':')?;
        if head.is_empty() || tail.is_empty() {
            return None;
        }
        if !head.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        if !tail.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        Some(Self {
            seconds: head.parse().ok()?,
            nanoseconds: tail.parse().ok()?,
        })
    }

    /// The smallest cursor strictly greater than this one.
    ///
    /// The store's tie-break: when two resources of a type would otherwise
    /// land on the same instant, the second takes this, preserving the
    /// uniqueness `APIs - Query Parameters.md:17` asks for.
    #[must_use]
    pub const fn next(self) -> Self {
        if self.nanoseconds >= 999_999_999 {
            Self {
                seconds: self.seconds.saturating_add(1),
                nanoseconds: 0,
            }
        } else {
            Self {
                seconds: self.seconds,
                // `saturating_add` rather than `+`, although the branch above
                // has already established that this cannot overflow. The lint
                // that forbids bare arithmetic here is the one keeping the
                // registry's write path panic-free, and an exception granted
                // because the author checked is exactly the kind that outlives
                // the reasoning behind it.
                nanoseconds: self.nanoseconds.saturating_add(1),
            }
        }
    }
}

impl Ord for TaiCursor {
    fn cmp(&self, other: &Self) -> Ordering {
        self.seconds
            .cmp(&other.seconds)
            .then(self.nanoseconds.cmp(&other.nanoseconds))
    }
}

impl PartialOrd for TaiCursor {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl fmt::Display for TaiCursor {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.seconds, self.nanoseconds)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ordering_is_lexicographic_on_seconds_then_nanoseconds() {
        assert!(TaiCursor::new(1, 0) < TaiCursor::new(1, 1));
        assert!(TaiCursor::new(1, 999_999_999) < TaiCursor::new(2, 0));
        assert!(TaiCursor::MIN < TaiCursor::new(0, 1));
        assert!(TaiCursor::new(u64::MAX, 0) < TaiCursor::MAX);
    }

    #[test]
    fn display_is_the_wire_form() {
        assert_eq!(
            TaiCursor::new(1700000000, 123456789).to_string(),
            "1700000000:123456789"
        );
        assert_eq!(TaiCursor::MIN.to_string(), "0:0");
        // No zero padding: `0:1` is not `0:000000001`.
        assert_eq!(TaiCursor::new(0, 1).to_string(), "0:1");
    }

    #[test]
    fn parse_accepts_exactly_the_raml_pattern() {
        assert_eq!(TaiCursor::parse("0:0"), Some(TaiCursor::MIN));
        assert_eq!(
            TaiCursor::parse("1700000000:123456789"),
            Some(TaiCursor::new(1700000000, 123456789)),
        );
    }

    #[test]
    fn parse_rejects_what_the_pattern_does_not_allow() {
        // Each of these is accepted by a naive integer parse in one language or
        // the other, and by the pattern in neither.
        for bad in [
            "", ":", "1:", ":1", "1", "a:b", "+1:0", "1:+0", " 1:0", "1:0 ", "-1:0", "1:-0",
            "1_0:0", "1:0:0", "1.0:0",
        ] {
            assert_eq!(TaiCursor::parse(bad), None, "should reject {bad:?}");
        }
    }

    #[test]
    fn next_is_the_smallest_greater_cursor_and_carries() {
        let c = TaiCursor::new(5, 7);
        assert_eq!(c.next(), TaiCursor::new(5, 8));
        assert!(c < c.next());

        let edge = TaiCursor::new(5, 999_999_999);
        assert_eq!(edge.next(), TaiCursor::new(6, 0));
        assert!(edge < edge.next());
    }

    #[test]
    fn new_normalises_a_nanosecond_overflow() {
        assert_eq!(TaiCursor::new(1, 1_000_000_000), TaiCursor::new(2, 0));
        assert_eq!(
            TaiCursor::new(1, 2_500_000_000),
            TaiCursor::new(3, 500_000_000)
        );
    }

    #[test]
    fn now_is_ahead_of_utc_by_the_tai_offset() {
        // Not a clock test: it checks the offset is applied at all, which is
        // the thing that would silently differ from Python.
        let utc = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("after the epoch")
            .as_secs();
        let tai = TaiCursor::now().seconds;
        let delta = tai.saturating_sub(utc);
        assert!(
            (TAI_UTC_OFFSET.unsigned_abs()..=TAI_UTC_OFFSET.unsigned_abs() + 2).contains(&delta),
            "TAI should lead UTC by {TAI_UTC_OFFSET}s, saw {delta}",
        );
    }
}
