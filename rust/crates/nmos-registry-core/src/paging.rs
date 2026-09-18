// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Query API pagination.
//!
//! Implements the `paged` trait of `QueryAPI.raml:25-63` and the worked
//! examples of `APIs - Query Parameters.md:34-369`. **The examples are the real
//! specification here** -- the prose leaves several boundary cases implicit and
//! only the nine worked cases pin them down -- so this reproduces all nine, and
//! `tests/paging.rs` asserts each one.
//!
//! # The model
//!
//! A page is a window over the collection ordered by a registry-assigned cursor
//! (creation or update time, selected by `paging.order`). Two rules fix
//! everything else:
//!
//! * `paging.since` is **non-inclusive** and `paging.until` is **inclusive**
//!   (`QueryAPI.raml:29,33`), so the window is `(since, until]`;
//! * the payload is always returned **most recent first**
//!   (`APIs - Query Parameters.md:90`, `QueryAPI.raml:40`).
//!
//! Which end of the window the page is taken from depends on which cursor the
//! client supplied, and that asymmetry is the part worth being explicit about:
//!
//! * **`since` given** -- page forwards from the bottom. The client is walking
//!   towards newer records, so the page is the *oldest* `limit` records above
//!   `since`, and `X-Paging-Until` reports where it got to.
//! * **`since` absent** -- page backwards from the top. The client is looking
//!   at the newest records, so the page is the *newest* `limit` records at or
//!   below `until`, and `X-Paging-Since` reports how far down it reached.
//!
//! That is why `since` "takes precedence where a resulting data set is
//! constrained by the server's value of limit" (`:30`, Example 5): with both
//! cursors supplied and more than `limit` records between them, the page is
//! anchored to `since` and the far end is reported back narrowed.
//!
//! # Cursor reporting is not the min and max of what was returned
//!
//! `X-Paging-Until` with no cursors supplied is the newest cursor in the
//! **unfiltered** collection, not of the filtered page. Edge Cases 3 and 4 both
//! return a page whose newest record is older than the reported `until`, or no
//! records at all, and still report the collection's own maximum. That is what
//! makes the `next` cursor usable as a "watch for changes from now on"
//! bookmark even when the current filter matches nothing.

use std::fmt;

use crate::cursor::TaiCursor;
use crate::resource::{Order, RegisteredResource};

/// The `paging.*` query parameters this module owns. Everything else on the
/// query string is a basic-query filter.
pub const PARAM_SINCE: &str = "paging.since";
/// The inclusive upper bound parameter.
pub const PARAM_UNTIL: &str = "paging.until";
/// The page-size parameter.
pub const PARAM_LIMIT: &str = "paging.limit";
/// The ordering parameter.
pub const PARAM_ORDER: &str = "paging.order";

/// Whether a query-string key is one this module owns.
#[must_use]
pub fn is_paging_param(name: &str) -> bool {
    matches!(name, PARAM_SINCE | PARAM_UNTIL | PARAM_LIMIT | PARAM_ORDER)
}

/// A paging parameter was malformed. The caller answers 400.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PagingError {
    /// The message for the response body.
    pub detail: String,
}

impl fmt::Display for PagingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.detail)
    }
}

impl std::error::Error for PagingError {}

impl PagingError {
    fn new(detail: impl Into<String>) -> Self {
        Self {
            detail: detail.into(),
        }
    }
}

/// The paging parameters of one request, parsed and bounds-checked.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PagingRequest {
    /// The exclusive lower bound, if the client supplied one.
    pub since: Option<TaiCursor>,
    /// The inclusive upper bound, if the client supplied one.
    pub until: Option<TaiCursor>,
    /// The effective page size, already clamped to the server's ceiling.
    pub limit: usize,
    /// Which cursor to page on.
    pub order: Order,
}

impl PagingRequest {
    /// The cursor this request pages on, for one resource.
    #[must_use]
    pub fn cursor_of(&self, resource: &RegisteredResource) -> TaiCursor {
        match self.order {
            Order::Created => resource.created,
            Order::Updated => resource.updated,
        }
    }
}

/// A rendered page: the records plus the cursors describing the window.
///
/// Deliberately not `PartialEq`: a resource carries an atomic health and is an
/// identity rather than a value, so comparing two pages structurally would be
/// asking a question with no good answer. Tests compare the ids and the
/// cursors, which is what the protocol actually specifies.
#[derive(Debug, Clone)]
pub struct Page<'a> {
    /// The records, **most recent first**.
    pub resources: Vec<&'a RegisteredResource>,
    /// What to report in `X-Paging-Since`.
    pub since: TaiCursor,
    /// What to report in `X-Paging-Until`.
    pub until: TaiCursor,
    /// What to report in `X-Paging-Limit`.
    pub limit: usize,
}

impl Page<'_> {
    /// The window this page describes, without the records.
    #[must_use]
    pub const fn window(&self) -> PageWindow {
        PageWindow {
            since: self.since,
            until: self.until,
            limit: self.limit,
        }
    }
}

/// The three numbers the `X-Paging-*` headers and the `Link` URLs are built
/// from.
///
/// Split out from [`Page`] because the headers need **only** these: the caller
/// that serves a response has already copied the bodies out from under the read
/// lock and no longer holds the borrowed records. Threading a `Page` through
/// would tie the response to the store's lifetime for no reason.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PageWindow {
    /// What to report in `X-Paging-Since`.
    pub since: TaiCursor,
    /// What to report in `X-Paging-Until`.
    pub until: TaiCursor,
    /// What to report in `X-Paging-Limit`.
    pub limit: usize,
}

/// Parse the four `paging.*` query parameters.
///
/// `max_limit` is a ceiling the server clamps to rather than refuses -- `:137`:
/// "If the client had requested a page size which the server was unable to
/// honour, the actual page size used would be returned in X-Paging-Limit".
///
/// # Errors
///
/// A cursor that does not match `^[0-9]+:[0-9]+$`, a limit that is not a
/// non-negative integer, an order that is not `create`/`update`, or an inverted
/// window.
pub fn parse_paging(
    lookup: impl Fn(&str) -> Option<String>,
    default_limit: usize,
    max_limit: usize,
) -> Result<PagingRequest, PagingError> {
    let since = cursor_param(&lookup, PARAM_SINCE)?;
    let until = cursor_param(&lookup, PARAM_UNTIL)?;

    let mut limit = default_limit;
    if let Some(raw) = lookup(PARAM_LIMIT) {
        // The RAML types this as an integer; a non-integer is malformed input
        // rather than something to silently round or ignore.
        let digits = raw.strip_prefix('-').unwrap_or(&raw);
        if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
            return Err(PagingError::new(format!(
                "{PARAM_LIMIT} must be an integer, got '{raw}'"
            )));
        }
        if raw.starts_with('-') {
            return Err(PagingError::new(format!(
                "{PARAM_LIMIT} must not be negative, got {raw}"
            )));
        }
        // Zero is a legitimate limit, not an error: it asks "give me the paging
        // cursors for this window but none of the records", which is how a
        // client probes a collection's bounds cheaply. AMWA IS-04-02
        // `test_21_4` exercises it directly and expects a 200 with an empty
        // body and `X-Paging-Limit: 0`. Only a negative limit is malformed.
        //
        // A value too large for `usize` is clamped rather than refused, which
        // is the same treatment any over-large limit gets.
        limit = raw.parse::<usize>().unwrap_or(max_limit).min(max_limit);
    }

    let order = match lookup(PARAM_ORDER) {
        None => Order::Updated,
        Some(raw) => Order::parse(&raw).ok_or_else(|| {
            PagingError::new(format!(
                "{PARAM_ORDER} must be one of [\"create\", \"update\"], got '{raw}'"
            ))
        })?,
    };

    // An inverted window describes no possible page: `since` is the exclusive
    // lower bound and `until` the inclusive upper one, so since > until asks
    // for records both newer and older than each other. Malformed, not empty.
    //
    // since == until is NOT an error -- it is the legitimate "has anything
    // arrived since this cursor?" poll, and must return an empty page with
    // paging headers so the client can keep re-issuing the same cursor
    // (`APIs - Query Parameters.md:313`).
    if let (Some(since), Some(until)) = (since, until)
        && since > until
    {
        return Err(PagingError::new(format!(
            "{PARAM_SINCE} ({since}) must not be later than {PARAM_UNTIL} ({until})"
        )));
    }

    Ok(PagingRequest {
        since,
        until,
        limit,
        order,
    })
}

fn cursor_param(
    lookup: &impl Fn(&str) -> Option<String>,
    name: &str,
) -> Result<Option<TaiCursor>, PagingError> {
    match lookup(name) {
        None => Ok(None),
        Some(raw) => TaiCursor::parse(&raw).map(Some).ok_or_else(|| {
            PagingError::new(format!(
                "{name} must match '<seconds>:<nanoseconds>', got '{raw}'"
            ))
        }),
    }
}

/// How many elements of an ascending slice have a cursor at or below `bound`.
///
/// `bisect_right` in Python's terms. The slice is ordered by `(cursor, id)`, so
/// records sharing a cursor are contiguous and a partition on the cursor alone
/// still lands on a clean boundary. Everything at or below the bound sits to
/// the left, which is exactly the at-or-below half of the window.
fn count_at_or_below(
    ascending: &[&RegisteredResource],
    request: &PagingRequest,
    bound: TaiCursor,
) -> usize {
    ascending.partition_point(|resource| request.cursor_of(resource) <= bound)
}

/// Select one page from a filtered collection.
///
/// `matched` is the resources that passed the basic-query filters -- `:26`
/// requires filtering before paging -- and `collection_max` is the newest
/// cursor in the **unfiltered** collection, needed only for the default upper
/// bound (see the module docs on Edge Cases 3 and 4).
///
/// # Both inputs must already be ascending
///
/// By `(cursor, id)`. `RegistryStore::iter_ordered` provides exactly that, and
/// filtering a sorted sequence preserves it, so nothing is sorted here.
///
/// Python keeps a `presorted=False` path that sorts, which was measured at
/// **82 ms per page at 20,000 senders** before the store maintained the order
/// incrementally. There is no reason to reintroduce that cost behind a default
/// argument, so the ordering is a precondition instead -- checked by a debug
/// assertion, which catches a caller that filtered into a `HashMap` and lost it.
#[must_use]
pub fn apply_paging<'a>(
    matched: &[&'a RegisteredResource],
    collection_max: Option<TaiCursor>,
    request: &PagingRequest,
) -> Page<'a> {
    debug_assert!(
        matched.windows(2).all(|pair| match pair {
            [a, b] => (request.cursor_of(a), &a.id) <= (request.cursor_of(b), &b.id),
            _ => true,
        }),
        "apply_paging was given an unsorted slice; the page would silently \
         skip or repeat records",
    );

    if let Some(since) = request.since {
        return page_forwards(matched, request, since);
    }
    page_backwards(matched, collection_max, request)
}

/// The oldest `limit` records strictly above `since`, bounded above by `until`.
fn page_forwards<'a>(
    ascending: &[&'a RegisteredResource],
    request: &PagingRequest,
    since: TaiCursor,
) -> Page<'a> {
    let start = count_at_or_below(ascending, request, since);
    let end = match request.until {
        Some(until) => count_at_or_below(ascending, request, until).max(start),
        None => ascending.len(),
    };

    // Sliced to the limit directly rather than materialising the window and
    // truncating it: the window can be the whole type, the page never exceeds
    // `limit`.
    let window_size = end.saturating_sub(start);
    let stop = end.min(start.saturating_add(request.limit));
    let page: Vec<&'a RegisteredResource> = ascending
        .get(start..stop)
        .map(<[&RegisteredResource]>::to_vec)
        .unwrap_or_default();

    // `since` is echoed exactly as requested. `until` reports the top of the
    // window that was actually served, and the distinction that matters is
    // whether the limit *truncated* the window:
    //
    // * Truncated -- narrow to the newest record served, so the client's `next`
    //   cursor resumes exactly where this page stopped. This is Example 5's
    //   "since takes precedence": `X-Paging-Until` comes back lower than asked.
    // * Not truncated -- the whole window was served, so the requested ceiling
    //   still describes it and must be echoed unchanged. Narrowing here would
    //   move the client's `next` cursor *backwards* to the last matching
    //   record, so a later record that did not match the filter would be
    //   replayed on the next page. A filter selecting discontiguous records
    //   makes that visible, which is what AMWA IS-04-02 `test_21_5` checks.
    //
    // With nothing served at all the window collapses onto the bounds
    // supplied: the requested ceiling if there was one, otherwise `since`
    // itself (Edge Case 2, where until == since makes the client re-issue the
    // identical cursor rather than skipping past records that have not arrived).
    let truncated = window_size > request.limit;
    let report_until = match (truncated, page.last(), request.until) {
        (true, Some(last), _) | (false, Some(last), None) => request.cursor_of(last),
        (_, _, Some(until)) => until,
        (_, None, None) => since,
    };

    let mut resources = page;
    resources.reverse();
    Page {
        resources,
        since,
        until: report_until,
        limit: request.limit,
    }
}

/// The newest `limit` records at or below `until`.
fn page_backwards<'a>(
    ascending: &[&'a RegisteredResource],
    collection_max: Option<TaiCursor>,
    request: &PagingRequest,
) -> Page<'a> {
    // Absent an explicit `until`, the ceiling is the newest cursor in the
    // UNFILTERED collection. See the module docs.
    let ceiling = request.until.or(collection_max).unwrap_or(TaiCursor::MIN);

    // The window is the prefix at or below the ceiling, so its length is the
    // boundary index and no element has to be visited at all.
    let window_size = count_at_or_below(ascending, request, ceiling);

    // A zero limit has to be handled before the slice: `window[-0:]` is the
    // whole list in Python, and `window_size - 0` would be the whole prefix
    // here, so neither falls out of the arithmetic.
    let page: Vec<&'a RegisteredResource> = if request.limit == 0 {
        Vec::new()
    } else {
        let start = window_size.saturating_sub(request.limit);
        ascending
            .get(start..window_size)
            .map(<[&RegisteredResource]>::to_vec)
            .unwrap_or_default()
    };

    // `since` is the exclusive lower bound that reproduces exactly this page:
    // the cursor of the record immediately below it. When the page did not
    // fill, the window reaches the bottom of the collection and the bound is
    // 0:0 (Edge Cases 1, 3 and 4).
    //
    // A zero limit served nothing, so there is no "record below the page" --
    // the window has no extent and both bounds sit on the ceiling.
    let report_since = if request.limit == 0 {
        ceiling
    } else if window_size > request.limit {
        window_size
            .checked_sub(request.limit)
            .and_then(|above| above.checked_sub(1))
            .and_then(|at| ascending.get(at))
            .map_or(TaiCursor::MIN, |resource| request.cursor_of(resource))
    } else {
        TaiCursor::MIN
    };

    let mut resources = page;
    resources.reverse();
    Page {
        resources,
        since: report_since,
        until: ceiling,
        limit: request.limit,
    }
}

/// Percent-encode one query-string component the way Python's
/// `quote(safe=":")` does.
///
/// # Why not a URL crate
///
/// `url::form_urlencoded` gets both of the things that matter here wrong for
/// this purpose: it percent-encodes `:` and it renders a space as `+`. Those
/// are precisely the two behaviours Python's `urlencode(safe=":",
/// quote_via=quote)` exists to avoid, so adopting the crate would mean
/// configuring almost all of it away.
///
/// **The colon is load-bearing.** A client comparing a `prev` link against the
/// `X-Paging-Since` header it was handed does a plain string match, so
/// `paging.until=1441716120:318744030` and
/// `paging.until=1441716120%3A318744030` are not interchangeable even though
/// they are the same URL. Every cursor in the specification's worked examples
/// is written with a literal colon (`APIs - Query Parameters.md:65`), and
/// RFC 3986 permits `:` in a query.
///
/// **The space is load-bearing too.** Edge Case 3 (`:330`) shows
/// `?label=My%20Node`, not `?label=My+Node`.
///
/// Everything else is encoded, so an `&` inside a filter value becomes `%26`
/// and a label of `foo&bar` round-trips instead of splitting the query.
#[must_use]
pub fn encode_component(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        // Python's `quote` never encodes letters, digits or `_.-~`; `safe=":"`
        // adds the colon.
        if byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-' | b'~' | b':') {
            out.push(char::from(byte));
        } else {
            out.push('%');
            out.push_str(&format!("{byte:02X}"));
        }
    }
    out
}

/// Join query pairs into an encoded query string.
#[must_use]
pub fn encode_query(pairs: &[(String, String)]) -> String {
    pairs
        .iter()
        .map(|(key, value)| format!("{}={}", encode_component(key), encode_component(value)))
        .collect::<Vec<_>>()
        .join("&")
}

/// The `X-Paging-*` and `Link` response headers.
///
/// `:13` makes `X-Paging-Limit` mandatory whenever paging is in use -- it is
/// how a client detects that the API pages at all -- so all three `X-Paging-*`
/// headers are always emitted.
///
/// `:32` says servers SHOULD return `prev` and `next` and MAY return `first`
/// and `last`; all four are provided. Their construction follows nmos-cpp:
/// `next` pages upward from the window's top cursor, `prev` downward from its
/// bottom, `first` is `paging.since=0:0` (`:100`), and `last` carries no paging
/// cursors at all, which is by definition the newest page (`:99`).
///
/// `filters` is the non-paging query parameters, preserved on every link --
/// Edge Case 3 shows `label=My%20Node` carried through.
#[must_use]
pub fn paging_headers(
    page: PageWindow,
    base_url: &str,
    filters: &[(String, String)],
    order: Order,
) -> Vec<(String, String)> {
    let build = |cursor_param: Option<(&str, TaiCursor)>| -> String {
        let mut query: Vec<(String, String)> = filters.to_vec();
        if let Some((name, cursor)) = cursor_param {
            query.push((name.to_owned(), cursor.to_string()));
        }
        query.push((PARAM_LIMIT.to_owned(), page.limit.to_string()));
        // Echoed only when it is not the default, so common URLs stay short.
        if order != Order::Updated {
            query.push((PARAM_ORDER.to_owned(), order.wire().to_owned()));
        }
        format!("<{base_url}?{}>", encode_query(&query))
    };

    let links = [
        format!("{}; rel=\"next\"", build(Some((PARAM_SINCE, page.until)))),
        format!("{}; rel=\"prev\"", build(Some((PARAM_UNTIL, page.since)))),
        format!(
            "{}; rel=\"first\"",
            build(Some((PARAM_SINCE, TaiCursor::MIN)))
        ),
        format!("{}; rel=\"last\"", build(None)),
    ];

    vec![
        ("Link".to_owned(), links.join(", ")),
        ("X-Paging-Limit".to_owned(), page.limit.to_string()),
        ("X-Paging-Since".to_owned(), page.since.to_string()),
        ("X-Paging-Until".to_owned(), page.until.to_string()),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lookup_from(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> + use<> {
        let owned: Vec<(String, String)> = pairs
            .iter()
            .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
            .collect();
        move |name: &str| {
            owned
                .iter()
                .find(|(k, _)| k == name)
                .map(|(_, v)| v.clone())
        }
    }

    #[test]
    fn the_defaults_are_the_documented_ones() {
        let request = parse_paging(lookup_from(&[]), 10, 100).unwrap();
        assert_eq!(request.since, None);
        assert_eq!(request.until, None);
        assert_eq!(request.limit, 10);
        assert_eq!(request.order, Order::Updated, "the default order is update");
    }

    #[test]
    fn a_limit_above_the_ceiling_is_clamped_not_refused() {
        // `:137` -- the actual page size used comes back in X-Paging-Limit.
        let request = parse_paging(lookup_from(&[("paging.limit", "5000")]), 10, 100).unwrap();
        assert_eq!(request.limit, 100);
    }

    #[test]
    fn a_zero_limit_is_legitimate() {
        // AMWA IS-04-02 `test_21_4` expects a 200 with an empty body.
        let request = parse_paging(lookup_from(&[("paging.limit", "0")]), 10, 100).unwrap();
        assert_eq!(request.limit, 0);
    }

    #[test]
    fn a_negative_or_non_integer_limit_is_malformed() {
        for bad in ["-1", "abc", "1.5", "", "1e3", " 1", "+1"] {
            assert!(
                parse_paging(lookup_from(&[("paging.limit", bad)]), 10, 100).is_err(),
                "accepted limit {bad:?}",
            );
        }
    }

    #[test]
    fn an_inverted_window_is_malformed_but_an_equal_one_is_not() {
        let inverted = parse_paging(
            lookup_from(&[("paging.since", "20:0"), ("paging.until", "10:0")]),
            10,
            100,
        );
        assert!(inverted.is_err(), "an inverted window was accepted");

        // The legitimate "has anything arrived since this cursor?" poll.
        let equal = parse_paging(
            lookup_from(&[("paging.since", "10:0"), ("paging.until", "10:0")]),
            10,
            100,
        );
        assert!(equal.is_ok(), "since == until must be allowed");
    }

    #[test]
    fn a_malformed_cursor_or_order_is_refused() {
        assert!(parse_paging(lookup_from(&[("paging.since", "abc")]), 10, 100).is_err());
        assert!(parse_paging(lookup_from(&[("paging.until", "1")]), 10, 100).is_err());
        assert!(parse_paging(lookup_from(&[("paging.order", "created")]), 10, 100).is_err());
    }

    #[test]
    fn the_colon_and_the_space_survive_encoding() {
        // The two things a general URL encoder gets wrong for this purpose.
        assert_eq!(
            encode_component("1441716120:318744030"),
            "1441716120:318744030"
        );
        assert_eq!(encode_component("My Node"), "My%20Node");
        // And everything else is still encoded, so a value cannot split the query.
        assert_eq!(encode_component("foo&bar"), "foo%26bar");
        assert_eq!(encode_component("a=b"), "a%3Db");
        assert_eq!(encode_component("100%"), "100%25");
        // Unreserved characters are never touched.
        assert_eq!(encode_component("a-b_c.d~e"), "a-b_c.d~e");
    }

    #[test]
    fn non_ascii_encodes_per_utf8_byte() {
        // Python's `quote` encodes the UTF-8 bytes, uppercase hex.
        assert_eq!(encode_component("café"), "caf%C3%A9");
    }
}
