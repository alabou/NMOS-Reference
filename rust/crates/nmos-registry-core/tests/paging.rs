// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Pagination, ported from `nmos/registry/tests/test_paging.py`.
//!
//! The nine worked examples of `APIs - Query Parameters.md:34-369` **are** the
//! specification for this area: the prose leaves several boundary cases
//! implicit and only the examples pin them down. All nine are here, each
//! naming the lines it comes from.
//!
//! Where the AMWA mock registry differs, the test says so. Those are not
//! gratuitous comparisons -- they are the cases most likely to be "fixed" back
//! to the wrong behaviour by someone who checked the mock rather than the
//! document.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

use nmos_registry_core::body::Body;
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::paging::{Page, apply_paging, paging_headers, parse_paging};
use nmos_registry_core::resource::{Order, RegisteredResource};
use nmos_registry_core::resource_type::ResourceType;
use serde_json::json;

/// The examples use a server whose default paging limit is 10 (`:52`).
const DEFAULT_LIMIT: usize = 10;
const MAX_LIMIT: usize = 100;
const BASE_URL: &str = "http://api.example.com/x-nmos/query/v1.1/nodes/";

/// One sample record, identified only by its cursor.
fn resource(nanoseconds: u32) -> RegisteredResource {
    let cursor = TaiCursor::new(0, nanoseconds);
    RegisteredResource::new(
        ResourceType::Node,
        format!("00000000-0000-1000-8000-{nanoseconds:012}"),
        Body::from_value(json!({"id": format!("node-{nanoseconds}")})),
        cursor.to_string(),
        cursor,
        cursor,
        None,
    )
}

/// `[0:1, 0:2, ... 0:20]` -- the sample data set of `:40-46`.
fn sample() -> Vec<RegisteredResource> {
    (1..=20).map(resource).collect()
}

fn refs(data: &[RegisteredResource]) -> Vec<&RegisteredResource> {
    data.iter().collect()
}

fn lookup(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> + use<> {
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

/// A page from `matched`, with the collection's newest cursor for the default
/// upper bound.
fn page_for<'a>(
    matched: &[&'a RegisteredResource],
    query: &[(&str, &str)],
    collection: Option<&[&RegisteredResource]>,
) -> Page<'a> {
    let request = parse_paging(lookup(query), DEFAULT_LIMIT, MAX_LIMIT).expect("valid paging");
    let source = collection.unwrap_or(matched);
    let collection_max = source.last().map(|r| request.cursor_of(r));
    apply_paging(matched, collection_max, &request)
}

fn cursors(page: &Page<'_>) -> Vec<String> {
    page.resources
        .iter()
        .map(|r| r.updated.to_string())
        .collect()
}

fn headers(page: &Page<'_>, filters: &[(&str, &str)], order: Order) -> Vec<(String, String)> {
    let owned: Vec<(String, String)> = filters
        .iter()
        .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
        .collect();
    paging_headers(page.window(), BASE_URL, &owned, order)
}

fn header(set: &[(String, String)], name: &str) -> String {
    set.iter()
        .find(|(k, _)| k == name)
        .map(|(_, v)| v.clone())
        .unwrap_or_else(|| panic!("no {name} header"))
}

// ---------------------------------------------------------------------------
// The five numbered examples
// ---------------------------------------------------------------------------

#[test]
fn example_1_the_initial_request_returns_the_newest_page_descending() {
    // `:50-99` -- "The data set returned when no paging.since or paging.until
    // parameters are specified MUST be from the most recently updated (or
    // created) resources in the collection, returned in descending order."
    //
    // The AMWA mock returns the OLDEST ten, ascending.
    let data = sample();
    let page = page_for(&refs(&data), &[], None);

    assert_eq!(
        cursors(&page),
        [
            "0:20", "0:19", "0:18", "0:17", "0:16", "0:15", "0:14", "0:13", "0:12", "0:11",
        ],
    );
    assert_eq!(page.since.to_string(), "0:10");
    assert_eq!(page.until.to_string(), "0:20");
    assert_eq!(page.limit, 10);
}

#[test]
fn example_2_a_custom_limit_narrows_the_page_and_the_lower_cursor() {
    // `:102-137`.
    let data = sample();
    let page = page_for(&refs(&data), &[("paging.limit", "5")], None);

    assert_eq!(cursors(&page), ["0:20", "0:19", "0:18", "0:17", "0:16"]);
    assert_eq!(page.since.to_string(), "0:15");
    assert_eq!(page.until.to_string(), "0:20");
    assert_eq!(page.limit, 5);
}

#[test]
fn example_3_since_is_non_inclusive_and_pages_forwards() {
    // `:139-173`. The window opens at 0:5, not 0:4.
    let data = sample();
    let page = page_for(&refs(&data), &[("paging.since", "0:4")], None);

    assert_eq!(
        cursors(&page),
        [
            "0:14", "0:13", "0:12", "0:11", "0:10", "0:9", "0:8", "0:7", "0:6", "0:5"
        ],
    );
    assert_eq!(
        page.since.to_string(),
        "0:4",
        "since is echoed as requested"
    );
    assert_eq!(page.until.to_string(), "0:14");
}

#[test]
fn example_4_until_is_inclusive_and_pages_backwards() {
    // `:175-209`. 0:16 itself is returned.
    let data = sample();
    let page = page_for(&refs(&data), &[("paging.until", "0:16")], None);

    assert_eq!(
        cursors(&page),
        [
            "0:16", "0:15", "0:14", "0:13", "0:12", "0:11", "0:10", "0:9", "0:8", "0:7"
        ],
    );
    assert_eq!(page.since.to_string(), "0:6");
    assert_eq!(page.until.to_string(), "0:16");
}

#[test]
fn example_5_since_takes_precedence_and_narrows_the_reported_until() {
    // `:211-249` -- "Whilst both since and until are specified, as this server
    // example has a default paging limit of 10, the since parameter takes
    // precedence. As a result of this the value of X-Paging-Until is lower
    // than requested".
    let data = sample();
    let page = page_for(
        &refs(&data),
        &[("paging.since", "0:4"), ("paging.until", "0:16")],
        None,
    );

    assert_eq!(
        cursors(&page),
        [
            "0:14", "0:13", "0:12", "0:11", "0:10", "0:9", "0:8", "0:7", "0:6", "0:5"
        ],
    );
    assert_eq!(page.since.to_string(), "0:4");
    assert_eq!(
        page.until.to_string(),
        "0:14",
        "the requested ceiling of 0:16 was not narrowed to what was served",
    );
}

// ---------------------------------------------------------------------------
// The four edge cases
// ---------------------------------------------------------------------------

#[test]
fn edge_1_a_window_entirely_below_the_data_is_empty() {
    // `:257-282` -- `?paging.until=0:20` with only 0:21 and 0:22 stored.
    let data = vec![resource(21), resource(22)];
    let page = page_for(&refs(&data), &[("paging.until", "0:20")], None);

    assert!(cursors(&page).is_empty());
    assert_eq!(page.since.to_string(), "0:0");
    assert_eq!(page.until.to_string(), "0:20");
}

#[test]
fn edge_2_at_the_end_of_the_data_both_cursors_report_the_same_instant() {
    // `:284-313` -- `?paging.since=0:20` with only 0:19 and 0:20 stored.
    //
    // The `next` link then repeats the identical request, which is deliberate:
    // "the client is expected to re-perform the same request ... If the client
    // were to increment the value of since requested it would be in danger of
    // moving ahead of the current time and missing records."
    let data = vec![resource(19), resource(20)];
    let page = page_for(&refs(&data), &[("paging.since", "0:20")], None);

    assert!(cursors(&page).is_empty());
    assert_eq!(page.since.to_string(), "0:20");
    assert_eq!(page.until.to_string(), "0:20");
}

#[test]
fn edge_3_a_filter_matching_one_record_still_reports_the_collections_maximum() {
    // `:315-342`. The filtered record is at 0:15; the newest in the whole
    // collection is 0:20. `X-Paging-Until` reports 0:20 -- the collection's
    // maximum, not the page's -- so the `next` cursor remains a valid
    // "everything from now on" bookmark.
    let all = sample();
    let matched = vec![resource(15)];
    let page = page_for(&refs(&matched), &[], Some(&refs(&all)));

    assert_eq!(cursors(&page), ["0:15"]);
    assert_eq!(page.since.to_string(), "0:0");
    assert_eq!(
        page.until.to_string(),
        "0:20",
        "the page's own newest cursor was reported instead of the collection's",
    );
}

#[test]
fn edge_4_a_filter_matching_nothing_still_reports_the_collections_cursors() {
    // `:344-369`. An empty payload, but the headers still describe the window.
    let all = sample();
    let page = page_for(&[], &[], Some(&refs(&all)));

    assert!(cursors(&page).is_empty());
    assert_eq!(page.since.to_string(), "0:0");
    assert_eq!(page.until.to_string(), "0:20");
}

// ---------------------------------------------------------------------------
// Headers and links
// ---------------------------------------------------------------------------

#[test]
fn example_1_produces_the_documented_header_set() {
    // `:62-69`.
    let data = sample();
    let page = page_for(&refs(&data), &[], None);
    let set = headers(&page, &[], Order::Updated);

    assert_eq!(header(&set, "X-Paging-Limit"), "10");
    assert_eq!(header(&set, "X-Paging-Since"), "0:10");
    assert_eq!(header(&set, "X-Paging-Until"), "0:20");

    let link = header(&set, "Link");
    assert!(
        link.contains(&format!(
            "<{BASE_URL}?paging.since=0:20&paging.limit=10>; rel=\"next\""
        )),
        "{link}",
    );
    assert!(
        link.contains(&format!(
            "<{BASE_URL}?paging.until=0:10&paging.limit=10>; rel=\"prev\""
        )),
        "{link}",
    );
}

#[test]
fn the_links_are_string_comparable_against_the_paging_headers() {
    // A client -- and the AMWA suite -- matches the `prev` link against the
    // `X-Paging-Since` header it was handed, as plain text. A percent-encoded
    // colon would not compare equal even though the URL is equivalent.
    let all = vec![resource(20)];
    let matched = vec![resource(15)];
    let page = page_for(&refs(&matched), &[], Some(&refs(&all)));
    let set = headers(&page, &[], Order::Updated);
    let link = header(&set, "Link");

    assert!(
        !link.contains("%3A"),
        "a cursor colon was percent-encoded: {link}"
    );
    assert!(link.contains(&format!("paging.until={}", header(&set, "X-Paging-Since"))));
    assert!(link.contains(&format!("paging.since={}", header(&set, "X-Paging-Until"))));
}

#[test]
fn first_is_the_zero_cursor_and_last_carries_none() {
    // `:98-100`.
    let data = sample();
    let page = page_for(&refs(&data), &[], None);
    let link = header(&headers(&page, &[], Order::Updated), "Link");

    assert!(link.contains(&format!(
        "<{BASE_URL}?paging.since=0:0&paging.limit=10>; rel=\"first\""
    )));
    assert!(link.contains(&format!("<{BASE_URL}?paging.limit=10>; rel=\"last\"")));
}

#[test]
fn filters_are_preserved_on_every_link_with_percent_twenty() {
    // Edge Case 3's links carry `label=My%20Node` through -- `%20`, not `+`.
    let all = vec![resource(20)];
    let matched = vec![resource(15)];
    let page = page_for(&refs(&matched), &[], Some(&refs(&all)));
    let link = header(
        &headers(&page, &[("label", "My Node")], Order::Updated),
        "Link",
    );

    assert!(link.contains("label=My%20Node"), "{link}");
    assert_eq!(
        link.matches("label=My%20Node").count(),
        4,
        "the filter must ride on all four links",
    );
}

#[test]
fn an_ampersand_in_a_filter_value_is_still_encoded() {
    // Leaving `:` literal must not leave `&` literal too. AMWA `test_21_8`
    // queries `?label=foo%26bar`; an unencoded `&` would split the query
    // string and change its meaning.
    let all = vec![resource(20)];
    let matched = vec![resource(15)];
    let page = page_for(&refs(&matched), &[], Some(&refs(&all)));
    let link = header(
        &headers(&page, &[("label", "foo&bar")], Order::Updated),
        "Link",
    );

    assert!(link.contains("label=foo%26bar"), "{link}");
}

#[test]
fn a_non_default_order_is_echoed_on_the_links() {
    // A `create`-ordered page must produce `create`-ordered links, or the
    // client's next request silently switches ordering.
    let data = sample();
    let page = page_for(&refs(&data), &[("paging.order", "create")], None);
    let link = header(&headers(&page, &[], Order::Created), "Link");

    assert!(link.contains("paging.order=create"), "{link}");
    assert_eq!(link.matches("paging.order=create").count(), 4);
}

#[test]
fn the_default_order_is_not_echoed() {
    // So common URLs stay short.
    let data = sample();
    let page = page_for(&refs(&data), &[], None);
    let link = header(&headers(&page, &[], Order::Updated), "Link");
    assert!(!link.contains("paging.order"), "{link}");
}

// ---------------------------------------------------------------------------
// Ordering
// ---------------------------------------------------------------------------

#[test]
fn order_by_create_pages_on_the_creation_cursor() {
    // Built so the two orderings disagree: the record created first is
    // updated last. A page that ignored `paging.order` would return them the
    // same way round either way.
    let mut first = resource(1);
    first.updated = TaiCursor::new(0, 99);
    let mut second = resource(2);
    second.updated = TaiCursor::new(0, 3);

    // Ascending by *update*: second (0:3) then first (0:99).
    let by_update_data = vec![&second, &first];
    let update_request = parse_paging(lookup(&[]), DEFAULT_LIMIT, MAX_LIMIT).unwrap();
    let by_update = apply_paging(&by_update_data, Some(first.updated), &update_request);
    assert_eq!(
        by_update
            .resources
            .iter()
            .map(|r| r.id.as_str())
            .collect::<Vec<_>>(),
        [first.id.as_str(), second.id.as_str()],
        "update order should put the most recently updated first",
    );

    // Ascending by *create*: first (0:1) then second (0:2).
    let by_create_data = vec![&first, &second];
    let create_request = parse_paging(
        lookup(&[("paging.order", "create")]),
        DEFAULT_LIMIT,
        MAX_LIMIT,
    )
    .unwrap();
    let by_create = apply_paging(&by_create_data, Some(second.created), &create_request);
    assert_eq!(
        by_create
            .resources
            .iter()
            .map(|r| r.id.as_str())
            .collect::<Vec<_>>(),
        [second.id.as_str(), first.id.as_str()],
        "create order should put the most recently created first",
    );
}

#[test]
fn a_zero_limit_returns_no_records_but_still_describes_the_window() {
    // AMWA IS-04-02 `test_21_4`: a 200 with an empty body and
    // `X-Paging-Limit: 0`. The window has no extent, so both cursors sit on
    // the ceiling.
    let data = sample();
    let page = page_for(&refs(&data), &[("paging.limit", "0")], None);

    assert!(page.resources.is_empty());
    assert_eq!(page.limit, 0);
    assert_eq!(page.since.to_string(), "0:20");
    assert_eq!(page.until.to_string(), "0:20");
}

#[test]
fn a_not_truncated_forward_window_echoes_the_requested_ceiling() {
    // The subtle half of Example 5's rule, and what AMWA `test_21_5` checks:
    // when the whole window fitted, the requested ceiling still describes it.
    //
    // Narrowing to the last *matching* record would move the client's `next`
    // cursor backwards, so a later record that did not match the filter would
    // be replayed on the following page.
    let all = sample();
    // A filter selecting a discontiguous, small subset well below the ceiling.
    let matched = vec![resource(5), resource(6)];
    let request = parse_paging(
        lookup(&[("paging.since", "0:1"), ("paging.until", "0:18")]),
        DEFAULT_LIMIT,
        MAX_LIMIT,
    )
    .unwrap();
    let page = apply_paging(&refs(&matched), Some(all[19].updated), &request);

    assert_eq!(cursors(&page), ["0:6", "0:5"]);
    assert_eq!(
        page.until.to_string(),
        "0:18",
        "the ceiling was narrowed although the whole window was served",
    );
}
