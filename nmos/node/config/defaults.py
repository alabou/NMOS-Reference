# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Default capability additions — IPMX, privacy, transport constraints.

When the JSON config provides minimal constraint sets, the builder adds
missing capabilities using CCF inheritance. The published constraints are
the config constraints + these internal additions.

Validation rules enforced on config constraint sets:
1. At least one CS with preference=100 (native) per sender/receiver
2. Native CS: every parameter must have exactly one value (tip of pyramid)
3. Native CS media_type must match the resource's format URN
4. Generic CS (preference!=100) must have media_type
5. Sub-constraint CS must have urn:x-matrox:cap:meta:format
6. Sub-constraint CS must have urn:x-matrox:cap:meta:layer
"""

from __future__ import annotations

from typing import Any


def complete_capabilities(
    caps: Any,
    is_ipmx: bool = False,
    has_privacy: bool = False,
    is_receiver: bool = False,
    verbose: bool = False,
) -> Any:
    """Complete capabilities with templates, preference validation, and transport additions.

    1. Apply codec templates to fill missing capabilities per media_type
    2. Validate and fix preference hierarchy on trunk constraint sets
    3. Add IPMX transport constraints if is_ipmx=True
    4. Add privacy constraints if has_privacy=True
    5. Return the combined Caps (config + additions)

    Uses CCF inheritance (<-) to fill gaps without overriding user-specified values.
    """
    try:
        from caps.MatroxCCF import Caps
    except ImportError:
        return caps

    if not isinstance(caps, Caps):
        return caps

    # Validate preference hierarchy
    _validate_preference_hierarchy(caps, verbose)

    for capset in caps.capsets:
        if capset.format is not None:
            continue  # Only add transport constraints to trunk capsets

        if is_ipmx:
            _add_ipmx_constraints(capset, verbose)

        # Privacy constraint always present — reflects node's privacy state.
        _add_privacy_constraints(capset, has_privacy, verbose)

    return caps


# ---------------------------------------------------------------------------
# Config JSON validation (runs BEFORE CCF conversion)
# ---------------------------------------------------------------------------

# Format URN → allowed media_type prefixes
_FORMAT_MEDIA_PREFIX: dict[str, list[str]] = {
    "urn:x-nmos:format:video": ["video/"],
    "urn:x-nmos:format:audio": ["audio/"],
    "urn:x-nmos:format:data": ["data/", "application/"],
    "urn:x-nmos:format:mux": ["video/", "audio/", "data/", "application/"],
}


def validate_constraint_sets(
    constraint_sets: list[dict[str, Any]],
    format_urn: str,
    label: str,
    verbose: bool = False,
) -> list[str]:
    """Validate constraint sets from a config JSON entry.

    Returns a list of error messages.  Empty list = valid.
    Prints errors to stdout for user feedback when verbose=True.

    Rules enforced:
    1. At least one CS with preference=100 (native)
    2. Native CS: every parameter has exactly one value
    3. Native CS media_type matches format URN
    4. Generic CS must have media_type
    5. Sub-CS must have urn:x-matrox:cap:meta:format
    6. Sub-CS must have urn:x-matrox:cap:meta:layer
    7. (mux only) For each of video/audio/data, the max layer count
       claimed by any mux-level CS must not exceed the number of
       distinct sub-constraint layer indices declared for that format.
       Catches e.g. ``data_layers.max=1`` with no data sub-constraint —
       the claim is structurally unreachable because nothing describes
       what that data layer carries.
    8. Pyramid coverage: every native CS (pref=100) at a given
       (format, layer) partition must declare every parameter that
       some non-native alternative at the same partition declares.
       Otherwise CCF's ``are_native_in_pyramid`` returns False,
       disables the native-skip rule, and collapses controller
       intersections to the native's single-value operating point —
       the operator loses the ability to narrow a range parameter.
       Mirrors the coverage requirement at
       [caps/MatroxCCF.py:are_native_in_pyramid](nmos-reference/caps/MatroxCCF.py).
    """
    errors: list[str] = []

    has_native = False

    for i, cs in enumerate(constraint_sets):
        cs_label = cs.get("urn:x-nmos:cap:meta:label", f"constraint_set[{i}]")
        pref = cs.get("urn:x-nmos:cap:meta:preference", 0)
        is_sub = "urn:x-matrox:cap:meta:format" in cs or cs.get("urn:x-matrox:cap:meta:layer") is not None

        # Rule 1: detect native
        if pref == 100:
            has_native = True

            # Rule 2: native CS must have single values for NMOS standard caps.
            # Matrox-specific caps (urn:x-matrox:cap:format:*) like layer counts
            # in mux configs are structural metadata and may use ranges.
            for key, value in cs.items():
                if not key.startswith("urn:x-nmos:cap:format:") and not key.startswith("urn:x-nmos:cap:transport:"):
                    continue  # Only validate standard NMOS format/transport caps
                if not isinstance(value, dict):
                    continue
                if "enum" in value:
                    if len(value["enum"]) != 1:
                        errors.append(
                            f"[{cs_label}] {key}: native CS must have exactly 1 value, "
                            f"got {len(value['enum'])} ({value['enum']})"
                        )
                elif "minimum" in value or "maximum" in value:
                    errors.append(
                        f"[{cs_label}] {key}: ranges not allowed in native CS (preference=100)"
                    )

            # Rule 3: native media_type must match format URN
            mt = _extract_media_type_from_cs(cs)
            if mt and format_urn:
                allowed = _FORMAT_MEDIA_PREFIX.get(format_urn, [])
                if allowed and not any(mt.lower().startswith(p) for p in allowed):
                    errors.append(
                        f"[{cs_label}] media_type '{mt}' does not match "
                        f"format '{format_urn}' (expected {allowed})"
                    )

        else:
            # Rule 4: generic CS must have media_type
            if not is_sub:
                mt = _extract_media_type_from_cs(cs)
                if not mt:
                    errors.append(
                        f"[{cs_label}] generic CS (preference={pref}) must have "
                        f"urn:x-nmos:cap:format:media_type"
                    )

        # Rules 5 & 6: sub-constraint validation
        if is_sub:
            if "urn:x-matrox:cap:meta:format" not in cs:
                errors.append(
                    f"[{cs_label}] sub-constraint CS must have "
                    f"urn:x-matrox:cap:meta:format"
                )
            if cs.get("urn:x-matrox:cap:meta:layer") is None:
                errors.append(
                    f"[{cs_label}] sub-constraint CS must have "
                    f"urn:x-matrox:cap:meta:layer"
                )

    # Rule 1: require at least one native CS
    if not has_native:
        errors.append(
            f"no constraint set with preference=100 (native) — "
            f"at least one CS must define the native operating point"
        )

    # Rule 7: mux-only — declared layer counts must be describable.
    if format_urn == "urn:x-nmos:format:mux":
        # Per-format highest "maximum" value seen in any mux-level CS.
        # Mux-level CSs are those WITHOUT ``urn:x-matrox:cap:meta:format``
        # / ``urn:x-matrox:cap:meta:layer`` — they describe the whole
        # stream, not a specific layer.
        max_by_format: dict[str, int] = {"video": 0, "audio": 0, "data": 0}
        # Per-format set of distinct layer indices declared by sub-CSs.
        layers_by_format: dict[str, set[int]] = {
            "video": set(), "audio": set(), "data": set(),
        }
        for cs in constraint_sets:
            is_sub_cs = (
                "urn:x-matrox:cap:meta:format" in cs
                or cs.get("urn:x-matrox:cap:meta:layer") is not None
            )
            if is_sub_cs:
                sub_fmt = cs.get("urn:x-matrox:cap:meta:format", "")
                layer_idx = cs.get("urn:x-matrox:cap:meta:layer")
                if (
                    isinstance(sub_fmt, str)
                    and sub_fmt.startswith("urn:x-nmos:format:")
                    and isinstance(layer_idx, int)
                ):
                    fmt_key = sub_fmt[len("urn:x-nmos:format:"):]
                    if fmt_key in layers_by_format:
                        layers_by_format[fmt_key].add(layer_idx)
            else:
                for fmt_key in ("video", "audio", "data"):
                    urn = f"urn:x-matrox:cap:format:{fmt_key}_layers"
                    v: Any = cs.get(urn)
                    if isinstance(v, dict):
                        mx: Any = v.get("maximum")
                        if isinstance(mx, int) and mx > max_by_format[fmt_key]:
                            max_by_format[fmt_key] = mx
        for fmt_key in ("video", "audio", "data"):
            mx = max_by_format[fmt_key]
            declared = layers_by_format[fmt_key]
            if mx > len(declared):
                errors.append(
                    f"{fmt_key}_layers.maximum={mx} but only {len(declared)} "
                    f"{fmt_key} sub-constraint layer index(es) declared "
                    f"({sorted(declared) or 'none'}) — every claimed layer "
                    f"needs at least one sub-constraint describing it"
                )

    # Rule 8: pyramid coverage — for CCF's ``are_native_in_pyramid`` to
    # return True, every native CS must be covered by at least one
    # non-native alt at the same (format, layer) partition. "Covered"
    # means the alt declares a superset of the native's params (every
    # alt-declared param must also be declared on the native, because
    # a missing param on the native is unconstrained/infinite which is
    # not ``⊆`` any finite alt declaration).
    #
    # This rule is permissive: we only require that SOME alt covers
    # the native (matching CCF's exists-quantifier). We additionally
    # restrict candidate alts to those whose ``media_type`` enum
    # intersects the native's ``media_type`` — a JPEG-XS alt can't
    # pyramid-cover an H.264 native at the same (video, layer 0)
    # partition even if param-sets align.
    _META_KEYS = {
        "urn:x-nmos:cap:meta:label",
        "urn:x-nmos:cap:meta:preference",
        "urn:x-nmos:cap:meta:enabled",
        "urn:x-nmos:cap:meta:format",
        "urn:x-nmos:cap:meta:layer",
        "urn:x-matrox:cap:meta:format",
        "urn:x-matrox:cap:meta:layer",
        "urn:x-matrox:cap:meta:layer_enabled",
        "urn:x-matrox:cap:meta:layer_compatibility_groups",
    }

    def _media_types(cs: dict[str, Any]) -> set[str]:
        mt_v: Any = cs.get("urn:x-nmos:cap:format:media_type")
        if not isinstance(mt_v, dict):
            return set()
        enum_v: Any = mt_v.get("enum")
        if not isinstance(enum_v, list):
            return set()
        return {str(x) for x in enum_v if isinstance(x, (str, int, float))}

    by_partition: dict[tuple[str | None, int | None], list[dict[str, Any]]] = {}
    for cs in constraint_sets:
        if cs.get("urn:x-nmos:cap:meta:enabled", True) is False:
            layer_en = cs.get("urn:x-matrox:cap:meta:layer_enabled", False)
            if layer_en is not True:
                continue  # disabled CS — ignore
        sub_fmt_any = cs.get("urn:x-matrox:cap:meta:format")
        sub_fmt = sub_fmt_any if isinstance(sub_fmt_any, str) else None
        layer_any = cs.get("urn:x-matrox:cap:meta:layer")
        layer_key = layer_any if isinstance(layer_any, int) else None
        by_partition.setdefault((sub_fmt, layer_key), []).append(cs)

    for part_key, part_css in by_partition.items():
        natives = [c for c in part_css
                   if c.get("urn:x-nmos:cap:meta:preference") == 100]
        alts = [c for c in part_css
                if c.get("urn:x-nmos:cap:meta:preference", 0) != 100]
        if not natives or not alts:
            continue
        for native in natives:
            native_mts = _media_types(native)
            native_params = {
                k for k in native.keys()
                if isinstance(k, str) and k not in _META_KEYS
            }
            candidates: list[tuple[dict[str, Any], set[str]]] = []
            for alt in alts:
                alt_mts = _media_types(alt)
                # media_type filter: alts must offer the native's type
                # (or have no media_type at all, which happens for
                # trunk-less alt shapes).
                if native_mts and alt_mts and not (native_mts & alt_mts):
                    continue
                alt_params = {
                    k for k in alt.keys()
                    if isinstance(k, str) and k not in _META_KEYS
                }
                candidates.append((alt, alt_params))
            if not candidates:
                continue  # no media-type-compatible alt → no coverage requirement
            # Native is covered if SOME candidate alt's params ⊆ native's params.
            covered = any(
                alt_params.issubset(native_params)
                for _, alt_params in candidates
            )
            if covered:
                continue
            # Pick the candidate with fewest missing params for the message.
            def _missing_count(c: tuple[dict[str, Any], set[str]]) -> int:
                return len(c[1] - native_params)
            best_alt, best_params = min(candidates, key=_missing_count)
            missing = sorted(best_params - native_params)
            native_label = native.get(
                "urn:x-nmos:cap:meta:label", f"(native at {part_key})",
            )
            best_label = best_alt.get(
                "urn:x-nmos:cap:meta:label", "(alt)",
            )
            errors.append(
                f"[{native_label}] missing pyramid-coverage params vs "
                f"{best_label!r}: {missing} — a native CS must declare "
                f"every param some non-native alt at the same (format, "
                f"layer) partition declares, otherwise CCF's "
                f"``are_native_in_pyramid`` returns False and narrowing "
                f"collapses to the native's single-value operating point"
            )

    if verbose and errors:
        print(f"  Config validation errors for '{label}':")
        for err in errors:
            print(f"    ! {err}")

    return errors


def _extract_media_type_from_cs(cs: dict[str, Any]) -> str | None:
    """Extract media_type string from a constraint set dict."""
    mt_val = cs.get("urn:x-nmos:cap:format:media_type")
    if mt_val is None:
        return None
    enum_list = mt_val.get("enum") if isinstance(mt_val, dict) else None
    if enum_list and isinstance(enum_list, list) and len(enum_list) > 0:
        return str(enum_list[0])
    return None


# ---------------------------------------------------------------------------
# CCF-level validation (runs AFTER CCF conversion)
# ---------------------------------------------------------------------------

def _is_fully_pinned(capset: Any) -> bool:
    """Check whether a constraint set is fully pinned (tip of the pyramid).

    A fully-pinned constraint set has every capability specifying exactly
    one allowed value — no ranges, no infinite, no multiple enum values.
    This is the native operating point of the device and qualifies for
    preference=100.

    Returns False if:
    - The constraint set has no capabilities (too broad)
    - Any capability is infinite (unconstrained)
    - Any capability has a min/max range (not a single-value enum)
    - Any capability enumerates more than one value
    """
    if not capset.caps:
        return False  # Empty caps = unconstrained, not a pinned point

    for cap in capset.caps.values():
        rv = cap.value
        # Infinite = unconstrained
        if rv.infinite:
            return False
        # Empty = NUL range (nothing allowed) — not a valid native point
        if rv.empty:
            return False
        # Has min/max range = not a single enumerated value
        if rv.min is not None or rv.max is not None:
            return False
        # Must have exactly one enumerated value
        if rv.values is None or len(rv.values) != 1:
            return False

    return True


def _validate_preference_hierarchy(caps: Any, verbose: bool) -> None:
    """Validate and fix the preference hierarchy on constraint sets.

    Rules enforced:
    - Native trunk (highest preference trunk): preference 100
    - Generic/alternative trunk(s): preference >= 1 (NOT 0)
    - Native sub-constraints: preference 100
    - Alternative sub-constraints: preference 0

    A generic trunk with preference 0 is ambiguous with alternative
    sub-constraints. The builder auto-corrects it to 1 so controllers
    can distinguish trunk alternatives from sub-constraint alternatives.
    """
    # Identify trunk constraint sets (no format/layer metadata)
    trunks = [cs for cs in caps.capsets if cs.format is None]
    subs = [cs for cs in caps.capsets if cs.format is not None]

    if len(trunks) == 0:
        return  # No trunk constraint sets at all

    # Single trunk: promote to preference=100 if fully pinned (tip of the
    # capability pyramid — every parameter has exactly one allowed value).
    # A broad constraint set (ranges, multiple enum values, or few parameters)
    # stays at its original preference.
    if len(trunks) == 1:
        native_trunk = trunks[0]
        if native_trunk.preference != 100 and _is_fully_pinned(native_trunk):
            if verbose:
                print(f"    ! Defaulting fully-pinned single trunk '{native_trunk.label}' "
                      f"preference: {native_trunk.preference} → 100 (native)")
            native_trunk.preference = 100
        return

    # Sort trunks by preference descending
    trunks_sorted = sorted(trunks, key=lambda cs: cs.preference, reverse=True)

    # The highest-preference trunk should be 100 (native)
    native_trunk = trunks_sorted[0]
    if native_trunk.preference != 100:
        if verbose:
            print(f"    ! Fixed native trunk '{native_trunk.label}' preference: "
                  f"{native_trunk.preference} → 100")
        native_trunk.preference = 100

    # All other trunks must have preference >= 1
    for trunk in trunks_sorted[1:]:
        if trunk.preference < 1:
            if verbose:
                print(f"    ! Fixed generic trunk '{trunk.label}' preference: "
                      f"{trunk.preference} → 1 "
                      f"(must be >= 1 to distinguish from sub-constraint alternatives)")
            trunk.preference = 1


def _add_ipmx_constraints(capset: Any, verbose: bool) -> None:
    """Add IPMX transport constraints to a CapSet if not already present.

    This reference node uses an internal clock (no PTP) and produces only
    asynchronous media, so it advertises the narrowed operating point
    clock_ref_type=[internal], synchronous_media=[false] — a deliberate
    node-capability subset of the general IPMX envelope (which allows
    [ptp, internal] and, for receivers, synchronous_media [true, false]).

    Intentionally NOT published here:
    - HKEP (HDCP): not supported by this reference node.
    - info_block: not yet standardized.
    - bit_rate / packet_time / st2110_21_sender_type: bit_rate and packet_time are
      transport properties extracted from the SDP (get_sdp_to_caps), not capability
      completion; st2110_21_sender_type is not published as a capability.
    """
    try:
        from caps.MatroxCCF import (
            Cap, RangeValue, RangeType,
            CapTransportClockRefType, CapTransportSynchronousMedia,
        )
        from nmos.enums import Internal
    except ImportError:
        return

    # Clock reference: internal only (this node has no PTP)
    if CapTransportClockRefType not in capset.caps:
        capset.caps[CapTransportClockRefType] = Cap(
            name=CapTransportClockRefType,
            value=RangeValue(values=(Internal.s,), type=RangeType.STRING),
        )
        if verbose:
            print(f"    + Added IPMX constraint: {CapTransportClockRefType} = [{Internal.s}]")

    # Asynchronous media only
    if CapTransportSynchronousMedia not in capset.caps:
        capset.caps[CapTransportSynchronousMedia] = Cap(
            name=CapTransportSynchronousMedia,
            value=RangeValue(values=(False,), type=RangeType.BOOL),
        )
        if verbose:
            print(f"    + Added IPMX constraint: {CapTransportSynchronousMedia} = [False]")


def _add_privacy_constraints(capset: Any, privacy_enabled: bool, verbose: bool) -> None:
    """Add privacy transport constraint to a CapSet.

    Always sets CapTransportPrivacy to reflect the node's privacy state.
    When privacy_enabled=True: enum=[True] (privacy required)
    When privacy_enabled=False: enum=[False] (privacy not available)
    """
    try:
        from caps.MatroxCCF import Cap, RangeValue, RangeType, CapTransportPrivacy
    except ImportError:
        return

    capset.caps[CapTransportPrivacy] = Cap(
        name=CapTransportPrivacy,
        value=RangeValue(values=(privacy_enabled,), type=RangeType.BOOL),
    )
    if verbose:
        print(f"    + Privacy constraint: {CapTransportPrivacy} = [{privacy_enabled}]")
