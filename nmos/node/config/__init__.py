# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""JSON Config System for NMOS Resource Construction.

Loads standard NMOS BCP-004-01 capability constraint sets from JSON files
and constructs the corresponding Source → Flow → Sender/Receiver pipeline
on a Node.

Usage:
    builder = ConfigBuilder(node, verbose=True)
    sender_ids = builder.load_senders("config-senders.json")
    receiver_ids = builder.load_receivers("config-receivers.json")
"""

from __future__ import annotations

from typing import Any

from nmos.errors import InvalidParameter
from nmos.json.engine import JsonEngine
from nmos.node.config.extract import (
    extract_params_from_capset,
    build_video_source,
    build_audio_source,
    build_video_flow,
    build_audio_flow,
    build_sender,
    build_receiver,
)
from nmos.node.config.pipelines import PipelineType, select_pipeline, build_pipeline
from nmos.node.config.defaults import complete_capabilities

class ConfigBuilder:
    """Reads JSON config files and constructs NMOS resources on a Node.

    The JSON config files use standard NMOS BCP-004-01 constraint_sets format.
    The CCF framework (MatroxCCF) processes them into Caps objects, from which
    concrete resource values are extracted and the pipeline is built.

    Source→Receiver linking: senders can specify ``linked_receiver_group``
    (e.g., ``"RTP 2"``) to link their sources to a previously-built receiver.
    The ConfigBuilder tracks receivers by their ``(group_name, group_index)``
    and resolves the link at sender build time.
    """

    # Transport URN → group name (mirrors _TRANSPORT_NAMES in types.py but
    # works with raw URN strings before enum resolution)
    _TRANSPORT_GROUP_NAMES: dict[str, str] = {
        "urn:x-nmos:transport:rtp": "RTP",
        "urn:x-nmos:transport:rtp.mcast": "RTP",
        "urn:x-nmos:transport:rtp.ucast": "RTP",
        "urn:x-matrox:transport:rtp.tcp": "RTP",
        "urn:x-matrox:transport:srt": "SRT",
        "urn:x-matrox:transport:srt.rtp": "SRT",
        "urn:x-matrox:transport:srt.mp2t": "SRT",
        "urn:x-matrox:transport:ndi": "NDI",
        "urn:x-matrox:transport:usb": "USB",
        "urn:x-matrox:transport:rtsp": "RTSP",
        "urn:x-matrox:transport:rtsp.tcp": "RTSP",
        "urn:x-nmos:transport:udp": "UDP",
        "urn:x-matrox:transport:udp": "UDP",
    }

    def __init__(self, node: Any, verbose: bool = False) -> None:
        self.node = node
        self.verbose = verbose
        # Receiver group tracking: "RTP 2" → [(format_urn, receiver_static_id), ...]
        self._receiver_groups: dict[str, list[tuple[str, str]]] = {}
        # Per-group format counters for sequential role matching
        self._sender_link_counters: dict[tuple[str, str], int] = {}  # ("RTP 2", format) → count

    def load_senders(self, path: str) -> list[str]:
        """Load config-senders.json and build all senders.

        Returns list of static sender IDs.
        """
        with open(path, "r") as f:
            data = JsonEngine.parse_any(f.read())

        sender_ids: list[str] = []
        for sender_config in data.get("senders", []):
            _coerce_config_namespaces(sender_config)
            static_id = self._build_sender_pipeline(sender_config)
            sender_ids.append(static_id)

        return sender_ids

    def load_receivers(self, path: str) -> list[str]:
        """Load config-receivers.json and build all receivers.

        Returns list of static receiver IDs.
        """
        with open(path, "r") as f:
            data = JsonEngine.parse_any(f.read())

        receiver_ids: list[str] = []
        for receiver_config in data.get("receivers", []):
            _coerce_config_namespaces(receiver_config)
            static_id = self._build_receiver_from_config(receiver_config)
            receiver_ids.append(static_id)

        return receiver_ids

    def _build_sender_pipeline(self, config: dict[str, Any]) -> str:
        """Build the full sender pipeline from a JSON config entry.

        1. Validate constraint sets (preference=100 required, single values, etc.)
        2. Apply native/generic templates to fill missing capabilities
        3. Convert constraint_sets → CCF Caps
        4. Complete capabilities (IPMX, privacy, preference validation)
        5. Select pipeline type (simple, raw+coded, mux)
        6. Build Source(s) + Flow(s) + Sender
        """
        from caps.MatroxCCF import convert_caps_json_to_caps
        from nmos.node.config.templates import apply_template_to_constraint_set
        from nmos.node.config.defaults import validate_constraint_sets
        from nmos.errors import InvalidParameter

        format_urn = config["format"]
        transport_urn = config["transport"]
        label = config.get("label", "?")

        constraint_sets = config.get("constraint_sets", [])

        # Step 1: Validate config constraint sets
        errors = validate_constraint_sets(
            constraint_sets, format_urn, label, verbose=self.verbose,
        )
        if errors:
            raise InvalidParameter(
                f"config '{label}' has invalid constraint sets: {'; '.join(errors)}"
            )

        # Step 2: Apply templates — native (preference=100) vs generic
        for cs in constraint_sets:
            is_sub = "urn:x-matrox:cap:meta:format" in cs
            pref = cs.get("urn:x-nmos:cap:meta:preference", 0)
            is_native = (pref == 100)
            apply_template_to_constraint_set(
                cs, receiver=False, sub=is_sub, native=is_native,
                verbose=self.verbose,
            )

        # Convert constraint_sets to CCF Caps
        caps = convert_caps_json_to_caps({
            "constraint_sets": constraint_sets
        })

        # Complete capabilities with internal additions
        # IPMX flag: per-sender config overrides node-level, or inherit from node
        is_ipmx = config.get("ipmx", self.node.ipmx)
        caps = complete_capabilities(
            caps, is_ipmx=is_ipmx,
            has_privacy=self.node.privacy_enabled,
            verbose=self.verbose,
        )

        # Resolve linked_receiver_group if present
        linked_receiver_id: str | None = None
        linked_receiver_is_mux: bool = False
        linked_receiver_group = config.get("linked_receiver_group")
        if linked_receiver_group is not None:
            linked_receiver_id, linked_receiver_is_mux = self._resolve_linked_receiver(
                linked_receiver_group, format_urn, label,
            )

        # Select and build pipeline
        pipeline_type = select_pipeline(format_urn, caps)

        static_id = build_pipeline(
            self.node, config, caps, pipeline_type,
            is_sender=True, verbose=self.verbose,
            linked_receiver_id=linked_receiver_id,
            linked_receiver_is_mux=linked_receiver_is_mux,
        )

        if self.verbose:
            print(f"  Built sender pipeline: {config.get('label', '?')} "
                  f"({pipeline_type.name}) → {static_id}")

        return static_id

    def _resolve_linked_receiver(
        self, group_key: str, sender_format: str, sender_label: str,
    ) -> tuple[str | None, bool]:
        """Resolve a linked_receiver_group string to a receiver dynamic UUID.

        Resolution rule:
        1. Look up the target group (e.g., "RTP 2")
        2. If the group has a mux receiver → link to it
        3. If no mux → find the Nth same-format receiver (Nth = how many
           senders of this format have already linked to this group)

        Returns (receiver_dynamic_uuid, is_mux_receiver).
        """
        from nmos.node import _get_resource_core

        receivers = self._receiver_groups.get(group_key)
        if receivers is None:
            raise InvalidParameter(
                f"sender '{sender_label}': linked_receiver_group '{group_key}' "
                f"not found (available: {list(self._receiver_groups.keys())})"
            )

        # Check for mux receiver first (unique in group)
        mux_receivers = [(fmt, sid) for fmt, sid in receivers if "mux" in fmt]
        if mux_receivers:
            # Mux case: link to the mux receiver
            _fmt, static_id = mux_receivers[0]
            receiver = self.node.receivers.get(static_id)
            if receiver is None:
                return None, True
            inner = receiver.get() if hasattr(receiver, 'get') else receiver
            rv = inner.value if hasattr(inner, 'value') else inner
            core = getattr(rv, 'ReceiverCore', rv)
            return core.ResourceCore.Id.value, True

        # Independent case: match by sender format, sequential role index
        same_format = [(fmt, sid) for fmt, sid in receivers if fmt == sender_format]
        if not same_format:
            raise InvalidParameter(
                f"sender '{sender_label}': no receiver with format '{sender_format}' "
                f"in group '{group_key}'"
            )

        counter_key = (group_key, sender_format)
        role_idx = self._sender_link_counters.get(counter_key, 0)
        self._sender_link_counters[counter_key] = role_idx + 1

        if role_idx >= len(same_format):
            raise InvalidParameter(
                f"sender '{sender_label}': no more receivers of format '{sender_format}' "
                f"in group '{group_key}' (have {len(same_format)}, need #{role_idx})"
            )

        _fmt, static_id = same_format[role_idx]
        receiver = self.node.receivers.get(static_id)
        if receiver is None:
            return None, False
        inner = receiver.get() if hasattr(receiver, 'get') else receiver
        rv = inner.value if hasattr(inner, 'value') else inner
        core = getattr(rv, 'ReceiverCore', rv)
        return core.ResourceCore.Id.value, False

    def _build_receiver_from_config(self, config: dict[str, Any]) -> str:
        """Build a receiver from a JSON config entry."""
        from caps.MatroxCCF import convert_caps_json_to_caps
        from nmos.node.config.templates import apply_template_to_constraint_set
        from nmos.node.config.defaults import validate_constraint_sets
        from nmos.errors import InvalidParameter

        format_urn = config.get("format", "")
        label = config.get("label", "?")

        constraint_sets = config.get("constraint_sets", [])

        # Step 1: Validate config constraint sets
        errors = validate_constraint_sets(
            constraint_sets, format_urn, label, verbose=self.verbose,
        )
        if errors:
            raise InvalidParameter(
                f"config '{label}' has invalid constraint sets: {'; '.join(errors)}"
            )

        # Step 2: Apply templates — native (preference=100) vs generic
        for cs in constraint_sets:
            is_sub = "urn:x-matrox:cap:meta:format" in cs
            pref = cs.get("urn:x-nmos:cap:meta:preference", 0)
            is_native = (pref == 100)
            apply_template_to_constraint_set(
                cs, receiver=True, sub=is_sub, native=is_native,
                verbose=self.verbose,
            )

        caps = convert_caps_json_to_caps({
            "constraint_sets": constraint_sets
        })

        is_ipmx = config.get("ipmx", self.node.ipmx)
        caps = complete_capabilities(
            caps, is_ipmx=is_ipmx,
            has_privacy=self.node.privacy_enabled,
            is_receiver=True,
            verbose=self.verbose,
        )

        static_id = build_pipeline(
            self.node, config, caps, PipelineType.SIMPLE,
            is_sender=False, verbose=self.verbose,
        )

        # Track receiver in group lookup for source→receiver linking
        transport_urn = config.get("transport", "")
        group_name = self._TRANSPORT_GROUP_NAMES.get(transport_urn, "GROUP")
        group_index = config.get("natural_group_index", 0)
        group_key = f"{group_name} {group_index}"
        format_urn = config.get("format", "")
        self._receiver_groups.setdefault(group_key, []).append((format_urn, static_id))

        if self.verbose:
            print(f"  Built receiver: {config.get('label', '?')} → {static_id} (group={group_key})")

        return static_id


# ---------------------------------------------------------------------------
# Namespace coercion for config JSON files
# ---------------------------------------------------------------------------

_COERCE_TABLE: dict[str, str] | None = None


def _get_coerce_table() -> dict[str, str]:
    """Build a coercion table mapping alternate-namespace URNs to configured ones.

    Each entry maps a specific suffix under the wrong namespace to the configured form.
    Built from namespaces.py — one entry per namespace constant.
    """
    global _COERCE_TABLE
    if _COERCE_TABLE is not None:
        return _COERCE_TABLE

    from nmos.codegen.namespaces import (
        SRT_TRANSPORT_NAMESPACE, NDI_TRANSPORT_NAMESPACE, USB_TRANSPORT_NAMESPACE,
        SYNCMEDIA_NAMESPACE, INFOBLOCK_NAMESPACE, CLOCKREF_NAMESPACE,
        CHANORDER_NAMESPACE, H26x_NAMESPACE,
        SYNCMEDIA_CAP_NAMESPACE, INFOBLOCK_CAP_NAMESPACE, H26x_CAP_NAMESPACE,
        CLOCKREF_CAP_NAMESPACE, CHANORDER_CAP_NAMESPACE,
        HKEP_CAP_NAMESPACE, PRIVACY_CAP_NAMESPACE, USB_CAP_NAMESPACE,
    )

    # (configured_prefix, suffix) → for each, we accept the alternate prefix too
    _KNOWN_SUFFIXES: list[tuple[str, str]] = [
        # Transport namespaces — specific transport families
        (SRT_TRANSPORT_NAMESPACE, "transport:srt"),
        (NDI_TRANSPORT_NAMESPACE, "transport:ndi"),
        (USB_TRANSPORT_NAMESPACE, "transport:usb"),
        # Attribute namespaces — specific attribute names
        (SYNCMEDIA_NAMESPACE, "synchronous_media"),
        (SYNCMEDIA_NAMESPACE, "layer"),
        (SYNCMEDIA_NAMESPACE, "layer_compatibility_groups"),
        (INFOBLOCK_NAMESPACE, "info_block"),
        (CLOCKREF_NAMESPACE, "clock_ref_type"),
        (CHANORDER_NAMESPACE, "channel_order"),
        # Cap namespaces
        (SYNCMEDIA_CAP_NAMESPACE, "cap:transport:synchronous_media"),
        (SYNCMEDIA_CAP_NAMESPACE, "cap:meta:layer"),
        (SYNCMEDIA_CAP_NAMESPACE, "cap:meta:layer_enabled"),
        (SYNCMEDIA_CAP_NAMESPACE, "cap:meta:format"),
        (SYNCMEDIA_CAP_NAMESPACE, "cap:meta:layer_compatibility_groups"),
        (SYNCMEDIA_CAP_NAMESPACE, "cap:format:video_layers"),
        (SYNCMEDIA_CAP_NAMESPACE, "cap:format:audio_layers"),
        (SYNCMEDIA_CAP_NAMESPACE, "cap:format:data_layers"),
        (INFOBLOCK_CAP_NAMESPACE, "cap:transport:info_block"),
        (INFOBLOCK_CAP_NAMESPACE, "cap:meta:info_block"),
        (H26x_CAP_NAMESPACE, "cap:format:constant_bit_rate"),
        (H26x_CAP_NAMESPACE, "cap:transport:parameter_sets_flow_mode"),
        (H26x_CAP_NAMESPACE, "cap:transport:parameter_sets_transport_mode"),
        (CLOCKREF_CAP_NAMESPACE, "cap:transport:clock_ref_type"),
        (CHANORDER_CAP_NAMESPACE, "cap:transport:channel_order"),
        (HKEP_CAP_NAMESPACE, "cap:transport:hkep"),
        (PRIVACY_CAP_NAMESPACE, "cap:transport:privacy"),
        (USB_CAP_NAMESPACE, "cap:transport:usb_class"),
    ]

    table: dict[str, str] = {}
    for configured_prefix, suffix in _KNOWN_SUFFIXES:
        configured_urn = configured_prefix + suffix
        # Map both alternate prefixes to the configured one
        for alt_prefix in ("urn:x-matrox:", "urn:x-nmos:", ""):
            alt_urn = alt_prefix + suffix
            if alt_urn != configured_urn:
                table[alt_urn] = configured_urn
        # Also handle prefix-only matches for transports (e.g., "transport:srt.rtp")
        if suffix.startswith("transport:"):
            # Match sub-variants like srt.rtp, srt.mp2t
            base = suffix  # e.g., "transport:srt"
            for alt_prefix in ("urn:x-matrox:", "urn:x-nmos:"):
                if alt_prefix != configured_prefix:
                    # Register the base and common sub-variants
                    table[alt_prefix + base] = configured_prefix + base

    _COERCE_TABLE = table
    return table


def _coerce_urn(urn: str) -> str:
    """Coerce a URN string to the configured namespace.

    Only affects namespace-switchable URNs. Standard NMOS URNs (e.g.,
    urn:x-nmos:transport:rtp.mcast) are NOT changed.
    """
    table = _get_coerce_table()
    # Exact match
    if urn in table:
        return table[urn]
    # Prefix match for transport sub-variants (srt.rtp, srt.mp2t, etc.)
    for alt, configured in table.items():
        if urn.startswith(alt + ".") or urn.startswith(alt + ":"):
            return configured + urn[len(alt):]
    return urn


def _coerce_config_namespaces(config: dict[str, Any]) -> None:
    """Coerce namespace-dependent URN strings in a config dict to the configured namespace.

    Config JSON files may use either urn:x-nmos: or urn:x-matrox: for namespace-switchable
    fields. This normalizes them to match namespaces.py so the node always emits the
    configured namespace.
    """

    def _coerce_dict(d: dict[str, Any]) -> None:
        for key in list(d.keys()):
            val = d[key]
            # Coerce string values (e.g., "transport": "urn:x-nmos:transport:srt")
            if isinstance(val, str):
                coerced = _coerce_urn(val)
                if coerced != val:
                    d[key] = coerced
            elif isinstance(val, dict):
                # Coerce dict keys (e.g., constraint_set URN keys)
                for k in list(val.keys()):
                    ck = _coerce_urn(k)
                    if ck != k:
                        val[ck] = val.pop(k)
                _coerce_dict(val)
            elif isinstance(val, list):
                for i, item in enumerate(val):
                    if isinstance(item, str):
                        val[i] = _coerce_urn(item)
                    elif isinstance(item, dict):
                        _coerce_dict(item)

    _coerce_dict(config)
