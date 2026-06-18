# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""A flow and its associated source must be initialised with the SAME
grain_rate.

They describe the same temporal stream, so they must not be derived from
independent inputs. The source's grain_rate comes from the operating-point
capset (one concrete value); a flow left to itself samples its OWN
grain_rate capability via ``next(iter(rv.enumerated))`` — a non-deterministic
set iteration that can land on an unrelated rate (the observed regression: a
``video/raw`` flow at 30000/1001 while its source was 60/1). The build path
now copies the source's grain_rate onto every flow it owns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nmos.node import Node, _get_flow_core, _get_source_core
from nmos.node.config import ConfigBuilder

_BUILTIN = Path(__file__).parent.parent / "config" / "builtin"


def _rational(field) -> tuple[int, int] | None:
    if not field.defined:
        return None
    return (field.value.Numerator.value, field.value.Denominator.value)


def _grain_rate(core) -> tuple[int, int] | None:
    return _rational(core.GrainRate)


def _build_node(config_name: str) -> Node:
    node = Node()
    node.init(serial_number="TST00001")
    config = json.loads((_BUILTIN / config_name).read_text())
    builder = ConfigBuilder(node, verbose=False)
    for sender_cfg in config.get("senders", []):
        builder._build_sender_pipeline(sender_cfg)
    return node


# config10 is the interesting case: its video sender yields BOTH a coded
# (H.264) and a raw (video/raw) flow off one source, and the raw capset
# offers many grain rates — the exact shape that produced the mismatch.
@pytest.mark.parametrize("config_name", ["config10.json"])
def test_every_flow_matches_its_source_grain_rate(config_name: str) -> None:
    node = _build_node(config_name)

    src_gr: dict[str, tuple[int, int] | None] = {}
    for _sid, sptr in node.sources:
        sc = _get_source_core(sptr)
        src_gr[sc.ResourceCore.Id.value] = _grain_rate(sc)

    flows_seen = 0
    audio_flows_seen = 0
    for _fid, fptr in node.flows:
        fc = _get_flow_core(fptr)
        if not fc.SourceId.defined:
            continue
        flows_seen += 1
        flow_gr = _grain_rate(fc)
        source_gr = src_gr.get(fc.SourceId.value)
        assert flow_gr == source_gr, (
            f"flow {fc.ResourceCore.Id.value} grain_rate {flow_gr} != its "
            f"source {fc.SourceId.value} grain_rate {source_gr}"
        )
        # Audio flows carry a sample_rate (the field that matters for audio);
        # it must equal the source's grain_rate too — not be sampled
        # independently from the flow's own caps.
        inner = fptr.get()
        if hasattr(inner, "SampleRate"):
            audio_flows_seen += 1
            flow_sr = _rational(inner.SampleRate)
            assert flow_sr == source_gr, (
                f"audio flow {fc.ResourceCore.Id.value} sample_rate {flow_sr} "
                f"!= its source grain_rate {source_gr}"
            )

    assert flows_seen > 0, "expected at least one flow built from the config"
    assert audio_flows_seen > 0, "expected at least one audio flow (sample_rate path)"


def test_raw_video_flow_inherits_source_rate_not_template_default() -> None:
    """Pin the specific regression: config10's video sender yields two flows
    (coded + raw) off one source; the raw one used to sample 30000/1001 from
    its own multi-value cap. Both must now equal the source's 60/1, and the
    rogue NTSC value must not appear on any flow."""
    node = _build_node("config10.json")

    all_rates: list[tuple[int, int] | None] = []
    flows_by_source: dict[str, list[tuple[int, int] | None]] = {}
    for _fid, fptr in node.flows:
        fc = _get_flow_core(fptr)
        rate = _grain_rate(fc)
        all_rates.append(rate)
        if fc.SourceId.defined:
            flows_by_source.setdefault(fc.SourceId.value, []).append(rate)

    # The rogue value sampled from the multi-value grain_rate cap is gone.
    assert (30000, 1001) not in all_rates, all_rates

    # The video source carries BOTH the coded and raw flow; both must match.
    multi_flow_sources = [rs for rs in flows_by_source.values() if len(rs) >= 2]
    assert multi_flow_sources, "expected the video source to own coded+raw flows"
    for rates in multi_flow_sources:
        assert all(r == rates[0] for r in rates), rates
        assert rates[0] == (60, 1), rates
