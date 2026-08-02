# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Basic queries and downgrade queries.

Basic queries
-------------
``APIs - Query Parameters.md:436-524``. Any attribute a resource could carry
may be used as a query parameter, matched by exact string equality, with ``.``
descending into nested objects and into objects held in arrays.

Two rules in that section are easy to miss and are the reason this module
exists rather than a dict comprehension at the call site:

* ``:498`` — for an attribute whose value is an *array*, the query matches when
  the array **contains** the value. The worked example is ``?tags.studio=HQ1``
  against ``"tags": {"studio": ["HQ1"]}``.
* ``:444`` — "If a query parameter is requested which does not match an
  attribute found in any resource, an empty result set MUST be returned." A
  path that does not resolve makes the resource not match; if it resolves
  nowhere the result is empty. This falls out of per-resource matching, and it
  is specifically *not* a 400 and never a 500. (The AMWA mock ignores every
  filter but ``id``, and raises KeyError into a 500 for an unknown ``id``.)

Matching is done against the resource's raw JSON rather than its typed view,
for the same reason the raw form is what gets served: a client filtering on a
vendor extension the generated types do not model should still get an answer.

Downgrade queries
-----------------
``APIs - Query Parameters.md:371-434``. A downgrade query asks the registry to
*also* return resources registered under older minor versions — it does not
strip attributes from the response. Stripping is a separate, unconditional
rule (``:392``): whatever the request, a versioned response must only contain
data matching that version's schema.

This registry stores and serves v1.3 exclusively, so both halves are no-ops:
there are no older-versioned resources to add, and every stored resource
already matches the requested version. The parameter is still parsed and
validated, because the one case that *is* observable is the error —
``:434``: a downgrade across major versions MUST be refused with a 400.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from nmos.registry.paging import PAGING_PARAMS

# ``QueryAPI.raml:70`` -- ``^v[0-9]+.[0-9]+$``.
_API_VERSION_RE = re.compile(r"^v([0-9]+)\.([0-9]+)$")

# The API version this registry serves. Everything about downgrade is
# relative to it.
API_VERSION = "v1.3"

PARAM_DOWNGRADE = "query.downgrade"
PARAM_RQL = "query.rql"
PARAM_ANCESTRY_ID = "query.ancestry_id"
PARAM_ANCESTRY_TYPE = "query.ancestry_type"
PARAM_ANCESTRY_GENERATIONS = "query.ancestry_generations"

# Optional query features this implementation does not provide. Each is a MAY
# in the specification, and each MUST answer 501 when unsupported rather than
# being silently ignored -- ``:528`` for RQL, ``:578`` for ancestry. Silently
# ignoring them would be the dangerous failure: a client would receive an
# unfiltered set and treat it as a filtered one.
UNSUPPORTED_PARAMS: dict[str, str] = {
    PARAM_RQL: "RQL queries",
    PARAM_ANCESTRY_ID: "ancestry queries",
    PARAM_ANCESTRY_TYPE: "ancestry queries",
    PARAM_ANCESTRY_GENERATIONS: "ancestry queries",
}


class QueryError(Exception):
    """A query parameter was invalid. The caller answers 400."""


class UnsupportedQuery(Exception):
    """A query parameter names an unimplemented feature. Answers 501."""


@dataclass(frozen=True)
class ParsedVersion:
    major: int
    minor: int

    @classmethod
    def parse(cls, text: str) -> ParsedVersion | None:
        match = _API_VERSION_RE.match(text)
        if match is None:
            return None
        return cls(int(match.group(1)), int(match.group(2)))


def check_unsupported(params: Mapping[str, str]) -> None:
    """Raise if the request uses an unimplemented optional query feature.

    Raises:
        UnsupportedQuery: The caller turns this into a 501.
    """
    for name, feature in UNSUPPORTED_PARAMS.items():
        if name in params:
            raise UnsupportedQuery(
                f"{feature} are not supported by this Query API ({name})",
            )


def check_downgrade(params: Mapping[str, str]) -> str | None:
    """Validate ``query.downgrade``. Returns the requested version, or None.

    Raises:
        QueryError: The value is malformed, or names a different major
            version — ``:377``: "Downgrades MUST only be performed between
            minor API versions as major versions might remove or re-purpose
            attributes", and ``:434`` makes that a 400.
    """
    requested = params.get(PARAM_DOWNGRADE)
    if requested is None:
        return None

    target = ParsedVersion.parse(requested)
    if target is None:
        raise QueryError(
            f"{PARAM_DOWNGRADE} must match '^v[0-9]+\\.[0-9]+$', "
            f"got {requested!r}",
        )

    current = ParsedVersion.parse(API_VERSION)
    assert current is not None  # API_VERSION is a module constant
    if target.major != current.major:
        raise QueryError(
            f"cannot downgrade from {API_VERSION} to {requested}: "
            f"downgrade queries must not cross major API versions",
        )
    return requested


def filter_params(params: Mapping[str, str]) -> list[tuple[str, str]]:
    """Split the basic-query filters out of a request's query string.

    Everything that is not a reserved ``paging.*`` or ``query.*`` parameter is
    a filter on a resource attribute (``:440``).
    """
    return [
        (name, value)
        for name, value in params.items()
        if name not in PAGING_PARAMS and not name.startswith("query.")
    ]


def matches(raw: Mapping[str, Any], filters: Sequence[tuple[str, str]]) -> bool:
    """Does one resource satisfy every filter?

    Args:
        raw: The resource's JSON, as registered.
        filters: ``(dotted_path, expected_string)`` pairs, ANDed together
            (Example 2 at ``:476`` combines two parameters).
    """
    return all(
        _path_matches(raw, path.split("."), expected)
        for path, expected in filters
    )


def _path_matches(
    value: Any, segments: Sequence[str], expected: str,
) -> bool:
    """Walk a dotted path and test the value at the end of it.

    Arrays are traversed rather than indexed: ``services.type=X`` matches when
    *any* element of ``services`` has ``type == X`` (Example 4, ``:500``).
    That "any" is also what makes the array-containment rule work for
    ``tags.studio=HQ1``, where the path ends on an array of strings.
    """
    if not segments:
        return _scalar_matches(value, expected)

    head, rest = segments[0], segments[1:]

    if isinstance(value, dict):
        if head not in value:
            return False
        return _path_matches(value[head], rest, expected)

    if isinstance(value, list):
        # The path continues into the elements of an array of objects.
        return any(_path_matches(item, segments, expected) for item in value)

    # A scalar with path segments left over: the attribute does not exist at
    # this depth, so the resource does not match (``:444``).
    return False


def _scalar_matches(value: Any, expected: str) -> bool:
    """Compare a resolved value against the query string's text.

    Query-string values are always strings, so the resource's value is
    rendered to its JSON scalar text before comparison. Booleans must render
    as ``true``/``false`` rather than Python's ``True``/``False``, or
    ``?persist=true`` would never match. ``None`` renders as ``null``.
    """
    if isinstance(value, list):
        # Array containment (``:498``).
        return any(_scalar_matches(item, expected) for item in value)
    if isinstance(value, bool):
        return ("true" if value else "false") == expected
    if value is None:
        return expected == "null"
    if isinstance(value, (int, float, str)):
        return str(value) == expected
    # Objects never compare equal to a query-string scalar.
    return False
