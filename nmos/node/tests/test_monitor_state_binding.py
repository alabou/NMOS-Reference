# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The published ``monitor_state`` attribute names are the IS-04 binding's.

"NMOS With Status Reporting.md" fixes these names, and they are the contract a
third-party controller reads. The status message was published as
``overall_status_message`` while the binding calls it ``overall_message`` — in
the prose ("It MAY have an ``overall_message`` attribute"), in the
BCP-008 property mapping table (overallStatusMessage → overall_message) and in
the specification's own example JSON. A controller implementing the published
binding therefore found no message at all, losing exactly the diagnostic text
BCP-008 recommends populating.

The names are asserted against the specification document itself rather than
against a hard-coded list, so the test tracks the spec if it is revised.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nmos.json.engine import JsonEngine
from nmos.types.generated.nmonitor_state import NMonitorState

_SPEC = (Path(__file__).resolve().parents[3]
         / "specs" / "NMOS With Status Reporting.md")


def _published_keys() -> set[str]:
    """Every JSON key the encoder emits for a fully-populated monitor_state."""
    state = NMonitorState()
    state.set_to_default()
    value = state.value
    value.MonitorOverallStatus.value = 1
    value.MonitorOverallStatusMessage.value = "receiver socket timeout"
    value.MonitorLinkStatus.value = 1
    value.MonitorConnectionStatus.value = 1
    value.MonitorStreamStatus.value = 1
    value.MonitorSynchronizationStatus.value = 0
    encoded = JsonEngine().encode(state, None)
    return set(re.findall(r'"([a-z_]+)"\s*:', encoded))


def test_status_message_uses_the_specified_attribute_name() -> None:
    keys = _published_keys()
    assert "overall_message" in keys, (
        f"the IS-04 binding names this attribute 'overall_message'; "
        f"published keys were {sorted(keys)}"
    )
    assert "overall_status_message" not in keys, (
        "'overall_status_message' is not an attribute name the binding defines"
    )


@pytest.mark.skipif(not _SPEC.exists(), reason="spec document not present")
def test_the_name_matches_the_specification_document() -> None:
    """Read it out of the spec rather than trusting this test's own memory."""
    spec = _SPEC.read_text()
    assert "`overall_message`" in spec
    assert "| overallStatusMessage | overall_message |" in spec
    # And the name must not appear in the document in the old spelling.
    assert "overall_status_message" not in spec

    keys = _published_keys()
    assert "overall_message" in keys


def test_status_and_counter_attribute_names_are_unchanged() -> None:
    """Guard against a rename sweeping up the neighbouring attributes.

    These were already correct; the fix touched one key and must not have
    disturbed the rest of the binding.
    """
    keys = _published_keys()
    for expected in ("overall_status", "link_status", "connection_status",
                     "stream_status", "synchronization_status"):
        assert expected in keys, f"{expected} missing from {sorted(keys)}"
