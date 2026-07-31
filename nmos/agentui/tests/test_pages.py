# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Controller selector module.

Page identification is tested against the real route shapes because getting it
wrong is quiet and expensive: an unrecognised page makes every verb's
"am I where I think I am" check fail, and a *mis*-recognised one makes those
checks pass while operating on the wrong markup.

The URLs below are the actual routes from ``nmos/controller/app.py``, including
the query strings the selection flow really carries.
"""

from __future__ import annotations

import pytest

from ..apps.nmos_controller import pages
from ..enums import PageId, RowAction, ToggleAction

PREFIX = "http://127.0.0.1:5050/controller"


class TestIdentify:
    """URL to page identity."""

    @pytest.mark.parametrize(("url", "expected"), [
        # The scheme and host must be stripped -- an earlier version compared the
        # whole URL against "/controller" and matched nothing at all.
        (f"{PREFIX}/", PageId.INDEX),
        (f"{PREFIX}", PageId.INDEX),
        (f"{PREFIX}/login?next=%2Fcontroller%2Fsenders", PageId.LOGIN),
        (f"{PREFIX}/senders", PageId.SENDERS),
        (f"{PREFIX}/receivers", PageId.RECEIVERS),
        # Sub-pages must not be swallowed by their list page's prefix.
        (f"{PREFIX}/senders/caps?sender_ids=a,b", PageId.SENDERS_CAPS),
        (f"{PREFIX}/senders/configure?sender_ids=a", PageId.SENDERS_CONFIGURE),
        (f"{PREFIX}/receivers/caps", PageId.RECEIVERS_CAPS),
        (f"{PREFIX}/receivers/view-caps", PageId.RECEIVERS_VIEW_CAPS),
        (f"{PREFIX}/receivers/configure?ids=x", PageId.RECEIVERS_CONFIGURE),
        (f"{PREFIX}/receivers/compatible-senders?receiver_ids=r1",
         PageId.RECEIVERS_COMPATIBLE_SENDERS),
        # Per-resource detail routes.
        (f"{PREFIX}/senders/abc-123/transport", PageId.TRANSPORT_DETAIL),
        (f"{PREFIX}/receivers/abc-123/transport", PageId.TRANSPORT_DETAIL),
        (f"{PREFIX}/senders/abc-123/sdp", PageId.SDP_VIEW),
        (f"{PREFIX}/senders/abc-123/is11", PageId.IS11_STATUS),
        (f"{PREFIX}/receivers/abc-123/monitor", PageId.MONITOR_DETAIL),
        (f"{PREFIX}/senders/abc-123/flow", PageId.FLOW_DETAIL),
        (f"{PREFIX}/senders/abc-123/resource", PageId.RESOURCE_DETAIL),
        # Standalone resource routes.
        (f"{PREFIX}/flows/f-1", PageId.FLOW_DETAIL),
        (f"{PREFIX}/sources/s-1", PageId.SOURCE_DETAIL),
        (f"{PREFIX}/devices/d-1", PageId.DEVICE_DETAIL),
        (f"{PREFIX}/nodes/n-1", PageId.NODE_DETAIL),
        # Anything else is reported, not guessed at.
        ("http://127.0.0.1:5050/x-nmos/node/v1.3/", PageId.UNKNOWN),
        ("http://127.0.0.1:5050/", PageId.UNKNOWN),
    ])
    def test_identify(self, url: str, expected: PageId) -> None:
        assert pages.identify(url) is expected

    def test_trailing_slash_is_irrelevant(self) -> None:
        assert pages.identify(f"{PREFIX}/senders/") is PageId.SENDERS

    def test_fragment_is_ignored(self) -> None:
        assert pages.identify(f"{PREFIX}/senders#row-3") is PageId.SENDERS

    def test_index_never_matches_by_prefix(self) -> None:
        # If the index were prefix-matched it would claim every Controller page.
        assert pages.identify(f"{PREFIX}/senders") is not PageId.INDEX


class TestRowActions:
    """Row actions are located by href suffix, scoped to their row."""

    RESOURCE = "5f2a1c00-0000-4000-8000-000000000001"

    def test_scoped_to_the_row(self) -> None:
        # Row-scoping is what makes the selector unambiguous on a page listing
        # dozens of resources with identical action labels.
        selector = pages.row_action(self.RESOURCE, RowAction.TRANSPORT)
        assert f'data-resource-id="{self.RESOURCE}"' in selector
        assert selector.endswith('a[href$="/transport"]')

    def test_resource_action_matched_by_href_not_text(self) -> None:
        # The visible text is the resource kind ("sender"/"receiver"), so text
        # matching would be kind-dependent.
        assert pages.row_action(self.RESOURCE, RowAction.RESOURCE).endswith(
            'a[href$="/resource"]')

    def test_is11_uses_path_segment_not_label(self) -> None:
        # Rendered label is "is-11"; the path segment is "is11".
        selector = pages.row_action(self.RESOURCE, RowAction.IS11)
        assert 'href$="/is11"' in selector
        assert "is-11" not in selector

    def test_device_action_points_at_devices_route(self) -> None:
        # Targets the owning device rather than this resource.
        assert 'href^="/controller/devices/"' in pages.row_action(
            self.RESOURCE, RowAction.DEVICE)

    def test_monitor_is_the_status_badge_link(self) -> None:
        # Lives in the status column wrapping the badge, not in .row-actions.
        selector = pages.row_action(self.RESOURCE, RowAction.MONITOR)
        assert "a.status-badge-link" in selector
        assert ".row-actions" not in selector

    def test_blocked_form_is_a_disabled_span(self) -> None:
        # A span cannot carry `disabled`, so the class is the only signal.
        assert pages.row_action_blocked(self.RESOURCE, RowAction.FLOW).endswith(
            "span.btn.disabled")

    def test_every_row_action_has_a_selector(self) -> None:
        for action in RowAction:
            assert pages.row_action(self.RESOURCE, action)


class TestToggles:
    """Master toggles are addressed by the data-action the page's own JS uses."""

    def test_all_three_actions_distinguishable(self) -> None:
        selectors = {pages.toggle(a) for a in ToggleAction}
        assert len(selectors) == 3

    def test_sender_and_receiver_activation_differ(self) -> None:
        # Both appear on the receivers configure page and act on different
        # resources, so conflating them would activate the wrong thing.
        assert pages.toggle(ToggleAction.ACTIVATE) != pages.toggle(
            ToggleAction.ACTIVATE_RECEIVERS)

    def test_selector_shape(self) -> None:
        assert pages.toggle(ToggleAction.CONSTRAIN) == (
            'button.btn-toggle[data-action="constrain"]')


class TestCapsSelectors:
    """Radio-versus-cell targeting, and the detail pairing key."""

    def test_radio_and_cell_are_different_targets(self) -> None:
        # Clicking the input selects without expanding, because the row handler
        # returns early for INPUT targets. Clicking a cell does both.
        radio = pages.caps_row_radio("s1", 0)
        cell = pages.caps_row_cell("s1", 0)
        assert radio.endswith('input[type="radio"]')
        assert cell.endswith("td.caps-set-cell")
        assert radio != cell

    def test_detail_row_pairing_key(self) -> None:
        assert pages.caps_details("s1", 3) == (
            'tr[data-caps-details-for="s1-3"]')

    def test_row_key_is_resource_then_index(self) -> None:
        assert pages.caps_row("abc-def", 2) == (
            'tr.caps-row[data-caps-row="abc-def-2"]')


class TestResultCells:
    """Sender-side and receiver-side outcomes land in different cells."""

    def test_sides_use_different_attributes(self) -> None:
        assert pages.result_cell("x") == '.result-cell[data-result-for="x"]'
        assert pages.result_cell("x", receiver_side=True) == (
            '.result-cell[data-result-for-receiver="x"]')

    def test_terminal_classes_exclude_pending(self) -> None:
        # An error is a legitimate ending; only "pending" means keep waiting.
        assert pages.RESULT_PENDING not in pages.RESULT_TERMINAL
        assert set(pages.RESULT_TERMINAL) == {"ok", "error"}


class TestParamWidgets:
    """Parameter widgets are identified by their full triple."""

    def test_triple_is_complete(self) -> None:
        selector = pages.param_widget(
            "s1", "urn:x-nmos:cap:format:frame_width", "trunk")
        assert 'data-sender-id="s1"' in selector
        assert 'data-param-urn="urn:x-nmos:cap:format:frame_width"' in selector
        assert 'data-cs-part="trunk"' in selector

    def test_same_urn_different_part_differs(self) -> None:
        # A multiplexed selection carries the same parameter for several parts.
        assert pages.param_widget("s1", "u", "trunk") != pages.param_widget(
            "s1", "u", "video")


class TestReverseLinks:
    """One selector must find either shape of the shape-shifter."""

    def test_selector_is_tag_agnostic(self) -> None:
        selector = pages.reverse_link("g1")
        assert selector == '[data-reverse-group="g1"]'
        assert "button" not in selector
        assert "a[" not in selector


class TestNavSelectors:
    """Navigation is located by href, since the links carry no ids."""

    def test_hrefs(self) -> None:
        assert pages.NAV_SENDERS == 'a.nav-link[href="/controller/senders"]'
        assert pages.NAV_SIGN_OUT == 'a.nav-link[href="/controller/logout"]'


class TestFormSubmits:
    """List-page submits are only reachable as their form's primary button."""

    def test_scoped_to_form(self) -> None:
        assert pages.form_submit(pages.RECEIVERS_FORM) == (
            '#receivers-form button[type="submit"].btn-primary')

    def test_secondary_uses_formaction(self) -> None:
        assert pages.form_secondary_submit(pages.RECEIVERS_FORM).endswith(
            "button[formaction]")


class TestSelectionStateSelectors:
    """The hidden inputs are selected by id, because name and id differ."""

    def test_selection_mode_uses_id(self) -> None:
        # receivers.html renders name="mode" id="selection_mode".
        assert pages.SELECTION_MODE == "#selection_mode"
