# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""``nmos.tls_identity`` — pairing, loading and classifying TLS identities.

Lives beside the other TLS tests because it needs the same PKI fixtures and
the same ``PKI_AVAILABLE`` gate.

Two of these pin *measured* OpenSSL behaviour rather than our own logic, and
they are the reason the module is shaped the way it is:

* a server context holds both flavours and serves each client the one it asked
  for, and
* a concatenated leaf file is accepted silently and then fails half of them.

Delete either and the next person to read ``load_identities`` will reasonably
conclude that concatenating two chains would be simpler.
"""

from __future__ import annotations

import socket
import ssl
import subprocess
import threading
from pathlib import Path

import pytest

from nmos.cert_check import CertCheckError
from nmos.tls_identity import (
    PeerRejectedAllIdentities,
    client_identity_order,
    is_peer_rejected_identity,
    client_contexts,
    has_identity,
    leaf_public_key_algorithm,
    load_identities,
    pair_identities,
    union_dns_identities,
)

from ._tls_helpers import (
    PKI_AVAILABLE,
    client_chain,
    client_key,
    root_ca,
    server_chain,
    server_key,
)

SERIAL = "SNX00000"
OTHER_SERIAL = "SNX00001"

requires_pki = pytest.mark.skipif(not PKI_AVAILABLE, reason="PKI not present")


# ---------------------------------------------------------------------------
# pair_identities / has_identity — no PKI needed
# ---------------------------------------------------------------------------

class TestPairing:
    def test_pairs_by_position(self) -> None:
        assert pair_identities(
            ["a.pem", "b.pem"], ["a.key", "b.key"],
            cert_flag="--nodeCertificate", key_flag="--nodeKey",
        ) == [("a.pem", "a.key"), ("b.pem", "b.key")]

    @pytest.mark.parametrize("certs,keys", [
        (["a.pem", "b.pem"], ["a.key"]),
        (["a.pem"], ["a.key", "b.key"]),
    ])
    def test_a_count_mismatch_is_refused_naming_both_flags(
        self, certs: list[str], keys: list[str],
    ) -> None:
        """Refused rather than zipped: ``zip`` would drop the extra silently
        and start a listener with an identity nobody asked for."""
        with pytest.raises(CertCheckError) as excinfo:
            pair_identities(
                certs, keys,
                cert_flag="--nodeCertificate", key_flag="--nodeKey",
            )
        message = str(excinfo.value)
        assert "--nodeCertificate" in message
        assert "--nodeKey" in message

    def test_empty_strings_are_not_identities(self) -> None:
        """``[""]`` is truthy, which is how a list-valued option silently
        inverts every ``if cert:`` guard. Filtered at the door instead."""
        assert pair_identities(
            [""], [""], cert_flag="--c", key_flag="--k",
        ) == []
        assert has_identity([""], [""]) is False
        assert has_identity([], []) is False
        assert has_identity(["a.pem"], ["a.key"]) is True

    def test_a_bare_string_is_accepted(self) -> None:
        """Namespaces built by hand in tests still pass scalars."""
        assert pair_identities(
            "a.pem", "a.key", cert_flag="--c", key_flag="--k",
        ) == [("a.pem", "a.key")]
        assert has_identity("a.pem", "a.key") is True

    def test_absent_options_are_not_identities(self) -> None:
        """``action="append"`` leaves ``None`` when the flag is omitted."""
        assert pair_identities(None, None, cert_flag="--c", key_flag="--k") == []
        assert has_identity(None, None) is False


# ---------------------------------------------------------------------------
# leaf_public_key_algorithm / union_dns_identities
# ---------------------------------------------------------------------------

@requires_pki
class TestClassification:
    def test_it_reads_the_algorithm_from_the_certificate(self) -> None:
        """Not from the filename: an operator's certificates need not follow
        this PKI's ``.ec.`` convention, and TCT must still be right."""
        assert leaf_public_key_algorithm(str(server_chain(SERIAL, "rsa"))) == "rsa"
        assert leaf_public_key_algorithm(str(server_chain(SERIAL, "ec"))) == "ecdsa"

    def test_an_unreadable_path_classifies_as_nothing(self, tmp_path: Path) -> None:
        assert leaf_public_key_algorithm(str(tmp_path / "absent.pem")) is None

    def test_a_file_with_no_certificate_classifies_as_nothing(
        self, tmp_path: Path,
    ) -> None:
        empty = tmp_path / "empty.pem"
        empty.write_text("not a certificate\n", encoding="utf-8")
        assert leaf_public_key_algorithm(str(empty)) is None

    def test_names_are_unioned_across_identities_in_first_seen_order(self) -> None:
        """Two *different* serials, deliberately. Both flavours of one serial
        carry identical SANs, so a same-serial pair cannot detect a union that
        silently returns only the first certificate's names."""
        first = str(server_chain(SERIAL, "rsa"))
        second = str(server_chain(OTHER_SERIAL, "rsa"))
        union = union_dns_identities([first, second])
        for name in union_dns_identities([first]):
            assert name in union
        for name in union_dns_identities([second]):
            assert name in union
        assert len(union) == len(set(union)), "union must not duplicate"

    def test_the_same_certificate_twice_adds_nothing(self) -> None:
        once = union_dns_identities([str(server_chain(SERIAL, "rsa"))])
        twice = union_dns_identities([str(server_chain(SERIAL, "rsa"))] * 2)
        assert once == twice


# ---------------------------------------------------------------------------
# The measured OpenSSL behaviour this module is built on
# ---------------------------------------------------------------------------

def _serve(context: ssl.SSLContext, count: int) -> tuple[int, socket.socket]:
    """A listener that accepts ``count`` handshakes on a free port."""
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(count + 1)

    def run() -> None:
        for _ in range(count):
            try:
                raw, _ = listener.accept()
            except OSError:
                return
            try:
                context.wrap_socket(raw, server_side=True).close()
            except OSError:
                pass

    threading.Thread(target=run, daemon=True).start()
    return listener.getsockname()[1], listener


def _presented_algorithm(port: int, cipher: str) -> str:
    """Which flavour the server gave a client that accepts only ``cipher``.

    TLS 1.2 and one cipher suite, because there the suite *determines* the
    certificate type — ``ECDHE-ECDSA-...`` cannot be answered with an RSA
    certificate. Under TLS 1.3 the suite says nothing about authentication.
    """
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    client.maximum_version = ssl.TLSVersion.TLSv1_2
    client.set_ciphers(cipher)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with client.wrap_socket(raw) as tls:
            der = tls.getpeercert(binary_form=True)
    assert der is not None
    text = subprocess.run(
        ["openssl", "x509", "-inform", "DER", "-noout", "-text"],
        input=der, capture_output=True, check=True,
    ).stdout.decode()
    return "ecdsa" if "id-ecPublicKey" in text else "rsa"


@requires_pki
class TestServerHoldsBothIdentities:
    def test_each_client_is_served_the_flavour_it_asked_for(self) -> None:
        """The whole basis of TCT=2. If this fails, one identity is
        unreachable and the deployment reports a posture it does not have."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        load_identities(context, [
            (str(server_chain(SERIAL, "rsa")), str(server_key(SERIAL, "rsa"))),
            (str(server_chain(SERIAL, "ec")), str(server_key(SERIAL, "ec"))),
        ])
        port, listener = _serve(context, 2)
        try:
            assert _presented_algorithm(
                port, "ECDHE-ECDSA-AES128-GCM-SHA256") == "ecdsa"
            assert _presented_algorithm(
                port, "ECDHE-RSA-AES128-GCM-SHA256") == "rsa"
        finally:
            listener.close()

    def test_concatenating_the_chains_is_accepted_and_then_broken(
        self, tmp_path: Path,
    ) -> None:
        """The anti-pattern, pinned so nobody "simplifies" the loop into it.

        One file holding both leaves loads without complaint — the first
        certificate becomes the leaf and the other is filed as an
        intermediate — and the server then serves RSA and fails every
        ECDSA-only client.
        """
        merged = tmp_path / "both.chain.pem"
        merged.write_bytes(
            server_chain(SERIAL, "rsa").read_bytes()
            + server_chain(SERIAL, "ec").read_bytes()
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(merged), str(server_key(SERIAL, "rsa")))

        port, listener = _serve(context, 2)
        try:
            assert _presented_algorithm(
                port, "ECDHE-RSA-AES128-GCM-SHA256") == "rsa"
            with pytest.raises(ssl.SSLError):
                _presented_algorithm(port, "ECDHE-ECDSA-AES128-GCM-SHA256")
        finally:
            listener.close()


@requires_pki
class TestClientContexts:
    def test_one_context_per_identity(self) -> None:
        """Clients get a context each, not one context with both: a
        multi-identity client ignores the peer's CertificateRequest."""
        pairs = [
            (str(server_chain(SERIAL, "rsa")), str(server_key(SERIAL, "rsa"))),
            (str(server_chain(SERIAL, "ec")), str(server_key(SERIAL, "ec"))),
        ]

        def make() -> ssl.SSLContext:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_verify_locations(str(root_ca("rsa")))
            return context

        contexts = client_contexts(pairs, make)
        assert len(contexts) == 2
        assert contexts[0] is not contexts[1]

    def test_no_identity_yields_no_contexts(self) -> None:
        """The ordinary case for a client that authenticates with nothing."""
        assert client_contexts([], ssl.create_default_context) == []


@requires_pki
class TestClientOffersEcdsaFirst:
    """Which identity a client offers first is a real preference, so it is
    decided by the code and not by the operator's argument order."""

    def _pair(self, flavor: str) -> tuple[str, str]:
        return (
            str(server_chain(SERIAL, flavor)),  # type: ignore[arg-type]
            str(server_key(SERIAL, flavor)),    # type: ignore[arg-type]
        )

    def test_ecdsa_leads_however_the_flags_were_ordered(self) -> None:
        rsa, ec = self._pair("rsa"), self._pair("ec")
        assert client_identity_order([rsa, ec])[0] == ec
        assert client_identity_order([ec, rsa])[0] == ec

    def test_a_single_identity_is_untouched(self) -> None:
        """The invariant that matters most: one identity behaves as before."""
        rsa = self._pair("rsa")
        assert client_identity_order([rsa]) == [rsa]

    def test_same_flavour_keeps_the_given_order(self) -> None:
        """Stable within a group, so a re-provisioning overlap of two RSA
        identities still tries the one the operator listed first."""
        first, second = self._pair("rsa"), self._pair("rsa")
        assert client_identity_order([first, second]) == [first, second]

    def test_unreadable_identities_sort_last(self) -> None:
        """Trying one first would spend the retry budget on the identity least
        likely to work."""
        ec = self._pair("ec")
        missing = ("/p/absent.pem", "/p/absent.key")
        assert client_identity_order([missing, ec]) == [ec, missing]


def _rejected_by(sigalgs: str, tls12: bool) -> BaseException:
    """Connect to a peer that demands `sigalgs`, offering an RSA identity.

    Driven with ``openssl s_server`` because Python's ``ssl`` cannot restrict
    the CertificateRequest, and a *real* rejection is the point: the classifier
    matches on OpenSSL's alert text, so a synthetic exception would test the
    test rather than the library.
    """
    import subprocess
    import time

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    server = subprocess.Popen(
        ["openssl", "s_server", "-accept", str(port), "-naccept", "1",
         "-cert", str(server_chain(SERIAL, "rsa")),
         "-key", str(server_key(SERIAL, "rsa")),
         "-CAfile", str(root_ca("rsa")), "-Verify", "2",
         "-client_sigalgs", sigalgs, "-quiet"]
        + (["-tls1_2"] if tls12 else []),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.8)
        client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client.check_hostname = False
        client.verify_mode = ssl.CERT_NONE
        if tls12:
            client.maximum_version = ssl.TLSVersion.TLSv1_2
        client.load_cert_chain(
            str(client_chain(SERIAL, "rsa")), str(client_key(SERIAL, "rsa")),
        )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
                with client.wrap_socket(raw) as tls:
                    tls.send(b"x")
                    tls.recv(16)
        except Exception as exc:  # noqa: BLE001 - the exception IS the result
            return exc
        raise AssertionError("the peer accepted an identity it should have refused")
    finally:
        server.kill()
        server.wait()


@requires_pki
class TestRejectionIsRecognised:
    """The classifier matches OpenSSL's alert text, which is brittle by nature.

    Driven against live rejections on both TLS versions so that an OpenSSL or
    aiohttp upgrade which renames an alert fails here loudly, rather than
    silently turning the identity fallback into a no-op.
    """

    @pytest.mark.parametrize("tls12", [True, False], ids=["tls1.2", "tls1.3"])
    def test_a_real_rejection_is_recognised(self, tls12: bool) -> None:
        exc = _rejected_by("ECDSA+SHA256", tls12)
        assert is_peer_rejected_identity(exc), (
            f"unrecognised rejection on {'TLS 1.2' if tls12 else 'TLS 1.3'}: "
            f"{type(exc).__name__}: {exc}"
        )

    def test_us_rejecting_their_certificate_is_not_a_rejection_of_ours(self) -> None:
        """The opposite fault: retrying with another of *our* certificates
        cannot repair a peer whose certificate we refused."""
        error = ssl.SSLCertVerificationError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "unable to get local issuer certificate",
        )
        assert is_peer_rejected_identity(error) is False

    def test_an_unrelated_error_is_not_a_rejection(self) -> None:
        assert is_peer_rejected_identity(TimeoutError("timed out")) is False

    def test_a_wrapped_rejection_is_found_through_the_cause_chain(self) -> None:
        """aiohttp wraps the ssl error, and under TLS 1.3 the wrapper's own
        message need not carry the alert."""
        inner = ssl.SSLError("[SSL: TLSV13_ALERT_CERTIFICATE_REQUIRED] alert")
        outer = OSError("Connection lost")
        outer.__cause__ = inner
        assert is_peer_rejected_identity(outer) is True


class TestPeerRejectedAllIdentities:
    def test_it_names_every_identity_tried(self) -> None:
        """The message TLS 1.3 cannot give on its own: it reports only
        "certificate required", which reads as "none configured"."""
        error = PeerRejectedAllIdentities([
            ("ecdsa", ssl.SSLError("alert one")),
            ("rsa", ssl.SSLError("alert two")),
        ])
        text = str(error)
        assert "ecdsa" in text and "rsa" in text
        assert len(error.failures) == 2
