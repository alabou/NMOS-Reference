# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for nmos.controller.grouping."""

from __future__ import annotations

from nmos.controller.grouping import (
    GROUP_HINT_TAG,
    GroupHint,
    device_address,
    device_serial,
    extract_group_hint,
    parse_group_hint,
    strip_transport_prefix,
)


class TestParseGroupHint:
    """Per ``specs/NMOS With Natural Groups.md`` §"Group Hint" the tag
    value follows one of two forms. Natural-group identity is
    ``(transport, group_index)``; format and role identify members."""

    def test_video_hint(self) -> None:
        hint = parse_group_hint("RTP 3:VIDEO 0")
        assert hint == GroupHint(
            transport="RTP", group_index=3, format="VIDEO", role=0,
        )
        assert hint.key == ("RTP", 3)
        assert str(hint) == "RTP 3:VIDEO 0"

    def test_audio_hint(self) -> None:
        hint = parse_group_hint("SRT 0:AUDIO 2")
        assert hint is not None
        assert hint.transport == "SRT"
        assert hint.group_index == 0
        assert hint.format == "AUDIO"
        assert hint.role == 2

    def test_role_index_omitted_defaults_to_zero(self) -> None:
        # Spec line 57: when <role-index> is absent the role is 0.
        hint = parse_group_hint("RTP 0:VIDEO")
        assert hint is not None
        assert hint.role == 0

    def test_case_insensitive_format_normalised_to_upper(self) -> None:
        # Spec line 53: <role-in-group> comparison is case-insensitive.
        # We normalise the stored form to uppercase for stable equality.
        hint = parse_group_hint("RTP 0:audio 1")
        assert hint is not None
        assert hint.format == "AUDIO"

    def test_malformed_returns_none(self) -> None:
        assert parse_group_hint("") is None
        assert parse_group_hint("no colon") is None
        assert parse_group_hint("RTP X:VIDEO 0") is None
        assert parse_group_hint("RTP 0:VIDEO X") is None
        # Non-letter transport name rejected (spec: [A-Za-z]+).
        assert parse_group_hint("123 0:VIDEO") is None

    def test_whitespace_tolerated(self) -> None:
        hint = parse_group_hint("  RTP 1:VIDEO 0  ")
        assert hint is not None
        assert hint.group_index == 1


class TestExtractGroupHint:
    def test_from_dict(self) -> None:
        tags = {GROUP_HINT_TAG: ["RTP 5:VIDEO 0"]}
        hint = extract_group_hint(tags)
        assert hint is not None
        assert hint.key == ("RTP", 5)

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
