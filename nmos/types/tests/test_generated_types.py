# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for generated NMOS types with real IS-04 JSON payloads."""

from __future__ import annotations

import json as stdlib_json

import pytest

from nmos.enums import EnumRegistry
from nmos.json.engine import JsonEngine


@pytest.fixture(autouse=True)
def _clean_registry() -> None:  # type: ignore[misc]
    """Save and restore enum registry around each test."""
    saved = dict(EnumRegistry._entries)
    yield  # type: ignore[misc]
    EnumRegistry._entries.clear()
    EnumRegistry._entries.update(saved)


# ---------------------------------------------------------------------------
# NRational -- simple object
# ---------------------------------------------------------------------------

class TestNRational:
    def test_encode_decode_roundtrip(self) -> None:
        from nmos.types.generated.nrational import NRational

        r = NRational()
        r.set_to_default()
        r.value.Numerator.value = 30000
        r.value.Denominator.value = 1001

        engine = JsonEngine()
        engine.reset()
        r.encode(engine, None)
        parsed = stdlib_json.loads(engine.get_output())

        assert parsed == {"numerator": 30000, "denominator": 1001}

        # Decode back
        r2 = NRational()
        r2.decode(JsonEngine(), parsed)
        assert r2.value.Numerator.value == 30000
        assert r2.value.Denominator.value == 1001

    def test_optional_denominator_default(self) -> None:
        from nmos.types.generated.nrational import NRational

        r = NRational()
        r.decode(JsonEngine(), {"numerator": 48000})
        assert r.value.Numerator.value == 48000
        assert r.value.Denominator.value == 1  # default

    def test_clone(self) -> None:
        from nmos.types.generated.nrational import NRational

        r = NRational()
        r.set_to_default()
        r.value.Numerator.value = 25
        c = r.clone()
        c.value.Numerator.value = 50
        assert r.value.Numerator.value == 25  # original unchanged

    def test_factory(self) -> None:
        from nmos.types.generated.nrational import (
            NRationalValue,
            make_nrational,
            make_nrational_value,
        )

        val = NRationalValue()
        val.Numerator.value = 60
        val.Denominator.value = 1
        val = make_nrational_value(val)

        obj = make_nrational(val)
        assert obj.defined
        assert obj.value.Numerator.value == 60


# ---------------------------------------------------------------------------
# NError -- simple object
# ---------------------------------------------------------------------------

class TestNError:
    def test_decode_encode(self) -> None:
        from nmos.types.generated.nerror import NError

        error_json = {"code": 404, "error": "Not Found", "debug": "resource xyz missing"}
        e = NError()
        e.decode(JsonEngine(), error_json)

        assert e.value.Code.value == 404
        assert e.value.Error.value == "Not Found"
        assert e.value.Debug.value == "resource xyz missing"

        engine = JsonEngine()
        engine.reset()
        e.encode(engine, None)
        reparsed = stdlib_json.loads(engine.get_output())
        assert reparsed == error_json

    def test_debug_null(self) -> None:
        from nmos.types.generated.nerror import NError

        e = NError()
        e.decode(JsonEngine(), {"code": 500, "error": "Internal", "debug": None})
        assert e.value.Debug.value is None


# ---------------------------------------------------------------------------
# NSenderSubscription -- defaults
# ---------------------------------------------------------------------------

class TestNSenderSubscription:
    def test_defaults_applied(self) -> None:
        from nmos.types.generated.nsender_subscription import NSenderSubscription

        s = NSenderSubscription()
        s.set_to_default()
        assert s.value.ReceiverId.value is None  # default None
        assert s.value.Active.value is False  # default False


# ---------------------------------------------------------------------------
# NDevice -- embedded ResourceCore + arrays
# ---------------------------------------------------------------------------

class TestNDevice:
    DEVICE_JSON = {
        "id": "d0e4a3e0-1234-5678-9abc-def012345678",
        "version": "1617723456:123456789",
        "label": "Production Camera 1",
        "description": "Main studio camera",
        "tags": {},
        "type": "urn:x-nmos:device:generic",
        "node_id": "a0b1c2d3-e4f5-4789-abcd-ef0123456789",
        "senders": ["a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5", "b2c3d4e5-f6a7-4b8c-9d0e-f1a2b3c4d5e6"],
        "receivers": ["c3d4e5f6-a7b8-4c9d-ae0f-1a2b3c4d5e6f"],
        "controls": [
            {
                "href": "http://192.168.1.100:8080/x-nmos/connection/v1.1",
                "type": "urn:x-nmos:control:sr-ctrl/v1.1",
                "authorization": True,
            }
        ],
    }

    def test_decode(self) -> None:
        from nmos.types.generated.ndevice import NDevice

        d = NDevice()
        d.decode(JsonEngine(), self.DEVICE_JSON)

        assert d.defined
        # Embedded fields accessible via ResourceCore
        assert d.value.ResourceCore.Id.value == "d0e4a3e0-1234-5678-9abc-def012345678"
        assert d.value.ResourceCore.Label.value == "Production Camera 1"
        # Version is TAI "1617723456:123456789" in JSON → UTC (sec-37, nsec) internally
        from nmos.json.types import NTime
        utc_sec = 1617723456 - NTime.TAI_UTC_OFFSET
        assert d.value.ResourceCore.Version.value == (utc_sec, 123456789)
        assert d.value.ResourceCore.Description.value == "Main studio camera"
        # Direct fields
        assert d.value.NodeId.value == "a0b1c2d3-e4f5-4789-abcd-ef0123456789"
        assert d.value.Senders.value == ["a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5", "b2c3d4e5-f6a7-4b8c-9d0e-f1a2b3c4d5e6"]
        assert d.value.Receivers.value == ["c3d4e5f6-a7b8-4c9d-ae0f-1a2b3c4d5e6f"]
        # Array of objects
        assert len(d.value.Controls.value) == 1
        ctrl = d.value.Controls.value[0]
        assert ctrl.Href.value == "http://192.168.1.100:8080/x-nmos/connection/v1.1"
        assert ctrl.Type.value == "urn:x-nmos:control:sr-ctrl/v1.1"

    def test_encode_roundtrip(self) -> None:
        from nmos.types.generated.ndevice import NDevice

        d = NDevice()
        d.decode(JsonEngine(), self.DEVICE_JSON)

        engine = JsonEngine()
        engine.reset()
        d.encode(engine, None)
        reparsed = stdlib_json.loads(engine.get_output())

        # Verify key fields survived round-trip
        assert reparsed["id"] == self.DEVICE_JSON["id"]
        assert reparsed["label"] == self.DEVICE_JSON["label"]
        assert reparsed["node_id"] == self.DEVICE_JSON["node_id"]
        assert reparsed["senders"] == self.DEVICE_JSON["senders"]
        assert reparsed["receivers"] == self.DEVICE_JSON["receivers"]
        assert len(reparsed["controls"]) == 1
        assert reparsed["controls"][0]["href"] == self.DEVICE_JSON["controls"][0]["href"]

    def test_embedded_fields_at_top_level_in_json(self) -> None:
        """Embedded ResourceCore fields appear at top level, not nested."""
        from nmos.types.generated.ndevice import NDevice

        d = NDevice()
        d.decode(JsonEngine(), self.DEVICE_JSON)

        engine = JsonEngine()
        engine.reset()
        d.encode(engine, None)
        reparsed = stdlib_json.loads(engine.get_output())

        # These are embedded fields -- must be at top level
        assert "id" in reparsed
        assert "version" in reparsed
        assert "label" in reparsed
        assert "description" in reparsed
        assert "tags" in reparsed

    def test_clone(self) -> None:
        from nmos.types.generated.ndevice import NDevice

        d = NDevice()
        d.decode(JsonEngine(), self.DEVICE_JSON)

        c = d.clone()
        c.value.ResourceCore.Label.value = "Cloned Device"
        assert d.value.ResourceCore.Label.value == "Production Camera 1"
        assert c.value.ResourceCore.Label.value == "Cloned Device"

    def test_convenience_accessors(self) -> None:
        from nmos.types.generated.ndevice import NDevice

        d = NDevice()
        d.decode(JsonEngine(), self.DEVICE_JSON)

        # get_<Field>() accessor
        node_id_field = d.get_NodeId()
        assert node_id_field.value == "a0b1c2d3-e4f5-4789-abcd-ef0123456789"

        # set_<Field>() accessor
        d.set_NodeId("new-node-id")
        assert d.value.NodeId.value == "new-node-id"


# ---------------------------------------------------------------------------
# NSender -- embedded + nested object + optional
# ---------------------------------------------------------------------------

class TestNSender:
    SENDER_JSON = {
        "id": "10000000-2000-4000-8000-def012345678",
        "version": "1617723456:0",
        "label": "Camera 1 Video",
        "description": "HD-SDI video from Camera 1",
        "tags": {},
        "flow_id": "20000000-3000-4000-9000-ef0123456789",
        "transport": "urn:x-nmos:transport:rtp.mcast",
        "device_id": "30000000-4000-4000-a000-f01234567890",
        "manifest_href": "http://192.168.1.100:8080/x-nmos/connection/v1.1/single/sender/transportfile",
        "interface_bindings": ["eth0"],
        "subscription": {
            "receiver_id": None,
            "active": False,
        },
    }

    def test_decode_with_nested_subscription(self) -> None:
        from nmos.types.generated.nsender import NSender

        s = NSender()
        s.decode(JsonEngine(), self.SENDER_JSON)

        assert s.value.ResourceCore.Id.value == "10000000-2000-4000-8000-def012345678"
        assert s.value.ResourceCore.Label.value == "Camera 1 Video"
        assert s.value.FlowId.value == "20000000-3000-4000-9000-ef0123456789"
        assert str(s.value.Transport.value) == "urn:x-nmos:transport:rtp.mcast"
        assert s.value.DeviceId.value == "30000000-4000-4000-a000-f01234567890"
        assert s.value.InterfaceBindings.value == ["eth0"]
        # Nested subscription object
        assert s.value.Subscription.value.ReceiverId.value is None
        assert s.value.Subscription.value.Active.value is False

    def test_encode_roundtrip(self) -> None:
        from nmos.types.generated.nsender import NSender

        s = NSender()
        s.decode(JsonEngine(), self.SENDER_JSON)

        engine = JsonEngine()
        engine.reset()
        s.encode(engine, None)
        reparsed = stdlib_json.loads(engine.get_output())

        assert reparsed["id"] == self.SENDER_JSON["id"]
        assert reparsed["flow_id"] == self.SENDER_JSON["flow_id"]
        assert reparsed["transport"] == self.SENDER_JSON["transport"]
        assert reparsed["subscription"]["receiver_id"] is None
        assert reparsed["subscription"]["active"] is False
