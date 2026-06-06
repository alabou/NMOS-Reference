# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Startup-time TLS certificate verification.

Helpers invoked during ``nmos_node.py`` startup to fail fast on
mis-configured cert / key / trusted-root-CA combinations.

The two helpers are:

* :func:`check_trusted_ca` — verifies that the leaf cert in a
  trusted-CA bundle chains to the global root CA.
* :func:`check_certificate` — verifies that a server cert chains
  to the global root CA via the intermediates packaged in the
  ``*.chain.pem`` file, that its SAN includes the expected
  per-serial DNS entry, and that the supplied private key
  matches the cert's public key.

Both raise :class:`CertCheckError` on failure (subclass of
``ValueError``); ``nmos_node.py`` translates that into a fatal
``SystemExit`` so the operator gets a single-line, actionable
diagnostic.
"""

from __future__ import annotations

from typing import Any, Sequence

from cryptography import x509
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
)
from OpenSSL import crypto


class CertCheckError(ValueError):
    """Raised when a startup cert/key/CA validation fails."""


# ---------------------------------------------------------------------------
# PEM loading
# ---------------------------------------------------------------------------

def _load_pem_certs(path: str) -> list[crypto.X509]:
    """Load every ``-----BEGIN CERTIFICATE-----`` block from a PEM file.

    Returns the certs in file order so a ``chain.pem`` file
    (leaf followed by intermediates) yields ``[leaf, *intermediates]``.
    Non-cert PEM blocks (e.g. private keys mixed in) are skipped.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise CertCheckError(f"cannot read {path!r}: {exc}") from exc

    certs: list[crypto.X509] = []
    marker = b"-----END CERTIFICATE-----"
    parts = data.split(marker)
    for raw in parts:
        chunk = raw.strip()
        if not chunk or b"-----BEGIN CERTIFICATE-----" not in chunk:
            continue
        full = chunk + b"\n" + marker + b"\n"
        try:
            certs.append(crypto.load_certificate(crypto.FILETYPE_PEM, full))
        except crypto.Error:
            # Mixed file with non-cert blocks — skip and keep walking.
            continue
    if not certs:
        raise CertCheckError(f"no PEM certificates found in {path!r}")
    return certs


# ---------------------------------------------------------------------------
# Chain verification
# ---------------------------------------------------------------------------

def _verify_chain(
    leaf: crypto.X509,
    roots: list[crypto.X509],
    intermediates: list[crypto.X509] | None = None,
) -> None:
    """Run pyOpenSSL's X509StoreContext, mapping ``X509StoreContextError``
    to :class:`CertCheckError` with a clear message."""
    store = crypto.X509Store()
    for r in roots:
        store.add_cert(r)
    ctx = (
        crypto.X509StoreContext(store, leaf, chain=intermediates)
        if intermediates
        else crypto.X509StoreContext(store, leaf)
    )
    try:
        ctx.verify_certificate()
    except crypto.X509StoreContextError as exc:
        raise CertCheckError(f"certificate chain validation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_trusted_ca(
    root_ca_paths: str | Sequence[str],
    trusted_ca_path: str,
) -> None:
    """Verify that ``trusted_ca_path``'s leaf cert chains to a cert in
    any of the ``root_ca_paths``.

    Supports multi-root validation: ``root_ca_paths`` may be a single
    path or any iterable of paths. Every PEM cert found across the
    supplied files is loaded into one trust store, and the leaf in
    ``trusted_ca_path`` must chain to one of them.

    Used during nmos_node.py startup whenever ``--nodeTrustedRootCA``
    (or ``--oauth2TrustedRootCA``) is supplied alongside the global
    ``--trustedRootCA``: it asserts that the per-service trusted-CA
    bundle is itself trusted by the global anchor, refusing to start
    otherwise.
    """
    paths: list[str] = (
        [root_ca_paths] if isinstance(root_ca_paths, str) else list(root_ca_paths)
    )
    trusted_certs = _load_pem_certs(trusted_ca_path)
    root_certs: list[crypto.X509] = []
    for p in paths:
        root_certs.extend(_load_pem_certs(p))
    _verify_chain(trusted_certs[0], root_certs)


def check_certificate(
    root_ca_paths: str | Sequence[str],
    cert_path: str,
    key_path: str,
    serial_number: str,
) -> None:
    """Verify a server cert + private key + serial-bound SAN entry.

    Supports multi-root validation: ``root_ca_paths`` may be a single
    path or any iterable of paths. Performs three independent checks:

    1. ``cert_path`` (treated as a ``chain.pem`` — leaf followed by
       intermediates) chains to a cert in any of the ``root_ca_paths``.
    2. The leaf's Subject Alternative Name list includes
       ``f"XYZ-{serial_number}"``. The actual Example-issued certs
       under ``Certificates/build.0/`` use the uppercase ``XYZ-`` prefix
       in their SAN list (verified against ExampleDeviceServer.*.pem),
       so this implementation follows the cert generator's actual
       output.
    3. The supplied private key (``key_path``) matches the leaf cert's
       public key (modulus equality for RSA, public-point equality
       for EC).
    """
    paths: list[str] = (
        [root_ca_paths] if isinstance(root_ca_paths, str) else list(root_ca_paths)
    )
    chain_certs = _load_pem_certs(cert_path)
    root_certs: list[crypto.X509] = []
    for p in paths:
        root_certs.extend(_load_pem_certs(p))

    leaf = chain_certs[0]
    intermediates = chain_certs[1:]
    _verify_chain(leaf, root_certs, intermediates=intermediates)

    # SAN check — uppercase "XYZ-<serial>" matching the certificate
    # generator's actual output.
    expected_dns = f"XYZ-{serial_number}"
    sans = _extract_dns_sans(leaf)
    if expected_dns not in sans:
        raise CertCheckError(
            f"certificate {cert_path!r} SAN does not include "
            f"{expected_dns!r}; found DNS SANs: {sans}"
        )

    # Key↔cert match — load both and compare public-key material.
    try:
        with open(key_path, "rb") as f:
            key_pem = f.read()
    except OSError as exc:
        raise CertCheckError(f"cannot read {key_path!r}: {exc}") from exc

    try:
        key_obj: Any = load_pem_private_key(key_pem, password=None)
    except (ValueError, TypeError) as exc:
        raise CertCheckError(
            f"cannot load private key {key_path!r}: {exc}"
        ) from exc

    # Compare via DER-serialized public-key blobs — works uniformly
    # across RSA / EC / Ed25519 / Ed448 / X25519 / X448 without
    # needing per-algorithm type narrowing.
    leaf_pubkey = leaf.to_cryptography().public_key()
    leaf_der = leaf_pubkey.public_bytes(
        encoding=Encoding.DER,
        format=PublicFormat.SubjectPublicKeyInfo,
    )
    key_der = key_obj.public_key().public_bytes(
        encoding=Encoding.DER,
        format=PublicFormat.SubjectPublicKeyInfo,
    )
    if leaf_der != key_der:
        raise CertCheckError(
            f"private key {key_path!r} does not match certificate {cert_path!r}"
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _extract_dns_sans(cert: crypto.X509) -> list[str]:
    """Return the DNS-name SAN entries from a cert (empty list if absent)."""
    crypt_cert = cert.to_cryptography()
    try:
        ext = crypt_cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName,
        )
    except x509.ExtensionNotFound:
        return []
    return list(ext.value.get_values_for_type(x509.DNSName))


def cert_dns_identities(cert_path: str) -> list[str]:
    """Return the DNS names by which a TLS server cert may be identified —
    its Subject CN (when present and parseable as a DNS name) plus every
    DNS-type Subject Alternative Name, in declaration order, with
    duplicates removed.

    Used to populate ``node.tls_server_cert_names`` so the IS-10
    audience check (``nmos.oauth2.aud_entry_allows_current_node``)
    can verify that a token's ``aud`` entry corresponds to one of
    this Node's cert identities — required by IS-10 / "NMOS With
    OAuth2.0" §"Validation" (the ``aud`` DNS name MUST be a CN or
    SAN of the TLS server cert).

    Returns an empty list when the file is missing/unreadable or
    contains no certs — the caller should treat that as "no TLS
    identity available", which causes the audience check to fail
    closed (correct behaviour for unconfigured production).
    """
    try:
        certs = _load_pem_certs(cert_path)
    except CertCheckError:
        return []
    if not certs:
        return []
    leaf = certs[0]
    out: list[str] = []
    seen: set[str] = set()

    # CN — modern certs sometimes omit it (relying on SAN), but when
    # it's present and is a DNS name, it counts as an identity.
    crypt_cert = leaf.to_cryptography()
    for attr in crypt_cert.subject:
        if attr.oid == x509.NameOID.COMMON_NAME:
            cn = str(attr.value)
            if cn and cn not in seen:
                out.append(cn)
                seen.add(cn)

    # DNS-type SANs.
    for name in _extract_dns_sans(leaf):
        if name and name not in seen:
            out.append(name)
            seen.add(name)
    return out
