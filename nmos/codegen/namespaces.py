# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Namespace configuration for the Python NMOS implementation.

Each constant selects between standard (urn:x-nmos:) and private (urn:x-matrox:)
namespaces for a feature area. Changing a value here and regenerating types
(python -m nmos.codegen.generate) switches the namespace for all affected
JSON keys, enums, and capabilities.

The alternate namespace is always accepted on decode (dual-namespace tolerance),
but only the configured namespace is used for encode.
"""

# ---------------------------------------------------------------------------
# Attribute namespaces (JSON keys on generated types)
# ---------------------------------------------------------------------------
SYNCMEDIA_NAMESPACE = "urn:x-matrox:"
INFOBLOCK_NAMESPACE = "urn:x-matrox:"
CLOCKREF_NAMESPACE = "urn:x-matrox:"
CHANORDER_NAMESPACE = "urn:x-matrox:"
HKEP_NAMESPACE = ""
PRIVACY_NAMESPACE = ""
USB_NAMESPACE = ""
H26x_NAMESPACE = ""

# ---------------------------------------------------------------------------
# Capability namespaces (constraint URNs in IS-04/IS-11 caps)
#
# IMPORTANT: caps/MatroxCCF.py defines its own CapFormat* / CapTransport*
# string constants (e.g., CapFormatConstantBitRate) that MUST use the same
# namespace as defined here.  If any *_CAP_NAMESPACE value changes, the
# corresponding constants in MatroxCCF.py MUST be updated to match.
# ---------------------------------------------------------------------------
SYNCMEDIA_CAP_NAMESPACE = "urn:x-matrox:"
INFOBLOCK_CAP_NAMESPACE = "urn:x-matrox:"
CLOCKREF_CAP_NAMESPACE = "urn:x-matrox:"
CHANORDER_CAP_NAMESPACE = "urn:x-matrox:"
HKEP_CAP_NAMESPACE = "urn:x-nmos:"
PRIVACY_CAP_NAMESPACE = "urn:x-nmos:"
USB_CAP_NAMESPACE = "urn:x-nmos:"
H26x_CAP_NAMESPACE = "urn:x-nmos:"

# ---------------------------------------------------------------------------
# Transport namespaces
# ---------------------------------------------------------------------------
NDI_TRANSPORT_NAMESPACE = "urn:x-matrox:"
SRT_TRANSPORT_NAMESPACE = "urn:x-matrox:"
USB_TRANSPORT_NAMESPACE = "urn:x-nmos:"


# ---------------------------------------------------------------------------
# Helper: build the namespace map used by codegen and enum registration
# ---------------------------------------------------------------------------

NAMESPACE_MAP: dict[str, str] = {
    # Attribute namespaces
    "SYNCMEDIA_NAMESPACE": SYNCMEDIA_NAMESPACE,
    "INFOBLOCK_NAMESPACE": INFOBLOCK_NAMESPACE,
    "CLOCKREF_NAMESPACE": CLOCKREF_NAMESPACE,
    "CHANORDER_NAMESPACE": CHANORDER_NAMESPACE,
    "HKEP_NAMESPACE": HKEP_NAMESPACE,
    "PRIVACY_NAMESPACE": PRIVACY_NAMESPACE,
    "USB_NAMESPACE": USB_NAMESPACE,
    "H26x_NAMESPACE": H26x_NAMESPACE,
    # Capability namespaces
    "SYNCMEDIA_CAP_NAMESPACE": SYNCMEDIA_CAP_NAMESPACE,
    "INFOBLOCK_CAP_NAMESPACE": INFOBLOCK_CAP_NAMESPACE,
    "H26x_CAP_NAMESPACE": H26x_CAP_NAMESPACE,
    "CLOCKREF_CAP_NAMESPACE": CLOCKREF_CAP_NAMESPACE,
    "CHANORDER_CAP_NAMESPACE": CHANORDER_CAP_NAMESPACE,
    "HKEP_CAP_NAMESPACE": HKEP_CAP_NAMESPACE,
    "PRIVACY_CAP_NAMESPACE": PRIVACY_CAP_NAMESPACE,
    "USB_CAP_NAMESPACE": USB_CAP_NAMESPACE,
    # Transport namespaces
    "NDI_TRANSPORT_NAMESPACE": NDI_TRANSPORT_NAMESPACE,
    "SRT_TRANSPORT_NAMESPACE": SRT_TRANSPORT_NAMESPACE,
    "USB_TRANSPORT_NAMESPACE": USB_TRANSPORT_NAMESPACE,
}


def alternate_namespaces(urn: str) -> list[str]:
    """Return all alternate-namespace variants of a URN.

    Used for dual-namespace decode tolerance. A field may have up to 3 forms:
    - Full URN with x-matrox: "urn:x-matrox:privacy"
    - Full URN with x-nmos:   "urn:x-nmos:privacy"
    - Bare name:              "privacy"

    Returns a list of alternates (excluding the input itself).
    """
    alternates: list[str] = []

    if urn.startswith("urn:x-matrox:"):
        bare = urn[len("urn:x-matrox:"):]
        alternates.append("urn:x-nmos:" + bare)
        alternates.append(bare)
    elif urn.startswith("urn:x-nmos:"):
        bare = urn[len("urn:x-nmos:"):]
        alternates.append("urn:x-matrox:" + bare)
        alternates.append(bare)
    else:
        # Bare name — add both URN variants
        alternates.append("urn:x-matrox:" + urn)
        alternates.append("urn:x-nmos:" + urn)

    return alternates
