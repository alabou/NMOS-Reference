# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Render-level tests for ``partials/privacy_section.html``.

These exist because of a bug that no logic test could have caught: the
exclusivity switch's ``checked`` and ``disabled`` attributes were emitted on
adjacent lines as bare ``{% if %}attr{% endif %}`` blocks. The controller's
Jinja environment runs with ``trim_blocks`` **and** ``lstrip_blocks``, which
between them delete the newline after ``{% endif %}`` and the indentation
before the next ``{% if %}`` — so when both conditions were true the render
produced ``checkeddisabled``: one unknown attribute, with the reservation
state and the lock both silently lost.

Two properties therefore have to be asserted at the level of *parsed
attributes*, not template source or substrings:

* a substring test (``'checked' in html``) passes against the bug, because
  ``checkeddisabled`` contains ``checked``;
* an ``html5lib``-style parse is what the browser actually does, so that is
  what the assertion has to model. The stdlib ``html.parser`` tokenises
  attributes the same way for this case.

The environment comes from :func:`create_controller_app` rather than a
locally-built one, so the test is bound to the flags production actually
uses. Constructing a copy here with ``trim_blocks=True`` hard-coded would
keep passing if someone changed the real app.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

import pytest
from jinja2 import Environment

from nmos.controller.app import create_controller_app


class _AttrCollector(HTMLParser):
    """Collect the attributes of every tag carrying a given id."""

    def __init__(self, wanted_id: str) -> None:
        super().__init__()
        self._wanted = wanted_id
        self.attrs: dict[str, str | None] | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        as_dict = dict(attrs)
        if as_dict.get("id") == self._wanted:
            self.attrs = as_dict


def _attributes_of(html: str, element_id: str) -> dict[str, str | None]:
    parser = _AttrCollector(element_id)
    parser.feed(html)
    assert parser.attrs is not None, (
        f"no element with id={element_id!r} in the render"
    )
    return parser.attrs


@pytest.fixture()
def production_env() -> Environment:
    """The Jinja environment the shipping controller renders with."""
    app = create_controller_app(None, admin_password="test")
    env = app["jinja_env"]
    assert isinstance(env, Environment)
    # Guard the premise of this whole module: if these flags are ever turned
    # off the glue hazard disappears and these tests become vacuous, so fail
    # loudly rather than passing for the wrong reason.
    assert env.trim_blocks is True
    assert env.lstrip_blocks is True
    return env


def _privacy_view(*, reserved_all: bool, any_active: bool) -> dict[str, Any]:
    """A privacy_view with PEP negotiable and the reservation service up.

    Only ``reserved_all`` and ``any_active`` vary — they are the two inputs
    that decide ``checked`` and ``disabled`` respectively (``any_active``
    reaches the switch as ``lock_all`` → ``excl_locked``).
    """
    return {
        "pep_available": True,
        "protocols": ["RTP"],
        "modes": ["AES-128-CTR"],
        "curves": [],
        "has_ecdh_modes": False,
        "exclusivity_ok": True,
        "any_active": any_active,
        "node_ids": ["node-a", "node-b"],
        "reserved_node_ids": ["node-a", "node-b"] if reserved_all else [],
        "reserved_all": reserved_all,
        "resource_summary": "1 sender · 1 receiver · 2 nodes",
        "fetch_failed": [],
    }


def _render(env: Environment, *, reserved_all: bool, any_active: bool) -> str:
    template = env.get_template("partials/privacy_section.html")
    return template.render(privacy_view=_privacy_view(
        reserved_all=reserved_all, any_active=any_active,
    ))


@pytest.mark.parametrize("reserved_all", [True, False])
@pytest.mark.parametrize("any_active", [True, False])
def test_exclusivity_switch_attributes_are_separated(
    production_env: Environment, reserved_all: bool, any_active: bool,
) -> None:
    """``checked`` and ``disabled`` must each survive as their own attribute.

    The failing combination was ``reserved_all=True, any_active=True`` — a
    held reservation on a selection with something running. Both attributes
    were lost, so the switch rendered off *and* enabled while the panel's own
    footer still said "reserved on 2 nodes": pressing it re-acquired instead
    of releasing, and the reservation could not be released from the page.
    """
    html = _render(production_env, reserved_all=reserved_all,
                   any_active=any_active)
    attrs = _attributes_of(html, "privacy-exclusivity")

    assert ("checked" in attrs) is reserved_all, (
        f"checked={'expected' if reserved_all else 'unexpected'}; "
        f"parsed attributes were {sorted(attrs)}"
    )
    assert ("disabled" in attrs) is any_active, (
        f"disabled={'expected' if any_active else 'unexpected'}; "
        f"parsed attributes were {sorted(attrs)}"
    )
    # The specific corruption: neither name may be merged into another.
    for name in attrs:
        assert name in {
            "type", "class", "id", "data-role", "checked", "disabled",
        }, f"unexpected attribute {name!r} — attributes were glued together"


def test_reserved_and_locked_render_both_attributes(
    production_env: Environment,
) -> None:
    """The regression itself, stated as one explicit assertion.

    Kept separate from the parametrised case so a failure names the scenario
    rather than a combination of parameter ids.
    """
    html = _render(production_env, reserved_all=True, any_active=True)
    attrs = _attributes_of(html, "privacy-exclusivity")
    assert "checked" in attrs and "disabled" in attrs
    assert "checkeddisabled" not in attrs


def test_footer_and_switch_agree_about_the_reservation(
    production_env: Environment,
) -> None:
    """The panel must not contradict its own switch.

    ``is-reserved``, the footer note and ``checked`` all derive from the same
    ``reserved_all``, so any render where the text says "reserved" while the
    switch reads off is a rendering fault rather than a state disagreement.
    """
    for any_active in (True, False):
        html = _render(production_env, reserved_all=True,
                       any_active=any_active)
        attrs = _attributes_of(html, "privacy-exclusivity")
        assert "is-reserved" in html
        assert "reserved on 2 nodes" in html
        assert "checked" in attrs, (
            f"panel claims a held reservation but the switch reads off "
            f"(any_active={any_active})"
        )
