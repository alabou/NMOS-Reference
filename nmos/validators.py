# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NMOS type assertion/validation functions.

Called from generated types' assert_valid() methods.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from nmos.errors import InvalidObject

# ---------------------------------------------------------------------------
# Compiled regex patterns (module-level constants)
# ---------------------------------------------------------------------------

_RESOURCE_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

_CLOCK_NAME = re.compile(r"^clk[0-9]+$")

_CLOCK_GMID = re.compile(
    r"^[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}"
    r"-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}$"
)

_TRANSPORT = re.compile(r"^urn:x-nmos:transport:|^urn:x-[a-z]+:transport:")

_FORMAT = re.compile(
    r"^urn:x-nmos:format:video$|^urn:x-nmos:format:audio$"
    r"|^urn:x-nmos:format:data$|^urn:x-nmos:format:mux$"
)

_DEVICE_TYPE = re.compile(r"^urn:x-nmos:device:|^urn:x-[a-z]+:device:")

_SERVICE_TYPE = re.compile(r"^urn:x-")

_DID = re.compile(r"^0x[0-9a-fA-F]{2}$")
_SDID = re.compile(r"^0x[0-9a-fA-F]{2}$")

_CHASSIS_ID = re.compile(r"^([0-9a-f]{2}-){5}([0-9a-f]{2})$|^.+$")

_PORT_ID = re.compile(r"^([0-9a-f]{2}-){5}([0-9a-f]{2})$")

_NODE_API_VERSION = re.compile(r"^v[0-9]+\.[0-9]+$")

# registrationapi-health-response.json constrains ``health`` to
# {"type": "string", "pattern": "^[0-9]+$"} -- the heartbeat time in TAI
# seconds carried as a decimal STRING, not as a JSON number.
_HEALTH = re.compile(r"^[0-9]+$")

_VIDEO_MEDIA_TYPE = re.compile(r"^video/[^\s/]+$")
_AUDIO_MEDIA_TYPE = re.compile(r"^audio/[^\s/]+$")
_DATA_MEDIA_TYPE = re.compile(r"^[^\s/]+/[^\s/]+$")
_MUX_MEDIA_TYPE = re.compile(r"^[^\s/]+/[^\s/]+$")

_AUDIO_CHANNEL_SYMBOL = re.compile(
    r"^NSC(0[0-9][0-9]|1[0-1][0-9]|12[0-8])$|^U(0[1-9]|[1-5][0-9]|6[0-4])$"
)

# ---------------------------------------------------------------------------
# Helper: validate a single regex match (exact-one findall length check)
# ---------------------------------------------------------------------------

def _match_exactly_one(pattern: re.Pattern[str], value: str, msg: str) -> None:
    """Raise InvalidObject if the pattern does not match exactly once."""
    results = pattern.findall(value)
    if len(results) != 1:
        raise InvalidObject(msg)


# ===========================================================================
# NMOS validators
# ===========================================================================

# --- String validators ---

def CheckResourceIdString(field: Any) -> None:
    """Validate a UUID resource ID string (NString)."""
    _match_exactly_one(_RESOURCE_ID, field.value, "invalid resource id")


def CheckArrayOfResourceIdString(field: Any) -> None:
    """Validate an array of UUID resource ID strings (NArrayOfString)."""
    for item in field.value:
        results = _RESOURCE_ID.findall(item)
        if len(results) != 1:
            raise InvalidObject("invalid resource id")


def CheckResourceIdNullableString(field: Any) -> None:
    """Validate a nullable UUID resource ID (NNullString | NNull)."""
    v = field.value  # None for JSON null, str otherwise
    if v is None:
        return
    if not isinstance(v, str):
        raise InvalidObject("invalid nullable string type")
    results = _RESOURCE_ID.findall(v)
    if len(results) != 1:
        raise InvalidObject("invalid resource id")


def CheckHealthString(field: Any) -> None:
    """Validate a Registration API health value (NString).

    Per registrationapi-health-response.json the value is the heartbeat time
    in TAI seconds rendered as a decimal string matching ``^[0-9]+$``. It is
    deliberately NOT a number on the wire, and deliberately NOT the
    ``seconds:nanoseconds`` form used by resource ``version`` attributes.
    """
    _match_exactly_one(_HEALTH, field.value, "invalid health value")


def CheckClockNameString(field: Any) -> None:
    """Validate a clock name string like clk0, clk1 (NString)."""
    _match_exactly_one(_CLOCK_NAME, field.value, "invalid clock name")


def CheckClockNameNullableString(field: Any) -> None:
    """Validate a nullable clock name (NNullString | NNull)."""
    v = field.value
    if v is None:
        return
    if not isinstance(v, str):
        raise InvalidObject("invalid nullable string type")
    results = _CLOCK_NAME.findall(v)
    if len(results) != 1:
        raise InvalidObject("invalid clock name")


def CheckClockGmidString(field: Any) -> None:
    """Validate a PTP grandmaster ID (NString)."""
    _match_exactly_one(_CLOCK_GMID, field.value, "invalid clock gmid")


def CheckDid(field: Any) -> None:
    """Validate a DID hex value like 0x41 (NString)."""
    _match_exactly_one(_DID, field.value, "invalid DID value")


def CheckSdid(field: Any) -> None:
    """Validate an SDID hex value like 0x01 (NString)."""
    _match_exactly_one(_SDID, field.value, "invalid SDID value")


def CheckDidSdid(field: Any) -> None:
    """Validate an array of DID/SDID value objects.

    Each element has optional Did and Sdid members with .defined and .value attributes.
    """
    for item in field.value:
        if item.Did.defined:
            results = _DID.findall(item.Did.value.nstring if hasattr(item.Did.value, 'nstring') else item.Did.value)
            if len(results) != 1:
                raise InvalidObject("invalid DID value")
        if item.Sdid.defined:
            results = _SDID.findall(item.Sdid.value.nstring if hasattr(item.Sdid.value, 'nstring') else item.Sdid.value)
            if len(results) != 1:
                raise InvalidObject("invalid SDID value")


def CheckPortIdString(field: Any) -> None:
    """Validate a port ID MAC-like string (NString)."""
    v = field.value
    results = _PORT_ID.findall(v)
    if len(results) != 1:
        raise InvalidObject(f"invalid port id {v}")


def CheckChassisIdNullableString(field: Any) -> None:
    """Validate a nullable chassis ID (NNullString | NNull)."""
    v = field.value
    if v is None:
        return
    if not isinstance(v, str):
        raise InvalidObject("invalid nullable string type")
    results = _CHASSIS_ID.findall(v)
    if len(results) != 1:
        raise InvalidObject("invalid chassis_id")


def CheckEndpointHostString(field: Any) -> None:
    """Validate an endpoint host — IP address or hostname (NString)."""
    v = field.value
    # Try parsing as IP first
    try:
        import ipaddress
        ipaddress.ip_address(v)
        return  # valid IP
    except ValueError:
        pass
    # Try as URL hostname
    try:
        result = urlparse(f"http://{v}/")
        if not result.hostname:
            raise InvalidObject("invalid endpoint host")
    except ValueError:
        raise InvalidObject("invalid endpoint host")


# --- Enum validators ---

def CheckEndpointProtocol(field: Any) -> None:
    """Validate endpoint protocol enum (NEnum): http or https."""
    v = str(field.value)
    if v not in ("http", "https"):
        raise InvalidObject("invalid endpoint protocol")


def CheckTransport(field: Any) -> None:
    """Validate an NMOS transport URN (NEnum)."""
    v = str(field.value)
    results = _TRANSPORT.findall(v)
    if len(results) != 1:
        raise InvalidObject("invalid transport")


def CheckFormat(field: Any) -> None:
    """Validate an NMOS format URN (NEnum)."""
    v = str(field.value)
    results = _FORMAT.findall(v)
    if len(results) != 1:
        raise InvalidObject("invalid format")


def CheckDeviceType(field: Any) -> None:
    """Validate an NMOS device type URN (NEnum)."""
    v = str(field.value)
    results = _DEVICE_TYPE.findall(v)
    if len(results) != 1:
        raise InvalidObject("invalid device type")


def CheckServiceType(field: Any) -> None:
    """Validate an NMOS service type URN (NEnum)."""
    v = str(field.value)
    results = _SERVICE_TYPE.findall(v)
    if len(results) != 1:
        raise InvalidObject(f"invalid service type {v}")


def CheckColorspace(field: Any) -> None:
    """Validate colorspace enum (NEnum)."""
    v = str(field.value)
    if v not in (
        "BT601", "BT709", "BT2020", "BT2100",
        "UNSPECIFIED", "ST2065-1", "ST2065-3", "XYZ", "ALPHA",
    ):
        raise InvalidObject("invalid colorspace")


def CheckInterlaceMode(field: Any) -> None:
    """Validate interlace mode enum (NEnum)."""
    v = str(field.value)
    if v not in ("progressive", "interlaced_tff", "interlaced_bff", "interlaced_psf"):
        raise InvalidObject("invalid interlace_mode")


def CheckTransferCharacteristic(field: Any) -> None:
    """Validate transfer characteristic enum (NEnum)."""
    v = str(field.value)
    if v not in ("SDR", "HLG", "PQ"):
        raise InvalidObject("invalid transfer_characteristic")


def CheckInputStatusState(field: Any) -> None:
    """Validate input status state enum (NEnum)."""
    v = str(field.value)
    if v not in ("no_signal", "awaiting_signal", "signal_present"):
        raise InvalidObject("invalid input status state")


def CheckOutputStatusState(field: Any) -> None:
    """Validate output status state enum (NEnum)."""
    v = str(field.value)
    if v not in ("no_signal", "signal_present"):
        raise InvalidObject("invalid output status state")


def CheckSenderStatusState(field: Any) -> None:
    """Validate sender status state enum (NEnum)."""
    v = str(field.value)
    if v not in (
        "unconstrained", "constrained", "active_constraints_violation",
        "no_essence", "awaiting_essence",
    ):
        raise InvalidObject("invalid sender status state")


def CheckReceiverStatusState(field: Any) -> None:
    """Validate receiver status state enum (NEnum)."""
    v = str(field.value)
    if v not in ("unknown", "compliant_stream", "non_compliant_stream"):
        raise InvalidObject("invalid receiver status state")


# --- Integer validators ---

def CheckPositiveInteger(field: Any) -> None:
    """Validate value is a positive integer > 0 (NInt)."""
    if field.value <= 0:
        raise InvalidObject("invalid positive integer")


def CheckPositiveUint16(field: Any) -> None:
    """Validate value is a positive 16-bit unsigned integer (1..65535) (NInt)."""
    v = field.value
    if v <= 0 or v > 65535:
        raise InvalidObject("invalid positive 16 bit unsigned integer")


def CheckUint16(field: Any) -> None:
    """Validate value is a 16-bit unsigned integer (0..65535) (NInt)."""
    v = field.value
    if v < 0 or v > 65535:
        raise InvalidObject("invalid 16 bit unsigned integer")


def CheckErrorCode(field: Any) -> None:
    """Validate an HTTP error code (400..599) (NInt)."""
    v = field.value
    if v < 400 or v > 599:
        raise InvalidObject("invalid error code")


def CheckConstraintSetPreference(field: Any) -> None:
    """Validate constraint set preference (-100..100) (NInt)."""
    v = field.value
    if v < -100 or v > 100:
        raise InvalidObject("invalid constraint set preference")


def CheckEndpointPort(field: Any) -> None:
    """Validate endpoint port number (0..65535) (NInt)."""
    v = field.value
    if v < 0 or v > 65535:
        raise InvalidObject("invalid endpoint port number")


def CheckCommandMessageType(field: Any) -> None:
    """Validate IS-12 command message type == 0 (NInt)."""
    if field.value != 0:
        raise InvalidObject("invalid command message type")


def CheckCommandResponseMessageType(field: Any) -> None:
    """Validate IS-12 command response message type == 1 (NInt)."""
    if field.value != 1:
        raise InvalidObject("invalid command response message type")


def CheckNotificationMessageType(field: Any) -> None:
    """Validate IS-12 notification message type == 2 (NInt)."""
    if field.value != 2:
        raise InvalidObject("invalid notification message type")


def CheckSubscriptionMessageType(field: Any) -> None:
    """Validate IS-12 subscription message type == 3 (NInt)."""
    if field.value != 3:
        raise InvalidObject("invalid subscription message type")


def CheckSubscriptionResponseMessageType(field: Any) -> None:
    """Validate IS-12 subscription response message type == 4 (NInt)."""
    if field.value != 4:
        raise InvalidObject("invalid subscription response message type")


def CheckErrorMessageType(field: Any) -> None:
    """Validate IS-12 error message type == 5 (NInt)."""
    if field.value != 5:
        raise InvalidObject("invalid error message type")


def CheckResetCause(field: Any) -> None:
    """Validate NcResetCause integer (0..5) (NInt)."""
    # 0=Unknown, 1=PowerOn, 2=InternalError, 3=Upgrade, 4=ControllerRequest, 5=ManualReset
    if field.value not in (0, 1, 2, 3, 4, 5):
        raise InvalidObject("invalid reset cause")


def CheckPropertyChangeType(field: Any) -> None:
    """Validate NcPropertyChangeType integer (0..3) (NInt)."""
    # 0=Value, 1=ItemAdded, 2=ItemChanged, 3=ItemRemoved
    if field.value not in (0, 1, 2, 3):
        raise InvalidObject("invalid change property type")


def CheckDeviceGenericState(field: Any) -> None:
    """Validate NcDeviceGenericState integer (0..5) (NInt)."""
    # 0=Unknown, 1=NormalOperation, 2=Initializing, 3=Updating, 4=LicensingError, 5=InternalError
    if field.value not in (0, 1, 2, 3, 4, 5):
        raise InvalidObject("invalid device generic state")


# --- Polymorphic / Nullable validators ---

def CheckNullInteger(field: Any) -> None:
    """Validate a nullable integer (NNull) — accepts None or int."""
    v = field.value
    if v is not None and not isinstance(v, int):
        raise InvalidObject("invalid null integer")


def CheckNullPositiveInteger(field: Any) -> None:
    """Validate a nullable positive integer (NNull) — accepts None or int >= 0."""
    v = field.value
    if v is None:
        return
    if not isinstance(v, int) or v < 0:
        raise InvalidObject("invalid null integer")


def CheckAutoBool(field: Any) -> None:
    """Validate auto-bool polymorphic (NNull): 'auto' string or bool."""
    v = field.value
    if isinstance(v, str):
        if v != "auto":
            raise InvalidObject("invalid string value")
    elif isinstance(v, bool):
        pass  # ok
    else:
        raise InvalidObject("invalid type")


def CheckAutoPort(field: Any) -> None:
    """Validate auto-port polymorphic (NNull): 'auto' string or int 0..65535."""
    v = field.value
    if isinstance(v, str):
        if v != "auto":
            raise InvalidObject("invalid string value")
    elif isinstance(v, int) and not isinstance(v, bool):
        if v < 0 or v > 65535:
            raise InvalidObject("invalid port value")
    else:
        raise InvalidObject("invalid type")


def CheckNullPort(field: Any) -> None:
    """Validate nullable port (NNull): None or int 0..65535."""
    v = field.value
    if v is None:
        return
    if isinstance(v, int) and not isinstance(v, bool):
        if v < 0 or v > 65535:
            raise InvalidObject("invalid port value")
    else:
        raise InvalidObject("invalid type")


def CheckNullAutoPort(field: Any) -> None:
    """Validate nullable auto-port (NNull): None, 'auto', or int 0..65535."""
    v = field.value
    if v is None:
        return
    if isinstance(v, str):
        if v != "auto":
            raise InvalidObject("invalid string value")
    elif isinstance(v, int) and not isinstance(v, bool):
        if v < 0 or v > 65535:
            raise InvalidObject("invalid port value")
    else:
        raise InvalidObject("invalid type")


def CheckActivationMode(field: Any) -> None:
    """Validate activation mode (NNull): None or one of the activation mode strings."""
    v = field.value
    if v is None:
        return
    if isinstance(v, str):
        if v not in ("activate_immediate", "activate_scheduled_absolute", "activate_scheduled_relative"):
            raise InvalidObject("invalid activation mode")
    # Non-string, non-None is silently accepted (only nil and string are checked)


# --- Array validators ---

def CheckNodeApiVersions(field: Any) -> None:
    """Validate node API versions array (NArrayOfString): non-empty, each matches vN.N."""
    v = field.value
    if len(v) == 0:
        raise InvalidObject("invalid empty node api versions")
    for item in v:
        results = _NODE_API_VERSION.findall(item)
        if len(results) != 1:
            raise InvalidObject("invalid node api version")


def CheckVideoMediaTypes(field: Any) -> None:
    """Validate video media types array (NArrayOfEnum). Empty is allowed."""
    for item in field.value:
        s = str(item)
        results = _VIDEO_MEDIA_TYPE.findall(s)
        if len(results) != 1:
            raise InvalidObject(f"invalid video media type {s}")


def CheckAudioMediaTypes(field: Any) -> None:
    """Validate audio media types array (NArrayOfEnum). Empty is allowed."""
    for item in field.value:
        s = str(item)
        results = _AUDIO_MEDIA_TYPE.findall(s)
        if len(results) != 1:
            raise InvalidObject("invalid audio media type")


def CheckDataMediaTypes(field: Any) -> None:
    """Validate data media types array (NArrayOfEnum). Empty is allowed."""
    for item in field.value:
        s = str(item)
        results = _DATA_MEDIA_TYPE.findall(s)
        if len(results) != 1:
            raise InvalidObject("invalid data media type")


def CheckMuxMediaTypes(field: Any) -> None:
    """Validate mux media types array (NArrayOfEnum). Empty is allowed."""
    for item in field.value:
        s = str(item)
        results = _MUX_MEDIA_TYPE.findall(s)
        if len(results) != 1:
            raise InvalidObject("invalid mux media type")


def CheckDataEventTypes(field: Any) -> None:
    """Validate data event types array (NArrayOfEnum): must be non-empty."""
    if len(field.value) == 0:
        raise InvalidObject("invalid empty data event types array")


def CheckAudioChannels(field: Any) -> None:
    """Validate audio channels array: non-empty, with valid optional Symbol.

    Each element has an optional Symbol member whose enum value must be a known
    audio channel symbol or match the NSC/U pattern.
    """
    v = field.value
    if len(v) == 0:
        raise InvalidObject("invalid empty audio channels label array")

    _KNOWN_AUDIO_SYMBOLS = {
        "L", "R", "C", "LFE", "Ls", "Rs", "Lss", "Rss", "Lrs", "Rrs",
        "Lc", "Rc", "Cs", "HI", "VIN", "M1", "M2", "Lt", "Rt",
        "Lst", "Rst", "S",
    }

    for item in v:
        if item.Symbol.defined:
            sym = str(item.Symbol.value)
            if sym not in _KNOWN_AUDIO_SYMBOLS:
                results = _AUDIO_CHANNEL_SYMBOL.findall(sym)
                if len(results) != 1:
                    raise InvalidObject("invalid audio channel symbol")


def CheckVideoComponents(field: Any) -> None:
    """Validate video components array: non-empty, with valid optional Name.

    Each element has an optional Name member whose enum value must be a known
    video component name.
    """
    v = field.value
    if len(v) == 0:
        raise InvalidObject("invalid empty video components array")

    _KNOWN_COMPONENT_NAMES = {
        "Y", "Cb", "Cr", "I", "Ct", "Cp", "A", "R", "G", "B", "DepthMap",
    }

    for item in v:
        if item.Name.defined:
            name = str(item.Name.value)
            if name not in _KNOWN_COMPONENT_NAMES:
                raise InvalidObject("invalid video component name")


# --- Constraint validators ---

def CheckConstraintsLength(field: Any) -> None:
    """Validate constraint set has at least one parameter constraint."""
    # field.value is a dict[EnumId, NConstraint]
    if len(field.value) == 0:
        raise InvalidObject("invalid empty constraint set")


def CheckTransportConstraintEnumLength(field: Any) -> None:
    """Validate transport constraint enum has at least one entry."""
    if len(field.value) == 0:
        raise InvalidObject("invalid empty transport constraint enum")


# --- Generic object validator ---

def CheckGenericObject(field: Any) -> None:
    """Validate a generic object is not None and is a dict (NGeneric)."""
    v = field.value
    if v is None:
        raise InvalidObject("invalid generic object")
    if not isinstance(v, dict):
        raise InvalidObject("invalid generic object")


# ===========================================================================
# Transport constraint validators
# ===========================================================================

# Allowed property sets for each transport type (string representations of EnumIds)

_RTP_TRANSPORT_PROPERTIES = {
    "multicast_ip", "destination_ip", "destination_port",
    "source_ip", "interface_ip", "source_port",
    "fec_enabled", "fec_destination_ip", "fec_mode", "fec_type",
    "fec_block_width", "fec_block_height",
    "fec1D_destination_port", "fec2D_destination_port",
    "fec1D_source_port", "fec2D_source_port",
    "rtcp_enabled", "rtcp_destination_ip", "rtcp_destination_port", "rtcp_source_port",
    "rtp_enabled",
    "ext_privacy_protocol", "ext_privacy_mode", "ext_privacy_iv",
    "ext_privacy_key_generator", "ext_privacy_key_id", "ext_privacy_key_version",
    "ext_privacy_ecdh_sender_public_key", "ext_privacy_ecdh_receiver_public_key",
    "ext_privacy_ecdh_curve",
}

_RTP_TCP_TRANSPORT_PROPERTIES = {
    "source_ip", "interface_ip", "source_port",
    "rtcp_enabled", "rtcp_source_port", "rtp_enabled",
    "ext_privacy_protocol", "ext_privacy_mode", "ext_privacy_iv",
    "ext_privacy_key_generator", "ext_privacy_key_id", "ext_privacy_key_version",
    "ext_privacy_ecdh_sender_public_key", "ext_privacy_ecdh_receiver_public_key",
    "ext_privacy_ecdh_curve",
}

_MQTT_TRANSPORT_PROPERTIES = {
    "destination_host", "source_host",
    "broker_topic", "broker_protocol", "broker_authorization",
    "connection_status_broker_topic",
}

_WEBSOCKET_TRANSPORT_PROPERTIES = {
    "connection_uri", "connection_authorization",
}

_NDI_TRANSPORT_PROPERTIES = {
    "interface_ip", "source_ip", "source_port",
    "source_name", "machine_name",
}

_SRT_TRANSPORT_PROPERTIES = {
    "source_ip", "source_port", "destination_ip", "destination_port",
    "protocol", "latency", "stream_id",
    "ext_privacy_protocol", "ext_privacy_mode", "ext_privacy_iv",
    "ext_privacy_key_generator", "ext_privacy_key_id", "ext_privacy_key_version",
    "ext_privacy_ecdh_sender_public_key", "ext_privacy_ecdh_receiver_public_key",
    "ext_privacy_ecdh_curve",
}

_USB_TRANSPORT_PROPERTIES = {
    "interface_ip", "source_ip", "source_port",
    "ext_privacy_protocol", "ext_privacy_mode", "ext_privacy_iv",
    "ext_privacy_key_generator", "ext_privacy_key_id", "ext_privacy_key_version",
    "ext_privacy_ecdh_sender_public_key", "ext_privacy_ecdh_receiver_public_key",
    "ext_privacy_ecdh_curve",
}

_RTSP_TRANSPORT_PROPERTIES = {
    "interface_ip", "source_ip", "source_port",
    "ext_privacy_protocol", "ext_privacy_mode", "ext_privacy_iv",
    "ext_privacy_key_generator", "ext_privacy_key_id", "ext_privacy_key_version",
    "ext_privacy_ecdh_sender_public_key", "ext_privacy_ecdh_receiver_public_key",
    "ext_privacy_ecdh_curve",
}

_UDP_TRANSPORT_PROPERTIES = {
    "multicast_ip", "destination_ip", "destination_port",
    "source_ip", "interface_ip", "source_port",
    "fec_enabled", "fec_destination_ip", "fec_mode", "fec_type",
    "fec_block_width", "fec_block_height",
    "fec1D_destination_port", "fec2D_destination_port",
    "fec1D_source_port", "fec2D_source_port",
    "ext_privacy_protocol", "ext_privacy_mode", "ext_privacy_iv",
    "ext_privacy_key_generator", "ext_privacy_key_id", "ext_privacy_key_version",
    "ext_privacy_ecdh_sender_public_key", "ext_privacy_ecdh_receiver_public_key",
    "ext_privacy_ecdh_curve",
}


def _check_transport_constraints(
    field: Any,
    allowed: set[str],
    transport_name: str,
    required_key: str | None = None,
) -> None:
    """Common logic for transport constraint validation.

    field.value is a dict[EnumId, NTransportConstraint].
    """
    v = field.value

    # Check required property if any
    if required_key is not None:
        found = False
        for k in v:
            if str(k) == required_key:
                found = True
                break
        if not found:
            raise InvalidObject(
                f"invalid {transport_name} transport constraints, missing required constraints"
            )

    # Check that all keys are either in the allowed set or start with "ext_"
    for k in v:
        ks = str(k)
        if ks not in allowed and not ks.startswith("ext_"):
            raise InvalidObject(
                f"invalid {transport_name} transport constraints, invalid property"
            )


def CheckRtpTransportConstraints(field: Any) -> None:
    """Validate RTP transport constraints: rtp_enabled required, known property set."""
    _check_transport_constraints(field, _RTP_TRANSPORT_PROPERTIES, "RTP", "rtp_enabled")


def CheckRtpTcpTransportConstraints(field: Any) -> None:
    """Validate RTP/TCP transport constraints: rtp_enabled required, known property set."""
    _check_transport_constraints(field, _RTP_TCP_TRANSPORT_PROPERTIES, "RTP", "rtp_enabled")


def CheckMqttTransportConstraints(field: Any) -> None:
    """Validate MQTT transport constraints: known property set."""
    _check_transport_constraints(field, _MQTT_TRANSPORT_PROPERTIES, "MQTT")


def CheckWebSocketTransportConstraints(field: Any) -> None:
    """Validate WebSocket transport constraints: known property set."""
    _check_transport_constraints(field, _WEBSOCKET_TRANSPORT_PROPERTIES, "WebSocket")


def CheckNdiTransportConstraints(field: Any) -> None:
    """Validate NDI transport constraints: known property set."""
    _check_transport_constraints(field, _NDI_TRANSPORT_PROPERTIES, "NDI")


def CheckSrtTransportConstraints(field: Any) -> None:
    """Validate SRT transport constraints: known property set."""
    _check_transport_constraints(field, _SRT_TRANSPORT_PROPERTIES, "SRT")


def CheckUsbTransportConstraints(field: Any) -> None:
    """Validate USB transport constraints: known property set."""
    _check_transport_constraints(field, _USB_TRANSPORT_PROPERTIES, "USB")


def CheckRtspTransportConstraints(field: Any) -> None:
    """Validate RTSP transport constraints: known property set."""
    _check_transport_constraints(field, _RTSP_TRANSPORT_PROPERTIES, "RTSP")


def CheckUdpTransportConstraints(field: Any) -> None:
    """Validate UDP transport constraints: known property set."""
    _check_transport_constraints(field, _UDP_TRANSPORT_PROPERTIES, "UDP")


# ===========================================================================
# Additional validators
# ===========================================================================

def CheckAuthenticationMode(field: Any) -> None:
    """Validate authentication mode enum (NEnum)."""
    v = str(field.value)
    if v not in ("local",):
        raise InvalidObject("invalid authentication mode")


def CheckRole(field: Any) -> None:
    """Validate role enum (NEnum)."""
    v = str(field.value)
    if v not in ("default_admin", "admin", "operator"):
        raise InvalidObject("invalid role")


def CheckNetworkSpeed(field: Any) -> None:
    """Validate network speed enum (NEnum)."""
    v = str(field.value)
    if v not in ("2G5", "10G", "25G"):
        raise InvalidObject("invalid network speed")


def CheckConnectorType(field: Any) -> None:
    """Validate connector type enum (NEnum)."""
    v = str(field.value)
    if v not in ("HDMI", "SDI"):
        raise InvalidObject("invalid connector type")


def CheckNetworkState(field: Any) -> None:
    """Validate network state enum (NEnum)."""
    v = str(field.value)
    if v not in (
        "undefined", "disconnected", "auto_negociation",
        "RJ45_100M", "RJ45_1G", "RJ45_2G5", "SFP_10G", "SFP_25G",
    ):
        raise InvalidObject("invalid network state")


def CheckVideoScan(field: Any) -> None:
    """Validate video scan enum (NEnum)."""
    v = str(field.value)
    if v not in ("progressive", "interlaced"):
        raise InvalidObject("invalid video scan")


def CheckPixelFormat(field: Any) -> None:
    """Validate pixel format enum (NEnum)."""
    v = str(field.value)
    _VALID_PIXEL_FORMATS = {
        "YUV_8_420", "YUV_10_420", "YUV_12_420",
        "YUV_8_422", "YUV_10_422", "YUV_12_422",
        "YUV_8_444", "YUV_10_444", "YUV_12_444",
        "RGB_8", "RGB_10", "RGB_12",
    }
    if v not in _VALID_PIXEL_FORMATS:
        raise InvalidObject("invalid pixel format")


def CheckColorSpace(field: Any) -> None:
    """Validate Matrox color space enum (NEnum). Distinct from NMOS CheckColorspace."""
    v = str(field.value)
    if v not in ("rgb", "yuv_601", "yuv_709", "yuv_2020"):
        raise InvalidObject("invalid color space")


def CheckColorTcs(field: Any) -> None:
    """Validate color transfer characteristics enum (NEnum)."""
    v = str(field.value)
    if v not in ("undefined", "SDR", "PQ", "HLG"):
        raise InvalidObject("invalid color transfer characteristics")


def CheckAudioFormat(field: Any) -> None:
    """Validate audio format enum (NEnum)."""
    v = str(field.value)
    if v not in ("PCM_16", "PCM_20", "PCM_24", "AM824"):
        raise InvalidObject("invalid audio format")


def CheckStreamState(field: Any) -> None:
    """Validate stream state enum (NEnum)."""
    v = str(field.value)
    if v not in ("disabled", "active", "inactive"):
        raise InvalidObject("invalid stream state")


def CheckPtpState(field: Any) -> None:
    """Validate PTP state enum (NEnum)."""
    v = str(field.value)
    if v not in (
        "ptp_disabled", "ptp_leader",
        "ptp_follower_no_leader_found", "ptp_follower_unlocked",
        "ptp_follower_locked", "ptp_change_in_progress",
    ):
        raise InvalidObject("invalid PTP state")


def CheckNmosInterface(field: Any) -> None:
    """Validate NMOS interface enum (NEnum): lan0, lan1, lan2, or auto."""
    v = str(field.value)
    if v not in ("lan0", "lan1", "lan2", "auto"):
        raise InvalidObject("invalid NMOS interface")


def CheckAutoInterface(field: Any) -> None:
    """Validate auto interface enum (NEnum): lan0, lan1, lan2 (no auto)."""
    v = str(field.value)
    if v not in ("lan0", "lan1", "lan2"):
        raise InvalidObject("invalid auto interface")


def CheckSelectPrivateKey(field: Any) -> None:
    """Validate private key selection enum (NEnum)."""
    v = str(field.value)
    if v not in (
        "manufacturer_rsa_server_private_key",
        "manufacturer_ecdsa_server_private_key",
        "customer_rsa_server_private_key",
        "customer_ecdsa_server_private_key",
    ):
        raise InvalidObject("invalid private key selection")


def CheckRegistryMode(field: Any) -> None:
    """Validate registry mode enum (NEnum)."""
    v = str(field.value)
    if v not in ("disabled", "dns_mdns", "dns", "mdns", "manual"):
        raise InvalidObject("invalid registry mode")


def CheckInputSelectEdid(field: Any) -> None:
    """Validate input EDID selection enum (NEnum)."""
    v = str(field.value)
    if v not in ("passthrough", "custom0", "base"):
        raise InvalidObject("invalid input EDID selection")


def CheckInputNoSignalOption(field: Any) -> None:
    """Validate input no-signal option enum (NEnum)."""
    v = str(field.value)
    if v not in ("message", "no_output", "bitmap"):
        raise InvalidObject("invalid input no signal option")


def CheckOutputSelectResolution(field: Any) -> None:
    """Validate output resolution selection enum (NEnum)."""
    v = str(field.value)
    if v not in ("stream", "edid_preference", "force"):
        raise InvalidObject("invalid output resolution selection")


def CheckOutputSelectPixelFormat(field: Any) -> None:
    """Validate output pixel format selection enum (NEnum)."""
    v = str(field.value)
    if v not in ("stream", "edid_preference", "force"):
        raise InvalidObject("invalid output pixel format selection")


def CheckOutputNoSignalOption(field: Any) -> None:
    """Validate output no-signal option enum (NEnum)."""
    v = str(field.value)
    if v not in ("message", "no_output", "bitmap", "blank", "last_frame"):
        raise InvalidObject("invalid output no signal option")


def CheckOutputSelectEdid(field: Any) -> None:
    """Validate output EDID selection enum (NEnum)."""
    v = str(field.value)
    if v not in ("custom0",):
        raise InvalidObject("invalid output EDID selection")


def CheckSyncType(field: Any) -> None:
    """Validate sync type enum (NEnum)."""
    v = str(field.value)
    if v not in ("ipmx", "2110", "blackburst"):
        raise InvalidObject("invalid sync type")


def CheckTxSelectResolution(field: Any) -> None:
    """Validate TX resolution selection enum (NEnum)."""
    v = str(field.value)
    if v not in ("input", "force"):
        raise InvalidObject("invalid TX resolution selection")


def CheckTxSelectPixelFormat(field: Any) -> None:
    """Validate TX pixel format selection enum (NEnum)."""
    v = str(field.value)
    if v not in ("input", "force"):
        raise InvalidObject("invalid TX pixel format selection")


def CheckIpAddressMode(field: Any) -> None:
    """Validate IP address mode enum (NEnum)."""
    v = str(field.value)
    if v not in ("dhcp", "static"):
        raise InvalidObject("invalid IP address mode")


def CheckTimeMode(field: Any) -> None:
    """Validate time mode enum (NEnum)."""
    v = str(field.value)
    if v not in ("ntp_server", "system_time", "device_time"):
        raise InvalidObject("invalid time mode")


def CheckPtpMode(field: Any) -> None:
    """Validate PTP mode enum (NEnum)."""
    v = str(field.value)
    if v not in ("follower", "bmc"):
        raise InvalidObject("invalid PTP mode")


def CheckIgmpVersion(field: Any) -> None:
    """Validate IGMP version enum (NEnum)."""
    v = str(field.value)
    if v not in ("none", "v2", "v3"):
        raise InvalidObject("invalid IGMP version")


def CheckTestSignalMode(field: Any) -> None:
    """Validate test signal mode enum (NEnum)."""
    v = str(field.value)
    if v not in ("video", "audio_video"):
        raise InvalidObject("invalid test signal mode")


def CheckConnectionMode(field: Any) -> None:
    """Validate connection mode enum (NEnum)."""
    v = str(field.value)
    if v not in ("manual", "sdp", "quick_connect"):
        raise InvalidObject("invalid connection mode")
