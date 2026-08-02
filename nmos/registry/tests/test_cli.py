# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``nmos_registry.py``'s command line and SSL context builders.

The CLI is the operator-facing surface: a flag that silently means something
other than what its name suggests is a configuration hazard, and the security
posture of both interfaces is decided entirely by these flags. These tests pin
the defaults, the normalisation, and the mapping from flags to TLS modes.
"""

from __future__ import annotations

import importlib.util
import ssl
import sys
from typing import Any
from pathlib import Path

import pytest

from nmos.api.tests._tls_helpers import (
    CERTS_DIR,
    PKI_AVAILABLE,
    root_ca,
    server_chain,
    server_key,
)
from nmos.node.security_tags import NAP, RAAM, RAP

# nmos_registry.py sits at the repository root rather than inside the package,
# matching nmos_node.py, so it is loaded by path rather than imported.
_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "nmos_registry_under_test", _ROOT / "nmos_registry.py",
)
assert _SPEC is not None and _SPEC.loader is not None
nmos_registry = importlib.util.module_from_spec(_SPEC)
sys.modules["nmos_registry_under_test"] = nmos_registry
_SPEC.loader.exec_module(nmos_registry)


SERIAL = "SNX00000"


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_ports_match_the_node_client_flags(self) -> None:
        """The defaults must line up with the Node's ``--rds*`` defaults.

        ``nmos_node.py`` defaults ``--rdsRegistrationPort`` to 8447,
        ``--rdsQueryPort`` to 8446 and ``--rdsWebSocketPort`` to 8448. Matching
        them means a Node with only ``--rdsHost`` set reaches this registry.
        """
        args = nmos_registry.parse_args([])
        assert args.registrationPort == 8447
        assert args.queryPort == 8446
        assert args.queryWebSocketPort == 8448

    def test_behaviour_defaults_match_the_spec_and_nmos_cpp(self) -> None:
        args = nmos_registry.parse_args([])
        # Behaviour - Registration.md:47 / nmos-cpp registration_expiry_interval
        assert args.garbageCollectionInterval == 12.0
        # nmos-cpp query_paging_default / query_paging_limit
        assert args.pagingLimit == 10
        assert args.pagingLimitMax == 100

    def test_tls_is_on_by_default(self) -> None:
        """Security must be opt-out, not opt-in.

        A registry that quietly served plain HTTP because a flag was forgotten
        would be a worse failure than one that refuses to start.
        """
        args = nmos_registry.parse_args([])
        assert args.registryDisableTLS is False
        assert args.oauth2 is False

    def test_ca_lists_normalise_to_empty_lists(self) -> None:
        """``action="append"`` leaves None when the flag is absent.

        Every consumer treats these as ``list[str]``, so the None has to be
        normalised once here rather than guarded at each use.
        """
        args = nmos_registry.parse_args([])
        for attr in (
            "registrationTrustedRootCA",
            "queryTrustedRootCA",
            "oauth2TrustedRootCA",
            "trustedRootCA",
        ):
            assert getattr(args, attr) == [], attr

    def test_ca_options_repeat(self) -> None:
        args = nmos_registry.parse_args([
            "--registrationTrustedRootCA", "a.pem",
            "--registrationTrustedRootCA", "b.pem",
        ])
        assert args.registrationTrustedRootCA == ["a.pem", "b.pem"]

    def test_host_arguments_are_stripped(self) -> None:
        """Guards against a stray space in a shell default turning a valid
        address into an unresolvable hostname."""
        args = nmos_registry.parse_args(["--registryAddr", "  192.0.2.1 "])
        assert args.registryAddr == "192.0.2.1"

    def test_audience_mode_is_constrained(self) -> None:
        for mode in ("serial", "cert", "either"):
            assert nmos_registry.parse_args(
                ["--oauth2AudienceMode", mode],
            ).oauth2AudienceMode == mode
        with pytest.raises(SystemExit):
            nmos_registry.parse_args(["--oauth2AudienceMode", "nonsense"])


# ---------------------------------------------------------------------------
# SSL context construction
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not PKI_AVAILABLE,
    reason=f"pre-generated TLS PKI not present at {CERTS_DIR}",
)
class TestSslContexts:
    def _args(self, *extra: str) -> object:
        return nmos_registry.parse_args([
            "--registryCertificate", str(server_chain(SERIAL)),
            "--registryKey", str(server_key(SERIAL)),
            *extra,
        ])

    def test_disable_tls_yields_no_context(self) -> None:
        args = self._args("--registryDisableTLS")
        assert nmos_registry.build_registration_ssl_context(args) is None
        assert nmos_registry.build_query_ssl_context(args) is None

    def test_missing_certificate_yields_no_context(self) -> None:
        """Falls back to plain HTTP with a warning rather than crashing.

        Mirrors ``nmos_node.py::build_server_ssl_context``. ``validate_startup_certs``
        is what turns this into a hard failure for a real launch.
        """
        args = nmos_registry.parse_args([])
        assert nmos_registry.build_registration_ssl_context(args) is None

    def test_server_tls_requests_no_client_certificate(self) -> None:
        """RAP=1: no Registration trust anchor means no client cert is asked
        for, so any Node can register over authenticated TLS."""
        ctx = nmos_registry.build_registration_ssl_context(self._args())
        assert ctx is not None
        assert ctx.verify_mode == ssl.CERT_NONE

    def test_registration_anchor_selects_mutual_tls(self) -> None:
        """RAP=2: an anchor on the Registration interface makes a client
        certificate mandatory."""
        args = self._args("--registrationTrustedRootCA", str(root_ca()))
        ctx = nmos_registry.build_registration_ssl_context(args)
        assert ctx is not None
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_optional_client_auth_downgrades_to_cert_optional(self) -> None:
        args = self._args(
            "--registrationTrustedRootCA", str(root_ca()),
            "--registrationOptionalClientAuth",
        )
        ctx = nmos_registry.build_registration_ssl_context(args)
        assert ctx is not None
        assert ctx.verify_mode == ssl.CERT_OPTIONAL

    def test_the_two_interfaces_are_configured_independently(self) -> None:
        """Registration may require mTLS while Query does not, or vice versa.

        This is the whole reason the trust anchors are per-interface rather
        than one global flag.
        """
        args = self._args("--registrationTrustedRootCA", str(root_ca()))
        registration = nmos_registry.build_registration_ssl_context(args)
        query = nmos_registry.build_query_ssl_context(args)
        assert registration is not None and query is not None
        assert registration.verify_mode == ssl.CERT_REQUIRED
        assert query.verify_mode == ssl.CERT_NONE

    def test_tr10_restrictions_are_applied(self) -> None:
        """Every context goes through ``apply_tr10_tls_restrictions``.

        TR-10-SEC §"TLS Communications and Cipher Suites" requires TLS 1.2 as
        a floor; a context that skipped the helper would silently permit 1.0.
        """
        ctx = nmos_registry.build_registration_ssl_context(self._args())
        assert ctx is not None
        assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

class TestStartupValidation:
    def test_tls_without_certificate_exits(self) -> None:
        """A fast, explicit failure beats a TLS handshake error at first use."""
        args = nmos_registry.parse_args([])
        with pytest.raises(SystemExit):
            nmos_registry.validate_startup_certs(args)

    def test_disable_tls_skips_validation(self) -> None:
        args = nmos_registry.parse_args(["--registryDisableTLS"])
        nmos_registry.validate_startup_certs(args)  # must not raise

    @pytest.mark.skipif(
        not PKI_AVAILABLE,
        reason=f"pre-generated TLS PKI not present at {CERTS_DIR}",
    )
    def test_interface_anchor_requires_the_global_anchor(self) -> None:
        """A per-interface CA is validated against ``--trustedRootCA``.

        Same rule as ``nmos_node.py``: without the global anchor there is
        nothing to check the interface anchor against, and an anchor nobody
        verified is an anchor that might trust the wrong issuer.
        """
        args = nmos_registry.parse_args([
            "--registryCertificate", str(server_chain(SERIAL)),
            "--registryKey", str(server_key(SERIAL)),
            "--registrationTrustedRootCA", str(root_ca()),
        ])
        with pytest.raises(SystemExit, match="trustedRootCA"):
            nmos_registry.validate_startup_certs(args)

    @pytest.mark.skipif(
        not PKI_AVAILABLE,
        reason=f"pre-generated TLS PKI not present at {CERTS_DIR}",
    )
    def test_inaccessible_certificate_exits(self) -> None:
        args = nmos_registry.parse_args([
            "--registryCertificate", "/nonexistent/cert.pem",
            "--registryKey", str(server_key(SERIAL)),
        ])
        with pytest.raises(SystemExit, match="not accessible"):
            nmos_registry.validate_startup_certs(args)


# ---------------------------------------------------------------------------
# Registry Access Policy classification
# ---------------------------------------------------------------------------

class TestPolicyClassification:
    """What the startup banner reports, in TR-10-SEC's own vocabulary.

    The banner names the running compliance mode so an operator does not have
    to derive it from the flags they think they passed. These pin the
    derivation, especially the combinations that are not obvious.
    """

    def _args(self, *extra: str) -> Any:
        """A TLS-enabled configuration; paths need not exist to classify."""
        return nmos_registry.parse_args([
            "--registryCertificate", "cert.pem",
            "--registryKey", "key.pem",
            *extra,
        ])

    # --- Registration: RAP ---

    def test_registration_rap_0_without_tls(self) -> None:
        args = nmos_registry.parse_args(["--registryDisableTLS"])
        assert nmos_registry.classify_registration_rap(args) is RAP.UNRESTRICTED_HTTP

    def test_registration_rap_1_with_server_tls(self) -> None:
        assert nmos_registry.classify_registration_rap(
            self._args(),
        ) is RAP.UNRESTRICTED_HTTPS

    def test_registration_rap_2_with_an_anchor(self) -> None:
        assert nmos_registry.classify_registration_rap(
            self._args("--registrationTrustedRootCA", "ca.pem"),
        ) is RAP.RESTRICTED_MTLS

    def test_registration_rap_0_when_the_certificate_is_missing(self) -> None:
        """TLS is not disabled, but no certificate was supplied.

        The listener falls back to plain HTTP, so the reported policy has to
        follow it -- claiming RAP=1 for a socket serving HTTP would be a false
        compliance claim.
        """
        args = nmos_registry.parse_args([])
        assert nmos_registry.classify_registration_rap(args) is RAP.UNRESTRICTED_HTTP

    # --- Query: NAP ---

    def test_query_nap_0_without_tls(self) -> None:
        args = nmos_registry.parse_args(["--registryDisableTLS"])
        assert nmos_registry.classify_query_nap(args) is NAP.UNRESTRICTED_RW

    def test_query_nap_1_with_optional_client_auth(self) -> None:
        """Unrestricted Read Only: reads open, writes per RAAM."""
        assert nmos_registry.classify_query_nap(
            self._args("--queryOptionalClientAuth"),
        ) is NAP.UNRESTRICTED_RO

    def test_query_nap_2_by_default(self) -> None:
        assert nmos_registry.classify_query_nap(self._args()) is NAP.RESTRICTED_RW

    def test_oauth2_overrides_nap_1(self) -> None:
        """§"Unrestricted Read Only": the policy "is not allowed when OAuth
        2.0 authorizations are used, in which case even read access MUST be
        explicitly provided by the OAuth 2.0 authorizations".

        Every read route is wrapped in ``check_oauth2``, so the deployment
        really is Restricted Read Write and must be reported as such --
        reporting NAP=1 would describe a configuration the specification
        forbids and that the code does not implement.
        """
        assert nmos_registry.classify_query_nap(
            self._args("--queryOptionalClientAuth", "--oauth2"),
        ) is NAP.RESTRICTED_RW

    # --- Query: RAAM ---

    def test_query_raam_mtls_by_default(self) -> None:
        assert nmos_registry.classify_query_raam(
            self._args("--queryTrustedRootCA", "ca.pem"),
        ) is RAAM.MTLS

    def test_query_raam_oauth2(self) -> None:
        assert nmos_registry.classify_query_raam(
            self._args("--oauth2"),
        ) is RAAM.OAUTH2

    def test_query_raam_mtls_plus_oauth2(self) -> None:
        assert nmos_registry.classify_query_raam(
            self._args("--queryTrustedRootCA", "ca.pem", "--oauth2"),
        ) is RAAM.MTLS_PLUS_OAUTH2


class TestBannerRendering:
    """The banner text itself, since it is the operator-facing artefact."""

    def _banner(self, argv: list[str], *, tls: bool) -> str:
        import io
        import ssl as _ssl
        from contextlib import redirect_stdout

        args = nmos_registry.parse_args(argv)
        context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER) if tls else None
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            nmos_registry._print_banner(args, context, context)
        return buffer.getvalue()

    def test_reports_both_policies(self) -> None:
        text = self._banner(
            [
                "--registryCertificate", "cert.pem", "--registryKey", "key.pem",
                "--registrationTrustedRootCA", "ca.pem",
                "--queryTrustedRootCA", "ca.pem",
            ],
            tls=True,
        )
        assert "RAP=2 RESTRICTED_MTLS" in text
        assert "NAP=2 RESTRICTED_RW" in text
        assert "RAAM=0 MTLS" in text

    def test_reports_oauth2_raam(self) -> None:
        text = self._banner(
            [
                "--registryCertificate", "cert.pem", "--registryKey", "key.pem",
                "--queryTrustedRootCA", "ca.pem", "--oauth2",
            ],
            tls=True,
        )
        assert "RAAM=2 MTLS_PLUS_OAUTH2" in text

    def test_reports_read_only_policy(self) -> None:
        text = self._banner(
            [
                "--registryCertificate", "cert.pem", "--registryKey", "key.pem",
                "--queryOptionalClientAuth",
            ],
            tls=True,
        )
        assert "NAP=1 UNRESTRICTED_RO" in text

    def test_no_tls_warns_about_non_compliance(self) -> None:
        """§"Unrestricted Read Write": a device so configured "MUST not claim
        compliance with this specification". Saying so beats leaving the
        operator to infer it from NAP=0."""
        text = self._banner(["--registryDisableTLS"], tls=False)
        assert "NAP=0 UNRESTRICTED_RW" in text
        assert "NOT compliant" in text

    def test_raam_is_omitted_when_nothing_is_restricted(self) -> None:
        """RAAM describes how restrictions are enforced. Printing it beside a
        plain-HTTP listener would imply a protection that is absent."""
        text = self._banner(["--registryDisableTLS"], tls=False)
        assert "RAAM" not in text

    def test_tls_banner_carries_no_false_warning(self) -> None:
        text = self._banner(
            [
                "--registryCertificate", "cert.pem", "--registryKey", "key.pem",
                "--queryTrustedRootCA", "ca.pem",
            ],
            tls=True,
        )
        assert "NOT compliant" not in text
        assert "no authorization mechanism" not in text

    def test_tls_without_any_raam_mechanism_is_called_out(self) -> None:
        """TLS alone restricts nothing.

        With no client-certificate anchor and no OAuth 2.0 there is no RAAM
        mechanism, so ``client_auth_required`` is false and every verb --
        including subscription creation -- is open to any client that
        completes the handshake. Reporting a bare ``RAAM=0 MTLS`` here would
        name a protection that is not in force, which is precisely the
        misreading this banner exists to prevent.
        """
        text = self._banner(
            ["--registryCertificate", "cert.pem", "--registryKey", "key.pem"],
            tls=True,
        )
        assert "RAAM=none configured" in text
        assert "RAAM=0 MTLS" not in text
        assert "no authorization mechanism" in text

    def test_query_authorization_detection(self) -> None:
        base = ["--registryCertificate", "cert.pem", "--registryKey", "key.pem"]
        assert not nmos_registry.query_has_authorization(
            nmos_registry.parse_args(base),
        )
        assert nmos_registry.query_has_authorization(
            nmos_registry.parse_args([*base, "--queryTrustedRootCA", "ca.pem"]),
        )
        assert nmos_registry.query_has_authorization(
            nmos_registry.parse_args([*base, "--oauth2"]),
        )


class TestRapClassification:
    """``InterfaceSecurity.rap_for`` -- the same policy from the app's side.

    The banner classifies from the CLI namespace; the app object classifies
    from its own snapshot. Both exist, so both are pinned.
    """

    def test_no_tls_is_rap_0(self) -> None:
        from nmos.registry import InterfaceSecurity

        security = InterfaceSecurity(client_auth_required=False)
        assert security.rap_for(tls=False) is RAP.UNRESTRICTED_HTTP

    def test_server_tls_is_rap_1(self) -> None:
        from nmos.registry import InterfaceSecurity

        security = InterfaceSecurity(client_auth_required=False)
        assert security.rap_for(tls=True) is RAP.UNRESTRICTED_HTTPS

    def test_mutual_tls_is_rap_2(self) -> None:
        from nmos.registry import InterfaceSecurity

        security = InterfaceSecurity(client_auth_required=True)
        assert security.rap_for(tls=True) is RAP.RESTRICTED_MTLS

    def test_client_auth_without_tls_is_still_rap_0(self) -> None:
        """TLS state belongs to the listener, not to this snapshot.

        Reporting RAP=2 for a listener that is not actually running TLS would
        be a false compliance claim.
        """
        from nmos.registry import InterfaceSecurity

        security = InterfaceSecurity(client_auth_required=True)
        assert security.rap_for(tls=False) is RAP.UNRESTRICTED_HTTP
