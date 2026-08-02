# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Pagination tests, transcribed from the worked examples in the spec.

``APIs - Query Parameters.md:34-369`` contains five numbered examples and four
edge cases, each giving a request, the expected payload, and the expected
headers. They are the real specification for paging — the prose leaves the
boundary behaviour implicit — so each one is reproduced here literally,
including the sample data set and the server's default limit of 10.

Sample data (``:40-46``): twenty Nodes with update timestamps ``0:1`` through
``0:20``. Payloads in the spec list bare timestamps in place of full resource
objects; these tests do the same by comparing the returned cursors.
"""

from __future__ import annotations

import pytest

from nmos.registry.paging import (
    Page,
    PagingError,
    apply_paging,
    paging_headers,
    parse_paging,
)
from nmos.registry.types import RegisteredResource, ResourceType, TaiCursor

# The examples use a server whose default paging limit is 10 (``:52``).
DEFAULT_LIMIT = 10
MAX_LIMIT = 100

BASE_URL = "http://api.example.com/x-nmos/query/v1.1/nodes/"


def resource(nanoseconds: int) -> RegisteredResource:
    """One sample record, identified only by its cursor."""
    cursor = TaiCursor(0, nanoseconds)
    return RegisteredResource(
        resource_type=ResourceType.NODE,
        id=f"00000000-0000-1000-8000-{nanoseconds:012d}",
        typed=None,
        raw={"id": f"node-{nanoseconds}"},
        version=str(cursor),
        created=cursor,
        updated=cursor,
        parent_id=None,
    )


@pytest.fixture
def sample() -> list[RegisteredResource]:
    """``[0:1, 0:2, ... 0:20]`` -- the sample data set of ``:40-46``."""
    return [resource(n) for n in range(1, 21)]


def page_for(
    data: list[RegisteredResource],
    query: dict[str, str],
    *,
    collection: list[RegisteredResource] | None = None,
) -> Page:
    request = parse_paging(
        query, default_limit=DEFAULT_LIMIT, max_limit=MAX_LIMIT,
    )
    return apply_paging(data, collection if collection is not None else data, request)


def cursors(page: Page) -> list[str]:
    return [str(r.updated) for r in page.resources]


# ---------------------------------------------------------------------------
# The five numbered examples
# ---------------------------------------------------------------------------

class TestWorkedExamples:
    def test_example_1_initial_request(self, sample: list[RegisteredResource]) -> None:
        """``GET /nodes`` -- ``:50-99``.

        "The data set returned when no paging.since or paging.until parameters
        are specified MUST be from the most recently updated (or created)
        resources in the collection, returned in descending order."

        The AMWA mock returns the OLDEST ten, ascending.
        """
        page = page_for(sample, {})
        assert cursors(page) == [
            "0:20", "0:19", "0:18", "0:17", "0:16",
            "0:15", "0:14", "0:13", "0:12", "0:11",
        ]
        assert str(page.since) == "0:10"
        assert str(page.until) == "0:20"
        assert page.limit == 10

    def test_example_2_custom_limit(self, sample: list[RegisteredResource]) -> None:
        """``?paging.limit=5`` -- ``:102-137``."""
        page = page_for(sample, {"paging.limit": "5"})
        assert cursors(page) == ["0:20", "0:19", "0:18", "0:17", "0:16"]
        assert str(page.since) == "0:15"
        assert str(page.until) == "0:20"
        assert page.limit == 5

    def test_example_3_since(self, sample: list[RegisteredResource]) -> None:
        """``?paging.since=0:4`` -- ``:139-173``.

        ``since`` is non-inclusive, so the window opens at 0:5, and paging
        runs forwards from there.
        """
        page = page_for(sample, {"paging.since": "0:4"})
        assert cursors(page) == [
            "0:14", "0:13", "0:12", "0:11", "0:10",
            "0:9", "0:8", "0:7", "0:6", "0:5",
        ]
        assert str(page.since) == "0:4"
        assert str(page.until) == "0:14"

    def test_example_4_until(self, sample: list[RegisteredResource]) -> None:
        """``?paging.until=0:16`` -- ``:175-209``.

        ``until`` is inclusive, so 0:16 itself is returned.
        """
        page = page_for(sample, {"paging.until": "0:16"})
        assert cursors(page) == [
            "0:16", "0:15", "0:14", "0:13", "0:12",
            "0:11", "0:10", "0:9", "0:8", "0:7",
        ]
        assert str(page.since) == "0:6"
        assert str(page.until) == "0:16"

    def test_example_5_since_takes_precedence(
        self, sample: list[RegisteredResource],
    ) -> None:
        """``?paging.since=0:4&paging.until=0:16`` -- ``:211-249``.

        "Whilst both since and until are specified, as this server example has
        a default paging limit of 10, the since parameter takes precedence. As
        a result of this the value of X-Paging-Until is lower than requested".
        """
        page = page_for(sample, {"paging.since": "0:4", "paging.until": "0:16"})
        assert cursors(page) == [
            "0:14", "0:13", "0:12", "0:11", "0:10",
            "0:9", "0:8", "0:7", "0:6", "0:5",
        ]
        assert str(page.since) == "0:4"
        assert str(page.until) == "0:14"  # narrowed from the requested 0:16


# ---------------------------------------------------------------------------
# The four edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_edge_1_before_the_start_of_the_data(self) -> None:
        """``?paging.until=0:20`` with only 0:21 and 0:22 stored -- ``:257-282``."""
        data = [resource(21), resource(22)]
        page = page_for(data, {"paging.until": "0:20"})
        assert cursors(page) == []
        assert str(page.since) == "0:0"
        assert str(page.until) == "0:20"

    def test_edge_2_at_the_end_of_the_data(self) -> None:
        """``?paging.since=0:20`` with only 0:19 and 0:20 stored -- ``:284-313``.

        Both cursors report 0:20, so the ``next`` link repeats the identical
        request. ``:313`` -- "the client is expected to re-perform the same
        request ... If the client were to increment the value of since
        requested it would be in danger of moving ahead of the current time
        and missing records."
        """
        data = [resource(19), resource(20)]
        page = page_for(data, {"paging.since": "0:20"})
        assert cursors(page) == []
        assert str(page.since) == "0:20"
        assert str(page.until) == "0:20"

    def test_edge_3_filter_yields_one_result(
        self, sample: list[RegisteredResource],
    ) -> None:
        """A filter matching one record, no paging parameters -- ``:315-342``.

        The filtered record is at 0:15; the newest record in the whole
        collection is 0:20. ``X-Paging-Until`` reports 0:20 -- the collection's
        maximum, not the page's -- so the ``next`` cursor remains a valid
        "everything from now on" bookmark.
        """
        matched = [resource(15)]
        page = page_for(matched, {}, collection=sample)
        assert cursors(page) == ["0:15"]
        assert str(page.since) == "0:0"
        assert str(page.until) == "0:20"

    def test_edge_4_filter_yields_no_results(
        self, sample: list[RegisteredResource],
    ) -> None:
        """A filter matching nothing -- ``:344-369``.

        An empty payload, but still the collection's cursors in the headers.
        """
        page = page_for([], {}, collection=sample)
        assert cursors(page) == []
        assert str(page.since) == "0:0"
        assert str(page.until) == "0:20"


# ---------------------------------------------------------------------------
# Headers and links
# ---------------------------------------------------------------------------

class TestHeaders:
    def test_example_1_headers(self, sample: list[RegisteredResource]) -> None:
        """The exact header set of Example 1 (``:62-69``)."""
        page = page_for(sample, {})
        headers = paging_headers(page, BASE_URL, [], "update")

        assert headers["X-Paging-Limit"] == "10"
        assert headers["X-Paging-Since"] == "0:10"
        assert headers["X-Paging-Until"] == "0:20"

        # Cursors carry a literal colon, exactly as the spec's example Link
        # header does (``:65``). A client string-matches the ``prev`` link
        # against the ``X-Paging-Since`` header it was handed, so a
        # percent-encoded colon would not compare equal even though the URL
        # is equivalent -- and the AMWA IS-04-02 test suite checks precisely
        # that substring.
        link = headers["Link"]
        assert (
            f'<{BASE_URL}?paging.since=0:20&paging.limit=10>; rel="next"'
            in link
        )
        assert (
            f'<{BASE_URL}?paging.until=0:10&paging.limit=10>; rel="prev"'
            in link
        )
        assert f"paging.until={headers['X-Paging-Since']}" in link
        assert f"paging.since={headers['X-Paging-Until']}" in link

    def test_paging_limit_always_present(
        self, sample: list[RegisteredResource],
    ) -> None:
        """``:13`` -- "Query API clients MUST detect whether pagination is
        being used by examining the HTTP response headers for X-Paging-Limit
        which MUST be returned in all cases where pagination is in use"."""
        page = page_for(sample, {})
        assert "X-Paging-Limit" in paging_headers(page, BASE_URL, [], "update")

    def test_first_and_last_links(self, sample: list[RegisteredResource]) -> None:
        """``:98-100`` -- ``first`` is ``paging.since=0:0``; ``last`` is the
        query with no paging cursors, which returns the newest page."""
        page = page_for(sample, {})
        link = paging_headers(page, BASE_URL, [], "update")["Link"]
        assert f'<{BASE_URL}?paging.since=0:0&paging.limit=10>; rel="first"' in link
        assert f'<{BASE_URL}?paging.limit=10>; rel="last"' in link

    def test_filters_are_preserved_on_links(self) -> None:
        """Edge Case 3's links carry ``label=My%20Node`` through."""
        page = page_for([resource(15)], {}, collection=[resource(20)])
        link = paging_headers(
            page, BASE_URL, [("label", "My Node")], "update",
        )["Link"]
        # ``%20``, not ``+`` -- the spec writes ?label=My%20Node (:330).
        assert "label=My%20Node" in link
        assert 'rel="next"' in link

    def test_non_default_order_is_echoed(
        self, sample: list[RegisteredResource],
    ) -> None:
        """A ``create``-ordered page must produce ``create``-ordered links."""
        page = page_for(sample, {"paging.order": "create"})
        link = paging_headers(page, BASE_URL, [], "create")["Link"]
        assert "paging.order=create" in link


# ---------------------------------------------------------------------------
# Parameter parsing
# ---------------------------------------------------------------------------

class TestParsing:
    def test_defaults(self) -> None:
        request = parse_paging({}, default_limit=10, max_limit=100)
        assert request.since is None
        assert request.until is None
        assert request.limit == 10
        assert request.order == "update"  # QueryAPI.raml:43
        assert request.order_by_created is False

    def test_limit_is_clamped_not_refused(self) -> None:
        """``:137`` -- an unhonourable page size is reduced and reported."""
        request = parse_paging(
            {"paging.limit": "5000"}, default_limit=10, max_limit=100,
        )
        assert request.limit == 100

    def test_malformed_cursor(self) -> None:
        """``QueryAPI.raml:31`` -- ``^[0-9]+:[0-9]+$``."""
        for bad in ("abc", "1", "-1:0", "1:0:0", ""):
            with pytest.raises(PagingError):
                parse_paging(
                    {"paging.since": bad}, default_limit=10, max_limit=100,
                )

    def test_malformed_limit(self) -> None:
        # Note "0" is absent: a zero limit is valid, see
        # test_zero_limit_is_valid below.
        for bad in ("abc", "-5", "1.5"):
            with pytest.raises(PagingError):
                parse_paging(
                    {"paging.limit": bad}, default_limit=10, max_limit=100,
                )

    def test_invalid_order(self) -> None:
        """``QueryAPI.raml:42`` -- the enum is exactly create|update."""
        with pytest.raises(PagingError):
            parse_paging(
                {"paging.order": "modified"}, default_limit=10, max_limit=100,
            )

    def test_zero_limit_is_valid(self) -> None:
        """``paging.limit=0`` asks for cursors without records.

        AMWA IS-04-02 ``test_21_4`` sends it and expects 200 with an empty
        body and ``X-Paging-Limit: 0``, so it must not be rejected.
        """
        request = parse_paging(
            {"paging.limit": "0"}, default_limit=10, max_limit=100,
        )
        assert request.limit == 0

    def test_since_after_until_is_rejected(self) -> None:
        """An inverted window is a bad request, answered 400.

        AMWA IS-04-02 ``test_21_6`` ("pagination (bad requests)") requires
        exactly this: "Specifying since after until is a bad request", and any
        status other than 400 -- including a 200 carrying paging headers --
        fails, because answering 200 means the API did not notice.
        """
        with pytest.raises(PagingError):
            parse_paging(
                {"paging.since": "0:20", "paging.until": "0:10"},
                default_limit=10, max_limit=100,
            )

    def test_since_equal_to_until_is_allowed(self) -> None:
        """``since == until`` is a legitimate poll, not an error.

        It is the "has anything arrived since this cursor?" request of Edge
        Case 2, and AMWA ``test_21_4`` ("requests that require empty
        responses") sends it expecting an empty page with paging headers.
        """
        request = parse_paging(
            {"paging.since": "0:10", "paging.until": "0:10"},
            default_limit=10, max_limit=100,
        )
        assert request.since == request.until

    def test_cursors_in_links_keep_a_literal_colon(self) -> None:
        """A percent-encoded colon breaks client-side cursor comparison.

        Clients (and the AMWA suite) match the ``prev`` link against the
        ``X-Paging-Since`` header as plain text.
        """
        page = page_for([resource(15)], {}, collection=[resource(20)])
        headers = paging_headers(page, BASE_URL, [], "update")
        link = headers["Link"]
        assert "%3A" not in link, link
        assert f"paging.until={headers['X-Paging-Since']}" in link
        assert f"paging.since={headers['X-Paging-Until']}" in link

    def test_ampersand_in_a_filter_value_is_still_encoded(self) -> None:
        """Leaving ``:`` literal must not leave ``&`` literal too.

        AMWA ``test_21_8`` queries ``?label=foo%26bar``; an unencoded ``&``
        would split the query string and change its meaning.
        """
        page = page_for([resource(15)], {}, collection=[resource(20)])
        link = paging_headers(
            page, BASE_URL, [("label", "foo&bar")], "update",
        )["Link"]
        assert "label=foo%26bar" in link

    def test_order_by_create_uses_the_creation_cursor(self) -> None:
        """``paging.order=create`` must page on creation, not update.

        Built so the two orderings disagree: the record created first is
        updated last.
        """
        first = resource(1)
        first.updated = TaiCursor(0, 99)
        second = resource(2)
        second.updated = TaiCursor(0, 3)
        data = [first, second]

        by_update = page_for(data, {})
        assert [r.id for r in by_update.resources] == [first.id, second.id]

        by_create = page_for(data, {"paging.order": "create"})
        assert [r.id for r in by_create.resources] == [second.id, first.id]
