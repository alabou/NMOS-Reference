# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for distributed-registry configuration resolution.

No etcd and no network: these are the checks that run before the event loop, and
every one of them exists because the alternative is a registry that starts
successfully and is wrong -- serving a cluster it should not have joined, or
believing it manages a member it does not.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from nmos.api.tests._tls_helpers import Flavor, etcd_chain, etcd_key
from nmos.cert_check import cert_dns_identities
from nmos.registry.distributed import (
    DistributedConfigError,
    resolve_distributed_config,
)
from nmos_registry import parse_args

pytestmark = pytest.mark.skipif(
    not __import__("nmos.registry.distributed", fromlist=["x"]).etcd_extra_available(),
    reason="etcd extra not installed",
)


def _certs(tmp_path: Path) -> list[str]:
    """Three placeholder PEM files -- existence is all the resolver checks."""
    made: list[str] = []
    for name in ("cert.pem", "key.pem", "ca.pem"):
        path = tmp_path / name
        path.write_text("placeholder")
        made.append(str(path))
    return made


def _args(tmp_path: Path, *extra: str) -> Any:
    cert, key, ca = _certs(tmp_path)
    return parse_args([
        "--registryDisableTLS",
        "--distributed",
        "--registryAdvertisedHost", "a",
        "--registryNeighbour", "b",
        "--registryNeighbour", "c",
        "--etcdCertificate", cert,
        "--etcdKey", key,
        "--etcdTrustedRootCA", ca,
        *extra,
    ])


# ---------------------------------------------------------------------------
# Not distributed
# ---------------------------------------------------------------------------

def test_standalone_resolves_to_none_and_imports_nothing() -> None:
    args = parse_args(["--registryDisableTLS"])
    assert resolve_distributed_config(args) is None


def test_neighbour_without_distributed_is_refused() -> None:
    """Silently ignoring it is how you believe you have a cluster and do not."""
    args = parse_args([
        "--registryDisableTLS", "--registryNeighbour", "b",
    ])
    with pytest.raises(DistributedConfigError, match="without --distributed"):
        resolve_distributed_config(args)


def test_bootstrap_without_distributed_is_refused() -> None:
    args = parse_args(["--registryDisableTLS", "--etcdBootstrap"])
    with pytest.raises(DistributedConfigError, match="without --distributed"):
        resolve_distributed_config(args)


# ---------------------------------------------------------------------------
# Member list
# ---------------------------------------------------------------------------

def test_three_member_cluster_resolves(tmp_path: Path) -> None:
    config = resolve_distributed_config(_args(tmp_path))
    assert config is not None
    assert config.layout.size == 3
    assert config.layout.failures_tolerated == 1
    assert config.layout.local.host == "a"
    # Local member first, so the channel pool's common case takes no hop.
    assert config.endpoints[0] == "a:2381"


def test_advertised_host_is_required(tmp_path: Path) -> None:
    cert, key, ca = _certs(tmp_path)
    args = parse_args([
        "--registryDisableTLS", "--distributed",
        "--etcdCertificate", cert, "--etcdKey", key,
        "--etcdTrustedRootCA", ca,
    ])
    with pytest.raises(
        DistributedConfigError, match="--registryAdvertisedHost",
    ):
        resolve_distributed_config(args)


def test_repeating_the_local_host_as_a_neighbour_is_refused(
    tmp_path: Path,
) -> None:
    cert, key, ca = _certs(tmp_path)
    args = parse_args([
        "--registryDisableTLS", "--distributed",
        "--registryAdvertisedHost", "a",
        "--registryNeighbour", "a",
        "--registryNeighbour", "b",
        "--etcdCertificate", cert, "--etcdKey", key,
        "--etcdTrustedRootCA", ca,
    ])
    with pytest.raises(DistributedConfigError, match="duplicate host"):
        resolve_distributed_config(args)


def test_even_sized_cluster_is_refused(tmp_path: Path) -> None:
    cert, key, ca = _certs(tmp_path)
    args = parse_args([
        "--registryDisableTLS", "--distributed",
        "--registryAdvertisedHost", "a",
        "--registryNeighbour", "b",
        "--etcdCertificate", cert, "--etcdKey", key,
        "--etcdTrustedRootCA", ca,
    ])
    with pytest.raises(DistributedConfigError, match="1, 3, 5 members"):
        resolve_distributed_config(args)


# ---------------------------------------------------------------------------
# TLS inputs
# ---------------------------------------------------------------------------

def test_missing_etcd_certificate_is_refused(tmp_path: Path) -> None:
    args = parse_args([
        "--registryDisableTLS", "--distributed",
        "--registryAdvertisedHost", "a",
    ])
    with pytest.raises(DistributedConfigError, match="--etcdCertificate"):
        resolve_distributed_config(args)


def test_unreadable_certificate_is_refused(tmp_path: Path) -> None:
    _cert, key, ca = _certs(tmp_path)
    args = parse_args([
        "--registryDisableTLS", "--distributed",
        "--registryAdvertisedHost", "a",
        "--etcdCertificate", str(tmp_path / "absent.pem"),
        "--etcdKey", key, "--etcdTrustedRootCA", ca,
    ])
    with pytest.raises(DistributedConfigError, match="not accessible"):
        resolve_distributed_config(args)


def test_disable_tls_skips_certificate_checks(tmp_path: Path) -> None:
    args = parse_args([
        "--registryDisableTLS", "--distributed", "--etcdDisableTLS",
        "--registryAdvertisedHost", "a",
    ])
    config = resolve_distributed_config(args)
    assert config is not None
    assert config.tls is False
    assert config.layout.initial_cluster().startswith("nmos-registry-a=http://")


def test_empty_certificate_name_is_refused(tmp_path: Path) -> None:
    """It is a security control, not a label."""
    args = _args(tmp_path, "--etcdCertificateName", "")
    with pytest.raises(
        DistributedConfigError, match="--etcdCertificateName must not be empty",
    ):
        resolve_distributed_config(args)


def test_certificate_name_defaults_to_the_generated_shared_san(
    tmp_path: Path,
) -> None:
    """A default naming a SAN that does not exist fails every etcd handshake."""
    config = resolve_distributed_config(_args(tmp_path))
    assert config is not None
    assert config.certificate_name == (
        "Example.Company.Device.Etcd.ABC.example.com"
    )


def test_the_default_san_is_present_in_the_shipped_certificates() -> None:
    """Read the assertion above off the certificates themselves.

    The test above pins the default against a string typed out here, which only
    ever restated an intention: both could be wrong together, and the failure
    would surface as an etcd handshake rejecting every peer. Now that the etcd
    set ships in ``Certificates/build.0.etcd/`` the claim is checkable, so it is
    checked -- against both certificate flavours, since a cluster may be brought
    up on either.
    """
    flavors: tuple[Flavor, ...] = ("rsa", "ec")
    for flavor in flavors:
        chain = etcd_chain("SNX10000", flavor)
        assert chain.is_file(), f"the shipped set is missing {chain}"
        assert etcd_key("SNX10000", flavor).is_file(), (
            "a certificate without its key cannot start etcd"
        )
        names = cert_dns_identities(str(chain))
        assert "Example.Company.Device.Etcd.ABC.example.com" in names, (
            f"{chain.name} does not carry the default --etcdCertificateName; "
            f"found {names}"
        )


# ---------------------------------------------------------------------------
# Endpoints and process management
# ---------------------------------------------------------------------------

def test_endpoints_are_derived_from_the_member_list(tmp_path: Path) -> None:
    config = resolve_distributed_config(_args(tmp_path))
    assert config is not None
    assert set(config.endpoints) == {"a:2381", "b:2381", "c:2381"}


def test_explicit_endpoints_override_derivation(tmp_path: Path) -> None:
    config = resolve_distributed_config(
        _args(tmp_path, "--etcdEndpoints", "x:1,y:2"),
    )
    assert config is not None
    assert config.endpoints == ("x:1", "y:2")


def test_external_requires_endpoints(tmp_path: Path) -> None:
    args = _args(tmp_path, "--etcdExternal")
    with pytest.raises(DistributedConfigError, match="requires --etcdEndpoints"):
        resolve_distributed_config(args)


def test_external_manages_no_process(tmp_path: Path) -> None:
    config = resolve_distributed_config(
        _args(tmp_path, "--etcdExternal", "--etcdEndpoints", "x:1"),
    )
    assert config is not None
    assert config.external is True
    assert config.manages_process is False
    assert config.bootstrap is False


def test_managed_member_prefers_the_repo_local_binary(tmp_path: Path) -> None:
    """Same convention as .playwright/: a pinned local install beats PATH."""
    config = resolve_distributed_config(_args(tmp_path))
    assert config is not None
    assert config.binary.endswith("etcd")


def test_explicit_binary_wins(tmp_path: Path) -> None:
    config = resolve_distributed_config(
        _args(tmp_path, "--etcdBinary", "/opt/etcd/bin/etcd"),
    )
    assert config is not None
    assert config.binary == "/opt/etcd/bin/etcd"


# ---------------------------------------------------------------------------
# The Windows platform rule (§6.1)
# ---------------------------------------------------------------------------

def test_windows_forces_external_and_requires_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    with pytest.raises(DistributedConfigError, match="requires --etcdEndpoints"):
        resolve_distributed_config(_args(tmp_path))

    config = resolve_distributed_config(
        _args(tmp_path, "--etcdEndpoints", "localhost:2381,localhost:2391"),
    )
    assert config is not None
    assert config.external is True
    assert config.manages_process is False


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--etcdBinary", "C:/etcd/etcd.exe"),
        ("--etcdDataDir", "C:/etcd/data"),
    ],
)
def test_windows_rejects_process_management_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str, value: str,
) -> None:
    """Rejected, not ignored: nobody may believe they configured a member."""
    monkeypatch.setattr(sys, "platform", "win32")
    args = _args(tmp_path, flag, value, "--etcdEndpoints", "h:1")
    with pytest.raises(DistributedConfigError, match="cannot be used on native Windows"):
        resolve_distributed_config(args)


def test_windows_rejects_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    args = _args(tmp_path, "--etcdBootstrap", "--etcdEndpoints", "h:1")
    with pytest.raises(DistributedConfigError, match="cannot be used on native Windows"):
        resolve_distributed_config(args)


def test_wsl_is_not_special_cased(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under WSL sys.platform is "linux", so it is an ordinary POSIX member.

    The gate must be one platform check with no heuristics -- no /proc/version
    sniffing, no WSL_DISTRO_NAME. A registry inside WSL2 gets the full
    supervisor and a Tier 1 etcd.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")

    config = resolve_distributed_config(_args(tmp_path))
    assert config is not None
    assert config.external is False
    assert config.manages_process is True
