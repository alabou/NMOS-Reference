# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for nmos.controller.grouping."""

from __future__ import annotations

from nmos.controller.grouping import (
    ASSET_INSTANCE_ID_TAG,
    GROUP_HINT_TAG,
    GroupHint,
    asset_instance_id,
    device_address,
    device_serial,
    extract_group_hint,
    parse_group_hint,
    strip_transport_prefix,
)


class TestParseGroupHint:
    """Base reference ``specs/NMOS With Natural Groups.md`` §"Group Hint":
    ``"<group-name> <group-index>:<role-in-group> <role-index>"``. The
    parser relaxes around that form for non-conforming third-party devices.
    Group identity is the normalised text before the first ``:`` (``key``);
    ``(format, role)`` identify a member; non-recognised role tokens make a
    hint *not groupable* (no format/role, raw ``role_name`` kept)."""

    # --- Conforming / groupable -------------------------------------------

    def test_video_hint(self) -> None:
        hint = parse_group_hint("RTP 3:VIDEO 0")
        assert hint == GroupHint(
            group_name="RTP 3", role_name="VIDEO 0",
            groupable=True, format="VIDEO", role=0,
        )
        assert hint.key == "RTP 3"
        assert hint.role_label == "VIDEO 0"
        assert str(hint) == "RTP 3:VIDEO 0"

    def test_audio_hint(self) -> None:
        hint = parse_group_hint("SRT 0:AUDIO 2")
        assert hint is not None
        assert hint.key == "SRT 0"
        assert hint.format == "AUDIO"
        assert hint.role == 2

    def test_role_index_omitted_defaults_to_zero(self) -> None:
        # Spec line 57: when <role-index> is absent the role is 0.
        hint = parse_group_hint("RTP 0:VIDEO")
        assert hint is not None and hint.groupable
        assert hint.role == 0

    def test_case_insensitive_format_normalised_to_upper(self) -> None:
        # Spec line 53: <role-in-group> comparison is case-insensitive.
        hint = parse_group_hint("rtp 0:audio 1")
        assert hint is not None
        assert hint.format == "AUDIO"
        assert hint.key == "RTP 0"  # group name also upper-normalised

    # --- Non-conforming relaxations (still groupable) ---------------------

    def test_format_abbreviations(self) -> None:
        # vid/aud/anc are accepted and normalised to the canonical token.
        assert parse_group_hint("RTP 2:VID 1").format == "VIDEO"
        assert parse_group_hint("SRT 1:AUD").format == "AUDIO"
        assert parse_group_hint("IP 4:ANC 2").format == "DATA"
        assert parse_group_hint("IP 0:Ancillary").format == "DATA"

    def test_dash_or_underscore_role_separator(self) -> None:
        for s in ("RTP 3:VIDEO-0", "RTP 3:VIDEO_0"):
            hint = parse_group_hint(s)
            assert hint is not None and hint.groupable
            assert hint.format == "VIDEO" and hint.role == 0

    def test_arbitrary_group_name(self) -> None:
        # The group name need not be a transport — anything before ':'.
        hint = parse_group_hint("Camera Group 2:VIDEO 0")
        assert hint is not None and hint.groupable
        assert hint.key == "CAMERA GROUP 2"

    def test_trailing_scope_is_ignored(self) -> None:
        # BCP-002-01 generic form may append ":<group-scope>".
        hint = parse_group_hint("RTP 3:VIDEO 0:device")
        assert hint is not None
        assert hint.key == "RTP 3" and hint.format == "VIDEO" and hint.role == 0

    def test_relaxed_group_index_forms(self) -> None:
        # Previously-rejected forms are now groupable: the group index is
        # not split out for identity, so a numeric/garbage group name and a
        # non-numeric role index are tolerated (role index defaults to 0).
        assert parse_group_hint("123 0:VIDEO").groupable is True
        assert parse_group_hint("RTP X:VIDEO 0").key == "RTP X"
        assert parse_group_hint("RTP 0:VIDEO X").role == 0

    # --- Not groupable -----------------------------------------------------

    def test_unrecognised_role_is_not_groupable(self) -> None:
        hint = parse_group_hint("RTP 0:THERMAL 1")
        assert hint is not None
        assert hint.groupable is False
        assert hint.format is None and hint.role is None
        # Raw post-':' text is preserved for display.
        assert hint.role_name == "THERMAL 1"
        assert hint.role_label == "THERMAL 1"
        # group-name still available (for display), but never grouped.
        assert hint.key == "RTP 0"

    def test_unrecognised_role_arbitrary_name(self) -> None:
        hint = parse_group_hint("My Stream:telemetry")
        assert hint is not None and hint.groupable is False
        assert hint.role_label == "telemetry"

    # --- Unparseable -------------------------------------------------------

    def test_returns_none(self) -> None:
        # No ':' → no group/role split to work with.
        assert parse_group_hint("") is None
        assert parse_group_hint("no colon") is None

    def test_group_index_property_for_usb_tiebreak(self) -> None:
        # Best-effort trailing integer; USB hints are conforming "USB N".
        assert parse_group_hint("USB 0:VIDEO 0").group_index == 0
        assert parse_group_hint("USB 7:AUDIO 1").group_index == 7

    def test_whitespace_tolerated(self) -> None:
        hint = parse_group_hint("  RTP 1:VIDEO 0  ")
        assert hint is not None
        assert hint.key == "RTP 1"


class TestExtractGroupHint:
    def test_from_dict(self) -> None:
        tags = {GROUP_HINT_TAG: ["RTP 5:VIDEO 0"]}
        hint = extract_group_hint(tags)
        assert hint is not None
        assert hint.key == "RTP 5"

    def test_from_dict_string_value(self) -> None:
        # Some registries store the bare string (not wrapped in a list).
        tags = {GROUP_HINT_TAG: "SRT 2:AUDIO 1"}
        hint = extract_group_hint(tags)
        assert hint is not None
        assert hint.role == 1

    def test_missing_tag(self) -> None:
        assert extract_group_hint({"some:other:tag": ["x"]}) is None
        assert extract_group_hint({}) is None
        assert extract_group_hint(None) is None


class TestDeviceSerial:
    def test_from_description(self) -> None:
        dev = {"description": "Example device SNX00001 (lab)"}
        assert device_serial(dev) == "SNX00001"

    def test_from_label(self) -> None:
        dev = {"label": "SNX00042 alpha"}
        assert device_serial(dev) == "SNX00042"

    def test_from_tag_values(self) -> None:
        dev = {
            "label": "no serial here",
            "tags": {
                "urn:x-matrox:serial/v1.0": ["SNX12345"],
            },
        }
        assert device_serial(dev) == "SNX12345"

    def test_no_match(self) -> None:
        dev = {"label": "generic", "description": "unlabeled"}
        assert device_serial(dev) is None

    def test_none_input(self) -> None:
        assert device_serial(None) is None

    def test_asset_instance_id_tag(self) -> None:
        # BCP-002-02 instance identifier — vendor-neutral, no SNX pattern.
        dev = {"tags": {ASSET_INSTANCE_ID_TAG: ["ACME-12AB-0007"]}}
        assert device_serial(dev) == "ACME-12AB-0007"

    def test_asset_tag_takes_precedence_over_snx(self) -> None:
        # When both are present the asset tag wins over the SNX fallback.
        dev = {
            "description": "box SNX00001",
            "tags": {ASSET_INSTANCE_ID_TAG: ["VENDOR-XYZ-42"]},
        }
        assert device_serial(dev) == "VENDOR-XYZ-42"

    def test_empty_asset_tag_falls_back_to_snx(self) -> None:
        dev = {
            "description": "box SNX00001",
            "tags": {ASSET_INSTANCE_ID_TAG: ["   "]},
        }
        assert device_serial(dev) == "SNX00001"

    def test_asset_instance_id_helper(self) -> None:
        dev = {"tags": {ASSET_INSTANCE_ID_TAG: [" trimmed-me "]}}
        assert asset_instance_id(dev) == "trimmed-me"
        assert asset_instance_id({"tags": {}}) is None
        assert asset_instance_id(None) is None


class TestDeviceAddress:
    def test_from_sr_ctrl_control(self) -> None:
        dev = {
            "controls": [
                {
                    "type": "urn:x-nmos:control:sr-ctrl/v1.1",
                    "href": "http://10.0.0.5:5060/x-nmos/connection/v1.1/",
                },
            ],
        }
        assert device_address(dev) == "10.0.0.5:5060"

    def test_highest_version_wins(self) -> None:
        dev = {
            "controls": [
                {
                    "type": "urn:x-nmos:control:sr-ctrl/v1.1",
                    "href": "http://host1:5000/x-nmos/connection/v1.1/",
                },
                {
                    "type": "urn:x-nmos:control:sr-ctrl/v1.2",
                    "href": "http://host2:5001/x-nmos/connection/v1.2/",
                },
            ],
        }
        # v1.2 > v1.1 lexicographically → host2:5001.
        assert device_address(dev) == "host2:5001"

    def test_missing_controls_returns_none(self) -> None:
        assert device_address({}) is None
        assert device_address({"controls": []}) is None
        assert device_address({"controls": [{"type": "other", "href": "x"}]}) is None
        assert device_address(None) is None

    def test_malformed_href_returns_none(self) -> None:
        dev = {
            "controls": [
                {"type": "urn:x-nmos:control:sr-ctrl/v1.1", "href": ""},
            ],
        }
        assert device_address(dev) is None


class TestStripTransportPrefix:
    def test_strip_rtp_mcast(self) -> None:
        assert strip_transport_prefix("urn:x-nmos:transport:rtp.mcast") == "rtp.mcast"

    def test_strip_srt(self) -> None:
        assert strip_transport_prefix("urn:x-nmos:transport:srt") == "srt"

    def test_missing_prefix_returns_empty(self) -> None:
        assert strip_transport_prefix("rtp") == ""
        assert strip_transport_prefix("urn:other:rtp") == ""

    def test_non_string_returns_empty(self) -> None:
        assert strip_transport_prefix(None) == ""
        assert strip_transport_prefix(42) == ""
