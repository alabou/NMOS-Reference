# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for SPKI pin computation and TLS trust resolution.

Certificates are generated in-test with ``cryptography`` — already a project
dependency — so these run in the default gate without touching the real
``Certificates/`` tree or needing a TLS node to be running.

The pin-format test earns its place: base64-of-SHA256-of-DER-SPKI is what
Chromium expects, and hex is the natural wrong guess. Getting it wrong produces
no error at all — the flag simply never matches, and the only symptom is the
certificate error the pin was supposed to suppress.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

from ..core import tls_pin
from ..enums import TlsPolicy
from ..errors import TlsPinError

PrivateKey = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey


def _key() -> PrivateKey:
    """Generate a test key cheaply.

    EC by default rather than RSA-2048. This is not micro-optimisation: an earlier
    version generated ~28 RSA-2048 keys across this module, roughly 14 seconds of
    solid CPU, and that starved a timing-sensitive asyncio timer test elsewhere in
    the suite badly enough to fail it. Test cost is not free when the suite shares
    a machine. The RSA path still has explicit coverage below.
    """
    return ec.generate_private_key(ec.SECP256R1())


def _rsa_key() -> rsa.RSAPrivateKey:
    """Generate an RSA key, for the cases that specifically need one."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _make_cert(
    subject: str,
    *,
    key: PrivateKey,
    issuer_name: str | None = None,
    issuer_key: PrivateKey | None = None,
    dns_names: tuple[str, ...] = (),
    is_ca: bool = False,
) -> x509.Certificate:
    """Build a self-signed or issuer-signed certificate."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, issuer_name or subject)])
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None),
                       critical=True)
    )
    if dns_names:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in dns_names]),
            critical=False,
        )
    signing_key: PrivateKey = issuer_key if issuer_key is not None else key
    return builder.sign(signing_key, hashes.SHA256())


def _pem(*certs: x509.Certificate) -> bytes:
    return b"".join(c.public_bytes(serialization.Encoding.PEM) for c in certs)


@pytest.fixture(scope="module")
def ca_and_leaf(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """A root CA plus a leaf that chains to it, with a serial-bound DNS SAN.

    Module-scoped: the files are read-only for every test that uses them, so
    regenerating the key material per test bought nothing but CPU time.
    """
    tmp_path = tmp_path_factory.mktemp("certs")
    ca_key = _key()
    ca = _make_cert("ExampleRootCA", key=ca_key, is_ca=True)

    leaf_key = _key()
    leaf = _make_cert(
        "ExampleDeviceServer",
        key=leaf_key,
        issuer_name="ExampleRootCA",
        issuer_key=ca_key,
        # Mirrors the real certs, whose SAN carries the uppercase XYZ- prefix.
        dns_names=("XYZ-SNX00001", "node1.example.com"),
    )

    paths = {
        "ca": tmp_path / "ExampleRootCA.pem",
        "chain": tmp_path / "server.chain.pem",
        "key": tmp_path / "server.key",
    }
    paths["ca"].write_bytes(_pem(ca))
    paths["chain"].write_bytes(_pem(leaf))
    paths["key"].write_bytes(leaf_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    return paths


class TestSpkiPin:
    """Pin computation."""

    def test_pin_is_base64_sha256_of_der_spki(self, ca_and_leaf: dict[str, Path]) -> None:
        chain = tls_pin.load_chain(str(ca_and_leaf["chain"]))
        pin = tls_pin.spki_pin(chain[0])

        expected_spki = chain[0].public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        expected = base64.b64encode(hashlib.sha256(expected_spki).digest()).decode()
        assert pin == expected

    def test_pin_is_not_hex(self, ca_and_leaf: dict[str, Path]) -> None:
        # The natural wrong guess. It fails silently: the flag never matches and
        # the certificate error the pin was meant to handle simply reappears.
        chain = tls_pin.load_chain(str(ca_and_leaf["chain"]))
        pin = tls_pin.spki_pin(chain[0])
        assert len(base64.b64decode(pin)) == 32
        assert not all(c in "0123456789abcdef" for c in pin.lower())

    def test_pin_is_stable(self, ca_and_leaf: dict[str, Path]) -> None:
        chain = tls_pin.load_chain(str(ca_and_leaf["chain"]))
        assert tls_pin.spki_pin(chain[0]) == tls_pin.spki_pin(chain[0])

    def test_different_keys_pin_differently(self, tmp_path: Path) -> None:
        # The property that makes pinning meaningful: a substituted certificate
        # still fails, so this is narrowing trust rather than abandoning it.
        first = _make_cert("a", key=_rsa_key())
        second = _make_cert("a", key=_key())
        assert tls_pin.spki_pin(first) != tls_pin.spki_pin(second)

    def test_ec_key_supported(self) -> None:
        # start-node1.sh --tct=1 selects the ECDSA certificate variant.
        cert = _make_cert("ec", key=_key())
        assert len(base64.b64decode(tls_pin.spki_pin(cert))) == 32

    def test_rsa_key_supported(self) -> None:
        # --tct=0 selects RSA, so the RSA path keeps explicit coverage even though
        # the rest of this module uses EC for speed.
        cert = _make_cert("rsa", key=_rsa_key())
        assert len(base64.b64decode(tls_pin.spki_pin(cert))) == 32


class TestLoadChain:
    """Reading the PEM chain, leaf first."""

    def test_leaf_first(self, tmp_path: Path) -> None:
        ca_key = _key()
        ca = _make_cert("root", key=ca_key, is_ca=True)
        leaf_key = _key()
        leaf = _make_cert("leaf", key=leaf_key, issuer_name="root",
                          issuer_key=ca_key)
        path = tmp_path / "chain.pem"
        path.write_bytes(_pem(leaf, ca))

        chain = tls_pin.load_chain(str(path))
        assert len(chain) == 2
        # The listener presents the first certificate, so order is load-bearing.
        assert chain[0].subject.rfc4514_string() == "CN=leaf"

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(TlsPinError, match="cannot read certificate"):
            tls_pin.load_chain(str(tmp_path / "absent.pem"))

    def test_not_a_pem(self, tmp_path: Path) -> None:
        path = tmp_path / "junk.pem"
        path.write_text("not a certificate")
        with pytest.raises(TlsPinError):
            tls_pin.load_chain(str(path))

    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.pem"
        path.write_bytes(b"")
        with pytest.raises(TlsPinError):
            tls_pin.load_chain(str(path))


class TestComputePins:
    """Leaf-only versus whole-chain pinning."""

    def test_leaf_policy_pins_one(self, tmp_path: Path) -> None:
        ca_key = _key()
        ca = _make_cert("root", key=ca_key, is_ca=True)
        leaf = _make_cert("leaf", key=_key(), issuer_name="root",
                          issuer_key=ca_key)
        path = tmp_path / "chain.pem"
        path.write_bytes(_pem(leaf, ca))

        chain = tls_pin.load_chain(str(path))
        assert len(tls_pin.compute_pins(chain, TlsPolicy.PIN_LEAF_SPKI)) == 1
        assert len(tls_pin.compute_pins(chain, TlsPolicy.PIN_CHAIN_SPKI)) == 2


class TestVerifyMaterial:
    """Verification happens before pinning, via the project's own checker."""

    def test_full_verification_with_readable_key(self, ca_and_leaf: dict[str, Path]) -> None:
        material = tls_pin.CertificateMaterial(
            cert_path=str(ca_and_leaf["chain"]),
            key_path=str(ca_and_leaf["key"]),
            serial_number="SNX00001",
            ca_paths=(str(ca_and_leaf["ca"]),),
        )
        verification, detail = tls_pin.verify_material(material)
        assert verification == "full"
        assert "chain+SAN+key" in detail

    def test_partial_verification_when_key_unreadable(self, ca_and_leaf: dict[str, Path]) -> None:
        # Expected whenever the node runs as another user. The weaker check is
        # reported as "partial" rather than presented as a complete one.
        material = tls_pin.CertificateMaterial(
            cert_path=str(ca_and_leaf["chain"]),
            key_path="/nonexistent/server.key",
            serial_number="SNX00001",
            ca_paths=(str(ca_and_leaf["ca"]),),
        )
        verification, detail = tls_pin.verify_material(material)
        assert verification == "partial"
        assert "not readable" in detail

    def test_wrong_serial_fails(self, ca_and_leaf: dict[str, Path]) -> None:
        material = tls_pin.CertificateMaterial(
            cert_path=str(ca_and_leaf["chain"]),
            key_path=str(ca_and_leaf["key"]),
            serial_number="SNX99999",
            ca_paths=(str(ca_and_leaf["ca"]),),
        )
        with pytest.raises(TlsPinError, match="failed verification"):
            tls_pin.verify_material(material)

    def test_untrusted_chain_fails(self, ca_and_leaf: dict[str, Path], tmp_path: Path) -> None:
        # Pinning a certificate that does not chain to the declared root would
        # pin whatever happened to be on disk.
        other = _make_cert("OtherRootCA", key=_key(), is_ca=True)
        other_path = tmp_path / "other-ca.pem"
        other_path.write_bytes(_pem(other))

        material = tls_pin.CertificateMaterial(
            cert_path=str(ca_and_leaf["chain"]),
            key_path=str(ca_and_leaf["key"]),
            serial_number="SNX00001",
            ca_paths=(str(other_path),),
        )
        with pytest.raises(TlsPinError):
            tls_pin.verify_material(material)

    def test_no_ca_paths_refuses(self) -> None:
        material = tls_pin.CertificateMaterial(cert_path="/x.pem")
        with pytest.raises(TlsPinError, match="no trusted-root CA"):
            tls_pin.verify_material(material)

    def test_no_cert_refuses(self, ca_and_leaf: dict[str, Path]) -> None:
        material = tls_pin.CertificateMaterial(ca_paths=(str(ca_and_leaf["ca"]),))
        with pytest.raises(TlsPinError, match="--nodeCertificate"):
            tls_pin.verify_material(material)


class TestDnsNames:
    """SAN extraction, reused from the project's own helper."""

    def test_sans_read(self, ca_and_leaf: dict[str, Path]) -> None:
        names = tls_pin.dns_names(str(ca_and_leaf["chain"]))
        assert "XYZ-SNX00001" in names

    def test_unreadable_file_yields_nothing(self, tmp_path: Path) -> None:
        assert tls_pin.dns_names(str(tmp_path / "absent.pem")) == ()


class TestResolveTls:
    """Choosing between the clean name path and pinning."""

    def _material(self, paths: dict[str, Path]) -> tls_pin.CertificateMaterial:
        return tls_pin.CertificateMaterial(
            cert_path=str(paths["chain"]),
            key_path=str(paths["key"]),
            serial_number="SNX00001",
            ca_paths=(str(paths["ca"]),),
        )

    def test_prefers_san_hostname_when_it_resolves(self, ca_and_leaf: dict[str, Path]) -> None:
        # The cleaner path: connect by the certificate's own name, chain validates,
        # and no browser flag is needed at all.
        result = tls_pin.resolve_tls(
            self._material(ca_and_leaf), host="127.0.0.1",
            resolves=lambda name: name == "XYZ-SNX00001")
        assert result.policy is TlsPolicy.SAN_HOSTNAME
        assert result.pins == ()
        assert tls_pin.chromium_args(result) == ()

    def test_falls_back_to_pinning(self, ca_and_leaf: dict[str, Path]) -> None:
        result = tls_pin.resolve_tls(
            self._material(ca_and_leaf), host="127.0.0.1",
            resolves=lambda name: False)
        assert result.policy is TlsPolicy.PIN_LEAF_SPKI
        assert len(result.pins) == 1
        assert result.verification == "full"

    def test_no_resolver_pins(self, ca_and_leaf: dict[str, Path]) -> None:
        result = tls_pin.resolve_tls(self._material(ca_and_leaf), host="127.0.0.1")
        assert result.policy is TlsPolicy.PIN_LEAF_SPKI


class TestChromiumArgs:
    """Only the narrow pinning flag can ever be emitted."""

    def test_pin_flag_shape(self, ca_and_leaf: dict[str, Path]) -> None:
        chain = tls_pin.load_chain(str(ca_and_leaf["chain"]))
        result = tls_pin.PinResult(
            policy=TlsPolicy.PIN_LEAF_SPKI,
            pins=tls_pin.compute_pins(chain, TlsPolicy.PIN_LEAF_SPKI))
        args = tls_pin.chromium_args(result)
        assert len(args) == 1
        assert args[0].startswith("--ignore-certificate-errors-spki-list=")

    def test_chain_policy_joins_with_commas(self, tmp_path: Path) -> None:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca = _make_cert("root", key=ca_key, is_ca=True)
        leaf = _make_cert("leaf", key=rsa.generate_private_key(
            public_exponent=65537, key_size=2048), issuer_name="root",
            issuer_key=ca_key)
        path = tmp_path / "chain.pem"
        path.write_bytes(_pem(leaf, ca))
        chain = tls_pin.load_chain(str(path))
        result = tls_pin.PinResult(
            policy=TlsPolicy.PIN_CHAIN_SPKI,
            pins=tls_pin.compute_pins(chain, TlsPolicy.PIN_CHAIN_SPKI))
        assert tls_pin.chromium_args(result)[0].count(",") == 1

    def test_plaintext_emits_no_flag(self) -> None:
        assert tls_pin.chromium_args(
            tls_pin.PinResult(policy=TlsPolicy.PLAINTEXT)) == ()

    def test_never_emits_blanket_bypass(self, ca_and_leaf: dict[str, Path]) -> None:
        # There is no code path to --ignore-certificate-errors or
        # --allow-insecure-localhost, because a run with verification disabled
        # looks identical to one where it works.
        chain = tls_pin.load_chain(str(ca_and_leaf["chain"]))
        for policy in TlsPolicy:
            result = tls_pin.PinResult(
                policy=policy, pins=tls_pin.compute_pins(chain, policy))
            rendered = " ".join(tls_pin.chromium_args(result))
            assert "--ignore-certificate-errors=" not in rendered
            assert "--allow-insecure-localhost" not in rendered
            assert rendered in ("", *[
                f"--ignore-certificate-errors-spki-list={','.join(result.pins)}"])


class TestTlsPolicyHasNoInsecureMember:
    """Disabling verification must not be expressible."""

    def test_no_insecure_option(self) -> None:
        names = {member.name for member in TlsPolicy}
        assert "INSECURE" not in names
        assert "IGNORE_ERRORS" not in names
