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
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509 import (
    Certificate, load_der_x509_certificate, load_pem_x509_certificates,
)

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
    connect_host: str = ""
    """Hostname to address the target by, when it should not be the discovered
    one. Empty means "keep what discovery found".

    Separate from :attr:`policy` because connecting by name and needing a pin
    are independent questions, and conflating them was a real defect: the SAN
    path used to imply "no browser flag needed", which is only true when the
    chain also anchors in a root the browser already trusts. With a private CA
    — which is what the reference PKI is — connecting by the certificate's own
    name still yields ``ERR_CERT_AUTHORITY_INVALID``. Now the name fixes
    hostname verification and the pin fixes authority verification, each on its
    own merits.
    """

    def to_json(self) -> dict[str, object]:
        """Render for the manifest, so the reader can see how trust was set."""
        return {
            "policy": self.policy,
            "pins": list(self.pins),
            "san_names": list(self.san_names),
            "verification": self.verification,
            "detail": self.detail,
            "chain_length": self.chain_length,
            "connect_host": self.connect_host,
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
    publicly_trusted: Callable[[str], bool] | None = None,
) -> PinResult:
    """Decide how the browser will trust this target.

    Two independent questions, answered separately:

    *Which name to connect by.* ``resolves`` is an optional predicate taking a
    hostname and reporting whether it resolves to ``host``; when it accepts one
    of the certificate's SANs, that name is used, which satisfies hostname
    verification.

    *Whether a pin is needed.* ``publicly_trusted`` reports whether a client
    using default trust actually accepts the server. Only when it does is the
    flagless :attr:`TlsPolicy.SAN_HOSTNAME` path taken. Omitting the predicate
    means "assume not trusted", which errs toward pinning — the safe direction,
    since a needless pin still verifies the key while a missing one blocks the
    page outright.
    """
    verification, detail = verify_material(material)
    chain = load_chain(material.cert_path)
    sans = dns_names(material.cert_path)

    # Connecting by one of the certificate's own names removes the hostname
    # half of verification. Done whenever a SAN resolves, independently of
    # whether a pin also turns out to be needed.
    connect_host = ""
    if resolves is not None:
        connect_host = next((name for name in sans if resolves(name)), "")

    # The authority half. A name-matched chain still fails in the browser when
    # its root is not one the browser ships — which is the normal case for the
    # reference PKI, whose ExampleRootCA is in no trust store. Measured rather
    # than assumed: `publicly_trusted` opens a real connection with default
    # trust and reports whether it verified.
    trusted = False
    if connect_host and publicly_trusted is not None:
        trusted = publicly_trusted(connect_host)

    if connect_host and trusted:
        return PinResult(
            policy=TlsPolicy.SAN_HOSTNAME,
            san_names=sans,
            verification=verification,
            detail=(f"{detail}; connecting by SAN {connect_host!r} and the chain "
                    f"anchors in a trusted root, so no browser flag is needed"),
            chain_length=len(chain),
            connect_host=connect_host,
        )

    pins = compute_pins(chain, prefer_policy)
    if connect_host:
        why = (f"connecting by SAN {connect_host!r}, but its root is not in the "
               f"browser's trust store")
    else:
        why = (f"the URL host {host!r} is not among the certificate's names "
               f"{list(sans)}")
    return PinResult(
        policy=prefer_policy,
        pins=pins,
        san_names=sans,
        verification=verification,
        detail=f"{detail}; pinned {len(pins)} SPKI hash(es) because {why}",
        chain_length=len(chain),
        connect_host=connect_host,
    )


def is_publicly_trusted(host: str, port: int, *, timeout: float = 2.0) -> bool:
    """Does a client using only default trust accept this server?

    A direct measurement rather than an inference: the same question the browser
    will ask, asked the same way. Assuming a chain is browser-trusted because it
    verified against the root the *node* was configured with is exactly the
    mistake that made the flagless path fail on the reference PKI, whose root is
    in no trust store.

    Any failure — unreachable, timeout, verification refused — answers "no",
    which routes the caller to pinning. That is the conservative direction.
    """
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                return True
    except (OSError, ssl.SSLError):
        return False


def pin_presented_leaf(
    host: str, port: int, ca_paths: tuple[str, ...], *, timeout: float = 2.0,
) -> tuple[str, str]:
    """Pin a server we have no certificate file for, such as the AS.

    The Authorization Server's certificate lives on the Authorization Server,
    not on the node whose command line discovery reads — so unlike the
    Controller's own certificate there is no path to load. It is fetched from
    the server instead.

    Fetching a key and trusting it would be trust-on-first-use, so the
    connection is made **with verification on** against ``ca_paths`` — the roots
    the node itself was told to trust for this Authorization Server. A leaf that
    does not chain to one of them never gets pinned. That keeps the same
    discipline as the file-based path: verify first, pin second.

    Returns ``(pin, detail)``, with an empty pin when the server could not be
    verified or reached.
    """
    if not ca_paths:
        return "", (f"no trusted root configured for {host}:{port}, so its "
                    f"certificate cannot be verified before pinning")
    context = ssl.create_default_context()
    for ca in ca_paths:
        try:
            context.load_verify_locations(ca)
        except OSError as exc:
            return "", f"cannot read {ca}: {exc}"
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as exc:
        return "", (f"{host}:{port} did not present a certificate verifiable "
                    f"against {list(ca_paths)}: {exc}")
    if not der:
        return "", f"{host}:{port} presented no certificate"

    leaf = load_der_x509_certificate(der)
    return spki_pin(leaf), (f"pinned the leaf {host}:{port} presented, after "
                            f"verifying it chains to the node's configured root")


def chromium_args(result: PinResult) -> tuple[str, ...]:
    """The Chromium flags implied by a pin result.

    Only ever returns the narrow pinning flag. There is no code path here that can
    emit ``--ignore-certificate-errors`` or ``--allow-insecure-localhost``, and the
    launcher separately asserts neither appears in the arguments it assembles.
    """
    if result.policy in (TlsPolicy.PIN_LEAF_SPKI, TlsPolicy.PIN_CHAIN_SPKI) and result.pins:
        return (f"--ignore-certificate-errors-spki-list={','.join(result.pins)}",)
    return ()
