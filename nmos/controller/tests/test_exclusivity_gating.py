# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The Exclusivity switch must predict a refusal, not discover one.

It is the only control in the privacy panel that issues a request the moment
it is toggled — Protocol / Mode / Curve only edit form state, and nothing
leaves the browser until a master toggle is pressed, which is separately
gated on writability via ``all_senders_writable``. So the switch needs the
same prediction.

It did not have it: ``excl_locked`` consulted only ``lock_all`` (anything
active) and ``exclusivity_ok`` (do the Nodes advertise the reservation
service), never whether this admin may write. Measured against a live rig
with a read-only operator, the Node answered
``POST /x-manufacturer/exclusive/v1.0/acquire`` with
``403 insufficient permissions`` while the UI offered the switch as enabled.

Rendering is asserted through parsed attributes rather than substrings,
because ``disabled`` and ``checked`` are conditional boolean attributes on
this element and a substring test cannot tell one apart from a collapsed
pair (see ``test_template_attributes.py``).
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from jinja2 import (
    ChainableUndefined,
    Environment,
    FileSystemLoader,
    select_autoescape,
)

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


class _Blank(ChainableUndefined):
    """Quiet undefined, so unrelated page context need not be supplied."""

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


class _Attrs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, dict[str, str | None]] = {}
        self.by_role: dict[str, dict[str, str | None]] = {}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        as_dict = dict(attrs)
        if as_dict.get("id"):
            self.by_id[str(as_dict["id"])] = as_dict
        if as_dict.get("data-role"):
            self.by_role[str(as_dict["data-role"])] = as_dict


def _privacy_view(*, writable: bool, exclusivity_ok: bool = True,
                  any_active: bool = False) -> dict[str, Any]:
    return {
        "pep_available": True,
        "protocols": ["RTP"],
        "modes": ["AES-128-CTR"],
        "curves": [],
        "has_ecdh_modes": False,
        "exclusivity_ok": exclusivity_ok,
        "writable": writable,
        "any_active": any_active,
        "node_ids": ["node-a"],
        "reserved_node_ids": [],
        "reserved_all": False,
        "resource_summary": "1 sender · 1 node",
        "fetch_failed": [],
    }


def _render(**kwargs: Any) -> _Attrs:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=_Blank,
    )
    html = env.get_template("partials/privacy_section.html").render(
        privacy_view=_privacy_view(**kwargs))
    parsed = _Attrs()
    parsed.feed(html)
    return parsed


def _switch(parsed: _Attrs) -> dict[str, str | None]:
    return parsed.by_id["privacy-exclusivity"]


def test_writable_selection_offers_the_switch() -> None:
    assert "disabled" not in _switch(_render(writable=True))


def test_non_writable_selection_disables_the_switch() -> None:
    """The regression: a read-only operator was offered a doomed control."""
    assert "disabled" in _switch(_render(writable=False)), (
        "the switch is offered although every write on this selection is "
        "refused with 403"
    )


@pytest.mark.parametrize("any_active", [True, False])
@pytest.mark.parametrize("exclusivity_ok", [True, False])
@pytest.mark.parametrize("writable", [True, False])
def test_switch_is_enabled_only_when_all_three_gates_are_open(
    writable: bool, exclusivity_ok: bool, any_active: bool,
) -> None:
    parsed = _render(writable=writable, exclusivity_ok=exclusivity_ok,
                     any_active=any_active)
    enabled = "disabled" not in _switch(parsed)
    assert enabled is (writable and exclusivity_ok and not any_active)


def test_reason_names_the_authorization_problem() -> None:
    """The tooltip is the only place an operator learns why."""
    parsed = _render(writable=False)
    label = parsed.by_role["privacy-exclusivity-label"]
    title = label.get("title") or ""
    assert "not authorised" in title.lower()
    assert "403" in title or "write" in title.lower()
    # And the live reconciler needs its own copy to swap to.
    assert label.get("data-title-unauthorized")


def test_panel_publishes_writability_for_the_live_reconciler() -> None:
    """``_reconcilePrivacyLock`` recomputes ``disabled`` on every status frame.

    A gate it cannot see is undone the moment the first frame arrives, so the
    server render alone is not sufficient — the flag has to reach the DOM.
    """
    assert _render(writable=False).by_role.get("privacy-exclusivity") is not None
    for writable, expected in ((True, "1"), (False, "0")):
        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(enabled_extensions=("html",)),
            trim_blocks=True, lstrip_blocks=True, undefined=_Blank,
        )
        html = env.get_template("partials/privacy_section.html").render(
            privacy_view=_privacy_view(writable=writable))
        assert f'data-exclusivity-writable="{expected}"' in html


def test_active_selection_reason_still_takes_precedence() -> None:
    """Ordering is deliberate: "deactivate the selection" is actionable, so it
    is the message shown when several gates are shut at once."""
    parsed = _render(writable=False, any_active=True)
    title = parsed.by_role["privacy-exclusivity-label"].get("title") or ""
    assert "deactivate" in title.lower()
