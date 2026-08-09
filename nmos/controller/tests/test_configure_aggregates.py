# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the configure pages' tri-state toggle aggregate.

A configure-page master toggle drives *every* selected resource, so its
position has to distinguish three cases: all resources on, all off, and the
resources disagreeing. Two independent implementations compute that aggregate
and they must never disagree:

* the server, in ``_aggregate_toggle_state`` over ``sender_state`` /
  ``receiver_state``, for the initial render; and
* the browser, in ``_reconcileConfigureToggles``, which recomputes it from the
  ``data-live-active`` flag on the result cells after each SSE status frame.

The second one selects cells by the *presence* of ``data-result-for-receiver``.
That makes the set of rendered attributes part of the contract, not a display
detail: a cell emitted for a sender with no paired receiver would contribute a
``false`` the server never counted, and the button would sit on ``mixed`` while
every real receiver was active. These tests pin both halves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import (
    ChainableUndefined,
    Environment,
    FileSystemLoader,
    select_autoescape,
)

from ..handlers import _aggregate_toggle_state

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


class _Blank(ChainableUndefined):
    """An undefined that renders empty and is falsy, iterable and mappable.

    ``receivers_configure.html`` reads far more context than the pairing
    behaviour under test needs. Supplying every unrelated key would couple this
    test to changes elsewhere in the page; letting the unrelated ones resolve to
    a quiet blank keeps it focused on the one branch it exists to check.
    """

    def __iter__(self) -> Any:
        return iter(())

    def items(self) -> Any:
        return iter(())

    def get(self, *args: Any, **kwargs: Any) -> "_Blank":
        return _Blank()

    def __call__(self, *args: Any, **kwargs: Any) -> "_Blank":
        return _Blank()

    def __bool__(self) -> bool:
        return False

    def __len__(self) -> int:
        return 0


def _render_receivers_configure(pair_by_sender: dict[str, Any]) -> str:
    """Render the page with one sender, paired or not per the argument.

    The environment mirrors ``create_controller_app``'s exactly — ``trim_blocks``
    and ``lstrip_blocks`` change how the conditional attribute block collapses,
    so rendering under different settings would not prove anything about what
    the server actually sends.
    """
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=_Blank,
    )
    constraint_set = {
        "index": 0, "hash": "h", "label": "Native",
        "media_type": "video/raw", "meta_format": "video", "meta_layer": 0,
        "preference": 100, "actual_meta_format": "", "actual_meta_layer": "",
        "flow_part": "", "params": [],
    }
    return env.get_template("receivers_configure.html").render(
        config_view={"senders": [{
            "id": "s1", "label": "S1",
            "parts": None, "constraint_set": constraint_set,
        }]},
        conset_by_sender={},
        pair_by_sender=pair_by_sender,
        sender_state={"s1": {"active": False}},
        receiver_state={"r1": {"active": True}},
    )


class TestAggregateToggleState:
    """The server half of the contract."""

    def test_all_on_is_true(self) -> None:
        assert _aggregate_toggle_state([True, True]) == "true"

    def test_all_off_is_false(self) -> None:
        assert _aggregate_toggle_state([False, False]) == "false"

    def test_disagreement_is_mixed(self) -> None:
        assert _aggregate_toggle_state([True, False]) == "mixed"

    def test_empty_selection_is_off(self) -> None:
        # Conservative: a malformed or stale URL must not render an
        # apparently active control.
        assert _aggregate_toggle_state([]) == "false"

    def test_single_resource_is_never_mixed(self) -> None:
        assert _aggregate_toggle_state([True]) == "true"
        assert _aggregate_toggle_state([False]) == "false"


class TestReceiverResultCellPairing:
    """The rendered half of the contract."""

    def test_paired_sender_emits_the_receiver_attributes(self) -> None:
        text = _render_receivers_configure({"s1": {"id": "r1"}})
        assert 'data-result-for-receiver="r1"' in text
        # ``receiver_state["r1"]`` is active, so the browser must start from
        # the same value the server aggregated.
        assert 'data-live-active="true"' in text

    def test_unpaired_sender_emits_no_receiver_attribute(self) -> None:
        text = _render_receivers_configure({})
        # Presence — not value — is what ``_reconcileConfigureToggles``
        # selects on, so an empty-valued attribute is as harmful as a
        # populated one.
        assert "data-result-for-receiver" not in text

    def test_unpaired_sender_still_renders_its_placeholder_cell(self) -> None:
        # The operator must still see the column; only the live-state
        # bookkeeping is withheld.
        text = _render_receivers_configure({})
        assert 'class="result-cell text-muted small">' in text
