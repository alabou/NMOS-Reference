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
    with pytest.raises(DistributedConfigError, match="duplicate member"):
        resolve_distributed_config(args)


def test_co_located_members_are_distinguished_by_port(tmp_path: Path) -> None:
    """One host, three members, three ports.

    The shape a single-machine secured cluster has to take: co-located members
    share the machine's address because etcd verifies a peer's certificate
    against the address its connection arrives from, so only the port is left
    to tell them apart.
    """
    cert, key, ca = _certs(tmp_path)
    args = parse_args([
        "--registryDisableTLS", "--distributed",
        "--registryAdvertisedHost", "a:2381",
        "--registryNeighbour", "a:2391",
        "--registryNeighbour", "a:2401",
        "--etcdCertificate", cert, "--etcdKey", key,
        "--etcdTrustedRootCA", ca,
    ])
    config = resolve_distributed_config(args)
    assert config is not None
    assert config.layout.size == 3
    assert sorted(m.client_port for m in config.layout.members) == [
        2381, 2391, 2401,
    ]
    # Peer ports follow the client port, as --etcdEndpoints already assumes.
    assert sorted(m.peer_port for m in config.layout.members) == [
        2382, 2392, 2402,
    ]
    # The local member is this process's, not merely the first sharing a host.
    assert config.layout.local.client_port == 2381


def test_a_member_with_a_malformed_port_is_refused(tmp_path: Path) -> None:
    cert, key, ca = _certs(tmp_path)
    args = parse_args([
        "--registryDisableTLS", "--distributed",
        "--registryAdvertisedHost", "a:not-a-port",
        "--etcdCertificate", cert, "--etcdKey", key,
        "--etcdTrustedRootCA", ca,
    ])
    with pytest.raises(DistributedConfigError, match="host:client_port"):
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
# A secured registry implies a secured etcd
# ---------------------------------------------------------------------------

def _secure_listener_args(tmp_path: Path) -> list[str]:
    """The flags that make the Registration and Query listeners TLS."""
    cert, key, _ = _certs(tmp_path)
    return ["--registryCertificate", cert, "--registryKey", key]


def test_plaintext_etcd_under_a_tls_registry_is_refused(tmp_path: Path) -> None:
    """The combination that is strictly worse than a plain-HTTP registry.

    etcd holds every registered resource. Encrypting the interface an operator
    inspects while leaving that database readable and writable by anyone who can
    reach the client port is the one arrangement that actively misleads.
    """
    cert, key, ca = _certs(tmp_path)
    args = parse_args([
        *_secure_listener_args(tmp_path),
        "--distributed", "--etcdDisableTLS",
        "--registryAdvertisedHost", "a",
        "--etcdCertificate", cert, "--etcdKey", key,
        "--etcdTrustedRootCA", ca,
    ])
    with pytest.raises(DistributedConfigError, match="--etcdDisableTLS cannot"):
        resolve_distributed_config(args)


def test_the_refusal_names_the_certificates_it_would_have_ignored(
    tmp_path: Path,
) -> None:
    """Silence here is the actual defect being fixed.

    ``tls`` derives from --etcdDisableTLS alone, so before this refusal a
    command line carrying both that flag and a full certificate set was accepted
    with the certificates dropped -- and the operator read back their own
    secured command line and believed it had taken effect.
    """
    cert, key, ca = _certs(tmp_path)
    args = parse_args([
        *_secure_listener_args(tmp_path),
        "--distributed", "--etcdDisableTLS",
        "--registryAdvertisedHost", "a",
        "--etcdCertificate", cert, "--etcdKey", key,
        "--etcdTrustedRootCA", ca,
    ])
    with pytest.raises(DistributedConfigError) as caught:
        resolve_distributed_config(args)

    message = str(caught.value)
    assert "IGNORED" in message
    for flag in ("--etcdCertificate", "--etcdKey", "--etcdTrustedRootCA"):
        assert flag in message


def test_plaintext_etcd_is_allowed_when_the_registry_is_plaintext_too(
    tmp_path: Path,
) -> None:
    """The development rig stays available: unsecured is fine, mixed is not."""
    args = parse_args([
        "--registryDisableTLS", "--distributed", "--etcdDisableTLS",
        "--registryAdvertisedHost", "a",
    ])
    config = resolve_distributed_config(args)
    assert config is not None
    assert config.tls is False


def test_a_tls_registry_over_a_secured_etcd_is_accepted(tmp_path: Path) -> None:
    """The configuration the secured rig actually uses."""
    cert, key, ca = _certs(tmp_path)
    args = parse_args([
        *_secure_listener_args(tmp_path),
        "--distributed",
        "--registryAdvertisedHost", "a",
        "--etcdCertificate", cert, "--etcdKey", key,
        "--etcdTrustedRootCA", ca,
    ])
    config = resolve_distributed_config(args)
    assert config is not None
    assert config.tls is True


def test_a_certificate_without_a_key_does_not_count_as_a_tls_registry(
    tmp_path: Path,
) -> None:
    """Same three inputs classify_registry_rap uses to tell RAP 0 from RAP 1/2.

    A certificate with no key produces no TLS listener, so the registry is RAP 0
    and --etcdDisableTLS is not the mixed configuration this rule refuses. The
    two classifications must agree, or a command line would be RAP 0 to one and
    secured to the other.
    """
    cert, _, _ = _certs(tmp_path)
    args = parse_args([
        "--registryCertificate", cert,
        "--distributed", "--etcdDisableTLS",
        "--registryAdvertisedHost", "a",
    ])
    config = resolve_distributed_config(args)
    assert config is not None
    assert config.tls is False


@pytest.fixture(autouse=True)
def resolves(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Pin name resolution for the whole module.

    The off-the-loopback rule resolves every member name, so without this the
    placeholder hosts these tests use ("a", "b", "c") each cost a real DNS
    round trip -- NXDOMAIN through the search list, which took this file from
    3 seconds to 45.

    Hermetic matters more than fast, though: on a machine with no DNS a
    routable name would not resolve, the rule would skip it, and a test
    asserting a refusal would pass for the wrong reason. Tests that care about
    an address say so; everything else resolves to loopback.
    """
    mapping: dict[str, str | None] = {}
    fallback: list[str | None] = ["127.0.0.1"]

    monkeypatch.setattr(
        "nmos.registry.distributed._resolve_host",
        lambda host: mapping.get(host, fallback[0]),
    )

    def pin(known: dict[str, str | None], *, others: str | None = "127.0.0.1") -> None:
        mapping.update(known)
        fallback[0] = others

    return pin


def test_an_unsecured_cluster_may_not_leave_the_machine(
    tmp_path: Path, resolves,
) -> None:
    """Plaintext etcd is a development convenience, not a deployment option.

    Off the loopback the member's database -- every registered resource, and
    every write that changes one -- is on a wire, unencrypted and
    unauthenticated. "We were only testing" is how that reaches a deployment,
    so the boundary is enforced rather than documented.
    """
    resolves({"registry-on-the-lan": "192.168.7.20"})
    args = parse_args([
        "--registryDisableTLS", "--distributed", "--etcdDisableTLS",
        "--registryAdvertisedHost", "registry-on-the-lan",
    ])
    with pytest.raises(DistributedConfigError, match="confined to one machine"):
        resolve_distributed_config(args)


def test_the_same_rule_applies_to_an_external_cluster(
    tmp_path: Path, resolves,
) -> None:
    """--etcdExternal describes its cluster with endpoints, not a member list.

    The refusal has to read whichever of the two actually describes the
    deployment, or pointing a registry at someone else's plaintext cluster on
    the network would slip past the rule that stops it running its own.
    """
    resolves({"etcd-on-the-lan": "10.4.0.9"})
    args = parse_args([
        "--registryDisableTLS", "--distributed", "--etcdExternal",
        "--etcdDisableTLS", "--etcdEndpoints", "etcd-on-the-lan:2381",
    ])
    with pytest.raises(DistributedConfigError, match="confined to one machine"):
        resolve_distributed_config(args)


def test_loopback_keeps_the_development_rig(tmp_path: Path) -> None:
    """The case --etcdDisableTLS exists for: packets that never reach a wire."""
    args = parse_args([
        "--registryDisableTLS", "--distributed", "--etcdDisableTLS",
        "--registryAdvertisedHost", "127.0.0.1",
    ])
    config = resolve_distributed_config(args)
    assert config is not None
    assert config.tls is False


def test_a_name_that_does_not_resolve_is_left_to_its_own_diagnosis(
    tmp_path: Path, resolves,
) -> None:
    """A DNS problem must not surface as a security message.

    Unresolvable hosts fail later with an error about the name; pre-empting
    that here would send an operator looking for a certificate they never
    needed.
    """
    resolves({}, others=None)          # nothing resolves at all
    args = parse_args([
        "--registryDisableTLS", "--distributed", "--etcdDisableTLS",
        "--registryAdvertisedHost", "no-such-host.invalid",
    ])
    config = resolve_distributed_config(args)
    assert config is not None


# ---------------------------------------------------------------------------
# A managed member has to bind an address, not a name
# ---------------------------------------------------------------------------

def test_a_managed_member_binds_a_resolved_address(tmp_path: Path) -> None:
    """etcd refuses a hostname in a listen URL -- "expected IP in URL for
    binding" -- so a member whose bind address defaulted to its own name could
    never start. It has to be NAMED for its certificate and LISTEN on an
    address, and nothing exercised that until the managed mode did.
    """
    args = parse_args([
        "--registryDisableTLS", "--distributed", "--etcdDisableTLS",
        "--registryAdvertisedHost", "localhost",
    ])
    config = resolve_distributed_config(args)
    assert config is not None

    local = config.layout.local
    assert local.host == "localhost"                  # named for the certificate
    assert local.bind_address == "127.0.0.1"          # listens on an address
    assert local.listen_peer_url(tls=False) == "http://127.0.0.1:2382"
    # The advertised URL keeps the name: that is what peers verify against.
    assert local.advertise_peer_url(tls=False) == "http://localhost:2382"


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
