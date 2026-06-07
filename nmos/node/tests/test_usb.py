# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NMOS With USB compliance tests (specs/NMOS With USB.md).

Covers:
  - IS-04 Source/Flow/Sender/Receiver resource attributes for USB (F1, FL1-FL2, S1-S4, R1-R4)
  - SDP manifest for USB TCP control endpoint (SD1-SD4)
  - IS-05 transport parameters (IS1-IS4)
  - Privacy encryption — descriptor, params, SDP (E1-E2)

Configs used:
  - config8u: proper USB data pipeline (rewritten in Part 1 of test-usb-1
    to match the canonical config8u definition). Pre-rewrite the config
    contained a broken MPEG2-TS-on-USB pipeline contradicting spec MUST
    requirements.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Add caps/ to path
_NMOS_ROOT = Path(__file__).parent.parent.parent.parent

try:
    import caps.MatroxCCF as MatroxCCF  # noqa: F401  # type: ignore[import-not-found]
    HAS_CCF = True
except ImportError:
    HAS_CCF = False

from nmos import enums
from nmos.node import Node, _generate_sdp_from_params
from nmos.node.activation import (
    get_transport_descriptor,
    init_receiver_activation,
    init_sender_activation,
)
from nmos.node.types import MAX_LEGS, Activation, Leg


BUILTIN_DIR = Path(__file__).parent.parent / "config" / "builtin"
CONFIG8U = BUILTIN_DIR / "config8u.json"

# Both URN variants accepted — USB_TRANSPORT_NAMESPACE is configurable
_USB_TRANSPORT_URNS = {
    "urn:x-nmos:transport:usb",
    "urn:x-matrox:transport:usb",
}

_USB_CAP_CLASS_URNS = {
    "urn:x-nmos:cap:transport:usb_class",
    "urn:x-matrox:cap:transport:usb_class",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_node(serial: str = "USBTST") -> Node:
    node = Node()
    node.init(serial_number=serial)
    return node


def _build_config8u(node: Node) -> None:
    from nmos.node.config import ConfigBuilder
    with open(CONFIG8U) as f:
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


def _find_usb_sender(node: Node) -> Any:
    for _sid, s in node.senders:
        tr = str(s.Transport.value) if s.Transport.defined else ""
        if tr in _USB_TRANSPORT_URNS:
            return s
    return None


def _find_usb_receiver(node: Node) -> Any:
    for _sid, r in node.receivers:
        inner = r.get() if hasattr(r, 'get') else r
        if inner is None:
            continue
        core = inner.value if hasattr(inner, 'value') else inner
        rc = getattr(core, 'ReceiverCore', core)
        if rc.Transport.defined and str(rc.Transport.value) in _USB_TRANSPORT_URNS:
            return r
    return None


def _get_sender_flow(node: Node, sender: Any) -> Any:
    fid = sender.FlowId.value if sender.FlowId.defined else None
    if fid is None:
        return None
    return node.flows.get(fid)


def _make_usb_sender_activation() -> Activation:
    desc = get_transport_descriptor(enums.TransportUsb)
    activation = Activation(
        sender_index=0,
        enabled_legs=1,
        staged=[desc.sender_params_type() for _ in range(MAX_LEGS)],
        active=[desc.sender_params_type() for _ in range(MAX_LEGS)],
        constraints=[desc.sender_constraints_type() for _ in range(MAX_LEGS)],
        staged_state=desc.sender_activation_type(),
        active_state=desc.sender_activation_type(),
        sender_name="USBTST",
    )
    init_sender_activation(
        activation, [Leg(enable=True)], enums.TransportUsb, desc,
        privacy_enabled=True, group_hint="",
    )
    return activation


def _make_usb_receiver_activation() -> Activation:
    desc = get_transport_descriptor(enums.TransportUsb)
    activation = Activation(
        receiver_index=0,
        enabled_legs=1,
        staged=[desc.receiver_params_type() for _ in range(MAX_LEGS)],
        active=[desc.receiver_params_type() for _ in range(MAX_LEGS)],
        constraints=[desc.receiver_constraints_type() for _ in range(MAX_LEGS)],
        staged_state=desc.receiver_activation_type(),
        active_state=desc.receiver_activation_type(),
    )
    init_receiver_activation(
        activation, [Leg(enable=True)], enums.TransportUsb, enums.FormatData,
        desc, privacy_enabled=True,
    )
    return activation


def _constraint_map(constraints: Any) -> dict[Any, Any]:
    return constraints.Constraints._inner if hasattr(constraints, "Constraints") else constraints._inner


def _constraint_enum_values(constraints: Any, json_key: str) -> list[str]:
    for key, constraint in _constraint_map(constraints).items():
        if json_key not in str(key):
            continue
        if hasattr(constraint, "get_Enum"):
            enum_field = constraint.get_Enum()
            if enum_field.defined:
                return [str(value) for value in enum_field.value]
    return []


# ===========================================================================
# Class 1 — TestUsbTransports: enum & registry sanity
# ===========================================================================

class TestUsbTransports:
    def test_transport_usb_enum_value(self) -> None:
        # Enum value depends on USB_TRANSPORT_NAMESPACE; must be one of the
        # two well-known URN forms.
        assert str(enums.TransportUsb) in _USB_TRANSPORT_URNS

    def test_data_usb_media_type_enum_value(self) -> None:
        # Python's enum registry must know `DataUsb = "application/usb"`.
        from nmos.enums import EnumRegistry
        e = EnumRegistry.get("application/usb")
        assert str(e) == "application/usb"

    def test_usb_privacy_enum_values(self) -> None:
        assert str(enums.USB) == "USB"
        assert str(enums.USB_KV) == "USB_KV"

    def test_cap_transport_usb_class_enum(self) -> None:
        assert str(enums.CapTransportUsbClass) in _USB_CAP_CLASS_URNS

    def test_usb_descriptor_has_privacy(self) -> None:
        # E1 — USB privacy requires key-version signalling.
        desc = get_transport_descriptor(enums.TransportUsb)
        assert desc.has_privacy is True
        assert desc.privacy_protocol is enums.USB_KV

    def test_usb_descriptor_sender_port_fn_is_27500_plus_index(self) -> None:
        # activation.py:926
        desc = get_transport_descriptor(enums.TransportUsb)
        assert desc.sender_port_fn(0) == 27500
        assert desc.sender_port_fn(1) == 27501
        assert desc.sender_port_fn(7) == 27507

    def test_usb_descriptor_registered_for_transport_urn(self) -> None:
        # Lookup by the transport enum must succeed
        desc = get_transport_descriptor(enums.TransportUsb)
        assert desc is not None


# ===========================================================================
# Class 2 — TestUsbSenderIs04: IS-04 Sender/Flow/Source attributes
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestUsbSenderIs04:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        _build_config8u(self.node)
        self.sender = _find_usb_sender(self.node)

    def test_config8u_has_usb_data_sender(self) -> None:
        assert self.sender is not None, "config8u must have a USB data sender"

    def test_usb_sender_transport_matches_enum(self) -> None:
        # S1 — Sender transport is USB transport URN
        assert self.sender.Transport.defined
        assert str(self.sender.Transport.value) in _USB_TRANSPORT_URNS
        assert str(self.sender.Transport.value) == str(enums.TransportUsb)

    def test_usb_sender_format_is_data(self) -> None:
        assert self.sender.Format.defined
        assert str(self.sender.Format.value) == "urn:x-nmos:format:data"

    def test_usb_sender_flow_media_type_is_application_usb(self) -> None:
        # FL1
        flow = _get_sender_flow(self.node, self.sender)
        assert flow is not None, "USB sender must reference a Flow"
        inner = flow.get() if hasattr(flow, 'get') else flow
        fv = inner.value if hasattr(inner, 'value') else inner
        assert fv.MediaType.defined
        assert str(fv.MediaType.value) == "application/usb"

    def test_usb_sender_flow_format_is_data(self) -> None:
        # FL2
        flow = _get_sender_flow(self.node, self.sender)
        assert flow is not None
        inner = flow.get() if hasattr(flow, 'get') else flow
        fv = inner.value if hasattr(inner, 'value') else inner
        fc = fv.FlowCore if hasattr(fv, 'FlowCore') else fv
        # Format is on the outer polymorphic (NFlowData); use type-name fallback
        assert "Data" in type(fv).__name__, (
            f"USB flow value type is {type(fv).__name__}, expected NFlowData*"
        )

    def test_usb_sender_source_format_is_data(self) -> None:
        # F1 — Source MUST have format=urn:x-nmos:format:data
        flow = _get_sender_flow(self.node, self.sender)
        assert flow is not None
        inner = flow.get() if hasattr(flow, 'get') else flow
        fv = inner.value if hasattr(inner, 'value') else inner
        fc = fv.FlowCore
        assert fc.SourceId.defined, "USB flow must have SourceId"
        src_ptr = self.node.sources.get(fc.SourceId.value)
        assert src_ptr is not None, "USB source must exist"
        src_inner = src_ptr.get() if hasattr(src_ptr, 'get') else src_ptr
        sv = src_inner.value if hasattr(src_inner, 'value') else src_inner
        # Source format either is directly accessible or inferred from type
        if hasattr(sv, 'Format') and sv.Format.defined:
            assert str(sv.Format.value) == "urn:x-nmos:format:data"
        else:
            assert "Data" in type(sv).__name__, (
                f"USB source value type is {type(sv).__name__}, expected NSourceData*"
            )

    def test_usb_sender_has_usb_class_capability(self) -> None:
        # S4 — Sender SHOULD provide urn:x-<ns>:cap:transport:usb_class.
        # Scan the JSON source directly to guarantee we observe the original
        # declaration (pipeline builder may normalise the constraint_sets).
        with open(CONFIG8U) as f:
            cfg = json.load(f)
        found = False
        for s in cfg["senders"]:
            if str(s.get("transport", "")) not in _USB_TRANSPORT_URNS:
                continue
            for cs in s.get("constraint_sets", []):
                for key in cs.keys():
                    if key in _USB_CAP_CLASS_URNS:
                        found = True
                        break
        assert found, "USB Sender should declare urn:x-<ns>:cap:transport:usb_class"


# ===========================================================================
# Class 3 — TestUsbReceiverIs04
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestUsbReceiverIs04:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        _build_config8u(self.node)
        self.receiver = _find_usb_receiver(self.node)
        assert self.receiver is not None, "config8u must have a USB receiver"

    def test_config8u_has_usb_receiver(self) -> None:
        assert self.receiver is not None

    def test_usb_receiver_transport_matches_enum(self) -> None:
        # R1
        inner = self.receiver.get() if hasattr(self.receiver, 'get') else self.receiver
        core = inner.value if hasattr(inner, 'value') else inner
        rc = getattr(core, 'ReceiverCore', core)
        assert rc.Transport.defined
        assert str(rc.Transport.value) == str(enums.TransportUsb)

    def test_usb_receiver_format_is_data(self) -> None:
        # R2
        inner = self.receiver.get() if hasattr(self.receiver, 'get') else self.receiver
        assert hasattr(inner, 'Format') and inner.Format.defined
        assert str(inner.Format.value) == "urn:x-nmos:format:data"

    def test_usb_receiver_media_types_contains_application_usb(self) -> None:
        # R2 — receiver declares application/usb in its constraint_sets.
        # Scan JSON to see the declared media_types.
        with open(CONFIG8U) as f:
            cfg = json.load(f)
        declared: set[str] = set()
        for r in cfg["receivers"]:
            if str(r.get("transport", "")) not in _USB_TRANSPORT_URNS:
                continue
            for cs in r.get("constraint_sets", []):
                mt = cs.get("urn:x-nmos:cap:format:media_type")
                if isinstance(mt, dict) and "enum" in mt:
                    declared.update(str(v) for v in mt["enum"])
        assert "application/usb" in declared, (
            f"USB receiver must include application/usb; got {declared}"
        )

    def test_usb_receiver_has_usb_class_capability(self) -> None:
        # R4
        with open(CONFIG8U) as f:
            cfg = json.load(f)
        found = False
        for r in cfg["receivers"]:
            if str(r.get("transport", "")) not in _USB_TRANSPORT_URNS:
                continue
            for cs in r.get("constraint_sets", []):
                for key in cs.keys():
                    if key in _USB_CAP_CLASS_URNS:
                        found = True
                        break
        assert found, "USB Receiver should declare urn:x-<ns>:cap:transport:usb_class"


# ===========================================================================
# Class 4 — TestUsbIs05TransportParams
# ===========================================================================

class TestUsbIs05TransportParams:
    """Introspect the generated USB transport param types directly —
    mirrors is05_types.py definitions."""

    def test_sender_transport_params_have_source_ip(self) -> None:
        # IS1
        from nmos.types.generated.nusb_sender_transport_params import NUsbSenderTransportParamsValue
        v = NUsbSenderTransportParamsValue()
        assert hasattr(v, "SourceIp")

    def test_sender_transport_params_have_source_port(self) -> None:
        from nmos.types.generated.nusb_sender_transport_params import NUsbSenderTransportParamsValue
        v = NUsbSenderTransportParamsValue()
        assert hasattr(v, "SourcePort")

    def test_sender_transport_params_have_privacy_fields(self) -> None:
        from nmos.types.generated.nusb_sender_transport_params import NUsbSenderTransportParamsValue
        v = NUsbSenderTransportParamsValue()
        for fname in ("ExtPrivacyProtocol", "ExtPrivacyMode", "ExtPrivacyIV",
                      "ExtPrivacyKeyGenerator", "ExtPrivacyKeyId", "ExtPrivacyKeyVersion",
                      "ExtPrivacyEcdhSenderPublicKey", "ExtPrivacyEcdhReceiverPublicKey",
                      "ExtPrivacyEcdhCurve"):
            assert hasattr(v, fname), f"sender params missing {fname}"

    def test_sender_default_port_matches_27500_plus_index(self) -> None:
        desc = get_transport_descriptor(enums.TransportUsb)
        assert desc.sender_port_fn(0) == 27500
        assert desc.sender_port_fn(2) == 27502

    def test_receiver_transport_params_have_source_ip_nullable(self) -> None:
        # IS2 + IS4 — SourceIp on receiver is nullable (NNullString)
        from nmos.types.generated.nusb_receiver_transport_params import NUsbReceiverTransportParamsValue
        v = NUsbReceiverTransportParamsValue()
        assert hasattr(v, "SourceIp")
        v.SourceIp.value = None
        assert v.SourceIp.defined
        assert v.SourceIp.value is None
        v.SourceIp.value = "10.0.0.5"
        assert v.SourceIp.value == "10.0.0.5"

    def test_receiver_transport_params_have_source_port(self) -> None:
        # IS2
        from nmos.types.generated.nusb_receiver_transport_params import NUsbReceiverTransportParamsValue
        v = NUsbReceiverTransportParamsValue()
        assert hasattr(v, "SourcePort")

    def test_receiver_transport_params_have_interface_ip(self) -> None:
        # IS2
        from nmos.types.generated.nusb_receiver_transport_params import NUsbReceiverTransportParamsValue
        v = NUsbReceiverTransportParamsValue()
        assert hasattr(v, "InterfaceIp")

    def test_receiver_transport_params_have_privacy_fields(self) -> None:
        from nmos.types.generated.nusb_receiver_transport_params import NUsbReceiverTransportParamsValue
        v = NUsbReceiverTransportParamsValue()
        for fname in ("ExtPrivacyProtocol", "ExtPrivacyMode", "ExtPrivacyIV",
                      "ExtPrivacyKeyGenerator", "ExtPrivacyKeyId", "ExtPrivacyKeyVersion",
                      "ExtPrivacyEcdhSenderPublicKey", "ExtPrivacyEcdhReceiverPublicKey",
                      "ExtPrivacyEcdhCurve"):
            assert hasattr(v, fname), f"receiver params missing {fname}"


# ===========================================================================
# Class 5 — TestUsbIs05Activation
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestUsbIs05Activation:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        _build_config8u(self.node)
        self.sender = _find_usb_sender(self.node)
        self.receiver = _find_usb_receiver(self.node)
        assert self.sender is not None
        assert self.receiver is not None

    def test_sender_activation_at_most_two_legs(self) -> None:
        # IS3 — redundancy limit: at most 2 transport param sets
        sender_id = self.sender.ResourceCore.Id.value
        activation = self.node.get_sender_activation(sender_id)
        assert activation is not None, "USB sender must have an activation"
        # Legs count across active/staged — the NMOS max is 2
        assert len(activation.active) <= 2, (
            f"USB sender must have at most 2 legs; got {len(activation.active)}"
        )

    def test_receiver_activation_at_most_two_legs(self) -> None:
        # IS3
        from nmos.node import _get_resource_core
        rc = _get_resource_core(self.receiver)
        recv_id = rc.Id.value
        activation = self.node.get_receiver_activation(recv_id)
        assert activation is not None, "USB receiver must have an activation"
        assert len(activation.active) <= 2

    def test_non_redundant_leg_can_be_null(self) -> None:
        # IS4 — when a Receiver does not use redundancy, the unused leg's
        # SourceIp / SourcePort MUST be settable to null. We verify the
        # types accept None (nullable via NNullString/NNull).
        from nmos.types.generated.nusb_receiver_transport_params import NUsbReceiverTransportParamsValue
        v = NUsbReceiverTransportParamsValue()
        # SourceIp: NNullString accepts None
        v.SourceIp.value = None
        assert v.SourceIp.value is None
        # SourcePort: NNull — accepts None
        # Use decode to verify null acceptance at the JSON level
        from nmos.json.engine import JsonEngine
        v2 = NUsbReceiverTransportParamsValue()
        v2.decode(JsonEngine(), {"source_ip": None, "source_port": None})
        assert v2.SourceIp.defined
        assert v2.SourceIp.value is None
        assert v2.SourcePort.defined

    def test_usb_transport_descriptor_exposes_activation_types(self) -> None:
        desc = get_transport_descriptor(enums.TransportUsb)
        assert desc.sender_activation_type is not None
        assert desc.receiver_activation_type is not None

    def test_usb_sender_initial_privacy_protocol_is_usb_kv(self) -> None:
        activation = _make_usb_sender_activation()
        assert str(activation.staged[0].ExtPrivacyProtocol.value) == "USB_KV"
        assert str(activation.active[0].ExtPrivacyProtocol.value) == "USB_KV"

    def test_usb_receiver_initial_privacy_protocol_is_usb_kv(self) -> None:
        activation = _make_usb_receiver_activation()
        assert str(activation.staged[0].ExtPrivacyProtocol.value) == "USB_KV"
        assert str(activation.active[0].ExtPrivacyProtocol.value) == "USB_KV"

    def test_usb_sender_privacy_protocol_constraint_only_allows_usb_kv(self) -> None:
        activation = _make_usb_sender_activation()
        enum_values = _constraint_enum_values(activation.constraints[0], "ext_privacy_protocol")
        assert enum_values == ["USB_KV"]

    def test_usb_receiver_privacy_protocol_constraint_only_allows_usb_kv(self) -> None:
        activation = _make_usb_receiver_activation()
        enum_values = _constraint_enum_values(activation.constraints[0], "ext_privacy_protocol")
        assert enum_values == ["USB_KV"]


# ===========================================================================
# Class 6 — TestUsbSdpGeneration
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestUsbSdpGeneration:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        _build_config8u(self.node)
        self.sender = _find_usb_sender(self.node)
        assert self.sender is not None
        sender_id = self.sender.ResourceCore.Id.value
        self.sdp = _generate_sdp_from_params(self.node, self.sender, sender_id)
        assert self.sdp is not None, "USB manifest SDP must be generated"

    def test_usb_sdp_media_line_is_application_tcp_usb(self) -> None:
        # SD2 — m=application <port> TCP usb
        assert "m=application " in self.sdp
        assert " TCP usb" in self.sdp

    def test_usb_sdp_session_name_matches_sender_label(self) -> None:
        # SD1
        label = self.sender.ResourceCore.Label.value
        assert f"s={label}" in self.sdp

    def test_usb_sdp_has_setup_passive(self) -> None:
        # SD4
        assert "a=setup:passive" in self.sdp

    def test_usb_sdp_c_line_has_sender_ip(self) -> None:
        # SD3 — c=IN IP4 <sender-tcp-server>
        assert "c=IN IP4 " in self.sdp

    def test_usb_sdp_port_is_sender_source_port(self) -> None:
        import re
        m = re.search(r"m=application (\d+) TCP usb", self.sdp)
        assert m is not None, "m-line must contain numeric port"
        port = int(m.group(1))
        assert 27500 <= port <= 27600, f"port {port} not in expected range"

    def test_usb_sdp_no_rtp_framing(self) -> None:
        # USB is TCP, not RTP — RTP/AVP must not appear
        assert "RTP/AVP" not in self.sdp

    def test_usb_sdp_no_udp_mp2t_artefact(self) -> None:
        # Regression check — must not fall into the SRT/UDP-mp2t branch
        assert "UDP mp2t" not in self.sdp

    def test_usb_sdp_privacy_in_manifest_when_pep_active(self) -> None:
        # E2 — manifest SDP contains a=privacy: when PEP active.
        # config8u declares privacy keys, so the node enables privacy.
        if self.node.privacy_enabled:
            assert "a=privacy:" in self.sdp
        else:
            assert "a=privacy:" not in self.sdp


# ===========================================================================
# Class 7 — TestUsbEncryption
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestUsbEncryption:
    def test_usb_privacy_protocol_registered_on_descriptor(self) -> None:
        # E1
        desc = get_transport_descriptor(enums.TransportUsb)
        assert desc.privacy_protocol is enums.USB_KV

    def test_usb_privacy_fields_present_on_sender_and_receiver(self) -> None:
        from nmos.types.generated.nusb_sender_transport_params import NUsbSenderTransportParamsValue
        from nmos.types.generated.nusb_receiver_transport_params import NUsbReceiverTransportParamsValue
        required = {"ExtPrivacyProtocol", "ExtPrivacyMode", "ExtPrivacyIV"}
        sv = NUsbSenderTransportParamsValue()
        rv = NUsbReceiverTransportParamsValue()
        for f in required:
            assert hasattr(sv, f), f"sender missing {f}"
            assert hasattr(rv, f), f"receiver missing {f}"

    def test_usb_sdp_omits_privacy_when_disabled(self) -> None:
        # If we disable privacy on the node, the manifest SDP must not
        # contain a=privacy:. This confirms E2's negative case.
        node = _make_node(serial="USBNOPEP")
        # Build without privacy keys
        with open(CONFIG8U) as f:
            cfg = json.load(f)
        stripped_cfg = json.loads(json.dumps(cfg))
        for kind in ("senders", "receivers"):
            for r in stripped_cfg.get(kind, []):
                r.pop("privacy_keys", None)
        from nmos.node.config import ConfigBuilder
        builder = ConfigBuilder(node, verbose=False)
        for r in stripped_cfg.get("receivers", []):
            try: builder._build_receiver_from_config(r)
            except Exception: pass
        for s in stripped_cfg.get("senders", []):
            try: builder._build_sender_pipeline(s)
            except Exception: pass

        # Force privacy off
        node.privacy_enabled = False
        sender = _find_usb_sender(node)
        if sender is None:
            pytest.skip("USB sender not built in stripped config")
        sdp = _generate_sdp_from_params(node, sender, sender.ResourceCore.Id.value)
        assert sdp is not None
        assert "a=privacy:" not in sdp, (
            "manifest SDP must NOT contain a=privacy when PEP is disabled"
        )
