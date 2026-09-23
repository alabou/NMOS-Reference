# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""TLS *identities* — the certificate/key pairs this process presents.

TR-10-SEC §"TLS Certificate Type (TCT)" allows a device to be configured as
RSA (0), ECDSA (1) or **Both** (2). "Both" means more than one identity, and
the two directions need opposite mechanisms. Getting them the wrong way round
produces failures that are hard to read, so the reasoning lives here rather
than at each call site.

Serving (``load_identities``)
    Every pair goes into **one** ``SSLContext``. OpenSSL slots a certificate by
    key type, so an RSA and an ECDSA identity occupy different slots, and it
    picks between them per handshake according to what the *client* offered —
    the negotiated cipher suite under TLS 1.2, ``signature_algorithms`` under
    TLS 1.3. Measured against this repository's PKI, in Python and again in
    Rust (``rust/crates/nmos-registry-bin/tests/dual_certificate.rs``).

Connecting (``client_contexts``)
    Every pair gets its **own** ``SSLContext``, tried in the order
    :func:`client_identity_order` gives — ECDSA before RSA. A client context
    holding two identities does *not* answer the server's
    ``CertificateRequest`` with whichever one was asked for: measured, it
    presents ECDSA under TLS 1.3 and the last-loaded identity under TLS 1.2,
    then fails outright if the peer wanted the other. Python exposes no
    client-certificate callback to correct that — not in 3.12, 3.13 or 3.14,
    and not in pyOpenSSL — so the choice is made by retrying instead.

The order the options are *given in* carries no preference. Its only meaning is
that the *n*th certificate pairs with the *n*th key, and an operator listing
RSA first must get the same behaviour as one listing ECDSA first. Where a
preference genuinely exists — which identity a client offers first — it is
imposed by :func:`client_identity_order` rather than inherited from the command
line.

Never concatenate two leaf chains into one file as a shortcut. OpenSSL accepts
it silently: the first certificate becomes the leaf and the rest are filed as
intermediates, so the server keeps working for one algorithm and fails every
client that needs the other. It looks like success until someone brings a
client of the wrong flavour.
"""

from __future__ import annotations

import ssl
from typing import Callable, Literal, Sequence

from cryptography.hazmat.primitives.asymmetric import ec, rsa

from nmos.cert_check import CertCheckError, _load_pem_certs, cert_dns_identities

__all__ = [
    "KeyAlgorithm",
    "as_paths",
    "client_contexts",
    "PeerRejectedAllIdentities",
    "client_identity_order",
    "is_peer_rejected_identity",
    "has_identity",
    "leaf_public_key_algorithm",
    "load_identities",
    "pair_identities",
    "union_dns_identities",
]

KeyAlgorithm = Literal["rsa", "ecdsa", "other"]
"""What a leaf certificate's public key is, as TCT classifies it."""


def as_paths(values: Sequence[str] | str | None) -> list[str]:
    """Normalise a repeatable path option to the paths actually given.

    ``action="append"`` yields ``None`` when the flag is absent and a list
    otherwise, and a caller may still pass a bare string. Empty strings are
    dropped because they are how the scalar form spelled "not configured":
    without this, ``[""]`` is a truthy list and every ``if certs:`` guard
    silently flips meaning.

    Public because the bare-string case must be handled everywhere one of
    these options is read, and the obvious spelling is catastrophically wrong:
    ``list("client.chain.pem")`` is sixteen one-character paths, not one path,
    and nothing complains until something counts them.
    """
    if values is None:
        return []
    if isinstance(values, str):
        return [values] if values else []
    return [value for value in values if value]


def pair_identities(
    certs: Sequence[str] | str | None,
    keys: Sequence[str] | str | None,
    *,
    cert_flag: str,
    key_flag: str,
) -> list[tuple[str, str]]:
    """Pair each certificate with its key, or explain why they do not pair.

    This is the only place a count mismatch is detected. It has to be checked
    rather than zipped away, because ``zip`` would silently drop the extra and
    start a listener with an identity the operator did not ask for.
    """
    cert_paths = as_paths(certs)
    key_paths = as_paths(keys)
    if len(cert_paths) != len(key_paths):
        raise CertCheckError(
            f"{cert_flag} was given {len(cert_paths)} time(s) but {key_flag} "
            f"{len(key_paths)} time(s); pass one key per certificate, in the "
            f"same order",
        )
    return list(zip(cert_paths, key_paths))


def has_identity(
    certs: Sequence[str] | str | None,
    keys: Sequence[str] | str | None,
) -> bool:
    """Is at least one complete identity configured?

    Replaces ``if cert and key:``, which changes meaning once the options are
    lists — ``[""]`` is truthy.
    """
    return bool(as_paths(certs)) and bool(as_paths(keys))


def load_identities(
    context: ssl.SSLContext,
    pairs: Sequence[tuple[str, str]],
) -> None:
    """Load every identity into one **server** context.

    See the module docstring: this is the serving direction. Do not use it to
    build a client context — a multi-identity client ignores the peer's
    ``CertificateRequest``. Use :func:`client_contexts` there.
    """
    for certificate, key in pairs:
        context.load_cert_chain(certificate, key)


def client_contexts(
    pairs: Sequence[tuple[str, str]],
    make_context: Callable[[], ssl.SSLContext],
) -> list[ssl.SSLContext]:
    """One context per identity, for the caller to try in turn.

    The counterpart of :func:`load_identities`, and deliberately not a variant
    of it: see the module docstring for why a client cannot hold two identities
    in one context.

    ``make_context`` builds each bare context — the caller supplies it because
    every client applies its own trust anchors and TR-10-SEC policy first, and
    those differ per call site.

    Returns an empty list when no identity is configured, which is the ordinary
    case for a client that authenticates with nothing but the server's
    certificate. The caller keeps its own unauthenticated context for that.
    """
    contexts: list[ssl.SSLContext] = []
    for certificate, key in pairs:
        context = make_context()
        context.load_cert_chain(certificate, key)
        contexts.append(context)
    return contexts


class PeerRejectedAllIdentities(Exception):
    """Every configured client identity was refused by one peer.

    Raised only after each identity has been offered and rejected in turn, so
    it means "this peer accepts none of what we have" rather than "one
    certificate did not suit". The distinction is what makes it safe to treat
    as evidence about the *peer*:

    * A single identity being refused says nothing about the peer -- it may
      simply prefer the other flavour, which is what the retry is for.
    * All of them being refused is ambiguous between our credentials being
      unusable and this peer's trust store lacking our issuer. Those cannot be
      told apart from here, and the second is precisely the "could affect just
      one Registration API in a cluster" case that `Behaviour -
      Registration.md` gives as the reason to try another member.

    So a caller with a list of targets should count this as one failure against
    the current target and move on by its usual rule -- not suppress failover,
    and not fail over before the identities are exhausted.
    """

    def __init__(self, failures: Sequence[tuple[str, BaseException]]) -> None:
        self.failures = list(failures)
        detail = "; ".join(
            f"{algorithm or 'unreadable'}: {error}" for algorithm, error in failures
        )
        super().__init__(f"peer rejected every client identity -- {detail}")


# Alert names OpenSSL reports when a peer refuses the certificate *we* sent.
#
# Matched on text because the exception class cannot carry this: only the
# TLS 1.2 path yields `ClientConnectorSSLError`. Under TLS 1.3 the client's
# flight is last, so the peer's objection arrives after the handshake and
# aiohttp surfaces it from the request as `ClientOSError` or
# `ServerDisconnectedError` -- or a bare `ssl.SSLError` -- with only the alert
# name to distinguish it. Brittle by nature, so `test_tls_identity.py` drives
# real rejections on both TLS versions to keep an OpenSSL upgrade that renames
# one of these from silently disabling the retry.
_REJECTION_ALERTS = (
    # TLS 1.3: "certificate required" -- misleading, since we did send one; it
    # means none we sent was acceptable.
    "TLSV13_ALERT_CERTIFICATE_REQUIRED",
    "SSLV3_ALERT_BAD_CERTIFICATE",
    "SSLV3_ALERT_UNSUPPORTED_CERTIFICATE",
    "SSLV3_ALERT_CERTIFICATE_REVOKED",
    "SSLV3_ALERT_CERTIFICATE_EXPIRED",
    "SSLV3_ALERT_CERTIFICATE_UNKNOWN",
    "TLSV1_ALERT_UNKNOWN_CA",
    "TLSV1_ALERT_ACCESS_DENIED",
    "TLSV1_ALERT_DECRYPT_ERROR",
    # TLS 1.2 says only this, for a rejected client certificate and for half a
    # dozen unrelated faults. Included because the mTLS case is the one that is
    # recoverable by retrying; an unrelated handshake failure simply fails
    # again on the next identity and is reported with both errors attached.
    "SSLV3_ALERT_HANDSHAKE_FAILURE",
)


def is_peer_rejected_identity(exc: BaseException) -> bool:
    """Did the peer refuse the certificate *we* presented?

    The trigger for offering the next identity. Deliberately **not** true for
    ``ssl.SSLCertVerificationError`` (and aiohttp's
    ``ClientConnectorCertificateError``), which mean we refused *theirs* --
    the opposite fault, with a different fix, and one that retrying with
    another of our own certificates cannot possibly repair.
    """
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    for error in _causes(exc):
        if isinstance(error, ssl.SSLCertVerificationError):
            return False
        text = str(error)
        if any(alert in text for alert in _REJECTION_ALERTS):
            return True
    return False


def _causes(exc: BaseException) -> list[BaseException]:
    """``exc`` and everything it was raised from.

    aiohttp wraps the original ``ssl.SSLError`` in a ``ClientConnectorSSLError``
    whose ``str`` keeps the alert, but not always -- the TLS 1.3 path can wrap
    it in a `ClientOSError` whose message does not -- so the chain is walked
    rather than the outermost message alone.
    """
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def client_identity_order(
    pairs: Sequence[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Order client identities by which to offer a peer first: ECDSA, then RSA.

    Unlike the serving direction, the order a *client* tries its identities in
    is a real, observable preference, so it is decided here rather than left to
    however the operator happened to list the flags. Two reasons to lead with
    ECDSA:

    * It is what a dual-identity **server** will present to a modern client
      anyway. OpenSSL selects from the client's ``signature_algorithms``, and
      those list ``ecdsa_secp256r1_sha256`` ahead of the RSA schemes, so
      leading with ECDSA means the first attempt usually matches what the peer
      would have chosen and the retry is never paid.
    * It is the stronger and cheaper of the two at equivalent security, so the
      weaker one should be the fallback rather than the default.

    Stable within each group, so listing two ECDSA identities keeps the order
    they were given in. Certificates that cannot be read, or that are neither
    RSA nor ECDSA, sort last: an unreadable identity is the least likely to
    work, and trying it first would spend the retry budget on it.
    """
    rank = {"ecdsa": 0, "rsa": 1}
    return sorted(
        pairs,
        key=lambda pair: rank.get(
            leaf_public_key_algorithm(pair[0]) or "", len(rank),
        ),
    )


def leaf_public_key_algorithm(cert_path: str) -> KeyAlgorithm | None:
    """Classify a certificate's leaf by public-key algorithm.

    ``None`` when the file cannot be read or holds no certificate. This is what
    TCT is derived from: asking the certificate is filename-independent, so it
    stays correct for operator-supplied files that do not follow this PKI's
    ``.ec.`` naming convention.
    """
    try:
        certs = _load_pem_certs(cert_path)
    except CertCheckError:
        return None
    if not certs:
        return None
    public_key = certs[0].to_cryptography().public_key()
    if isinstance(public_key, rsa.RSAPublicKey):
        return "rsa"
    if isinstance(public_key, ec.EllipticCurvePublicKey):
        return "ecdsa"
    return "other"


def union_dns_identities(cert_paths: Sequence[str] | str | None) -> list[str]:
    """Every DNS name any configured leaf can be identified by, in order.

    With two identities the OAuth 2.0 audience check must accept a token naming
    either one, so the names are unioned rather than taken from the first
    certificate. ``cert_dns_identities`` already de-duplicates within one file;
    this preserves first-seen order across files.
    """
    names: list[str] = []
    for path in as_paths(cert_paths):
        for name in cert_dns_identities(path):
            if name not in names:
                names.append(name)
    return names
