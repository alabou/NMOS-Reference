# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""SDP transport file generation and receiver SDP processing.

Uses the sdp/ module (MatroxSdp, MatroxSdpWrite) for SDP parsing and encoding.
The SDP module has its own enum system (sdp.EnumId) separate from nmos.enums.EnumId.

Sender activation: generates SDP transport file from active transport params.
Receiver activation: parses incoming SDP to extract transport params + privacy.
"""

from __future__ import annotations

from typing import Any

from nmos.node.types import Activation, Privacy


# ---------------------------------------------------------------------------
# SDP privacy enum conversion (sdp.EnumId → nmos.enums.EnumId)
# ---------------------------------------------------------------------------

# Privacy protocol/mode conversion between SDP and NMOS enum systems.
# The two enum systems use the SAME string values, so we convert via string.

def _convert_sdp_enum_to_nmos(sdp_enum: Any) -> Any:
    """Convert an SDP EnumId to an NMOS EnumId via string matching.

    Both enum systems use the same URN string values for shared concepts.
    Returns None if the enum cannot be resolved.
    """
    if sdp_enum is None:
        return None
    try:
        from nmos.enums import EnumRegistry
        return EnumRegistry.get(str(sdp_enum))
    except (ImportError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Sender SDP generation
# ---------------------------------------------------------------------------

def generate_sender_sdp_transport_file(
    node: Any,
    sender_id: str,
    activation: Activation,
) -> Any:
    """Generate an SDP transport file from active sender transport parameters.

    Populates a MatroxSdp object from the active params (source_ip, dest_ip,
    dest_port, privacy, codec info), then encodes to SDP text.

    Returns the MatroxSdp object, or None if generation is not possible.
    """
    try:
        from sdp.MatroxSdp import MatroxSdp
        from sdp.MatroxSdpWrite import encode as sdp_encode
    except ImportError:
        return None

    sdp = MatroxSdp()

    # The actual SDP population from active transport params is
    # transport-specific and depends on the sender's format, codec,
    # and transport protocol. This is a framework that will be populated
    # per-transport as the format/codec integration is completed.
    #
    # For each enabled leg:
    #   active = activation.active[leg_index]
    #   Extract source_ip, dest_ip, dest_port from active params
    #   Set up media section (m= line)
    #   Set up connection (c= line)
    #   Set up source filter (a=source-filter)
    #   Set up rtpmap/fmtp (codec-specific)
    #   Set up privacy (a=privacy: ...)

    # For now, return a basic SDP structure
    # This will be fully populated when format/codec handlers are integrated

    return sdp


# ---------------------------------------------------------------------------
# Receiver SDP processing
# ---------------------------------------------------------------------------

def process_receiver_sdp_transport_file(
    activation: Activation,
    sdp_text: str,
    leg_index: int = 0,
) -> dict[str, Any]:
    """Parse an SDP transport file and extract transport parameters.

    Uses MatroxSdp.decode() to parse, then extracts:
    - SourceIp from source filter
    - MulticastIp from connection address (if multicast)
    - DestinationPort from media port
    - Privacy params from privacy descriptor

    Returns a dict of field_name → value to be patched into staged params.
    """
    try:
        from sdp.MatroxSdp import MatroxSdp
    except ImportError:
        return {}

    sdp = MatroxSdp()
    err = sdp.decode(sdp_text)
    if err is not None:
        return {}  # parse failed

    params: dict[str, Any] = {}

    # Extract from primary media (first media section)
    media = sdp.primary_media
    if media is None:
        return params

    # Source IP from source filter (sdpMediaDescriptor.SourceFilterSrcAddress)
    if media.source_filter_src_address:
        params["SourceIp"] = media.source_filter_src_address

    # Connection address → multicast IP (sdpMediaDescriptor.ConnectionAddress)
    if media.connection_address:
        addr = media.connection_address
        try:
            first_octet = int(addr.split(".")[0])
            if 224 <= first_octet <= 239:
                params["MulticastIp"] = addr
        except (ValueError, IndexError):
            pass

    # Destination port from media (sdpMediaDescriptor.Port)
    if media.port:
        params["DestinationPort"] = media.port

    # RTCP port (sdpMediaDescriptor.RtcpPort)
    if media.rtcp_port:
        params["RtcpDestinationPort"] = media.rtcp_port

    # Privacy parameters from SDP (sdpMediaDescriptor.Privacy.*)
    privacy_desc = media.privacy_desc

    if privacy_desc is not None:
        if privacy_desc.protocol is not None:
            nmos_protocol = _convert_sdp_enum_to_nmos(privacy_desc.protocol)
            if nmos_protocol:
                params["ExtPrivacyProtocol"] = nmos_protocol

        if privacy_desc.mode is not None:
            nmos_mode = _convert_sdp_enum_to_nmos(privacy_desc.mode)
            if nmos_mode:
                params["ExtPrivacyMode"] = nmos_mode

        if privacy_desc.iv:
            params["ExtPrivacyIV"] = privacy_desc.iv
        if privacy_desc.key_generator:
            params["ExtPrivacyKeyGenerator"] = privacy_desc.key_generator
        if privacy_desc.key_version:
            params["ExtPrivacyKeyVersion"] = privacy_desc.key_version
        if privacy_desc.key_id:
            params["ExtPrivacyKeyId"] = privacy_desc.key_id

    return params
