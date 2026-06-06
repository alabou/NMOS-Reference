# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Privacy Encryption Protocol (PEP) support for the controller UI.

Implements BCP-005-03 ("NMOS With Privacy Encryption") on the
controller side:

* **Option intersection** — given the IS-05 transport-parameter
  constraints of every selected sender + every selected receiver,
  compute the set of PEP protocols / modes / curves the operator is
  allowed to pick. Empty = cannot negotiate.

* **ECDH detection** — the mode name contains ``ECDH_`` when and only
  when the mode requires an ECDH public-key exchange prior to
  activation.

* **Field transfer** — at receiver activation, the controller
  forwards specific ``ext_privacy_*`` fields from the sender's
  currently-active IS-05 transport params to the receiver's staged
  params. The set depends on whether the mode is ECDH or not.

Source of truth for "is PEP supported on this resource": the
presence of non-``NULL`` enum values in the resource's IS-05
transport-parameter constraints on ``ext_privacy_mode``. No separate
``urn:x-matrox:cap:transport:privacy`` capability is consulted —
the presence of the ``ext_privacy_*`` keys in the constraints is
the whole signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


# IS-05 transport parameter names carrying PEP configuration. These
# are the field keys that appear under ``transport_params[0]`` in the
# per-leg array returned by GET /single/{senders|receivers}/{id}/
# {constraints|active|staged}/ — spec-stable, not Python-specific.
EXT_PRIVACY_PROTOCOL = "ext_privacy_protocol"
EXT_PRIVACY_MODE = "ext_privacy_mode"
EXT_PRIVACY_ECDH_CURVE = "ext_privacy_ecdh_curve"
EXT_PRIVACY_IV = "ext_privacy_iv"
EXT_PRIVACY_KEY_GENERATOR = "ext_privacy_key_generator"
EXT_PRIVACY_KEY_VERSION = "ext_privacy_key_version"
EXT_PRIVACY_KEY_ID = "ext_privacy_key_id"
EXT_PRIVACY_ECDH_SENDER_PUBLIC_KEY = "ext_privacy_ecdh_sender_public_key"
EXT_PRIVACY_ECDH_RECEIVER_PUBLIC_KEY = "ext_privacy_ecdh_receiver_public_key"

# Sentinel value the spec uses to mean "no privacy" on each enum.
# When the intersection contains only ``"NULL"`` (or is empty after
# filtering), PEP is not available for this selection.
_PRIVACY_NULL = "NULL"


@dataclass
class PrivacyOptions:
    """Computed intersection of PEP options for a selection.

    ``protocols`` / ``modes`` / ``curves`` are the sorted common
    values across every sender AND every receiver. If any of them is
    empty the operator's selection has no negotiable PEP
    configuration — the Privacy panel should render its "cannot
    negotiate" state.

    ``exclusivity_ok`` is ``True`` iff every device in the selection
    advertises the Node Reservation service. Independent of PEP: a
    device can support reservation without PEP.

    ``pep_available`` is a quick flag: ``True`` when at least
    ``protocols`` and ``modes`` are non-empty (curves is allowed to
    be empty when no selected mode is ECDH).
    """

    protocols: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    curves: list[str] = field(default_factory=list)
    exclusivity_ok: bool = False

    @property
    def pep_available(self) -> bool:
        return bool(self.protocols) and bool(self.modes)


def _extract_enum_values(
    transport_params: Any, key: str,
) -> set[str] | None:
    """Pull the enum value-set for ``key`` out of a per-leg
    transport-parameter-constraints object.

    ``transport_params`` is what
    ``GET /single/{senders|receivers}/{id}/constraints/`` returns —
    an array of per-leg dicts. We only look at the first leg
    (``transport_params[0]`` — single-leg IS-05 is the only mode the
    controller engages with).

    Returns:
      * a set of string values (excluding ``NULL``) if ``key`` is
        declared with an ``enum`` constraint,
      * ``None`` if the key is absent entirely or only mentions
        ``NULL`` — treated as "PEP not supported for this parameter"
        (fail-closed). Returns unavailable when the only enum entry
        is ``NULL``.
    """
    if not isinstance(transport_params, list) or not transport_params:
        return None
    leg_any: Any = transport_params[0]
    if not isinstance(leg_any, dict):
        return None
    leg: dict[Any, Any] = leg_any
    raw_constraint: Any = leg.get(key)
    if not isinstance(raw_constraint, dict):
        return None
    enum_raw: Any = raw_constraint.get("enum")
    if not isinstance(enum_raw, list):
        return None
    values: set[str] = set()
    for v_any in enum_raw:
        if isinstance(v_any, str) and v_any != _PRIVACY_NULL:
            values.add(v_any)
    return values or None


def _intersect_all(sets: Iterable[set[str] | None]) -> set[str]:
    """Set intersection that returns empty when any input is ``None``
    (fail-closed) or when the running intersection becomes empty.
    """
    iterator = iter(sets)
    try:
        first = next(iterator)
    except StopIteration:
        return set()
    if first is None:
        return set()
    result: set[str] = set(first)
    for s in iterator:
        if s is None:
            return set()
        result &= s
        if not result:
            return set()
    return result


def compute_privacy_options(
    sender_constraints: list[Any],
    receiver_constraints: list[Any],
    sender_devices: list[dict[str, Any]] | None = None,
    receiver_devices: list[dict[str, Any]] | None = None,
    device_service_resolver: Any = None,
) -> PrivacyOptions:
    """Compute the PEP options surface for a selection.

    Args:
      sender_constraints: the ``transport_params`` list from
        ``GET /single/senders/{id}/constraints/`` for each selected
        sender, in any order.
      receiver_constraints: same for each selected receiver.
      sender_devices / receiver_devices: the owning-device dicts for
        the senders / receivers respectively (order doesn't matter).
        Used only for the ``exclusivity_ok`` field.
      device_service_resolver: callable
        ``(device: dict) -> str | None`` — typically
        ``RemoteNodeClient.exclusive_service_base``. When omitted,
        ``exclusivity_ok`` is ``False``.

    Returns a ``PrivacyOptions`` with sorted lists and the
    exclusivity flag.
    """
    protocol_sets = [
        _extract_enum_values(tp, EXT_PRIVACY_PROTOCOL)
        for tp in sender_constraints + receiver_constraints
    ]
    mode_sets = [
        _extract_enum_values(tp, EXT_PRIVACY_MODE)
        for tp in sender_constraints + receiver_constraints
    ]
    curve_sets = [
        _extract_enum_values(tp, EXT_PRIVACY_ECDH_CURVE)
        for tp in sender_constraints + receiver_constraints
    ]

    protocols = sorted(_intersect_all(protocol_sets))
    modes = sorted(_intersect_all(mode_sets))
    curves = sorted(_intersect_all(curve_sets))

    exclusivity_ok = False
    if device_service_resolver is not None:
        devices = list(sender_devices or []) + list(receiver_devices or [])
        if devices:
            exclusivity_ok = all(
                device_service_resolver(d) is not None for d in devices
            )

    return PrivacyOptions(
        protocols=protocols,
        modes=modes,
        curves=curves,
        exclusivity_ok=exclusivity_ok,
    )


def is_ecdh_mode(mode: str | None) -> bool:
    """``True`` iff ``mode`` requires an ECDH public-key exchange
    prior to activation.

    String-based branching on the mode enum (``ECDH_AES256CTR`` /
    ``ECDH_AES128CTR`` / etc.). The spec defines ECDH variants with
    an ``ECDH_`` prefix, so substring detection on the prefix is the
    stable signal.
    """
    if not mode:
        return False
    return mode.startswith("ECDH_")


def sender_to_receiver_fields(
    sender_active_transport_params: Any,
    *,
    ecdh: bool,
) -> dict[str, Any]:
    """Extract the PEP fields to copy from a sender's currently-active
    transport params into the paired receiver's ``PATCH staged``
    body.

    Always forwarded when PEP is enabled:
      * ``ext_privacy_key_generator``
      * ``ext_privacy_key_version``
      * ``ext_privacy_key_id``

    Additionally when ``ecdh`` is ``True``:
      * ``ext_privacy_ecdh_sender_public_key``

    Per-transport activation flow appends these fields to the
    receiver's ``transport_params[0]`` after a GET of the sender's
    active transport params.

    ``sender_active_transport_params`` is the top-level
    ``transport_params`` array from
    ``GET /single/senders/{id}/active/`` — a list with one dict per
    leg. We read leg[0] only.

    Absent fields (sender didn't populate them) are simply omitted
    from the result — the receiver will use its own default.
    """
    if (not isinstance(sender_active_transport_params, list)
            or not sender_active_transport_params):
        return {}
    leg_any: Any = sender_active_transport_params[0]
    if not isinstance(leg_any, dict):
        return {}
    leg: dict[Any, Any] = leg_any

    out: dict[str, Any] = {}
    for key in (
        EXT_PRIVACY_KEY_GENERATOR,
        EXT_PRIVACY_KEY_VERSION,
        EXT_PRIVACY_KEY_ID,
    ):
        if key in leg:
            out[key] = leg[key]
    if ecdh and EXT_PRIVACY_ECDH_SENDER_PUBLIC_KEY in leg:
        out[EXT_PRIVACY_ECDH_SENDER_PUBLIC_KEY] = (
            leg[EXT_PRIVACY_ECDH_SENDER_PUBLIC_KEY]
        )
    return out


def receiver_to_sender_fields(
    receiver_active_transport_params: Any,
) -> dict[str, Any]:
    """Extract the PEP field to copy from a receiver's currently-active
    transport params into the paired sender's ``PATCH staged`` body.

    Only used in ECDH modes:
      * ``ext_privacy_ecdh_receiver_public_key``

    The receiver regenerates this value when it is deactivated (per
    spec); the controller reads the new value after deactivation and
    threads it into the sender's activation PATCH. If the field is
    absent, returns empty — caller treats as "receiver has no public
    key yet" and must deactivate+reactivate the receiver first.
    """
    if (not isinstance(receiver_active_transport_params, list)
            or not receiver_active_transport_params):
        return {}
    leg_any: Any = receiver_active_transport_params[0]
    if not isinstance(leg_any, dict):
        return {}
    leg: dict[Any, Any] = leg_any
    if EXT_PRIVACY_ECDH_RECEIVER_PUBLIC_KEY not in leg:
        return {}
    return {
        EXT_PRIVACY_ECDH_RECEIVER_PUBLIC_KEY: (
            leg[EXT_PRIVACY_ECDH_RECEIVER_PUBLIC_KEY]
        ),
    }
