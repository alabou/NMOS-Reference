# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NDI compliance tests per specs/NMOS With NDI.md.

Tests cover:
  - Transport enum value
  - IS-04 Receiver attributes (format, transport, media_types=application/ndi)
  - Sub-flow media type rules (F9-F13):
      uncompressed: video/raw, audio/L16|L20|L24
      compressed: video/H264|H265, audio/mpeg4-generic
  - IS-05 NDI transport parameters (source_name, machine_name, source_ip, source_port)
  - NDI activation rules:
      manifest_href MUST be null (TX3) — xfail, known shared gap
      source_name character set [a-zA-Z0-9_] (TP4)
      machine_name + source_name non-null at master_enable=true (TP6) — xfail
      receiver_id null on NDI sender activation (TP2) — xfail
  - Transport descriptor: has_sdp=False, has_privacy=False, resolve_noop

Configs used:
  - config6: RTP senders + NDI receiver (video/H264, audio/L24)
  - config6a: RTP senders + NDI receiver (video/H265, audio/L24)

Existing tests this file complements (does NOT duplicate):
  - nmos/node/tests/test_activation.py: NDI transport descriptor, init, source_name
    derivation, machine_name setup (already covers TP4 char-set rule via init).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


from nmos.enums import TransportNdi, FormatMux
from nmos.node import Node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUILTIN_DIR = Path(__file__).parent.parent / "config" / "builtin"
NDI_TRANSPORT = "urn:x-matrox:transport:ndi"
NDI_MEDIA_TYPE = "application/ndi"


def _make_node(serial: str = "NDITST") -> Node:
    node = Node()
    node.init(serial_number=serial)
    return node


def _build_config(node: Node, config_name: str) -> None:
    from nmos.node.config import ConfigBuilder
    config_path = BUILTIN_DIR / f"{config_name}.json"
    if not config_path.exists():
        pytest.skip(f"{config_name}.json not found")
    with open(config_path) as f:
        config = json.load(f)
    builder = ConfigBuilder(node, verbose=False)
    for r in config.get("receivers", []):
        try:
            builder._build_receiver_from_config(r)
        except Exception:
            pass
    for s in config.get("senders", []):
        try:
            builder._build_sender_pipeline(s)
        except Exception:
            pass


def _find_ndi_receiver(node: Node) -> tuple[str, object] | None:
    from nmos.node import _get_receiver_core
    for _sid, r in node.receivers:
        rc = _get_receiver_core(r)
        tr = str(rc.Transport.value) if rc.Transport.defined else ""
        if tr == NDI_TRANSPORT:
            return rc.ResourceCore.Id.value, r
    return None


def _receiver_inner(recv: object) -> object:
    return recv.get() if hasattr(recv, 'get') else recv


def _receiver_media_types(recv: object) -> list[str]:
    inner = _receiver_inner(recv)
    caps = inner.Caps
    if not caps.defined:
        return []
    return [str(mt) for mt in caps._value.MediaTypes._inner]


def _receiver_constraint_sets(recv: object) -> list[object]:
    inner = _receiver_inner(recv)
    caps = inner.Caps
    if not caps.defined:
        return []
    cs_arr = caps._value.ConstraintSets
    if not cs_arr.defined:
        return []
    return list(cs_arr.value)


# ===================================================================
# Class 1: TestNdiTransport — enum + config presence
# ===================================================================

class TestNdiTransport:
    """Verify NDI transport enum and config presence."""

    def test_transport_ndi_enum_value(self) -> None:
        """Transport enum MUST match spec URN."""
        assert str(TransportNdi) == NDI_TRANSPORT

    def test_config6_has_ndi_receiver(self) -> None:
        node = _make_node()
        _build_config(node, "config6")
        result = _find_ndi_receiver(node)
        assert result is not None, "config6 should have at least 1 NDI receiver"

    def test_config6a_has_ndi_receiver(self) -> None:
        node = _make_node()
        _build_config(node, "config6a")
        result = _find_ndi_receiver(node)
        assert result is not None, "config6a should have at least 1 NDI receiver"


# ===================================================================
# Class 2: TestNdiReceiverIs04 — §NDI IS-04 Receivers
# ===================================================================

class TestNdiReceiverIs04:
    """Verify NDI receiver IS-04 resource attributes per spec."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        _build_config(self.node, "config6")
        result = _find_ndi_receiver(self.node)
        assert result is not None
        self.receiver_id, self.receiver = result

    def test_receiver_format_is_mux(self) -> None:
        """RX2: NDI receiver MUST have format=urn:x-nmos:format:mux."""
        inner = _receiver_inner(self.receiver)
        assert str(inner.Format.value) == str(FormatMux)

    def test_receiver_transport_is_ndi(self) -> None:
        """RX1: NDI receiver MUST have transport=urn:x-matrox:transport:ndi."""
        from nmos.node import _get_receiver_core
        rc = _get_receiver_core(self.receiver)
        assert str(rc.Transport.value) == NDI_TRANSPORT

    def test_receiver_media_types_has_application_ndi(self) -> None:
        """RX3: NDI mux Receiver MUST list `application/ndi` in caps.media_types.
        (Spec §NDI IS-04 Receivers)."""
        mt_list = _receiver_media_types(self.receiver)
        assert NDI_MEDIA_TYPE in mt_list, \
            f"NDI receiver media_types MUST contain 'application/ndi', got {mt_list}"

    def test_receiver_has_constraint_sets(self) -> None:
        """NDI receiver MUST express capabilities via constraint_sets."""
        cs = _receiver_constraint_sets(self.receiver)
        assert len(cs) > 0, "NDI receiver must have constraint_sets"

    def test_receiver_caps_has_audio_video_data_layers(self) -> None:
        """F4: NDI receiver trunk constraint_set MUST have audio_layers,
        video_layers, data_layers."""
        # Parse config JSON directly — the caps layer constraints come from constraint_sets
        config_path = BUILTIN_DIR / "config6.json"
        with open(config_path) as f:
            config = json.load(f)
        found_all = False
        for r in config.get("receivers", []):
            if r.get("transport") != NDI_TRANSPORT:
                continue
            for cs in r.get("constraint_sets", []):
                if "urn:x-matrox:cap:meta:format" in cs:
                    continue  # sub-flow capset
                has_video = "urn:x-matrox:cap:format:video_layers" in cs
                has_audio = "urn:x-matrox:cap:format:audio_layers" in cs
                has_data = "urn:x-matrox:cap:format:data_layers" in cs
                if has_video and has_audio and has_data:
                    found_all = True
                    break
        assert found_all, \
            "NDI receiver trunk constraint_set MUST have all three _layers capabilities"


# ===================================================================
# Class 3: TestNdiSubflowMediaTypes — §F9-F13
# ===================================================================

class TestNdiSubflowMediaTypes:
    """Verify NDI sub-flow media types per §Uncompressed / §Compressed."""

    def _get_receiver_sub_flow_media_types(
        self, config_name: str, fmt_urn: str
    ) -> set[str]:
        """Load a config and return the set of media types used in sub-flow
        constraint_sets for the given format (video/audio/data) on the NDI receiver."""
        config_path = BUILTIN_DIR / f"{config_name}.json"
        if not config_path.exists():
            pytest.skip(f"{config_name}.json not found")
        with open(config_path) as f:
            config = json.load(f)
        media_types: set[str] = set()
        for r in config.get("receivers", []):
            if r.get("transport") != NDI_TRANSPORT:
                continue
            for cs in r.get("constraint_sets", []):
                if cs.get("urn:x-matrox:cap:meta:format") != fmt_urn:
                    continue
                mt = cs.get("urn:x-nmos:cap:format:media_type", {})
                for v in mt.get("enum", []):
                    media_types.add(v)
        return media_types

    def test_config6_supports_uncompressed_video_raw(self) -> None:
        """F10: Uncompressed video sub-flow media_type=video/raw."""
        mts = self._get_receiver_sub_flow_media_types(
            "config6", "urn:x-nmos:format:video"
        )
        assert "video/raw" in mts, \
            f"NDI receiver should support uncompressed video/raw, got {mts}"

    def test_config6_supports_uncompressed_audio(self) -> None:
        """F11: Uncompressed audio sub-flow ∈ {audio/L16, L20, L24}."""
        mts = self._get_receiver_sub_flow_media_types(
            "config6", "urn:x-nmos:format:audio"
        )
        pcm_types = {"audio/L16", "audio/L20", "audio/L24"}
        assert mts & pcm_types, \
            f"NDI receiver should support uncompressed PCM audio, got {mts}"

    def test_config6a_supports_compressed_video_h264(self) -> None:
        """F12: config6a NDI receiver supports compressed video/H264."""
        mts = self._get_receiver_sub_flow_media_types(
            "config6a", "urn:x-nmos:format:video"
        )
        assert "video/H264" in mts, \
            f"config6a NDI receiver should support video/H264, got {mts}"

    def test_config6a_supports_compressed_video_h265(self) -> None:
        """F12: config6a NDI receiver supports compressed video/H265."""
        mts = self._get_receiver_sub_flow_media_types(
            "config6a", "urn:x-nmos:format:video"
        )
        assert "video/H265" in mts, \
            f"config6a NDI receiver should support video/H265, got {mts}"

    def test_config6a_supports_compressed_audio_mpeg4_generic(self) -> None:
        """F13: config6a NDI receiver supports compressed audio/mpeg4-generic (AAC)."""
        mts = self._get_receiver_sub_flow_media_types(
            "config6a", "urn:x-nmos:format:audio"
        )
        assert "audio/mpeg4-generic" in mts, \
            f"config6a NDI receiver should support audio/mpeg4-generic, got {mts}"

    def test_compressed_video_media_types_are_h264_or_h265(self) -> None:
        """F12: Compressed video sub-flow MUST be video/H264 or video/H265.
        Verifies the rule by checking that ANY compressed video media type
        used in ANY NDI config is one of these two."""
        all_compressed: set[str] = set()
        for cfg_name in ("config6", "config6a"):
            mts = self._get_receiver_sub_flow_media_types(
                cfg_name, "urn:x-nmos:format:video"
            )
            # Compressed = anything starting with video/ but not video/raw
            for mt in mts:
                if mt.startswith("video/") and mt != "video/raw":
                    all_compressed.add(mt)
        assert all_compressed, \
            "Expected at least one NDI config to declare compressed video sub-flows"
        allowed = {"video/H264", "video/H265"}
        invalid = all_compressed - allowed
        assert not invalid, \
            f"NDI compressed video MUST be H264/H265 only, found: {invalid}"

    def test_compressed_audio_media_type_is_mpeg4_generic(self) -> None:
        """F13: Compressed audio sub-flow MUST be audio/mpeg4-generic."""
        all_compressed: set[str] = set()
        _PCM = {"audio/L8", "audio/L16", "audio/L20", "audio/L24"}
        for cfg_name in ("config6", "config6a"):
            mts = self._get_receiver_sub_flow_media_types(
                cfg_name, "urn:x-nmos:format:audio"
            )
            for mt in mts:
                if mt.startswith("audio/") and mt not in _PCM:
                    all_compressed.add(mt)
        assert all_compressed, \
            "Expected at least one NDI config to declare compressed audio sub-flows"
        allowed = {"audio/mpeg4-generic"}
        invalid = all_compressed - allowed
        assert not invalid, \
            f"NDI compressed audio MUST be audio/mpeg4-generic, found: {invalid}"

    def test_uncompressed_and_compressed_sub_flows_coexist_in_config6a(self) -> None:
        """F9: A sender MAY provide both uncompressed and compressed sub-flow options
        (via layer_compatibility_groups). Config6a exposes both via groups [0]=uncompressed
        and [1]=compressed."""
        config_path = BUILTIN_DIR / "config6a.json"
        with open(config_path) as f:
            config = json.load(f)
        groups_seen: set[int] = set()
        for r in config.get("receivers", []):
            if r.get("transport") != NDI_TRANSPORT:
                continue
            for cs in r.get("constraint_sets", []):
                if "urn:x-matrox:cap:meta:format" not in cs:
                    continue
                for g in cs.get("urn:x-matrox:cap:meta:layer_compatibility_groups", []):
                    groups_seen.add(g)
        assert 0 in groups_seen, "Expected uncompressed group 0 in config6a NDI receiver"
        assert 1 in groups_seen, "Expected compressed group 1 in config6a NDI receiver"


# ===================================================================
# Class 4: TestNdiSenderTransportParams — §NDI IS-05 sender fields (TP1)
# ===================================================================

class TestNdiSenderTransportParams:
    """Verify NDI Sender transport params type per spec §NDI IS-05."""

    def test_sender_params_has_source_ip(self) -> None:
        from nmos.types.generated.nndi_sender_transport_params import (
            NNdiSenderTransportParamsValue,
        )
        params = NNdiSenderTransportParamsValue()
        assert hasattr(params, 'SourceIp')

    def test_sender_params_has_source_port(self) -> None:
        from nmos.types.generated.nndi_sender_transport_params import (
            NNdiSenderTransportParamsValue,
        )
        params = NNdiSenderTransportParamsValue()
        assert hasattr(params, 'SourcePort')

    def test_sender_params_has_source_name(self) -> None:
        from nmos.types.generated.nndi_sender_transport_params import (
            NNdiSenderTransportParamsValue,
        )
        params = NNdiSenderTransportParamsValue()
        assert hasattr(params, 'SourceName')

    def test_sender_params_has_machine_name(self) -> None:
        from nmos.types.generated.nndi_sender_transport_params import (
            NNdiSenderTransportParamsValue,
        )
        params = NNdiSenderTransportParamsValue()
        assert hasattr(params, 'MachineName')

    def test_sender_params_has_no_source_url(self) -> None:
        """TP1: Senders MUST support all properties except `source_url`."""
        from nmos.types.generated.nndi_sender_transport_params import (
            NNdiSenderTransportParamsValue,
        )
        params = NNdiSenderTransportParamsValue()
        assert not hasattr(params, 'SourceUrl'), \
            "NDI sender params MUST NOT have source_url per spec"

    def test_sender_port_fn_returns_5960(self) -> None:
        """NDI sender port is fixed to 5960."""
        from nmos.node.activation import get_transport_descriptor
        desc = get_transport_descriptor(TransportNdi)
        assert desc.sender_port_fn(0) == 5960
        assert desc.sender_port_fn(5) == 5960  # index-independent


# ===================================================================
# Class 5: TestNdiReceiverTransportParams — §NDI IS-05 receiver fields (TP1)
# ===================================================================

class TestNdiReceiverTransportParams:
    """Verify NDI Receiver transport params type per spec §NDI IS-05."""

    def test_receiver_params_has_interface_ip(self) -> None:
        from nmos.types.generated.nndi_receiver_transport_params import (
            NNdiReceiverTransportParamsValue,
        )
        params = NNdiReceiverTransportParamsValue()
        assert hasattr(params, 'InterfaceIp')

    def test_receiver_params_has_source_ip(self) -> None:
        from nmos.types.generated.nndi_receiver_transport_params import (
            NNdiReceiverTransportParamsValue,
        )
        params = NNdiReceiverTransportParamsValue()
        assert hasattr(params, 'SourceIp')

    def test_receiver_params_has_source_port(self) -> None:
        from nmos.types.generated.nndi_receiver_transport_params import (
            NNdiReceiverTransportParamsValue,
        )
        params = NNdiReceiverTransportParamsValue()
        assert hasattr(params, 'SourcePort')

    def test_receiver_params_has_source_name(self) -> None:
        from nmos.types.generated.nndi_receiver_transport_params import (
            NNdiReceiverTransportParamsValue,
        )
        params = NNdiReceiverTransportParamsValue()
        assert hasattr(params, 'SourceName')

    def test_receiver_params_has_machine_name(self) -> None:
        from nmos.types.generated.nndi_receiver_transport_params import (
            NNdiReceiverTransportParamsValue,
        )
        params = NNdiReceiverTransportParamsValue()
        assert hasattr(params, 'MachineName')

    def test_receiver_params_has_no_source_url(self) -> None:
        """TP1: Receivers MUST support all properties except `source_url`."""
        from nmos.types.generated.nndi_receiver_transport_params import (
            NNdiReceiverTransportParamsValue,
        )
        params = NNdiReceiverTransportParamsValue()
        assert not hasattr(params, 'SourceUrl'), \
            "NDI receiver params MUST NOT have source_url per spec"

    def test_receiver_port_fn_returns_5960(self) -> None:
        """NDI receiver port is fixed to 5960."""
        from nmos.node.activation import get_transport_descriptor
        desc = get_transport_descriptor(TransportNdi)
        assert desc.receiver_port_fn(0) == 5960
        assert desc.receiver_port_fn(3) == 5960

    def test_receiver_staged_defaults(self) -> None:
        """Staged params of NDI receiver have expected defaults per _init_ndi_receiver_extra.
        SourcePort should be 5960 (fixed).
        """
        node = _make_node()
        _build_config(node, "config6")
        result = _find_ndi_receiver(node)
        assert result is not None
        rid, _ = result
        act = node.get_receiver_activation(rid)
        assert act is not None and act.staged
        staged = act.staged[0]
        assert staged.SourcePort.value == 5960


# ===================================================================
# Class 6: TestNdiTransportDescriptor — §Senders no SDP + transport properties
# ===================================================================

class TestNdiTransportDescriptor:
    """Verify NDI transport descriptor properties."""

    def test_ndi_transport_descriptor_has_no_sdp(self) -> None:
        """TX3 / §SDP format-specific parameters: manifest_href MUST be null.
        The transport descriptor has has_sdp=False which prevents SDP generation."""
        from nmos.node.activation import get_transport_descriptor
        desc = get_transport_descriptor(TransportNdi)
        assert desc.has_sdp is False, "NDI transport descriptor has_sdp MUST be False"

    def test_ndi_transport_descriptor_has_no_privacy(self) -> None:
        """NDI transport does not support PEP privacy (has_privacy=False)."""
        from nmos.node.activation import get_transport_descriptor
        desc = get_transport_descriptor(TransportNdi)
        assert desc.has_privacy is False

    def test_ndi_transport_descriptor_no_auto_resolver(self) -> None:
        """NDI uses resolve_noop — no 'auto' resolution (spec: no auto IPs/ports)."""
        from nmos.node.activation import get_transport_descriptor
        from nmos.node.activation_engine import resolve_noop
        desc = get_transport_descriptor(TransportNdi)
        assert desc.sender_auto_resolvers.get("flip_resolve") is resolve_noop
        assert desc.receiver_auto_resolvers.get("flip_resolve") is resolve_noop


# ===================================================================
# Class 7: TestNdiActivationRules — §Senders, TP2, TP4-TP6, TX3 (gaps)
# ===================================================================

class TestNdiActivationRules:
    """NDI activation-time requirements. Some are xfail where a known shared gap exists."""

    def test_source_name_derived_from_grouphint_uses_allowed_chars(self) -> None:
        """TP4: source_name MUST match [a-zA-Z0-9_]+.
        Verifies _init_ndi_sender_extra strips spaces and replaces colons with _.
        """
        import re
        from nmos.node.activation import get_transport_descriptor
        from nmos.enums import TransportNdi
        from nmos.node.tests.test_activation import _make_activation, _make_legs  # type: ignore
        from nmos.node.activation import init_sender_activation

        desc = get_transport_descriptor(TransportNdi)
        activation = _make_activation(desc)
        legs = _make_legs()
        init_sender_activation(
            activation, legs, TransportNdi, desc,
            group_hint="NDI 3:VIDEO 0",
        )
        staged = activation.staged[0]
        source_name = staged.SourceName.value
        assert source_name, "SourceName must be set"
        # Check it matches the allowed character class (TP4)
        assert re.fullmatch(r"[A-Za-z0-9_]+", source_name), \
            f"source_name '{source_name}' must match [A-Za-z0-9_]+"

    def test_machine_name_derived_from_sender_name(self) -> None:
        """TP6 (partial): machine_name set from sender_name (serial)."""
        from nmos.node.activation import get_transport_descriptor, init_sender_activation
        from nmos.enums import TransportNdi
        from nmos.node.tests.test_activation import _make_activation, _make_legs  # type: ignore

        desc = get_transport_descriptor(TransportNdi)
        activation = _make_activation(desc)
        activation.sender_name = "SNX12345"
        legs = _make_legs()
        init_sender_activation(activation, legs, TransportNdi, desc, group_hint="")
        staged = activation.staged[0]
        assert staged.MachineName.value == "SNX12345"

    @pytest.mark.xfail(
        reason="manifest_href is currently set for NDI senders at activation (no NDI exception in the activation path). "
        "Spec says MUST be null. Known shared gap."
    )
    def test_ndi_sender_manifest_href_is_null_when_active(self) -> None:
        """TX3: NDI sender manifest_href MUST be null (no SDP transport file).
        Current behavior: the activation path unconditionally sets manifest_href to the
        transportfile URL for ALL senders on activation, including NDI. This is a
        divergence from spec. Test kept as xfail to document the gap.
        """
        # This would require an actual activation call; skip implementation
        # details and just mark as xfail per the shared-gap documentation.
        pytest.fail("xfail placeholder — manifest_href null enforcement not implemented")

    @pytest.mark.xfail(
        reason="NDI sender activation is not special-cased to force receiver_id=null. "
        "Spec says MUST be null (1-to-N semantic). Known shared gap."
    )
    def test_ndi_sender_activation_receiver_id_is_null(self) -> None:
        """TP2: NDI Sender activation receiver_id MUST be null (one-to-many transport).
        Known shared gap — this is not enforced specifically for NDI.
        """
        pytest.fail("xfail placeholder — NDI receiver_id null enforcement not implemented")
