# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Polymorphic type predicates.

Each entry maps a polymorphic parent type name to a dict of
concrete_type_name -> list of (json_key, expected_value, comparison).

Comparison types:
- "eq": field value == expected (string or int)
- "neq": field value != expected
- "in": field value in expected (comma-separated set)
- "notin": field value not in expected (comma-separated set)
- "type_bool": field value is a boolean
- "type_int": field value is an integer
- "type_float": field value is a float
- "type_str": field value is a string
- "fallback": always matches (last resort)
"""

from __future__ import annotations

# NClock: discriminate on ref_type
NCLOCK_PREDICATES: dict[str, list[tuple[str, str, str]]] = {
    "NClockInternal": [("ref_type", "internal", "eq")],
    "NClockPtp": [("ref_type", "ptp", "eq")],
}

# NReceiver: discriminate on format
NRECEIVER_PREDICATES: dict[str, list[tuple[str, str, str]]] = {
    "NReceiverVideo": [("format", "urn:x-nmos:format:video", "eq")],
    "NReceiverAudio": [("format", "urn:x-nmos:format:audio", "eq")],
    "NReceiverData": [("format", "urn:x-nmos:format:data", "eq")],
    "NReceiverMux": [("format", "urn:x-nmos:format:mux", "eq")],
}

# NSource: discriminate on format
NSOURCE_PREDICATES: dict[str, list[tuple[str, str, str]]] = {
    "NSourceVideo": [("format", "urn:x-nmos:format:video", "eq")],
    "NSourceAudio": [("format", "urn:x-nmos:format:audio", "eq")],
    "NSourceData": [("format", "urn:x-nmos:format:data", "eq")],
    "NSourceMux": [("format", "urn:x-nmos:format:mux", "eq")],
}

# NFlow: discriminate on format + media_type
NFLOW_PREDICATES: dict[str, list[tuple[str, str, str]]] = {
    "NFlowVideoRaw": [
        ("format", "urn:x-nmos:format:video", "eq"),
        ("media_type", "video/raw", "eq"),
    ],
    "NFlowVideoCoded": [
        ("format", "urn:x-nmos:format:video", "eq"),
        ("media_type", "video/raw", "neq"),
    ],
    "NFlowAudioRaw": [
        ("format", "urn:x-nmos:format:audio", "eq"),
        ("media_type", "audio/L8,audio/L16,audio/L20,audio/L24,audio/AM824", "in"),
    ],
    "NFlowAudioCoded": [
        ("format", "urn:x-nmos:format:audio", "eq"),
        ("media_type", "audio/L8,audio/L16,audio/L20,audio/L24,audio/AM824", "notin"),
    ],
    "NFlowDataSdianc": [
        ("format", "urn:x-nmos:format:data", "eq"),
        ("media_type", "video/smpte291", "eq"),
    ],
    "NFlowDataJson": [
        ("format", "urn:x-nmos:format:data", "eq"),
        ("media_type", "application/json", "eq"),
    ],
    "NFlowData": [
        ("format", "urn:x-nmos:format:data", "eq"),
        ("media_type", "video/smpte291,application/json", "notin"),
    ],
    "NFlowMux": [
        ("format", "urn:x-nmos:format:mux", "eq"),
    ],
}

# NConstraint: discriminate on type of values in enum/minimum/maximum fields
NCONSTRAINT_PREDICATES: dict[str, list[tuple[str, str, str]]] = {
    "NConstraintBool": [("enum", "", "type_bool")],
    "NConstraintInt": [("enum", "", "type_int")],
    "NConstraintFloat": [("enum", "", "type_float")],
    "NConstraintString": [("enum", "", "type_str")],
    "NConstraintRational": [("", "", "fallback")],
}

# NcMessage: discriminate on message_type integer
NCMESSAGE_PREDICATES: dict[str, list[tuple[str, str, str]]] = {
    "NcCommandMessage": [("message_type", "0", "eq")],
    "NcCommandResponseMessage": [("message_type", "1", "eq")],
    "NcNotificationMessage": [("message_type", "2", "eq")],
    "NcSubscriptionMessage": [("message_type", "3", "eq")],
    "NcSubscriptionResponseMessage": [("message_type", "4", "eq")],
    "NcErrorMessage": [("message_type", "5", "eq")],
}

# Map parent type name -> predicates dict
ALL_PREDICATES: dict[str, dict[str, list[tuple[str, str, str]]]] = {
    "NClock": NCLOCK_PREDICATES,
    "NReceiver": NRECEIVER_PREDICATES,
    "NSource": NSOURCE_PREDICATES,
    "NFlow": NFLOW_PREDICATES,
    "NConstraint": NCONSTRAINT_PREDICATES,
    "NcMessage": NCMESSAGE_PREDICATES,
}
