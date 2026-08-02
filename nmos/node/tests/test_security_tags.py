# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.node.security_tags — TR-10-SEC §8 tag derivation.

The launch-time argparse namespace is the source of truth. These tests
fabricate small namespaces matching the three reference-node start
scripts (start-node1.sh, start-node1-noauth2.sh, start-node1-nomtls.sh)
and assert the derived NAP/RAP/RAAM/OAIM/TCT values match the spec
table for Configurations A (mTLS-only), B (OAuth 2.0 + server TLS),
and C (mTLS + OAuth 2.0).
"""

from __future__ import annotations

from argparse import Namespace

from nmos.node.security_tags import (
    NAP,
    OAIM,
    RAAM,
    RAP,
    TAG_NAP,
    TAG_OAIM,
    TAG_RAAM,
    TAG_RAP,
    TAG_TCT,
    TCT,
    SecurityConfig,
    compute_security_tags,
    has_authorization_mechanism,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ns(**overrides) -> Namespace:
    """Build an argparse namespace with the security-relevant defaults.

    Mirrors the defaults declared by ``nmos_node.py`` so each test only
    needs to set the flags that differ from the baseline.
    """
    base = dict(
        nodeCertificate="",
        nodeKey="",
        nodeDisableTLS=False,
        nodeTrustedRootCA=None,
        nodeOptionalClientAuth=False,
        rdsDisableTLS=False,
        rdsClientCertificate="",
        rdsClientKey="",
        oauth2=False,
        oauth2AudienceMode="serial",
    )
    base.update(overrides)
    return Namespace(**base)


# ---------------------------------------------------------------------------
# Configuration A — mTLS without OAuth 2.0 (start-node1-noauth2.sh)
# ---------------------------------------------------------------------------

def test_config_a_full_restricted_rw() -> None:
    """A typical Configuration A: TLS + mTLS, no OAuth2, default OAIM, RSA cert."""
    args = _ns(
        nodeCertificate="/p/ExampleDeviceServer.ABC.SNX00001.chain.pem",
        nodeKey="/p/ExampleDeviceServer.ABC.SNX00001.key",
        nodeTrustedRootCA=["/p/ExampleRootCA.pem"],
    )
    cfg = compute_security_tags(args)
    assert cfg == SecurityConfig(
        nap=NAP.RESTRICTED_RW,
        rap=RAP.UNRESTRICTED_HTTPS,
        raam=RAAM.MTLS,
        oaim=OAIM.SERIAL_NUMBER,
        tct=TCT.RSA,
    )


def test_config_a_unrestricted_ro_via_optional_client_auth() -> None:
    """Configuration A + --nodeOptionalClientAuth → NAP=1 (reads truly open)."""
    args = _ns(
        nodeCertificate="/p/ExampleDeviceServer.ABC.SNX00001.chain.pem",
        nodeKey="/p/ExampleDeviceServer.ABC.SNX00001.key",
        nodeTrustedRootCA=["/p/ExampleRootCA.pem"],
        nodeOptionalClientAuth=True,
    )
    assert compute_security_tags(args).nap is NAP.UNRESTRICTED_RO


def test_config_a_ecdsa_cert_emits_tct_ecdsa() -> None:
    args = _ns(
        nodeCertificate="/p/ExampleDeviceServer.ABC.SNX00001.chain.ec.pem",
        nodeKey="/p/ExampleDeviceServer.ABC.SNX00001.ec.key",
        nodeTrustedRootCA=["/p/ExampleRootCA.ec.pem"],
    )
    assert compute_security_tags(args).tct is TCT.ECDSA


# ---------------------------------------------------------------------------
# Configuration B — OAuth 2.0 with server TLS (start-node1-nomtls.sh)
# ---------------------------------------------------------------------------

def test_config_b_oauth2_only_no_mtls() -> None:
    args = _ns(
        nodeCertificate="/p/ExampleDeviceServer.ABC.SNX00001.chain.pem",
        nodeKey="/p/ExampleDeviceServer.ABC.SNX00001.key",
        # nodeTrustedRootCA absent — no client-cert verification on inbound
        oauth2=True,
        oauth2AudienceMode="serial",
    )
    cfg = compute_security_tags(args)
    assert cfg.raam is RAAM.OAUTH2
    assert cfg.nap is NAP.RESTRICTED_RW
    assert cfg.oaim is OAIM.SERIAL_NUMBER


def test_config_b_optional_client_auth_collapses_to_nap_2() -> None:
    """Per §9.2, NAP=1 is forbidden under OAuth 2.0. When --nodeOptionalClientAuth
    is set alongside --oauth2, the OAuth2 middleware still requires a Bearer
    token on every request, so reads are NOT truly open. We emit NAP=2 to
    honestly reflect what the device does."""
    args = _ns(
        nodeCertificate="/p/ExampleDeviceServer.ABC.SNX00001.chain.pem",
        nodeKey="/p/ExampleDeviceServer.ABC.SNX00001.key",
        oauth2=True,
        nodeOptionalClientAuth=True,
    )
    assert compute_security_tags(args).nap is NAP.RESTRICTED_RW


def test_config_b_oaim_cert_mode() -> None:
    args = _ns(
        nodeCertificate="/p/ExampleDeviceServer.ABC.SNX00001.chain.pem",
        nodeKey="/p/ExampleDeviceServer.ABC.SNX00001.key",
        oauth2=True,
        oauth2AudienceMode="cert",
    )
    assert compute_security_tags(args).oaim is OAIM.CERT_NAME


def test_config_b_oaim_either_mode() -> None:
    args = _ns(
        nodeCertificate="/p/ExampleDeviceServer.ABC.SNX00001.chain.pem",
        nodeKey="/p/ExampleDeviceServer.ABC.SNX00001.key",
        oauth2=True,
        oauth2AudienceMode="either",
    )
    assert compute_security_tags(args).oaim is OAIM.EITHER


# ---------------------------------------------------------------------------
# Configuration C — mTLS + OAuth 2.0 (start-node1.sh, optional)
# ---------------------------------------------------------------------------

def test_config_c_both_mtls_and_oauth2() -> None:
    args = _ns(
        nodeCertificate="/p/ExampleDeviceServer.ABC.SNX00001.chain.pem",
        nodeKey="/p/ExampleDeviceServer.ABC.SNX00001.key",
        nodeTrustedRootCA=["/p/ExampleRootCA.pem"],
        oauth2=True,
    )
    cfg = compute_security_tags(args)
    assert cfg.raam is RAAM.MTLS_PLUS_OAUTH2
    assert cfg.nap is NAP.RESTRICTED_RW


# ---------------------------------------------------------------------------
# Non-compliant: NAP=0 (HTTP only)
# ---------------------------------------------------------------------------

def test_no_cert_yields_nap_0() -> None:
    args = _ns()  # no cert, no key, no tls
    assert compute_security_tags(args).nap is NAP.UNRESTRICTED_RW


def test_explicit_disable_tls_yields_nap_0() -> None:
    args = _ns(
        nodeCertificate="/p/ExampleDeviceServer.ABC.SNX00001.chain.pem",
        nodeKey="/p/ExampleDeviceServer.ABC.SNX00001.key",
        nodeDisableTLS=True,
    )
    assert compute_security_tags(args).nap is NAP.UNRESTRICTED_RW


# ---------------------------------------------------------------------------
# RAP modes
# ---------------------------------------------------------------------------

def test_rap_http_when_disabled() -> None:
    args = _ns(rdsDisableTLS=True)
    assert compute_security_tags(args).rap is RAP.UNRESTRICTED_HTTP


def test_rap_mtls_when_client_cert_provided() -> None:
    args = _ns(
        rdsClientCertificate="/p/ExampleDeviceClient.ABC.SNX00001.chain.pem",
        rdsClientKey="/p/ExampleDeviceClient.ABC.SNX00001.key",
    )
    assert compute_security_tags(args).rap is RAP.RESTRICTED_MTLS


def test_rap_https_default() -> None:
    args = _ns()  # no rds* flags
    assert compute_security_tags(args).rap is RAP.UNRESTRICTED_HTTPS


# ---------------------------------------------------------------------------
# Tag-dict shape (the exact wire format consumed by the validator)
# ---------------------------------------------------------------------------

def test_to_tags_emits_five_urns_with_digit_strings() -> None:
    cfg = SecurityConfig(
        nap=NAP.RESTRICTED_RW,
        rap=RAP.UNRESTRICTED_HTTPS,
        raam=RAAM.OAUTH2,
        oaim=OAIM.CERT_NAME,
        tct=TCT.ECDSA,
    )
    tags = cfg.to_tags()
    assert tags == {
        TAG_NAP: ["2"],
        TAG_RAP: ["1"],
        TAG_RAAM: ["1"],
        TAG_OAIM: ["1"],
        TAG_TCT: ["1"],
    }
    # Every value must be a list whose first element is a single decimal
    # digit string — the format ``GET /self`` returns and the validator
    # parses.
    for urn, value in tags.items():
        assert isinstance(value, list)
        assert len(value) == 1
        assert value[0].isdigit()
        assert len(value[0]) == 1


# ---------------------------------------------------------------------------
# has_authorization_mechanism — is the reported NAP actually enforceable?
# ---------------------------------------------------------------------------
#
# NAP reports the configured policy; RAAM reports the mechanism enforcing it.
# The two can disagree: with TLS up and no mechanism at all, the tags advertise
# NAP=2 (Restricted Read Write) while every verb is in fact open. That is not
# one of the three policies -- §9.1 defines Unrestricted Read Write as "HTTP
# without TLS" -- so the tag is left as-is and the launcher warns instead.
# These tests pin which flag combinations count as "enforceable", because that
# predicate is what decides whether the operator is told.

def test_mtls_anchor_is_an_authorization_mechanism() -> None:
    """Configuration A: the TLS layer verifies a client certificate."""
    args = _ns(
        nodeCertificate="/p/cert.pem",
        nodeKey="/p/key.pem",
        nodeTrustedRootCA=["/p/ExampleRootCA.pem"],
    )
    assert has_authorization_mechanism(args) is True


def test_oauth2_is_an_authorization_mechanism() -> None:
    """Configuration B: no client-certificate anchor, but tokens are required.

    The case that makes this predicate more than ``bool(nodeTrustedRootCA)``.
    """
    args = _ns(
        nodeCertificate="/p/cert.pem", nodeKey="/p/key.pem", oauth2=True,
    )
    assert has_authorization_mechanism(args) is True
    assert compute_security_tags(args).nap is NAP.RESTRICTED_RW


def test_optional_client_auth_is_an_authorization_mechanism() -> None:
    """NAP=1: enforcement moves to the application, on writes only."""
    args = _ns(
        nodeCertificate="/p/cert.pem",
        nodeKey="/p/key.pem",
        nodeOptionalClientAuth=True,
    )
    assert has_authorization_mechanism(args) is True
    assert compute_security_tags(args).nap is NAP.UNRESTRICTED_RO


def test_tls_alone_is_not_an_authorization_mechanism() -> None:
    """The blind spot: encrypted, but nothing restricts anything.

    ``client_auth_required`` is false and the SSL context never sets
    ``verify_mode``, so reads and writes alike are accepted from any client
    completing the handshake -- while the tags still say NAP=2.
    """
    args = _ns(nodeCertificate="/p/cert.pem", nodeKey="/p/key.pem")
    assert has_authorization_mechanism(args) is False
    assert compute_security_tags(args).nap is NAP.RESTRICTED_RW


def test_all_three_certifiable_configurations_are_enforceable() -> None:
    """A, B and C must never trigger the warning.

    Config B is the one at risk from a naive check, since it carries no
    client-certificate anchor.
    """
    config_a = _ns(
        nodeCertificate="/p/cert.pem", nodeKey="/p/key.pem",
        nodeTrustedRootCA=["/p/ca.pem"],
    )
    config_b = _ns(
        nodeCertificate="/p/cert.pem", nodeKey="/p/key.pem", oauth2=True,
    )
    config_c = _ns(
        nodeCertificate="/p/cert.pem", nodeKey="/p/key.pem",
        nodeTrustedRootCA=["/p/ca.pem"], oauth2=True,
    )
    for args in (config_a, config_b, config_c):
        assert has_authorization_mechanism(args) is True
        assert compute_security_tags(args).nap is NAP.RESTRICTED_RW
