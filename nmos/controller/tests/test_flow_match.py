# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for nmos.controller.flow_match.

These exercise the controller-side reuse of the node converter
``get_flow_to_caps`` (via a shim node) and the most-specific constraint-set
matching rule. MatroxCCF is required; the whole module skips without it,
mirroring how ``flow_match`` itself degrades to "no match".
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("caps.MatroxCCF")

from nmos.controller.flow_match import (  # noqa: E402
    FlowMatch,
    flow_caps_from_json,
    flow_match_for_sender,
)

# Valid IS-04 resource ids (UUID v4 variant — required by the id validator).
_FLOW_ID = "11111111-1111-4111-8111-111111111111"
_SOURCE_ID = "22222222-2222-4222-8222-222222222222"
_DEVICE_ID = "33333333-3333-4333-8333-333333333333"


def _audio_source() -> dict[str, Any]:
    """A stereo audio source, as the registry publishes it (carries the
    Matrox ``synchronous_media`` extension + ``clock_name`` the transport
    caps are derived from)."""
    return {
        "id": _SOURCE_ID,
        "version": "0:0",
        "label": "src",
        "description": "",
        "tags": {},
        "device_id": _DEVICE_ID,
        "parents": [],
        "caps": {},
        "format": "urn:x-nmos:format:audio",
        "clock_name": "clk0",
        "urn:x-matrox:synchronous_media": True,
        "channels": [
            {"label": "L", "symbol": "L"},
            {"label": "R", "symbol": "R"},
        ],
    }


def _am824_flow() -> dict[str, Any]:
    return {
        "id": _FLOW_ID,
        "version": "0:0",
        "label": "am824",
        "description": "",
        "tags": {},
        "source_id": _SOURCE_ID,
        "device_id": _DEVICE_ID,
        "parents": [],
        "format": "urn:x-nmos:format:audio",
        "media_type": "audio/AM824",
        "sample_rate": {"numerator": 48000, "denominator": 1},
        "grain_rate": {"numerator": 48000, "denominator": 1},
    }


def _l24_flow() -> dict[str, Any]:
    flow = _am824_flow()
    flow["media_type"] = "audio/L24"
    flow["bit_depth"] = 24
    return flow


def _video_source() -> dict[str, Any]:
    src = _audio_source()
    src["format"] = "urn:x-nmos:format:video"
    src.pop("channels")
    return src


def _video_raw_flow() -> dict[str, Any]:
    """1080p50 YCbCr-4:2:2 10-bit raw video (4:2:2 ⇒ luma width is twice
    the chroma width, equal heights)."""
    return {
        "id": _FLOW_ID,
        "version": "0:0",
        "label": "v",
        "description": "",
        "tags": {},
        "source_id": _SOURCE_ID,
        "device_id": _DEVICE_ID,
        "parents": [],
        "format": "urn:x-nmos:format:video",
        "media_type": "video/raw",
        "grain_rate": {"numerator": 50, "denominator": 1},
        "frame_width": 1920,
        "frame_height": 1080,
        "interlace_mode": "progressive",
        "colorspace": "BT709",
        "transfer_characteristic": "SDR",
        "components": [
            {"name": "Y", "width": 1920, "height": 1080, "bit_depth": 10},
            {"name": "Cb", "width": 960, "height": 1080, "bit_depth": 10},
            {"name": "Cr", "width": 960, "height": 1080, "bit_depth": 10},
        ],
    }


# ---------------------------------------------------------------------------
# flow_caps_from_json
# ---------------------------------------------------------------------------


class TestFlowCapsFromJson:
    def test_video_color_sampling_derived_from_components(self) -> None:
        caps = flow_caps_from_json(_video_raw_flow(), _video_source())
        assert caps is not None
        assert list(caps.caps[
            "urn:x-nmos:cap:format:color_sampling"
        ].value.values) == ["YCbCr-4:2:2"]
        # component_depth also derives from the components array.
        assert list(caps.caps[
            "urn:x-nmos:cap:format:component_depth"
        ].value.values) == [10]

    def test_audio_am824_channel_count_from_source(self) -> None:
        # AM824 must decode as CODED audio (Go parity) — a previous codegen
        # defect routed it to raw, which requires bit_depth and broke this.
        caps = flow_caps_from_json(_am824_flow(), _audio_source())
        assert caps is not None
        assert list(caps.caps[
            "urn:x-nmos:cap:format:media_type"
        ].value.values) == ["audio/AM824"]
        # channel_count comes from len(source.channels), NOT the flow.
        assert list(caps.caps[
            "urn:x-nmos:cap:format:channel_count"
        ].value.values) == [2]

    def test_audio_channel_count_tracks_source(self) -> None:
        src = _audio_source()
        src["channels"] = [
            {"label": f"c{i}", "symbol": "L"} for i in range(8)
        ]
        caps = flow_caps_from_json(_am824_flow(), src)
        assert caps is not None
        assert list(caps.caps[
            "urn:x-nmos:cap:format:channel_count"
        ].value.values) == [8]

    def test_audio_l24_raw_channel_count_from_source(self) -> None:
        caps = flow_caps_from_json(_l24_flow(), _audio_source())
        assert caps is not None
        assert list(caps.caps[
            "urn:x-nmos:cap:format:media_type"
        ].value.values) == ["audio/L24"]
        assert list(caps.caps[
            "urn:x-nmos:cap:format:channel_count"
        ].value.values) == [2]

    def test_audio_without_source_returns_none(self) -> None:
        # Audio channel_count is derived from the source — without it the
        # node converter asserts, and we degrade to None (no green).
        assert flow_caps_from_json(_am824_flow(), None) is None

    def test_non_dict_flow_returns_none(self) -> None:
        assert flow_caps_from_json(None, None) is None
        assert flow_caps_from_json("nope", None) is None
        assert flow_caps_from_json([], None) is None

    def test_garbage_flow_returns_none(self) -> None:
        assert flow_caps_from_json({"id": "not-a-uuid"}, None) is None


# ---------------------------------------------------------------------------
# flow_match_for_sender
# ---------------------------------------------------------------------------


# Native trunk senders author their constraint sets WITHOUT cap:meta:format
# / cap:meta:layer (part-less, like config10's "Native AM824"); only mux
# sub-layer CS carry a part. A part-less CS matches the part-less flow
# conset that get_flow_to_caps produces (CCF ``is_same_part`` gates on
# format/layer).
def _cs_native_am824() -> dict[str, Any]:
    return {
        "urn:x-nmos:cap:meta:label": "Native AM824",
        "urn:x-nmos:cap:meta:preference": 100,
        "urn:x-nmos:cap:format:media_type": {"enum": ["audio/AM824"]},
        "urn:x-nmos:cap:format:channel_count": {"enum": [2]},
        "urn:x-nmos:cap:format:sample_rate": {
            "enum": [{"numerator": 48000, "denominator": 1}]
        },
    }


def _cs_generic_audio() -> dict[str, Any]:
    return {
        "urn:x-nmos:cap:meta:label": "Generic Audio",
        "urn:x-nmos:cap:meta:preference": 0,
        "urn:x-nmos:cap:format:media_type": {"enum": ["audio/AM824", "audio/L24"]},
        "urn:x-nmos:cap:format:channel_count": {"enum": [1, 2, 4, 8]},
    }


class TestFlowMatchForSender:
    def test_single_matching_cs(self) -> None:
        match = flow_match_for_sender(
            _am824_flow(), _audio_source(), [_cs_native_am824()],
        )
        assert match.matched_cs_indices == (0,)

    def test_most_specific_cs_wins(self) -> None:
        # Both CS include the AM824/2ch/48k flow; the native (pref 100)
        # is more specific than the generic (pref 0) and must win.
        sets = [_cs_generic_audio(), _cs_native_am824()]
        match = flow_match_for_sender(_am824_flow(), _audio_source(), sets)
        # Native is index 1 in this list.
        assert match.matched_cs_indices == (1,)

    def test_no_match_returns_none(self) -> None:
        # 4-channel-only CS cannot include a 2-channel flow.
        cs = _cs_native_am824()
        cs["urn:x-nmos:cap:format:channel_count"] = {"enum": [4]}
        match = flow_match_for_sender(_am824_flow(), _audio_source(), [cs])
        assert match.matched_cs_indices == ()

    def test_match_hinges_on_source_channel_count(self) -> None:
        # The CS pins channel_count to 8; the flow only matches when the
        # source actually has 8 channels (proving the count is sourced
        # from the source, not the flow).
        cs = _cs_native_am824()
        cs["urn:x-nmos:cap:format:channel_count"] = {"enum": [8]}

        stereo = _audio_source()
        assert flow_match_for_sender(
            _am824_flow(), stereo, [cs],
        ).matched_cs_indices == ()

        eight = _audio_source()
        eight["channels"] = [
            {"label": f"c{i}", "symbol": "L"} for i in range(8)
        ]
        assert flow_match_for_sender(
            _am824_flow(), eight, [cs],
        ).matched_cs_indices == (0,)

    def test_matched_values_populated_from_flow_capset(self) -> None:
        match = flow_match_for_sender(
            _am824_flow(), _audio_source(), [_cs_native_am824()],
        )
        assert match.matched_cs_indices == (0,)
        assert match.values_by_part["trunk"][
            "urn:x-nmos:cap:format:media_type"
        ] == "audio/AM824"
        assert match.values_by_part["trunk"][
            "urn:x-nmos:cap:format:channel_count"
        ] == 2

    def test_empty_or_missing_inputs(self) -> None:
        assert flow_match_for_sender(None, None, None) == FlowMatch()
        assert flow_match_for_sender(_am824_flow(), _audio_source(), []) == FlowMatch()
        assert flow_match_for_sender(None, None, [_cs_native_am824()]) == FlowMatch()


# ---------------------------------------------------------------------------
# Mux: per-part matching (trunk + sub-layers)
# ---------------------------------------------------------------------------

_MUX_FLOW = "11111111-0000-4000-8000-00000000aaaa"
_SUB_A = "22222222-0000-4000-8000-00000000aaaa"
_SUB_B = "33333333-0000-4000-8000-00000000aaaa"
_MUX_SRC = "44444444-0000-4000-8000-00000000aaaa"
_SRC_A = "55555555-0000-4000-8000-00000000aaaa"
_SRC_B = "66666666-0000-4000-8000-00000000aaaa"
_DEV = "77777777-0000-4000-8000-00000000aaaa"


def _src(sid: str, fmt: str, channels: int = 0) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": sid, "version": "0:0", "label": "s", "description": "",
        "tags": {}, "device_id": _DEV, "parents": [], "caps": {},
        "format": fmt, "clock_name": "clk0",
        "urn:x-matrox:synchronous_media": True,
    }
    if channels:
        d["channels"] = [
            {"label": f"c{i}", "symbol": "L"} for i in range(channels)
        ]
    return d


def _mux_flow() -> dict[str, Any]:
    return {
        "id": _MUX_FLOW, "version": "0:0", "label": "mux", "description": "",
        "tags": {}, "device_id": _DEV, "parents": [_SUB_A, _SUB_B],
        "source_id": _MUX_SRC, "format": "urn:x-nmos:format:mux",
        "media_type": "application/AM824",
        "urn:x-matrox:video_layers": 0,
        "urn:x-matrox:audio_layers": 2,
        "urn:x-matrox:data_layers": 0,
    }


def _sub_flow(fid: str, src_id: str, layer: int) -> dict[str, Any]:
    return {
        "id": fid, "version": "0:0", "label": "sub", "description": "",
        "tags": {}, "device_id": _DEV, "parents": [], "source_id": src_id,
        "format": "urn:x-nmos:format:audio", "media_type": "audio/AM824",
        "sample_rate": {"numerator": 48000, "denominator": 1},
        "grain_rate": {"numerator": 48000, "denominator": 1},
        "urn:x-matrox:layer": layer,
    }


def _sub_layer_cs(label, pref, layer, media, channels):
    return {
        "urn:x-nmos:cap:meta:label": label,
        "urn:x-nmos:cap:meta:preference": pref,
        "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
        "urn:x-matrox:cap:meta:layer": layer,
        "urn:x-nmos:cap:format:media_type": {"enum": media},
        "urn:x-nmos:cap:format:channel_count": {"enum": channels},
    }


def _mux_constraint_sets():
    return [
        {  # 0 — trunk, native
            "urn:x-nmos:cap:meta:label": "Native Mux",
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["application/AM824"]},
        },
        {  # 1 — trunk, generic
            "urn:x-nmos:cap:meta:label": "Mux constraints",
            "urn:x-nmos:cap:meta:preference": 1,
            "urn:x-nmos:cap:format:media_type": {"enum": ["application/AM824"]},
        },
        # 2 — audio sub 0, native (matches)
        _sub_layer_cs("Native Audio sub 0", 100, 0, ["audio/AM824"], [2]),
        # 3 — audio sub 0, PCM (does NOT match an AM824 sub-flow)
        _sub_layer_cs("PCM sub 0", 0, 0, ["audio/L24"], [2]),
        # 4 — audio sub 1, native (matches)
        _sub_layer_cs("Native Audio sub 1", 100, 1, ["audio/AM824"], [2]),
    ]


class _StubCache:
    """Minimal duck-typed cache for the mux parent chase — only the readers
    ``flow_match`` uses (``get_flow`` / ``get_source``)."""

    def __init__(self) -> None:
        self._flows: dict[str, Any] = {}
        self._sources: dict[str, Any] = {}

    def get_flow(self, fid):
        return self._flows.get(fid)

    def get_source(self, sid):
        return self._sources.get(sid)


def _mux_cache() -> _StubCache:
    c = _StubCache()
    c._sources[_MUX_SRC] = _src(_MUX_SRC, "urn:x-nmos:format:mux")
    c._sources[_SRC_A] = _src(_SRC_A, "urn:x-nmos:format:audio", channels=2)
    c._sources[_SRC_B] = _src(_SRC_B, "urn:x-nmos:format:audio", channels=2)
    c._flows[_SUB_A] = _sub_flow(_SUB_A, _SRC_A, 0)
    c._flows[_SUB_B] = _sub_flow(_SUB_B, _SRC_B, 1)
    c._flows[_MUX_FLOW] = _mux_flow()
    return c


class TestMuxFlowMatch:
    def test_trunk_and_sublayers_all_match(self) -> None:
        """A mux greens its most-specific trunk CS AND the most-specific
        sub-layer CS per (format, layer) — chasing parent sub-flows +
        sub-sources via the cache."""
        cache = _mux_cache()
        m = flow_match_for_sender(
            _mux_flow(), _src(_MUX_SRC, "urn:x-nmos:format:mux"),
            _mux_constraint_sets(), cache=cache,
        )
        # Native Mux (0) + Native Audio sub 0 (2) + Native Audio sub 1 (4).
        # PCM sub 0 (3) does not match an AM824 sub-flow.
        assert m.matched_cs_indices == (0, 2, 4)
        assert set(m.values_by_part) == {
            "trunk",
            "urn:x-nmos:format:audio:0",
            "urn:x-nmos:format:audio:1",
        }
        # Per-part operating values are distinct: trunk is the mux media_type,
        # each sub-layer is its sub-flow's.
        assert m.values_by_part["trunk"][
            "urn:x-nmos:cap:format:media_type"
        ] == "application/AM824"
        assert m.values_by_part["urn:x-nmos:format:audio:0"][
            "urn:x-nmos:cap:format:channel_count"
        ] == 2

    def test_without_cache_only_trunk_matches(self) -> None:
        """No cache → no parent chase → only the trunk CS can match."""
        m = flow_match_for_sender(
            _mux_flow(), _src(_MUX_SRC, "urn:x-nmos:format:mux"),
            _mux_constraint_sets(), cache=None,
        )
        assert m.matched_cs_indices == (0,)  # Native Mux only
        assert set(m.values_by_part) == {"trunk"}

    def test_sublayer_no_match_drops_that_part(self) -> None:
        """If a sub-flow fits no sub-layer CS of its part, that part simply
        contributes no green (others unaffected)."""
        cache = _mux_cache()
        # Sub-flow B becomes 8-channel; no layer-1 CS allows 8ch.
        cache._sources[_SRC_B] = _src(_SRC_B, "urn:x-nmos:format:audio", channels=8)
        m = flow_match_for_sender(
            _mux_flow(), _src(_MUX_SRC, "urn:x-nmos:format:mux"),
            _mux_constraint_sets(), cache=cache,
        )
        # Trunk (0) + sub 0 (2) still match; layer-1 drops out.
        assert m.matched_cs_indices == (0, 2)
