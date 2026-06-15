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

from nmos.enums import (
    FormatVideo, FormatAudio, FormatData, FormatMux,
    VideoRaw, DataSmpte291, DataJson,
    AudioRawL8, AudioRawL16, AudioRawL20, AudioRawL24,
    Internal, Ptp,
)

# Comma-joined media-type lists used by the "in"/"notin" comparisons.
# Raw audio is exactly L8/L16/L20/L24; every other audio media type
# (AM824, coded formats) is classified as NFlowAudioCoded.
_RAW_AUDIO_MEDIA_TYPES = ",".join([
    AudioRawL8.s, AudioRawL16.s, AudioRawL20.s, AudioRawL24.s,
])
_DATA_MEDIA_TYPES = ",".join([DataSmpte291.s, DataJson.s])

# NClock: discriminate on ref_type
NCLOCK_PREDICATES: dict[str, list[tuple[str, str, str]]] = {
    "NClockInternal": [("ref_type", Internal.s, "eq")],
    "NClockPtp": [("ref_type", Ptp.s, "eq")],
}

# NReceiver: discriminate on format
NRECEIVER_PREDICATES: dict[str, list[tuple[str, str, str]]] = {
    "NReceiverVideo": [("format", FormatVideo.s, "eq")],
    "NReceiverAudio": [("format", FormatAudio.s, "eq")],
    "NReceiverData": [("format", FormatData.s, "eq")],
    "NReceiverMux": [("format", FormatMux.s, "eq")],
}

# NSource: discriminate on format
NSOURCE_PREDICATES: dict[str, list[tuple[str, str, str]]] = {
    "NSourceVideo": [("format", FormatVideo.s, "eq")],
    "NSourceAudio": [("format", FormatAudio.s, "eq")],
    "NSourceData": [("format", FormatData.s, "eq")],
    "NSourceMux": [("format", FormatMux.s, "eq")],
}

# NFlow: discriminate on format + media_type
NFLOW_PREDICATES: dict[str, list[tuple[str, str, str]]] = {
    "NFlowVideoRaw": [
        ("format", FormatVideo.s, "eq"),
        ("media_type", VideoRaw.s, "eq"),
    ],
    "NFlowVideoCoded": [
        ("format", FormatVideo.s, "eq"),
        ("media_type", VideoRaw.s, "neq"),
    ],
    "NFlowAudioRaw": [
        ("format", FormatAudio.s, "eq"),
        ("media_type", _RAW_AUDIO_MEDIA_TYPES, "in"),
    ],
    "NFlowAudioCoded": [
        ("format", FormatAudio.s, "eq"),
        ("media_type", _RAW_AUDIO_MEDIA_TYPES, "notin"),
    ],
    "NFlowDataSdianc": [
        ("format", FormatData.s, "eq"),
        ("media_type", DataSmpte291.s, "eq"),
    ],
    "NFlowDataJson": [
        ("format", FormatData.s, "eq"),
        ("media_type", DataJson.s, "eq"),
    ],
    "NFlowData": [
        ("format", FormatData.s, "eq"),
        ("media_type", _DATA_MEDIA_TYPES, "notin"),
    ],
    "NFlowMux": [
        ("format", FormatMux.s, "eq"),
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
