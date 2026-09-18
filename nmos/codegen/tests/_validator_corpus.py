# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Generate the validator parity corpus: what Python decides, case by case.

The Rust port re-implements the 67 `Check*` assertions by hand, and a
re-implementation is only as good as the evidence that it agrees. This module
produces that evidence in a form the Rust test suite can read directly: for
every case, the verdict Python reaches and, when it rejects, the exact message
it would put in an HTTP 400 body.

Why a generated file rather than assertions written twice
---------------------------------------------------------
Assertions written by hand on both sides test the author's belief twice. The
Python is the specification, so the only assertion worth making is "Rust agrees
with what Python actually did" -- which means running Python and recording it.

Several cases here look pointless until you know what they are pinning:

* trailing newlines, because Python's ``$`` used to match before one and the
  anchors are now ``\\Z`` on both sides;
* ``True`` where an integer is expected, because ``bool`` subclasses ``int`` in
  Python and several checks are ``isinstance(v, int)``;
* values that are neither string nor null for the activation mode, because that
  check has no ``else`` branch and silently accepts them.

Regenerate with::

    python -m nmos.codegen.tests._validator_corpus
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nmos import validators as V
from nmos.errors import NmosError

OUTPUT = (
    Path(__file__).parent.parent.parent.parent
    / "rust" / "crates" / "nmos-json" / "tests" / "validator_cases.json"
)


class Field:
    """The shape the validators expect: a `.value`, and `.defined` for members."""

    def __init__(self, value: Any) -> None:
        self.value = value
        self.defined = value is not None


class Member:
    """An element with optional sub-members, for the two array validators that
    reach into their items (`CheckDidSdid`, `CheckAudioChannels`)."""

    def __init__(self, **fields: Any) -> None:
        for name, value in fields.items():
            setattr(self, name, Field(value))


UUID = "3b8be755-08ff-452b-b217-c9151eb21193"

# (validator name, label, value). The label is what the Rust side matches on.
CASES: list[tuple[str, str, Any]] = [
    ("CheckResourceIdString", "canonical", UUID),
    ("CheckResourceIdString", "uppercase", UUID.upper()),
    ("CheckResourceIdString", "trailing_newline", UUID + "\n"),
    ("CheckResourceIdString", "garbage", "nope"),
    ("CheckResourceIdString", "empty", ""),
    ("CheckHealthString", "digits", "1600000000"),
    ("CheckHealthString", "trailing_newline", "1600000000\n"),
    ("CheckHealthString", "not_digits", "12a"),
    ("CheckClockNameString", "valid", "clk0"),
    ("CheckClockNameString", "invalid", "clock0"),
    ("CheckClockGmidString", "valid", "08-00-11-ff-fe-22-04-00"),
    ("CheckClockGmidString", "short", "08-00-11-ff-fe-22-04"),
    ("CheckDid", "valid", "0x60"),
    ("CheckDid", "no_prefix", "60"),
    ("CheckSdid", "valid", "0xAB"),
    ("CheckPortIdString", "valid", "08-00-11-ff-fe-22"),
    ("CheckPortIdString", "invalid_carries_value", "zz"),
    ("CheckEndpointProtocol", "http", "http"),
    ("CheckEndpointProtocol", "https", "https"),
    ("CheckEndpointProtocol", "ftp", "ftp"),
    ("CheckEndpointPort", "low", 0),
    ("CheckEndpointPort", "high", 65535),
    ("CheckEndpointPort", "too_high", 65536),
    ("CheckEndpointPort", "negative", -1),
    ("CheckEndpointHostString", "ipv4", "192.168.1.1"),
    ("CheckEndpointHostString", "ipv6", "::1"),
    ("CheckEndpointHostString", "hostname", "example.com"),
    ("CheckEndpointHostString", "empty", ""),
    ("CheckEndpointHostString", "with_slash", "example.com/x"),
    ("CheckEndpointHostString", "with_space", "exa mple.com"),
    ("CheckEndpointHostString", "userinfo", "a@b"),
    ("CheckEndpointHostString", "bare_at", "@"),
    ("CheckEndpointHostString", "with_port", "host:99"),
    ("CheckEndpointHostString", "ipv6_bracketed", "[::1]"),
    ("CheckEndpointHostString", "leading_slash", "/x"),
    ("CheckEndpointHostString", "leading_colon", ":8080"),
    ("CheckEndpointHostString", "query", "a?b"),
    ("CheckEndpointHostString", "fragment", "a#b"),
    ("CheckEndpointHostString", "embedded_newline", "a\nb"),
    ("CheckEndpointHostString", "dots", ".."),
    ("CheckEndpointHostString", "dash", "-"),
    ("CheckTransport", "nmos_rtp", "urn:x-nmos:transport:rtp"),
    ("CheckTransport", "vendor", "urn:x-matrox:transport:srt"),
    ("CheckTransport", "not_a_urn", "rtp"),
    ("CheckFormat", "video", "urn:x-nmos:format:video"),
    ("CheckFormat", "mux", "urn:x-nmos:format:mux"),
    ("CheckFormat", "trailing_newline", "urn:x-nmos:format:video\n"),
    ("CheckFormat", "unknown", "urn:x-nmos:format:smell"),
    ("CheckDeviceType", "generic", "urn:x-nmos:device:generic"),
    ("CheckDeviceType", "bad", "generic"),
    ("CheckServiceType", "valid", "urn:x-manufacturer:service:thing"),
    ("CheckServiceType", "invalid_carries_value", "nope"),
    ("CheckColorspace", "BT709", "BT709"),
    ("CheckColorspace", "unknown", "BT9999"),
    ("CheckInterlaceMode", "progressive", "progressive"),
    ("CheckInterlaceMode", "unknown", "weave"),
    ("CheckTransferCharacteristic", "SDR", "SDR"),
    ("CheckTransferCharacteristic", "unknown", "LOG"),
    ("CheckInputStatusState", "valid", "signal_present"),
    ("CheckOutputStatusState", "invalid", "awaiting_signal"),
    ("CheckSenderStatusState", "valid", "no_essence"),
    ("CheckReceiverStatusState", "valid", "compliant_stream"),
    ("CheckPositiveInteger", "one", 1),
    ("CheckPositiveInteger", "zero", 0),
    ("CheckPositiveInteger", "negative", -5),
    ("CheckPositiveUint16", "one", 1),
    ("CheckPositiveUint16", "zero", 0),
    ("CheckPositiveUint16", "max", 65535),
    ("CheckPositiveUint16", "over", 65536),
    ("CheckUint16", "zero", 0),
    ("CheckUint16", "over", 65536),
    ("CheckErrorCode", "400", 400),
    ("CheckErrorCode", "599", 599),
    ("CheckErrorCode", "399", 399),
    ("CheckErrorCode", "600", 600),
    ("CheckConstraintSetPreference", "zero", 0),
    ("CheckConstraintSetPreference", "min", -100),
    ("CheckConstraintSetPreference", "max", 100),
    ("CheckConstraintSetPreference", "over", 101),
    ("CheckNullInteger", "null", None),
    ("CheckNullInteger", "int", 5),
    ("CheckNullInteger", "bool_is_an_int_in_python", True),
    # A non-integer is the case that decides the Rust signature. Taking
    # `Option<i64>` would turn every one of these into `None` -- the same value
    # a JSON null produces -- and so accept them all, while Python's
    # `isinstance` check separates "null" from "the wrong type".
    ("CheckNullInteger", "string", "8080"),
    ("CheckNullInteger", "float", 1.5),
    ("CheckNullInteger", "object", {"a": 1}),
    ("CheckNullInteger", "array", [1]),
    ("CheckNullPositiveInteger", "null", None),
    ("CheckNullPositiveInteger", "zero", 0),
    ("CheckNullPositiveInteger", "negative", -1),
    ("CheckNullPositiveInteger", "string", "8080"),
    ("CheckNullPositiveInteger", "float", 1.5),
    ("CheckNullPositiveInteger", "object", {"a": 1}),
    ("CheckNullPositiveInteger", "array", [1]),
    ("CheckNullPositiveInteger", "bool_is_an_int_in_python", True),
    ("CheckAutoBool", "auto", "auto"),
    ("CheckAutoBool", "bool", True),
    ("CheckAutoBool", "other_string", "yes"),
    ("CheckAutoPort", "auto", "auto"),
    ("CheckAutoPort", "port", 8080),
    ("CheckAutoPort", "over", 70000),
    ("CheckAutoPort", "other_string", "nope"),
    ("CheckNullPort", "null", None),
    ("CheckNullPort", "port", 8080),
    ("CheckNullPort", "over", 70000),
    ("CheckNullAutoPort", "null", None),
    ("CheckNullAutoPort", "auto", "auto"),
    ("CheckNullAutoPort", "port", 8080),
    ("CheckActivationMode", "null", None),
    ("CheckActivationMode", "immediate", "activate_immediate"),
    ("CheckActivationMode", "unknown", "activate_whenever"),
    ("CheckNodeApiVersions", "one", ["v1.3"]),
    ("CheckNodeApiVersions", "empty", []),
    ("CheckNodeApiVersions", "bad_version", ["1.3"]),
    ("CheckVideoMediaTypes", "valid", ["video/raw"]),
    ("CheckVideoMediaTypes", "invalid_carries_value", ["audio/L24"]),
    ("CheckAudioMediaTypes", "valid", ["audio/L24"]),
    ("CheckAudioMediaTypes", "invalid", ["video/raw"]),
    ("CheckDataMediaTypes", "valid", ["application/json"]),
    ("CheckMuxMediaTypes", "valid", ["video/MP2T"]),
    ("CheckDataEventTypes", "one", ["boolean"]),
    ("CheckDataEventTypes", "empty", []),
    ("CheckConstraintsLength", "one", {"a": 1}),
    ("CheckConstraintsLength", "empty", {}),
    ("CheckTransportConstraintEnumLength", "one", ["a"]),
    ("CheckTransportConstraintEnumLength", "empty", []),
    ("CheckGenericObject", "object", {"a": 1}),
    ("CheckGenericObject", "null", None),
    ("CheckGenericObject", "list", [1]),
    ("CheckArrayOfResourceIdString", "valid", [UUID]),
    ("CheckArrayOfResourceIdString", "invalid", [UUID, "nope"]),
    ("CheckResourceIdNullableString", "null", None),
    ("CheckResourceIdNullableString", "valid", UUID),
    ("CheckResourceIdNullableString", "invalid", "nope"),
    ("CheckClockNameNullableString", "null", None),
    ("CheckClockNameNullableString", "valid", "clk1"),
    ("CheckChassisIdNullableString", "null", None),
    ("CheckChassisIdNullableString", "mac", "08-00-11-ff-fe-22"),
    ("CheckChassisIdNullableString", "free_text", "any chassis"),
    ("CheckChassisIdNullableString", "trailing_newline", "any chassis\n"),
    ("CheckVideoComponents", "empty", []),
    ("CheckRtpTransportConstraints", "ok", {"rtp_enabled": 1, "source_ip": 1}),
    ("CheckRtpTransportConstraints", "ext_key", {"rtp_enabled": 1, "ext_x": 1}),
    ("CheckRtpTransportConstraints", "missing_required", {"source_ip": 1}),
    ("CheckRtpTransportConstraints", "bad_property", {"rtp_enabled": 1, "bogus": 1}),
    ("CheckRtpTcpTransportConstraints", "missing_required", {"source_ip": 1}),
    ("CheckMqttTransportConstraints", "ok", {"broker_topic": 1}),
    ("CheckMqttTransportConstraints", "bad", {"nope": 1}),
    ("CheckWebSocketTransportConstraints", "ok", {"connection_uri": 1}),
    ("CheckNdiTransportConstraints", "ok", {"source_name": 1}),
    ("CheckSrtTransportConstraints", "ok", {"latency": 1}),
    ("CheckUsbTransportConstraints", "ok", {"source_ip": 1}),
    ("CheckRtspTransportConstraints", "ok", {"source_ip": 1}),
    ("CheckUdpTransportConstraints", "ok", {"multicast_ip": 1}),
]

# The two validators that reach into element members need real elements.
STRUCTURED: list[tuple[str, str, Any]] = [
    ("CheckDidSdid", "both_valid", [Member(Did="0x60", Sdid="0x01")]),
    ("CheckDidSdid", "bad_did", [Member(Did="zz", Sdid="0x01")]),
    ("CheckDidSdid", "bad_sdid", [Member(Did="0x60", Sdid="zz")]),
    ("CheckDidSdid", "absent", [Member(Did=None, Sdid=None)]),
    ("CheckAudioChannels", "known", [Member(Symbol="L")]),
    ("CheckAudioChannels", "numbered", [Member(Symbol="NSC001")]),
    ("CheckAudioChannels", "unknown", [Member(Symbol="Zz")]),
    ("CheckAudioChannels", "empty", []),
    ("CheckVideoComponents", "known", [Member(Name="Y")]),
    ("CheckVideoComponents", "unknown", [Member(Name="Q")]),
]


def run_one(name: str, value: Any) -> dict[str, Any]:
    """Run one validator and record exactly what Python decided."""
    fn = getattr(V, name)
    try:
        fn(Field(value))
    except NmosError as exc:
        return {"ok": False, "kind": type(exc).__name__, "message": exc.msg or str(exc)}
    return {"ok": True}


def build() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, label, value in CASES:
        record = run_one(name, value)
        record.update({"validator": name, "label": label, "input": value})
        out.append(record)
    for name, label, value in STRUCTURED:
        record = run_one(name, value)
        record.update({"validator": name, "label": label, "input": "<structured>"})
        out.append(record)
    return out


def main() -> None:
    cases = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n")
    rejected = sum(1 for c in cases if not c["ok"])
    print(f"{len(cases)} cases ({rejected} rejected) -> {OUTPUT}")


if __name__ == "__main__":
    main()
