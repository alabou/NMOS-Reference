# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IPMX security configuration tags — VSF TR-10-SECURITY §8.

The spec mandates that a compliant IPMX Node publishes five tags in the
Node's ``tags`` attribute, each carrying the current effective value of a
security-configuration option. The validator (under ``security/``) reads
these tags from ``GET /x-nmos/node/v1.3/self`` and cross-checks them
against the operator's declared configuration.

The five tags and their domains:

  ``urn:x-vsf:tag:tr-10-sec:nap-config/v1.0``   Node Access Policy
  ``urn:x-vsf:tag:tr-10-sec:rap-config/v1.0``   Registry Access Policy
  ``urn:x-vsf:tag:tr-10-sec:raam-config/v1.0``  Restricted Access Authorization Mode
  ``urn:x-vsf:tag:tr-10-sec:oaim-config/v1.0``  OAuth 2.0 Audience Identification Mode
  ``urn:x-vsf:tag:tr-10-sec:tct-config/v1.0``   TLS Certificate Type

Each value is a JSON array of strings: ``["<digit>"]`` where ``<digit>``
is the single decimal digit (in string form) of the configuration value.
An optional second array element (≤128 chars) may carry a free-form
description; v1 omits it.

The reference-node's existing CLI flags don't use the spec's parameter
names, but the equivalent functionality is present. This module derives
each tag value from the launch-time argparse namespace, so the tags
honestly reflect what the node actually does. No new CLI knobs are
introduced apart from ``--oauth2AudienceMode`` (added at the same time
as this module — see ``nmos_node.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

# ---------------------------------------------------------------------------
# Tag URNs (TR-10-SEC §8)
# ---------------------------------------------------------------------------

TAG_NAP = "urn:x-vsf:tag:tr-10-sec:nap-config/v1.0"
TAG_RAP = "urn:x-vsf:tag:tr-10-sec:rap-config/v1.0"
TAG_RAAM = "urn:x-vsf:tag:tr-10-sec:raam-config/v1.0"
TAG_OAIM = "urn:x-vsf:tag:tr-10-sec:oaim-config/v1.0"
TAG_TCT = "urn:x-vsf:tag:tr-10-sec:tct-config/v1.0"


# ---------------------------------------------------------------------------
# Enumerations (TR-10-SEC §9, §10, §11, §12)
# ---------------------------------------------------------------------------

class NAP(IntEnum):
    """Node Access Policy — TR-10-SEC §9 / §12.1."""
    UNRESTRICTED_RW = 0   # HTTP only, non-compliant — device "shall not claim compliance"
    UNRESTRICTED_RO = 1   # Reads open, writes require auth (not allowed with OAuth 2.0 — §9.2)
    RESTRICTED_RW = 2     # Full auth required — mandatory for compliance


class RAP(IntEnum):
    """Registry Access Policy — TR-10-SEC §10 / §12.2."""
    UNRESTRICTED_HTTP = 0
    UNRESTRICTED_HTTPS = 1  # server-auth TLS
    RESTRICTED_MTLS = 2     # mutual TLS to registry


class RAAM(IntEnum):
    """Restricted Access Authorization Mode — TR-10-SEC §11 / §12.3."""
    MTLS = 0                 # Mutual TLS only
    OAUTH2 = 1               # OAuth 2.0 with server TLS
    MTLS_PLUS_OAUTH2 = 2     # Both


class OAIM(IntEnum):
    """OAuth 2.0 Audience Identification Mode — TR-10-SEC §12.4.

    Selects how the ``aud`` claim of a Bearer token is matched against
    the Node. SERIAL_NUMBER (the default) requires that some aud entry
    contains the BCP-002-02 Instance Identifier as a substring AND that
    that entry also matches the TLS server certificate identity (CN /
    SAN). CERT_NAME matches aud entries against cert names directly
    with RFC 4592 DNS-wildcard semantics. EITHER tries both per entry.
    """
    SERIAL_NUMBER = 0
    CERT_NAME = 1
    EITHER = 2


class TCT(IntEnum):
    """TLS Certificate Type — TR-10-SEC §12.5."""
    RSA = 0    # ≥2048-bit keys
    ECDSA = 1  # ≥secp256r1 or X25519
    BOTH = 2   # Both simultaneously


# ---------------------------------------------------------------------------
# Configuration snapshot
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SecurityConfig:
    """Effective TR-10-SEC §8 configuration of a Node at launch time.

    Built from the argparse namespace by ``compute_security_tags``. The
    snapshot is immutable for the lifetime of the Node — admin-driven
    runtime reconfiguration is a separate spec concern (§12.14) and not
    implemented in reference-node v1.
    """
    nap: NAP
    rap: RAP
    raam: RAAM
    oaim: OAIM
    tct: TCT

    def to_tags(self) -> dict[str, list[str]]:
        """Map the snapshot to the dict shape used by NMOS Node ``tags``.

        The validator under ``security/`` reads these from
        ``GET /x-nmos/node/v1.3/self`` and asserts the first array
        element matches the operator's ``--config`` / ``--expect-*``
        declaration.
        """
        return {
            TAG_NAP: [str(int(self.nap))],
            TAG_RAP: [str(int(self.rap))],
            TAG_RAAM: [str(int(self.raam))],
            TAG_OAIM: [str(int(self.oaim))],
            TAG_TCT: [str(int(self.tct))],
        }


# ---------------------------------------------------------------------------
# Derivation from argparse namespace
# ---------------------------------------------------------------------------

# Mapping from --oauth2AudienceMode string to OAIM enum. The flag accepts
# the human-readable names; the tag emits the spec's numeric value.
_OAIM_BY_NAME: dict[str, OAIM] = {
    "serial": OAIM.SERIAL_NUMBER,
    "cert": OAIM.CERT_NAME,
    "either": OAIM.EITHER,
}


def _compute_nap(args: Any) -> NAP:
    """Derive NAP from the launch-time flags.

    Rules (in order, first match wins):
      - No TLS on the Node API → NAP=0 (HTTP only, non-compliant).
      - --nodeOptionalClientAuth AND NOT --oauth2 → NAP=1 (reads truly open,
        writes require client cert). NAP=1 is forbidden under OAuth 2.0
        per §9.2 ("even read access shall be explicitly provided by the
        OAuth 2.0 authorizations") so we only emit NAP=1 when OAuth 2.0
        is off — otherwise it collapses to NAP=2 because the OAuth 2.0
        middleware will require a Bearer token even on reads.
      - Otherwise → NAP=2.
    """
    cert = getattr(args, "nodeCertificate", "") or ""
    disable_tls = bool(getattr(args, "nodeDisableTLS", False))
    if disable_tls or not cert:
        return NAP.UNRESTRICTED_RW
    if getattr(args, "nodeOptionalClientAuth", False) and not getattr(args, "oauth2", False):
        return NAP.UNRESTRICTED_RO
    return NAP.RESTRICTED_RW


def _compute_rap(args: Any) -> RAP:
    """Derive RAP from the registry-facing flags.

    - --rdsDisableTLS → RAP=0 (HTTP).
    - --rdsClientCertificate + --rdsClientKey set → RAP=2 (mTLS to registry).
    - Otherwise → RAP=1 (server-auth TLS).
    """
    if getattr(args, "rdsDisableTLS", False):
        return RAP.UNRESTRICTED_HTTP
    if getattr(args, "rdsClientCertificate", "") and getattr(args, "rdsClientKey", ""):
        return RAP.RESTRICTED_MTLS
    return RAP.UNRESTRICTED_HTTPS


def _compute_raam(args: Any) -> RAAM:
    """Derive RAAM from the OAuth2 + node-mTLS flags.

    --nodeTrustedRootCA is ``action="append"`` so it parses as ``list[str]``
    or ``None``. Presence of any element means client-cert verification is
    enabled on inbound Node API requests, i.e. mTLS is in use.
    """
    has_mtls = bool(getattr(args, "nodeTrustedRootCA", None))
    has_oauth = bool(getattr(args, "oauth2", False))
    if has_mtls and has_oauth:
        return RAAM.MTLS_PLUS_OAUTH2
    if has_oauth:
        return RAAM.OAUTH2
    return RAAM.MTLS


def _compute_oaim(args: Any) -> OAIM:
    """Read OAIM from the explicit --oauth2AudienceMode flag (default ``serial``).

    Spec §12.4 mandates support for all three modes. Reference-node's
    OAuth 2.0 validator already implements all three (see
    ``nmos/oauth2/__init__.py:498-565`` — RFC 4592 wildcards included);
    this flag merely selects which one applies at runtime.
    """
    mode = getattr(args, "oauth2AudienceMode", None) or "serial"
    try:
        return _OAIM_BY_NAME[mode]
    except KeyError as exc:
        # argparse `choices=` should already prevent this; the fallback
        # is a defensive guard for direct programmatic use.
        raise ValueError(
            f"unknown --oauth2AudienceMode {mode!r}; expected one of "
            f"{sorted(_OAIM_BY_NAME)}"
        ) from exc


def _compute_tct(args: Any) -> TCT:
    """Infer TCT from the cert-file flavor.

    The Certificates/build.0/ workspace uses two filename conventions for
    server certs: ``ExampleDeviceServer.ABC.<serial>.chain.pem`` for RSA
    and ``ExampleDeviceServer.ABC.<serial>.chain.ec.pem`` for ECDSA.
    Reference-node currently accepts a single cert/key pair, so TCT
    reflects which flavor is mounted. TCT=2 (Both simultaneously) is a
    deferred feature — see Phase 0b item 3 in the plan; until it lands,
    this function never returns ``TCT.BOTH``.
    """
    cert = getattr(args, "nodeCertificate", "") or ""
    if ".ec." in cert or cert.endswith(".ec.pem") or cert.endswith(".ec.chain.pem"):
        return TCT.ECDSA
    return TCT.RSA


def compute_security_tags(args: Any) -> SecurityConfig:
    """Snapshot the Node's TR-10-SEC §8 configuration from argparse args.

    Called once at startup; the resulting ``SecurityConfig`` is then
    serialized to the Node's ``tags`` attribute via ``to_tags()``.
    """
    return SecurityConfig(
        nap=_compute_nap(args),
        rap=_compute_rap(args),
        raam=_compute_raam(args),
        oaim=_compute_oaim(args),
        tct=_compute_tct(args),
    )
