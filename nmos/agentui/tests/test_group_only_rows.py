# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""``read_rows`` must not report a group-collapsed page as an empty one.

The compatible-senders page has two shapes. In ``single`` / ``subset`` mode it
lists individual senders as member rows. In ``group`` mode
``device_block.html`` collapses those rows (``group_only``) and offers whole
groups instead — which happens whenever *every* member of the receiver's
natural group was ticked, including any group with exactly one member.

``read_rows`` matches ``tr.member-row[data-resource-id]`` and used to return an
empty tuple in the second shape. That is indistinguishable from "this receiver
has no compatible sender", which is precisely how the operating guide tells
readers to interpret an empty list — so every routing scenario stopped early
announcing that a receiver had no compatible sender while the page displayed
several of them.

These tests pin the distinction: empty means empty, and collapsed says so.

The fake surface is a ``selector -> snapshots`` map rather than a parsed
document (see ``fake_surface`` for why), so what is asserted here is the
decision rule. That the selectors themselves match the real markup is the
e2e suite's job — ``test_scenarios.py::TestCompatibleSendersShapes``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ..apps.nmos_controller import pages
from ..apps.nmos_controller.session import ControllerSession
from ..core.adapter import Credentials, Precondition, Target
from ..core.journal import Journal
from ..core.step import Recorder
from ..core.surface import Surface, snapshot_of
from ..enums import PageId
from ..errors import GroupOnlyRendering
from .fake_surface import FakeSurface

COMPATIBLE_URL = "https://xyz-snx00001:5050/controller/receivers/compatible-senders"
RECEIVERS_URL = "https://xyz-snx00001:5050/controller/receivers"


class _Adapter:
    """Enough of the controller adapter to classify the two pages used here."""

    name = "nmos-controller"
    main_selector = "main"

    def discover(self) -> Target:
        return Target(app="nmos-controller", scheme="https",
                      host="xyz-snx00001", port=5050)

    def entry_url(self, target: Target) -> str:
        return target.origin + "/controller/"

    def identify_page(self, url: str) -> PageId:
        if "compatible-senders" in url:
            return PageId.RECEIVERS_COMPATIBLE_SENDERS
        if url.rstrip("/").endswith("/receivers"):
            return PageId.RECEIVERS
        return PageId.INDEX

    def authenticate(self, surface: Surface, credentials: object) -> None:
        """Not exercised by these tests."""

    def preconditions(self) -> tuple[Precondition, ...]:
        return ()


def _member_row(resource_id: str) -> object:
    return snapshot_of(
        selector=pages.MEMBER_ROWS, tag="tr", classes=("member-row",),
        attrs={"data-resource-id": resource_id},
    )


def _group_radio(ids: str) -> object:
    # ``name`` is deliberately absent: it is not on the surface's attribute
    # allowlist, and the selector already encodes it.
    return snapshot_of(
        selector=pages.GROUP_RADIOS, tag="input", attrs={"data-ids": ids},
    )


def _session(tmp_path: Path, surface: FakeSurface) -> ControllerSession:
    journal = Journal(tmp_path, scenario="unit", run_id="run-under-test")
    recorder = Recorder(surface, journal, _Adapter())
    return ControllerSession(surface, recorder, _Adapter(),
                             Credentials(password="x"))


class TestGroupCollapsedPage:
    """A page offering only groups says so rather than reporting nothing."""

    def test_raises_instead_of_returning_empty(self, tmp_path: Path) -> None:
        surface = FakeSurface(
            {pages.MEMBER_ROWS: (), pages.GROUP_RADIOS: (_group_radio("s1"),)},
            url=COMPATIBLE_URL,
        )
        with pytest.raises(GroupOnlyRendering):
            _session(tmp_path, surface).read_rows()

    def test_carries_the_group_count_and_the_page(self, tmp_path: Path) -> None:
        # The count is carried so a caller can report what it saw without
        # re-reading the page, and the message has to name the usable verbs --
        # an error that only says "no" repeats the original problem.
        surface = FakeSurface(
            {pages.MEMBER_ROWS: (),
             pages.GROUP_RADIOS: (_group_radio("s1"), _group_radio("s2,s3"))},
            url=COMPATIBLE_URL,
        )
        with pytest.raises(GroupOnlyRendering) as caught:
            _session(tmp_path, surface).read_rows()
        assert caught.value.group_count == 2
        assert caught.value.actual is PageId.RECEIVERS_COMPATIBLE_SENDERS
        assert "read_groups()" in str(caught.value)


class TestGenuinelyEmptyPage:
    """No rows and no groups still means no rows -- the honest empty case."""

    def test_returns_empty_tuple(self, tmp_path: Path) -> None:
        surface = FakeSurface(
            {pages.MEMBER_ROWS: (), pages.GROUP_RADIOS: ()},
            url=COMPATIBLE_URL,
        )
        assert _session(tmp_path, surface).read_rows() == ()

    def test_empty_list_page_is_not_an_error(self, tmp_path: Path) -> None:
        # A receivers page with nothing on it is a rig problem, not a shape
        # problem, and must keep returning empty rather than raising.
        surface = FakeSurface(
            {pages.MEMBER_ROWS: (), pages.GROUP_RADIOS: ()},
            url=RECEIVERS_URL,
        )
        assert _session(tmp_path, surface).read_rows() == ()


class TestRowsPresent:
    """Rows win: a page with members is never reported as collapsed."""

    def test_member_rows_are_returned(self, tmp_path: Path) -> None:
        surface = FakeSurface(
            {pages.MEMBER_ROWS: (_member_row("r1"), _member_row("r2"))},
            url=COMPATIBLE_URL,
        )
        rows = _session(tmp_path, surface).read_rows()
        assert [r.resource_id for r in rows] == ["r1", "r2"]

    def test_rows_and_groups_together_do_not_raise(self, tmp_path: Path) -> None:
        # Both shapes' markup present (a group radio *and* its member rows) is
        # the ordinary single/subset rendering; only the absence of rows is a
        # collapse.
        surface = FakeSurface(
            {pages.MEMBER_ROWS: (_member_row("r1"),),
             pages.GROUP_RADIOS: (_group_radio("r1"),)},
            url=COMPATIBLE_URL,
        )
        rows = _session(tmp_path, surface).read_rows()
        assert [r.resource_id for r in rows] == ["r1"]
