# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Making the browser trust the target's certificate without disabling checks.

The obvious way to reach an HTTPS endpoint whose certificate does not match the
URL is to turn verification off. That option is deliberately absent from this
driver, for one reason: a run with verification disabled looks exactly like a run
with verification working. Every screenshot is identical. So a demo could quietly
stop proving anything about the server's identity and nobody would see it.

Two honest alternatives are implemented instead.

**Connect by the certificate's own name.** The node binds a numeric address while
its certificate carries a ``XYZ-<serial>`` DNS SAN. If that name resolves to the
same address, connecting by name produces a fully clean chain and needs no browser
flag at all. This is preferred whenever it is available.

**Pin the key.** Otherwise, compute the SHA-256 of the certificate's
SubjectPublicKeyInfo and pass it to Chromium's
``--ignore-certificate-errors-spki-list``. That flag suppresses certificate errors
*only* for chains containing a listed key, so a substituted certificate still
fails the handshake. It is a narrowing of trust to one specific key, not an
abandonment of it.

Chain verification happens first, through the project's existing
:mod:`nmos.cert_check` — pinning a certificate that does not chain to the declared
root would pin whatever happened to be on the disk.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509 import Certificate, load_pem_x509_certificates

from nmos import cert_check

from ..enums import TlsPolicy
from ..errors import TlsPinError


@dataclass(frozen=True, slots=True)
class PinResult:
    """The outcome of resolving TLS trust for a target."""

    policy: TlsPolicy
    pins: tuple[str, ...] = ()
    san_names: tuple[str, ...] = ()
    verification: str = "none"
    detail: str = ""
    chain_length: int = 0

    def to_json(self) -> dict[str, object]:
        """Render for the manifest, so the reader can see how trust was set."""
        return {
            "policy": self.policy,
            "pins": list(self.pins),
            "san_names": list(self.san_names),
            "verification": self.verification,
            "detail": self.detail,
            "chain_length": self.chain_length,
        }


@dataclass(frozen=True, slots=True)
class CertificateMaterial:
    """The paths a node was started with, as read from its command line."""

    cert_path: str = ""
    key_path: str = ""
    serial_number: str = ""
    ca_paths: tuple[str, ...] = field(default_factory=tuple)


def load_chain(cert_path: str | Path) -> tuple[Certificate, ...]:
    """Load every certificate from a PEM file, leaf first.

    The node is started with a ``*.chain.pem`` — leaf followed by intermediates —
    and the leaf is what the listener presents, so order matters here.
    """
    path = Path(cert_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise TlsPinError(f"cannot read certificate {path}: {exc}") from exc

    try:
        certs = load_pem_x509_certificates(data)
    except ValueError as exc:
        raise TlsPinError(f"{path} is not a readable PEM chain: {exc}") from exc
    if not certs:
        raise TlsPinError(f"{path} contains no certificates")
    return tuple(certs)


def spki_pin(certificate: Certificate) -> str:
    """Compute one Chromium SPKI pin for a certificate.

    The format is base64 of the SHA-256 digest of the DER-encoded
    SubjectPublicKeyInfo — the same construction HPKP used. Hex would be the
    natural guess and is silently wrong: the flag would simply never match, and
    the only symptom would be a certificate error that the pin was supposed to
    have handled.
    """
    spki = certificate.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")


def compute_pins(
    chain: tuple[Certificate, ...],
    policy: TlsPolicy = TlsPolicy.PIN_LEAF_SPKI,
) -> tuple[str, ...]:
    """Pin the leaf only, or every certificate in the chain.

    Leaf-only is the default because it is the tightest. Pinning the whole chain
    is useful when the leaf is reissued often under a stable intermediate, at the
    cost of trusting anything that intermediate signs.
    """
    if policy is TlsPolicy.PIN_CHAIN_SPKI:
        return tuple(spki_pin(cert) for cert in chain)
    return (spki_pin(chain[0]),)


def verify_material(material: CertificateMaterial) -> tuple[str, str]:
    """Verify the certificate before it is trusted.

    Delegates to :mod:`nmos.cert_check`, which the node itself uses at start-up,
    rather than reimplementing chain validation — a second implementation would be
    a second opportunity to be subtly wrong about what counts as valid.

    Returns ``(verification, detail)`` where verification is:

    ``full``
        The chain, the serial-bound DNS SAN, and the key match were all checked.
    ``partial``
        The private key was unreadable — expected when the node runs as another
        user — so only the chain was verified. Recorded honestly rather than
        presented as a complete check.
    ``none``
        No trust material was supplied; only valid for a plaintext target.
    """
    if not material.ca_paths:
        raise TlsPinError(
            "no trusted-root CA was found on the node's command line, so its "
            "certificate cannot be verified before pinning"
        )
    if not material.cert_path:
        raise TlsPinError("the node was started without --nodeCertificate")

    ca_paths = list(material.ca_paths)

    if material.key_path and Path(material.key_path).is_file():
        try:
            cert_check.check_certificate(
                ca_paths,
                material.cert_path,
                material.key_path,
                material.serial_number,
            )
        except (cert_check.CertCheckError, OSError) as exc:
            raise TlsPinError(
                f"the node's certificate {material.cert_path} failed "
                f"verification against {ca_paths}: {exc}"
            ) from exc
        return "full", f"chain+SAN+key verified against {ca_paths}"

    # The key belongs to the node's user and may well not be readable here. That
    # is not an error -- but the resulting check is weaker, and saying so is the
    # difference between a verified pin and one that merely looks verified.
    try:
        cert_check.check_trusted_ca(ca_paths, material.cert_path)
    except (cert_check.CertCheckError, OSError) as exc:
        raise TlsPinError(
            f"the node's certificate {material.cert_path} does not chain to "
            f"{ca_paths}: {exc}"
        ) from exc
    return "partial", (
        f"chain verified against {ca_paths}; private key not readable, so the "
        f"key-match and SAN checks were skipped"
    )


def dns_names(cert_path: str) -> tuple[str, ...]:
    """The DNS SANs of a certificate's leaf.

    Reuses the project's own extractor so this driver and the node agree on what
    identities a certificate asserts.
    """
    try:
        return tuple(cert_check.cert_dns_identities(cert_path))
    except (cert_check.CertCheckError, OSError):
        return ()


def resolve_tls(
    material: CertificateMaterial,
    *,
    host: str,
    prefer_policy: TlsPolicy = TlsPolicy.PIN_LEAF_SPKI,
    resolves: Callable[[str], bool] | None = None,
) -> PinResult:
    """Decide how the browser will trust this target.

    ``resolves`` is an optional predicate taking a hostname and reporting whether
    it resolves to ``host``; when it accepts one of the certificate's SANs, the
    cleaner name-based path is chosen and no browser flag is needed at all.
    """
    verification, detail = verify_material(material)
    chain = load_chain(material.cert_path)
    sans = dns_names(material.cert_path)

    if resolves is not None:
        for name in sans:
            if resolves(name):
                return PinResult(
                    policy=TlsPolicy.SAN_HOSTNAME,
                    san_names=sans,
                    verification=verification,
                    detail=(f"{detail}; connecting by SAN {name!r}, so the chain "
                            f"validates without any browser flag"),
                    chain_length=len(chain),
                )

    pins = compute_pins(chain, prefer_policy)
    return PinResult(
        policy=prefer_policy,
        pins=pins,
        san_names=sans,
        verification=verification,
        detail=(f"{detail}; pinned {len(pins)} SPKI hash(es) because the URL host "
                f"{host!r} is not among the certificate's names {list(sans)}"),
        chain_length=len(chain),
    )


def chromium_args(result: PinResult) -> tuple[str, ...]:
    """The Chromium flags implied by a pin result.

    Only ever returns the narrow pinning flag. There is no code path here that can
    emit ``--ignore-certificate-errors`` or ``--allow-insecure-localhost``, and the
    launcher separately asserts neither appears in the arguments it assembles.
    """
    if result.policy in (TlsPolicy.PIN_LEAF_SPKI, TlsPolicy.PIN_CHAIN_SPKI) and result.pins:
        return (f"--ignore-certificate-errors-spki-list={','.join(result.pins)}",)
    return ()
