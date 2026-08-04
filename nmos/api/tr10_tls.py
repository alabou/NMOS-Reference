# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""TR-10-SEC §3 TLS cipher-suite + key-exchange-group restriction.

The spec enumerates the exact set of TLS 1.2 / 1.3 cipher suites and ECDH
key-exchange groups a compliant IPMX device is allowed to use:

  "Only the cipher suites and key exchange groups listed as 'shall',
  'should', or 'may' in this section shall be used. All other cipher
  suites are prohibited."

This module pins every ``ssl.SSLContext`` reference-node creates to that
whitelist. Both server contexts (Node API, control split listener) and
client contexts (registry, OAuth 2.0 AS, RDS query, RDS WebSocket) are
restricted — TR-10-SEC §3 doesn't carve out a direction-specific
exemption.

Python ssl-module limitations (as of CPython 3.12 + OpenSSL 3.0):

  - ``SSLContext.set_ciphers()`` restricts the TLS 1.2 cipher list. It is
    used here to enforce the whitelist for TLS 1.2 connections.
  - TLS 1.3 cipher suites are governed by ``SSL_CTX_set_ciphersuites()``
    in OpenSSL, which Python's ssl module does not expose. OpenSSL 3.0's
    default TLS 1.3 cipher list is ``TLS_AES_256_GCM_SHA384 :
    TLS_CHACHA20_POLY1305_SHA256 : TLS_AES_128_GCM_SHA256`` — all three
    are explicitly allowed by TR-10-SEC §3, so the default is compliant.
    A ``set_ciphersuites()`` call is wired in defensively for future
    Python/OpenSSL builds that expose it.
  - TLS group selection: the server accepts OpenSSL 3.0 defaults,
    which include all four TR-10-SEC whitelisted groups (X25519,
    secp256r1, secp521r1, X448). The validator's per-curve probe
    relaunches the registry proxy + fake AS with OPENSSL_CONF
    restricting ``Groups`` to one curve at a time, then verifies
    the DUT's CLIENT successfully handshakes against each — this
    is the §8-5 positive coverage path. (When Python adds
    ``SSLContext.set_groups``, this module can pin the whitelist
    explicitly per-context and close the secp384r1 closed-list
    side of §8-9 too.)

The validator's negative probe — "open a handshake with a prohibited
cipher and expect refusal" — passes against this implementation for all
TLS 1.2 prohibited suites and (in practice) for all TLS 1.3 prohibited
suites under OpenSSL 3.0.
"""

from __future__ import annotations

import ssl

# ---------------------------------------------------------------------------
# Allowed TLS 1.2 cipher suites (IANA names) — TR-10-SEC §3
# ---------------------------------------------------------------------------

# SHALL — mandatory support.
_TLS12_SHALL: tuple[str, ...] = (
    "TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256",
)

# SHOULD — should be supported.
_TLS12_SHOULD: tuple[str, ...] = (
    "TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256",
    "TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384",
    "TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384",
    "TLS_DHE_RSA_WITH_AES_128_GCM_SHA256",
    "TLS_DHE_RSA_WITH_AES_256_GCM_SHA384",
)

# MAY — permitted for backward compatibility / constrained devices.
# CBC-mode cipher suites are explicitly NOT included even though
# TR-10-SEC §3 permits them as MAY: the spec also says they should
# not be used unless ``encrypt_then_mac`` is successfully negotiated
# (SEC-8-7). Python's ``ssl`` module does not expose enough TLS
# extension introspection to make that guarantee, so we omit CBC
# ciphers from the whitelist entirely. The §3 SHALL ciphers
# (TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256 + TLS_AES_128_GCM_SHA256)
# plus the GCM/ChaCha20 SHOULD/MAY suites cover all the spec-mandated
# negotiation outcomes without the EtM caveat.
_TLS12_MAY: tuple[str, ...] = (
    "TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256",
    "TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256",
)

# IANA → OpenSSL cipher-string name. OpenSSL's set_ciphers() accepts the
# OpenSSL names (without the leading TLS_ and joined with hyphens); the
# IANA-to-OpenSSL conversion below mirrors the table at
# https://www.openssl.org/docs/man3.0/man1/openssl-ciphers.html.
_IANA_TO_OPENSSL: dict[str, str] = {
    "TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256":     "ECDHE-RSA-AES128-GCM-SHA256",
    "TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256":   "ECDHE-ECDSA-AES128-GCM-SHA256",
    "TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384":   "ECDHE-ECDSA-AES256-GCM-SHA384",
    "TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384":     "ECDHE-RSA-AES256-GCM-SHA384",
    "TLS_DHE_RSA_WITH_AES_128_GCM_SHA256":       "DHE-RSA-AES128-GCM-SHA256",
    "TLS_DHE_RSA_WITH_AES_256_GCM_SHA384":       "DHE-RSA-AES256-GCM-SHA384",
    "TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256":   "ECDHE-RSA-CHACHA20-POLY1305",
    "TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256": "ECDHE-ECDSA-CHACHA20-POLY1305",
}

# Full ordered list of TR-10-SEC-allowed TLS 1.2 cipher suites (IANA names).
TR10_TLS12_CIPHERS: tuple[str, ...] = _TLS12_SHALL + _TLS12_SHOULD + _TLS12_MAY

# OpenSSL cipher-string suitable for SSLContext.set_ciphers(). Ordered
# SHALL-first (preferred by server during negotiation) so handshakes
# converge on the strongest mutually-supported suite.
TR10_TLS12_CIPHER_STRING: str = ":".join(
    _IANA_TO_OPENSSL[c] for c in TR10_TLS12_CIPHERS
)


# ---------------------------------------------------------------------------
# Allowed TLS 1.3 cipher suites (IANA = OpenSSL names) — TR-10-SEC §3
# ---------------------------------------------------------------------------

# SHALL.
_TLS13_SHALL: tuple[str, ...] = (
    "TLS_AES_128_GCM_SHA256",
)
# SHOULD.
_TLS13_SHOULD: tuple[str, ...] = (
    "TLS_AES_256_GCM_SHA384",
    "TLS_AES_128_CCM_SHA256",
)
# MAY.
_TLS13_MAY: tuple[str, ...] = (
    "TLS_CHACHA20_POLY1305_SHA256",
)
TR10_TLS13_CIPHERS: tuple[str, ...] = _TLS13_SHALL + _TLS13_SHOULD + _TLS13_MAY

# OpenSSL TLS 1.3 ciphersuite string (used iff set_ciphersuites is
# available — see comment above).
TR10_TLS13_CIPHER_STRING: str = ":".join(TR10_TLS13_CIPHERS)


# ---------------------------------------------------------------------------
# Allowed ECDH key-exchange groups — TR-10-SEC §3
# ---------------------------------------------------------------------------

# SHALL — mandatory support.
_GROUPS_SHALL: tuple[str, ...] = (
    "x25519",        # OpenSSL name for X25519 / 25519
    "prime256v1",    # OpenSSL name for secp256r1
)
# SHOULD — should be supported.
_GROUPS_SHOULD: tuple[str, ...] = (
    "secp521r1",
    "x448",          # OpenSSL name for X448 / 448
)
TR10_GROUPS: tuple[str, ...] = _GROUPS_SHALL + _GROUPS_SHOULD

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_tr10_tls_restrictions(
    ctx: ssl.SSLContext,
    *,
    gcrl_path: str | None = None,
) -> None:
    """Apply TR-10-SEC §3 TLS-restriction policy to ``ctx``.

    Called by every SSLContext factory in reference-node (server and
    client side both) so the same whitelist governs every TLS endpoint
    the device participates in.

    Behavior:
      - Minimum TLS version pinned to 1.2 (TR-10-SEC SHALL).
      - TLS 1.2 cipher list restricted to the TR-10-SEC whitelist via
        ``set_ciphers(TR10_TLS12_CIPHER_STRING)``.
      - TLS 1.3 cipher list left at OpenSSL defaults — those defaults
        are already a TR-10-SEC-subset under OpenSSL 3.0; explicit
        restriction via ``set_ciphersuites()`` is attempted defensively
        and skipped silently if the method is unavailable.
      - ECDH groups left at OpenSSL 3.0 defaults — which include the
        four §3 whitelisted groups (X25519, secp256r1, secp521r1,
        X448). The closed-list rule of §8-9 ("only listed groups
        shall be used") leaves secp384r1 outside the whitelist; that
        gap will close when Python exposes ``SSLContext.set_groups``
        in a future release, at which point this function can pin
        the whitelist explicitly. Per-curve POSITIVE coverage is
        exercised by the validator at test time — it relaunches the
        registry proxy + fake AS with OPENSSL_CONF restricting Groups
        to one curve at a time and verifies the DUT successfully
        completes outbound handshakes.
      - Compression and renegotiation disabled (defense-in-depth — not
        explicitly TR-10-SEC mandated but standard hardening practice).
      - When ``gcrl_path`` is supplied (TR-10-SEC §12.14 GCRL), the
        PEM bundle at that path is loaded into ``ctx``'s verify store
        and ``VERIFY_CRL_CHECK_LEAF`` is enabled. The bundle may
        contain multiple ``-----BEGIN X509 CRL-----`` blocks (one per
        configured CA — CTCA, NESTCA, CESTCA); OpenSSL parses each
        block and matches it to its issuer CA from the trust store.
        Default (``gcrl_path=None``): no CRL checking — Node performs
        normal cert verification only. This per §14.3.3.5-3 means a
        device deployed without CRL configuration does not fail-closed
        on revocation; deployments that require revocation enforcement
        configure ``--gcrl``.
    """
    # TLS 1.2 minimum (TR-10-SEC §3 SHALL).
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    # TLS 1.2 cipher whitelist. ``set_ciphers`` returns silently on
    # success or raises ssl.SSLError if none of the requested ciphers
    # are supported by the linked OpenSSL build — we want the latter to
    # propagate so a misconfiguration is loud at startup.
    ctx.set_ciphers(TR10_TLS12_CIPHER_STRING)

    # TLS 1.3 cipher whitelist (defensive — may be a no-op on stdlib
    # builds without set_ciphersuites). The fallback to OpenSSL 3.0
    # defaults is TR-10-SEC-compliant; see the module docstring.
    set_ciphersuites = getattr(ctx, "set_ciphersuites", None)
    if callable(set_ciphersuites):
        set_ciphersuites(TR10_TLS13_CIPHER_STRING)

    # Defense-in-depth: disable compression (CRIME / BREACH vectors)
    # and explicit renegotiation. OpenSSL 3.0 already disables TLS-1.3
    # renegotiation; these flags cover the TLS 1.2 path.
    ctx.options |= ssl.OP_NO_COMPRESSION
    no_reneg = getattr(ssl, "OP_NO_RENEGOTIATION", 0)
    if no_reneg:
        ctx.options |= no_reneg

    # TR-10-SEC §12.14 Global CRL. Opt-in: only applied when the
    # operator passed ``--gcrl`` to nmos_node.py. The bundle is loaded
    # alongside the CA trust store; OpenSSL matches each CRL block to
    # its issuer CA at verify time and rejects any cert whose serial
    # is listed in the matching CRL.
    if gcrl_path:
        import os
        if not os.path.exists(gcrl_path):
            # SEC-14.3.3.5-3: "If a CRL is required but cannot be
            # retrieved ... the Node shall treat all certificates that
            # would have been validated against that CRL as invalid
            # and shall deny access." Fail closed at startup is the
            # cleanest way to honour this when the operator declared a
            # CRL path that we cannot read.
            raise RuntimeError(
                f"apply_tr10_tls_restrictions: --gcrl points at a "
                f"path that doesn't exist ({gcrl_path!r}) — refusing "
                f"to apply §3/§12.14 policy without the declared CRL "
                f"(SEC-14.3.3.5-3 fail-closed)"
            )
        ctx.load_verify_locations(cafile=gcrl_path)
        ctx.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF


# Placeholder kept for any external caller that imported the helper —
# now a no-op since the runtime warning case has gone away with the
# removal of the set_groups call.
def _maybe_warn_about_groups() -> None:
    probe = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    if not callable(getattr(probe, "set_groups", None)):
        import logging
        logging.getLogger(__name__).debug(
            "Python %s.%s does not expose SSLContext.set_groups; "
            "server-side TLS group selection follows OpenSSL "
            "defaults. Per-curve coverage is exercised by the "
            "validator's SEC-8-5 probe.",
            *__import__("sys").version_info[:2],
        )
