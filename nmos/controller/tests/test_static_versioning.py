# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The static-asset version must be one number, not two that drift apart.

Two things carry the controller's front-end version, and they answer different
questions:

* the ``?v=`` on each ``/controller/static/`` reference in ``base.html`` decides
  which file the **browser fetches** — without a bump, a returning browser keeps
  serving the previous JavaScript against freshly rendered markup; and
* ``CONTROLLER_JS_VERSION`` inside ``controller.js`` is the only way to observe
  which file it actually **ran** — it is logged to the console and exposed as
  ``window.controller.version`` for anything that needs to report it.

They are useful only while they agree. They started equal at 41, were then
bumped independently, and the constant stalled at 43 while the cache-bust
reached 60 — so for eighteen versions the reported version named JavaScript
that had not been served in months. Nothing failed, which is exactly why it
went unnoticed; these tests are the missing alarm.
"""

from __future__ import annotations

import re
from pathlib import Path

_CONTROLLER_DIR = Path(__file__).resolve().parents[1]
_BASE_HTML = _CONTROLLER_DIR / "templates" / "base.html"
_CONTROLLER_JS = _CONTROLLER_DIR / "static" / "controller.js"

#: Every versioned local static reference in the page head/footer.
_ASSET_REF = re.compile(r'/controller/static/(\S+?)\?v=(\d+)')

_JS_VERSION = re.compile(r'CONTROLLER_JS_VERSION\s*=\s*"(\d+)"')


def _asset_versions() -> dict[str, str]:
    return {
        path: version
        for path, version in _ASSET_REF.findall(_BASE_HTML.read_text())
    }


def _js_version() -> str:
    match = _JS_VERSION.search(_CONTROLLER_JS.read_text())
    assert match is not None, "CONTROLLER_JS_VERSION not found in controller.js"
    return match.group(1)


def test_every_static_reference_is_versioned() -> None:
    # An unversioned reference is cached indefinitely by the browser and can
    # never be invalidated by a bump, so it must not exist at all.
    refs = re.findall(r'/controller/static/(\S+?)["\'?]', _BASE_HTML.read_text())
    versioned = set(_asset_versions())
    unversioned = sorted(set(refs) - versioned)
    assert not unversioned, f"static refs missing ?v=: {unversioned}"


def test_all_static_assets_share_one_version() -> None:
    # base.html has always bumped its references in lockstep. Keeping that
    # true is what lets a single number identify the whole front end.
    versions = set(_asset_versions().values())
    assert len(versions) == 1, f"mixed ?v= values in base.html: {versions}"


def test_js_constant_matches_the_cache_bust() -> None:
    # The regression this whole module exists for.
    assert _js_version() == next(iter(_asset_versions().values()))
