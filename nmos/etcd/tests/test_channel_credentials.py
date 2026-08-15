# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The client half of etcd mutual TLS: credentials, and how a channel uses them.

Everything here was previously untested. The registry's *server* half -- the
argv handed to etcd -- had coverage, but the credentials the registry presents
to that server had none at all, so a change that silently stopped sending the
client certificate would have gone unnoticed until a deployment failed.

These are unit tests: no etcd process, no handshake. Proof that the two halves
actually interoperate is in ``nmos/registry/tests/test_etcd_mtls_e2e.py``, which
is where a real member is started with real certificates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nmos.etcd.channel import (
    Endpoint,
    EtcdChannelPool,
    build_credentials,
    parse_endpoints,
)
from nmos.etcd.errors import EtcdError

SSL_TARGET_NAME_OVERRIDE = "grpc.ssl_target_name_override"


@pytest.fixture
def pem_files(tmp_path: Path) -> tuple[str, str, str, str]:
    """Two roots, a certificate and a key, with distinguishable contents."""
    root_rsa = tmp_path / "root-rsa.pem"
    root_ec = tmp_path / "root-ec.pem"
    certificate = tmp_path / "member.chain.pem"
    key = tmp_path / "member.key"
    root_rsa.write_bytes(b"-----ROOT RSA-----\n")
    root_ec.write_bytes(b"-----ROOT EC-----\n")
    certificate.write_bytes(b"-----CERT-----\n")
    key.write_bytes(b"-----KEY-----\n")
    return str(root_rsa), str(root_ec), str(certificate), str(key)


# ---------------------------------------------------------------------------
# build_credentials
# ---------------------------------------------------------------------------

def test_every_trusted_root_reaches_the_credentials(
    pem_files: tuple[str, str, str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All roots are concatenated, not just the first.

    Both sides of mutual TLS must trust the same set, and they reach it by
    different routes: this one concatenates in memory, while etcd's
    --trusted-ca-file takes a single path, so ``EtcdSupervisor`` combines
    several roots into one file first (see
    ``test_supervisor.py::test_every_trusted_root_reaches_etcd_not_just_the_first``).
    Passing only the first here would split the trust store the other way
    round.
    """
    import grpc

    root_rsa, root_ec, certificate, key = pem_files
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(grpc, "ssl_channel_credentials", fake)

    build_credentials(
        trusted_root_ca=[root_rsa, root_ec],
        certificate=certificate,
        key=key,
    )

    assert captured["root_certificates"] == b"-----ROOT RSA-----\n-----ROOT EC-----\n"
    assert captured["certificate_chain"] == b"-----CERT-----\n"
    assert captured["private_key"] == b"-----KEY-----\n"


def test_a_missing_file_names_the_path(tmp_path: Path) -> None:
    """A typo'd certificate path must not surface as a handshake failure.

    Without this the operator sees TLS errors from etcd and starts debugging
    certificates that are perfectly valid, because the one that never loaded is
    not mentioned anywhere.
    """
    missing = str(tmp_path / "absent.pem")
    with pytest.raises(EtcdError, match="absent.pem"):
        build_credentials(
            trusted_root_ca=[missing], certificate=missing, key=missing,
        )


# ---------------------------------------------------------------------------
# How the pool applies them
# ---------------------------------------------------------------------------

def test_credentials_select_a_secure_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import grpc

    created: dict[str, Any] = {}

    def fake_secure(target: str, credentials: Any, options: Any = None) -> Any:
        created["kind"] = "secure"
        created["target"] = target
        created["options"] = dict(options or [])
        return object()

    def fake_insecure(target: str, options: Any = None) -> Any:
        created["kind"] = "insecure"
        created["target"] = target
        created["options"] = dict(options or [])
        return object()

    monkeypatch.setattr(grpc.aio, "secure_channel", fake_secure)
    monkeypatch.setattr(grpc.aio, "insecure_channel", fake_insecure)

    pool = EtcdChannelPool(
        parse_endpoints(["XYZ-SNX10000:2381"]),
        credentials=object(),          # type: ignore[arg-type]
        target_name="Example.Company.Device.Etcd.ABC.example.com",
        rpc_timeout=1.0,
    )
    pool.channel(Endpoint(target="XYZ-SNX10000:2381", local=True))

    assert created["kind"] == "secure"
    assert created["options"][SSL_TARGET_NAME_OVERRIDE] == (
        "Example.Company.Device.Etcd.ABC.example.com"
    )


def test_no_credentials_select_an_insecure_channel_and_no_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--etcdDisableTLS must not leave a target-name override behind.

    An override on an insecure channel would be inert rather than dangerous,
    but it would also mean the two modes share state they should not, and this
    is the pair of assertions that keeps them separate.
    """
    import grpc

    created: dict[str, Any] = {}

    def fake_insecure(target: str, options: Any = None) -> Any:
        created["kind"] = "insecure"
        created["options"] = dict(options or [])
        return object()

    monkeypatch.setattr(grpc.aio, "insecure_channel", fake_insecure)

    pool = EtcdChannelPool(
        parse_endpoints(["127.0.0.1:2381"]),
        credentials=None, target_name=None, rpc_timeout=1.0,
    )
    pool.channel(Endpoint(target="127.0.0.1:2381", local=True))

    assert created["kind"] == "insecure"
    assert SSL_TARGET_NAME_OVERRIDE not in created["options"]


def test_the_override_is_what_lets_one_name_validate_every_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endpoints may be addresses; the certificate is still verified by name.

    Members are reached at whatever endpoint they were configured with -- an
    address, a name, a WSL-forwarded localhost -- but every one of them presents
    a certificate carrying the same shared etcd SAN. The override is what makes
    those two facts compatible, so it must not depend on the endpoint's form.
    """
    import grpc

    seen: list[dict[str, Any]] = []

    def fake_secure(target: str, credentials: Any, options: Any = None) -> Any:
        seen.append({"target": target, "options": dict(options or [])})
        return object()

    monkeypatch.setattr(grpc.aio, "secure_channel", fake_secure)

    endpoints = ["127.0.0.1:2381", "XYZ-SNX10001:2391", "localhost:2401"]
    pool = EtcdChannelPool(
        parse_endpoints(endpoints),
        credentials=object(),          # type: ignore[arg-type]
        target_name="Example.Company.Device.Etcd.ABC.example.com",
        rpc_timeout=1.0,
    )
    for endpoint in pool.endpoints:
        pool.channel(endpoint)

    assert len(seen) == 3
    for entry in seen:
        assert entry["options"][SSL_TARGET_NAME_OVERRIDE] == (
            "Example.Company.Device.Etcd.ABC.example.com"
        )


def test_channels_are_cached_so_the_handshake_is_paid_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second call returns the first channel, without constructing another.

    The construction is faked rather than real for a reason that is not
    fastidiousness: a genuine ``grpc.aio`` channel binds to the running event
    loop, so building one in a synchronous test makes the result depend on
    whatever loop state the rest of the suite happens to have left behind. This
    test is about the pool's caching, and nothing else should be able to break
    it.
    """
    import grpc

    calls: list[str] = []

    def fake_insecure(target: str, options: Any = None) -> Any:
        calls.append(target)
        return object()

    monkeypatch.setattr(grpc.aio, "insecure_channel", fake_insecure)

    pool = EtcdChannelPool(
        parse_endpoints(["127.0.0.1:2381"]),
        credentials=None, target_name=None, rpc_timeout=1.0,
    )
    endpoint = Endpoint(target="127.0.0.1:2381", local=True)

    assert pool.channel(endpoint) is pool.channel(endpoint)
    assert calls == ["127.0.0.1:2381"]
