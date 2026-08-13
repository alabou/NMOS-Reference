# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Query API pagination.

Implements the ``paged`` trait of ``QueryAPI.raml:25-63`` and the worked
examples of ``APIs - Query Parameters.md:34-369``. The examples are the real
specification here — the prose leaves several boundary cases implicit and only
the nine worked cases pin them down — so this module is written to reproduce
all nine exactly, and ``tests/test_paging.py`` asserts each one.

The model
---------
A page is a window over the collection ordered by a registry-assigned cursor
(creation or update time, selected by ``paging.order``). Two rules fix
everything else:

* ``paging.since`` is **non-inclusive**, ``paging.until`` is **inclusive**
  (``QueryAPI.raml:29,33``). The window is therefore ``(since, until]``.
* The payload is always returned **most recent first**
  (``APIs - Query Parameters.md:90``, ``QueryAPI.raml:40``).

Which end of the window the page is taken from depends on which cursor the
client supplied, and that is the part worth being explicit about:

* ``since`` given — page forwards from the bottom of the window. The client is
  walking towards newer records, so the page is the *oldest* ``limit`` records
  above ``since``, and ``X-Paging-Until`` reports where it got to.
* ``since`` absent — page backwards from the top. The client is looking at the
  newest records, so the page is the *newest* ``limit`` records at or below
  ``until``, and ``X-Paging-Since`` reports how far down it reached.

That asymmetry is why ``since`` "takes precedence where a resulting data set is
constrained by the server's value of limit" (``:30``, Example 5): with both
cursors supplied and more than ``limit`` records between them, the page is
anchored to ``since`` and the far end is reported back narrowed.

Cursor reporting is not just the min/max of what was returned. ``X-Paging-Until``
with no cursors supplied is the newest cursor in the **unfiltered** collection,
not of the filtered page — Edge Cases 3 and 4 both return a page whose newest
record is older than the reported ``until``, or no records at all, and still
report the collection's own maximum. That is what makes the ``next`` cursor
usable as a "watch for changes from now on" bookmark even when the current
filter matches nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence
from urllib.parse import quote, urlencode

from nmos.registry.types import RegisteredResource, TaiCursor

# ``paging.order`` values, ``QueryAPI.raml:39-43``. Default is ``update``.
ORDER_CREATE = "create"
ORDER_UPDATE = "update"
VALID_ORDERS = frozenset({ORDER_CREATE, ORDER_UPDATE})

# Query-string parameters this module owns. Everything else on the query
# string is a basic-query filter -- see ``query_filter.py``.
PARAM_SINCE = "paging.since"
PARAM_UNTIL = "paging.until"
PARAM_LIMIT = "paging.limit"
PARAM_ORDER = "paging.order"
PAGING_PARAMS = frozenset({PARAM_SINCE, PARAM_UNTIL, PARAM_LIMIT, PARAM_ORDER})


class PagingError(Exception):
    """A paging parameter was malformed. The caller answers 400."""


@dataclass(frozen=True)
class PagingRequest:
    """The paging parameters of one request, parsed and bounds-checked."""

    since: TaiCursor | None
    until: TaiCursor | None
    limit: int
    order: str

    @property
    def order_by_created(self) -> bool:
        return self.order == ORDER_CREATE

    def cursor_of(self, resource: RegisteredResource) -> TaiCursor:
        """The cursor this request pages on."""
        return resource.created if self.order_by_created else resource.updated


@dataclass(frozen=True)
class Page:
    """A rendered page: the records plus the cursors describing the window."""

    resources: list[RegisteredResource]
    since: TaiCursor
    until: TaiCursor
    limit: int


def parse_paging(
    params: dict[str, str], *, default_limit: int, max_limit: int,
) -> PagingRequest:
    """Parse the four ``paging.*`` query parameters.

    Args:
        params: The request's query string as a flat mapping.
        default_limit: Server default page size when the client asks for none.
        max_limit: Ceiling the server will honour. A larger request is clamped
            rather than refused — ``:137``: "If the client had requested a page
            size which the server was unable to honour, the actual page size
            used would be returned in X-Paging-Limit".

    Raises:
        PagingError: A cursor does not match ``^[0-9]+:[0-9]+$``, the limit is
            not a positive integer, or the order is not ``create``/``update``.
    """
    since = _cursor_param(params, PARAM_SINCE)
    until = _cursor_param(params, PARAM_UNTIL)

    limit = default_limit
    raw_limit = params.get(PARAM_LIMIT)
    if raw_limit is not None:
        # The RAML types this as an integer; a non-integer is malformed input
        # rather than something to silently round or ignore.
        if not raw_limit.lstrip("-").isdigit():
            raise PagingError(f"{PARAM_LIMIT} must be an integer, got {raw_limit!r}")
        limit = int(raw_limit)
        # Zero is a legitimate limit, not an error: it asks "give me the paging
        # cursors for this window but none of the records", which is how a
        # client probes a collection's bounds cheaply. AMWA IS-04-02
        # ``test_21_4`` exercises it directly and expects a 200 with an empty
        # body and ``X-Paging-Limit: 0``. Only a negative limit is malformed.
        if limit < 0:
            raise PagingError(f"{PARAM_LIMIT} must not be negative, got {limit}")
        limit = min(limit, max_limit)

    order = params.get(PARAM_ORDER, ORDER_UPDATE)
    if order not in VALID_ORDERS:
        raise PagingError(
            f"{PARAM_ORDER} must be one of {sorted(VALID_ORDERS)}, got {order!r}",
        )

    # An inverted window describes no possible page: `since` is the exclusive
    # lower bound and `until` the inclusive upper one, so since > until asks
    # for records both newer and older than each other. That is a malformed
    # request rather than a request with no matches, and is answered 400.
    #
    # since == until is NOT an error -- it is the legitimate "has anything
    # arrived since this cursor?" poll, and must return an empty page with
    # paging headers so the client can keep re-issuing the same cursor
    # (APIs - Query Parameters.md:313).
    if since is not None and until is not None and since > until:
        raise PagingError(
            f"{PARAM_SINCE} ({since}) must not be later than "
            f"{PARAM_UNTIL} ({until})",
        )

    return PagingRequest(since=since, until=until, limit=limit, order=order)


def _cursor_param(params: dict[str, str], name: str) -> TaiCursor | None:
    raw = params.get(name)
    if raw is None:
        return None
    cursor = TaiCursor.parse(raw)
    if cursor is None:
        raise PagingError(
            f"{name} must match '<seconds>:<nanoseconds>', got {raw!r}",
        )
    return cursor


def apply_paging(
    matched: Iterable[RegisteredResource],
    collection: Iterable[RegisteredResource],
    request: PagingRequest,
) -> Page:
    """Select one page from a filtered collection.

    Args:
        matched: The resources that passed the basic-query filters. ``:26``
            requires filtering to happen before paging, so this is already the
            filtered set.
        collection: Every extant resource of this type, unfiltered. Needed
            only to compute the default upper cursor — see the module
            docstring on Edge Cases 3 and 4.
        request: The parsed paging parameters.

    Returns:
        The page, most recent first, with the cursors to report in
        ``X-Paging-Since`` / ``X-Paging-Until``.
    """
    key = request.cursor_of
    # Sorted on (cursor, id), not on the cursor alone. The cursor is normally
    # unique within a type -- ``APIs - Query Parameters.md:17`` asks for that,
    # and the store enforces it by falling forward a nanosecond on collision --
    # but "normally" is not "always", and Python's sort is stable, so a tie
    # would otherwise be broken by dictionary insertion order.
    #
    # That is invisible in a single registry and wrong in a cluster: two members
    # that received the same two resources in a different order would return
    # the same page with its contents in a different order, and a client paging
    # across members would see records repeat or vanish. The id is arbitrary but
    # identical everywhere, which is all a tie-break has to be.
    ascending = sorted(matched, key=lambda resource: (key(resource), resource.id))

    if request.since is not None:
        # Paging forwards: the oldest `limit` records strictly above `since`,
        # bounded above by `until` when the client supplied one.
        window = [
            resource for resource in ascending
            if key(resource) > request.since
            and (request.until is None or key(resource) <= request.until)
        ]
        page = window[: request.limit]

        # `since` is echoed exactly as requested. `until` reports the top of
        # the window that was actually served, and the distinction that
        # matters is whether the limit *truncated* the window:
        #
        # * Truncated (more records matched than fit) -- narrow to the newest
        #   record served, so the client's `next` cursor resumes exactly where
        #   this page stopped. This is Example 5's "since takes precedence":
        #   X-Paging-Until comes back lower than requested.
        # * Not truncated -- the whole window was served, so the requested
        #   ceiling still describes it and must be echoed unchanged. Narrowing
        #   here would be wrong: it would move the client's `next` cursor
        #   backwards to the last *matching* record, so a later record that
        #   did not match the filter would be replayed on the next page.
        #   A filter selecting discontiguous records makes this visible, which
        #   is what AMWA IS-04-02 ``test_21_5`` checks.
        #
        # With nothing served at all, the window collapses onto the bound(s)
        # supplied: the requested ceiling if there was one, otherwise `since`
        # itself (Edge Case 2, where until == since makes the client re-issue
        # the identical cursor rather than skipping past records that have not
        # arrived yet).
        truncated = len(window) > request.limit
        if truncated and page:
            report_until = key(page[-1])
        elif request.until is not None:
            report_until = request.until
        elif page:
            report_until = key(page[-1])
        else:
            report_until = request.since

        return Page(
            resources=list(reversed(page)),
            since=request.since,
            until=report_until,
            limit=request.limit,
        )

    # Paging backwards: the newest `limit` records at or below `until`.
    # Absent an explicit `until`, the ceiling is the newest cursor in the
    # UNFILTERED collection.
    ceiling = request.until if request.until is not None else _max_cursor(
        collection, key,
    )
    window = [resource for resource in ascending if key(resource) <= ceiling]
    # ``window[-0:]`` is the whole list, not an empty one, so a zero limit has
    # to be handled before the slice rather than falling out of it.
    if request.limit == 0:
        page = []
    else:
        page = window[-request.limit:] if request.limit <= len(window) else window

    # `since` is the exclusive lower bound that reproduces exactly this page:
    # the cursor of the record immediately below it. When the page did not
    # fill, the window reaches the bottom of the collection and the bound is
    # 0:0 (Edge Cases 1, 3 and 4).
    #
    # A zero limit served nothing, so there is no "record below the page" --
    # the window has no extent and both bounds sit on the ceiling.
    if request.limit == 0:
        report_since = ceiling
    elif len(window) > request.limit:
        report_since = key(window[-request.limit - 1])
    else:
        report_since = TaiCursor.min()

    return Page(
        resources=list(reversed(page)),
        since=report_since,
        until=ceiling,
        limit=request.limit,
    )


def _max_cursor(
    collection: Iterable[RegisteredResource],
    key: Callable[[RegisteredResource], TaiCursor],
) -> TaiCursor:
    """Newest cursor in the collection, or 0:0 when it is empty."""
    highest = TaiCursor.min()
    for resource in collection:
        cursor = key(resource)
        if cursor > highest:
            highest = cursor
    return highest


def paging_headers(
    page: Page, base_url: str, filters: Sequence[tuple[str, str]], order: str,
) -> dict[str, str]:
    """Build the ``X-Paging-*`` and ``Link`` response headers.

    ``:13`` makes ``X-Paging-Limit`` mandatory whenever paging is in use —
    it is how a client detects that the API pages at all — so all three
    ``X-Paging-*`` headers are always emitted.

    ``:32`` says servers SHOULD return ``prev`` and ``next`` and MAY return
    ``first`` and ``last``; all four are provided. Their construction follows
    nmos-cpp: ``next`` pages upward from the window's top cursor, ``prev``
    downward from its bottom, ``first`` is ``paging.since=0:0`` (``:100``),
    and ``last`` carries no paging cursors at all, which is by definition the
    newest page (``:99``).

    Args:
        page: The page just produced.
        base_url: Absolute URL of this collection, used verbatim as the link
            target so the links stay valid behind a proxy.
        filters: The non-paging query parameters, preserved on every link —
            Edge Case 3 shows ``label=My%20Node`` carried through.
        order: The effective ``paging.order``, echoed only when it is not the
            default so common URLs stay short.
    """
    def encode(query: list[tuple[str, str]]) -> str:
        """Percent-encode a query string, leaving ``:`` literal.

        The colon matters. A client comparing the ``prev`` link against the
        ``X-Paging-Since`` header it was given does a plain string match, so
        ``paging.until=1441716120:318744030`` and
        ``paging.until=1441716120%3A318744030`` are not interchangeable even
        though they are the same URL. Every cursor in the specification's
        worked examples is written with a literal colon
        (``APIs - Query Parameters.md:65``), and RFC 3986 permits ``:`` in a
        query, so that is what is emitted.

        ``quote`` rather than the default ``quote_plus``: a space becomes
        ``%20``, matching the specification's ``?label=My%20Node`` (Edge Case
        3, ``:330``) rather than ``+``. Filter values are otherwise fully
        encoded -- an ``&`` inside a value still becomes ``%26``, so a label
        of ``foo&bar`` round-trips instead of splitting the query.
        """
        return urlencode(query, safe=":", quote_via=quote)

    def link(cursor_param: str, cursor: TaiCursor) -> str:
        query: list[tuple[str, str]] = list(filters)
        query.append((cursor_param, str(cursor)))
        query.append((PARAM_LIMIT, str(page.limit)))
        if order != ORDER_UPDATE:
            query.append((PARAM_ORDER, order))
        return f"<{base_url}?{encode(query)}>"

    def bare_link() -> str:
        query = list(filters)
        query.append((PARAM_LIMIT, str(page.limit)))
        if order != ORDER_UPDATE:
            query.append((PARAM_ORDER, order))
        return f"<{base_url}?{encode(query)}>"

    links = [
        f'{link(PARAM_SINCE, page.until)}; rel="next"',
        f'{link(PARAM_UNTIL, page.since)}; rel="prev"',
        f'{link(PARAM_SINCE, TaiCursor.min())}; rel="first"',
        f'{bare_link()}; rel="last"',
    ]

    return {
        "Link": ", ".join(links),
        "X-Paging-Limit": str(page.limit),
        "X-Paging-Since": str(page.since),
        "X-Paging-Until": str(page.until),
    }
