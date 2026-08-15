# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""End-to-end against SECURED etcd members: real processes, real certificates.

    pytest nmos/registry/tests/test_etcd_mtls_e2e.py -m e2e

Why this file exists
--------------------
Every other test of etcd security in this repository asserts that the right
strings were assembled -- the argv handed to etcd, the paths handed to gRPC.
None of them proved that the two halves interoperate, and none of them proved
that the restriction the whole design rests on is actually enforced. A member
started with --client-cert-allowed-hostname and a client that never presents a
certificate would pass every one of those tests.

So the assertions here are about behaviour under a real TLS handshake:

  * the registry backend reads and writes through a member secured with the
    shipped certificate set,
  * an ordinary device certificate signed by the SAME Product CA is refused,
    which is the control that stops any device on the network from writing to
    the registry database,
  * three secured members form a cluster over mutual TLS between peers, and
    keep serving when one of them dies.

Addressing
----------
Members share one bind address and separate by port. That is not a convenience:
etcd verifies the certificate a peer presents against the address its connection
arrives from, and every connection between loopback addresses is sourced from
127.0.0.1 whatever its destination -- so members on separate loopback addresses
cannot complete a peer handshake with certificates that carry DNS SANs only.
See ``etcd_cluster.py`` for the same reasoning in the rig.

Client connections use the bind ADDRESS rather than the member name, with the
shared etcd SAN as the gRPC target-name override. The override is what makes the
certificate verify, and using the address keeps the tests independent of the
resolver -- gRPC's c-ares resolver does not always honour /etc/hosts.
"""

from __future__ import annotations

import asyncio
import shutil
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nmos.registry.tests._fixtures import make_node
from nmos.registry.tests.test_etcd_backend import _eventually, build_registry
from nmos.registry.types import Body, ResourceType

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[3]
CERT_ROOT = REPO_ROOT / "Certificates"
ETCD_CERTS = CERT_ROOT / "build.0.etcd"
BUNDLED_ETCD = REPO_ROOT / ".etcd" / "etcd"

# The SAN every etcd certificate shares, and so both the gRPC target-name
# override and what etcd's --client/peer-cert-allowed-hostname checks.
from nmos.etcd.cluster import DEFAULT_ETCD_CERTIFICATE_NAME  # noqa: E402

MEMBER_HOST = "XYZ-SNX1000{index}"
BIND_ADDRESS = "127.0.0.1"
NAMESPACE = "/nmos-test/registry/mtls"


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------

def _etcd_binary() -> str | None:
    if BUNDLED_ETCD.is_file():
        return str(BUNDLED_ETCD)
    return shutil.which("etcd")


def _resolves_to_bind_address(host: str) -> bool:
    try:
        found = {
            info[4][0] for info in socket.getaddrinfo(host, None, socket.AF_INET)
        }
    except OSError:
        return False
    return BIND_ADDRESS in found


@pytest.fixture(scope="session")
def etcd_binary() -> str:
    binary = _etcd_binary()
    if binary is None:
        pytest.skip("etcd not installed; run ./install-etcd.sh")
    return binary


@pytest.fixture(scope="session", autouse=True)
def require_certificates_and_hosts() -> None:
    """Skip rather than fail where the rig has not been set up.

    Both preconditions are machine configuration, not code: the certificate set
    ships with the repository, but a checkout without it (or without the hosts
    entries these certificates are named for) cannot run a secured member and
    should say so plainly.
    """
    if not (ETCD_CERTS / "pem").is_dir():
        pytest.skip(f"etcd certificate set missing: {ETCD_CERTS}")
    missing = [
        MEMBER_HOST.format(index=index)
        for index in range(3)
        if not _resolves_to_bind_address(MEMBER_HOST.format(index=index))
    ]
    if missing:
        pytest.skip(
            f"{', '.join(missing)} must resolve to {BIND_ADDRESS} in /etc/hosts "
            f"— a secured member is addressed by the name its certificate "
            f"attests",
        )


# ---------------------------------------------------------------------------
# Identities
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind((BIND_ADDRESS, 0))
        port: int = probe.getsockname()[1]
        return port


def etcd_identity(index: int) -> tuple[str, str, str]:
    """(certificate, key, trusted root CA) for secured member ``index``."""
    stem = f"ExampleDeviceServer.ABC.SNX1000{index}.etcd"
    return (
        str(ETCD_CERTS / "pem" / f"{stem}.chain.pem"),
        str(ETCD_CERTS / "key" / f"{stem}.key"),
        str(CERT_ROOT / "build.0" / "ExampleRootCA-bundle.pem"),
    )


def ordinary_device_identity() -> tuple[str, str, str]:
    """A perfectly valid device certificate from the SAME Product CA.

    The certificate an attacker on this network already has: it chains to the
    trust anchor etcd is given, so only the SAN restriction separates it from
    the etcd identities.
    """
    return (
        str(CERT_ROOT / "build.0" / "pem"
            / "ExampleDeviceClient.ABC.SNX00001.chain.pem"),
        str(CERT_ROOT / "build.0" / "key" / "ExampleDeviceClient.ABC.SNX00001.key"),
        str(CERT_ROOT / "build.0" / "ExampleRootCA-bundle.pem"),
    )


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

def _layout(ports: list[tuple[int, int]], local: int) -> Any:
    from nmos.etcd.cluster import MemberSpec, derive_cluster

    specs = [
        MemberSpec(
            host=MEMBER_HOST.format(index=index),
            client_port=client,
            peer_port=peer,
            name=f"secured-{index}",
            bind_address=BIND_ADDRESS,
        )
        for index, (client, peer) in enumerate(ports)
    ]
    return derive_cluster(
        specs,
        local_host=specs[local].host,
        local_peer_port=specs[local].peer_port,
        namespace=NAMESPACE,
        tls=True,
    )


def _supervisor(
    binary: str, data_dir: Path, ports: list[tuple[int, int]], local: int,
) -> Any:
    from nmos.etcd.supervisor import EtcdSupervisor

    certificate, key, ca = etcd_identity(local)
    return EtcdSupervisor(
        layout=_layout(ports, local),
        binary=binary,
        data_dir=data_dir,
        bootstrap=True,
        tls=True,
        certificate=certificate,
        key=key,
        trusted_root_ca=(ca,),
        certificate_name=DEFAULT_ETCD_CERTIFICATE_NAME,
        startup_timeout=60.0,
    )


@pytest.fixture
async def secured_member(
    etcd_binary: str, tmp_path: Path,
) -> AsyncIterator[tuple[Any, str]]:
    """One secured member. Yields (supervisor, client endpoint)."""
    ports = [(_free_port(), _free_port())]
    supervisor = _supervisor(etcd_binary, tmp_path / "m0", ports, local=0)
    await supervisor.start()
    try:
        yield supervisor, f"{BIND_ADDRESS}:{ports[0][0]}"
    finally:
        await supervisor.stop()


@pytest.fixture
async def secured_cluster(
    etcd_binary: str, tmp_path: Path,
) -> AsyncIterator[tuple[list[Any], list[str]]]:
    """Three secured members with mutual TLS between peers."""
    ports = [(_free_port(), _free_port()) for _ in range(3)]
    supervisors = [
        _supervisor(etcd_binary, tmp_path / f"m{index}", ports, local=index)
        for index in range(3)
    ]
    # Started together: with initial-cluster-state=new every member blocks
    # until it can reach a quorum of peers, so starting them one at a time and
    # waiting for each would deadlock on the first.
    await asyncio.gather(*(s.start() for s in supervisors))
    endpoints = [f"{BIND_ADDRESS}:{client}" for client, _ in ports]
    try:
        yield supervisors, endpoints
    finally:
        for supervisor in reversed(supervisors):
            await supervisor.stop()


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def _pool(endpoints: list[str], identity: tuple[str, str, str] | None) -> Any:
    """A channel pool, optionally presenting a client certificate."""
    from nmos.etcd.channel import EtcdChannelPool, build_credentials, parse_endpoints

    credentials = None
    if identity is not None:
        certificate, key, ca = identity
        credentials = build_credentials(
            trusted_root_ca=[ca], certificate=certificate, key=key,
        )
    return EtcdChannelPool(
        parse_endpoints(endpoints),
        credentials=credentials,
        target_name=DEFAULT_ETCD_CERTIFICATE_NAME if identity else None,
        rpc_timeout=5.0,
    )


async def _member_count(pool: Any) -> int:
    from nmos.etcd.channel import unary_method
    from nmos.etcd.generated import rpc_pb2

    member_list = unary_method(
        "Cluster", "MemberList",
        rpc_pb2.MemberListRequest, rpc_pb2.MemberListResponse,
    )
    answer = await pool.call(member_list, rpc_pb2.MemberListRequest())
    return len(answer.members)


def _secured_config(endpoints: list[str], namespace: str) -> Any:
    from nmos.registry.distributed import DistributedConfig

    certificate, key, ca = etcd_identity(0)
    ports = [(int(e.rpartition(":")[2]), int(e.rpartition(":")[2]) + 1)
             for e in endpoints]
    return DistributedConfig(
        layout=_layout(ports, 0),
        endpoints=tuple(endpoints),
        namespace=namespace,
        external=True,
        binary="",
        data_dir=Path(),
        bootstrap=False,
        tls=True,
        certificate=certificate,
        key=key,
        trusted_root_ca=(ca,),
        certificate_name=DEFAULT_ETCD_CERTIFICATE_NAME,
        client_crl_file="",
        peer_crl_file="",
        rpc_timeout=5.0,
        mutation_timeout=10.0,
    )


# ---------------------------------------------------------------------------
# The registry over mutual TLS
# ---------------------------------------------------------------------------

async def test_the_backend_reads_and_writes_through_a_secured_member(
    secured_member: tuple[Any, str],
) -> None:
    """The claim nothing previously tested: the two halves interoperate.

    The certificate the member presents on its client listener is the same one
    the registry presents back as a client certificate -- the dual EKU in
    practice, not in the argv.
    """
    from nmos.registry.etcd_backend import EtcdRegistryBackend

    _, endpoint = secured_member
    registry = build_registry()
    backend = EtcdRegistryBackend(
        registry, _secured_config([endpoint], NAMESPACE),
    )
    await backend.start()
    try:
        node = make_node()
        await backend.register(ResourceType.NODE, Body.from_data(node))
        await _eventually(
            lambda: registry.store.get(ResourceType.NODE, node["id"]) is not None,
        )
    finally:
        await backend.close()


# ---------------------------------------------------------------------------
# The restriction, actually enforced
# ---------------------------------------------------------------------------

async def test_an_ordinary_device_certificate_cannot_reach_the_database(
    secured_member: tuple[Any, str],
) -> None:
    """The control the entire design rests on.

    ExampleDeviceClient.ABC.SNX00001 is signed by the same Product CA as every
    etcd certificate, so it satisfies --trusted-ca-file on its own. What it does
    NOT carry is the shared etcd SAN, and --client-cert-allowed-hostname is what
    turns that difference into a refusal. Asserting the flag is in the argv (as
    test_supervisor.py does) does not test this; only a handshake does.
    """
    from nmos.etcd.errors import EtcdError

    _, endpoint = secured_member
    pool = _pool([endpoint], ordinary_device_identity())
    try:
        with pytest.raises(EtcdError):
            await _member_count(pool)
    finally:
        await pool.close()


async def test_a_client_presenting_no_certificate_is_refused(
    secured_member: tuple[Any, str],
) -> None:
    """--client-cert-auth: TLS alone is not enough, a certificate is required."""
    from nmos.etcd.errors import EtcdError

    _, endpoint = secured_member
    pool = _pool([endpoint], identity=None)      # plaintext, as --etcdDisableTLS
    try:
        with pytest.raises(EtcdError):
            await _member_count(pool)
    finally:
        await pool.close()


async def test_the_etcd_identity_is_accepted_by_the_same_member(
    secured_member: tuple[Any, str],
) -> None:
    """The positive half of the two refusals above.

    Without it, a member that refused *everything* -- misconfigured, wrong CA,
    not actually serving -- would pass both negative tests and look like proof
    that the restriction works.
    """
    _, endpoint = secured_member
    pool = _pool([endpoint], etcd_identity(0))
    try:
        assert await _member_count(pool) == 1
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Several trusted roots
# ---------------------------------------------------------------------------

def ec_identity(index: int) -> tuple[str, str, str]:
    """The ECDSA flavour of a member's certificate.

    It chains to ``ExampleRootCA.ec.pem`` and to nothing else -- verified in
    this repository by ``openssl verify``, and the reason it can tell apart a
    member that trusts both roots from one that trusts only the first.
    """
    stem = f"ExampleDeviceServer.ABC.SNX1000{index}.etcd"
    return (
        str(ETCD_CERTS / "pem" / f"{stem}.ec.chain.pem"),
        str(ETCD_CERTS / "key" / f"{stem}.ec.key"),
        str(CERT_ROOT / "build.0" / "ExampleRootCA-bundle.pem"),
    )


@pytest.fixture
async def member_trusting_two_roots(
    etcd_binary: str, tmp_path: Path,
) -> AsyncIterator[str]:
    """A member handed the two root generations as SEPARATE files.

    Not the combined bundle: separate roots are what ``--etcdTrustedRootCA``
    accepts when repeated, and the case that used to be silently truncated.
    """
    from nmos.etcd.supervisor import EtcdSupervisor

    ports = [(_free_port(), _free_port())]
    certificate, key, _ = etcd_identity(0)          # RSA member certificate
    supervisor = EtcdSupervisor(
        layout=_layout(ports, 0),
        binary=etcd_binary,
        data_dir=tmp_path / "split",
        bootstrap=True,
        tls=True,
        certificate=certificate,
        key=key,
        trusted_root_ca=(
            str(CERT_ROOT / "build.0" / "ExampleRootCA.pem"),
            str(CERT_ROOT / "build.0" / "ExampleRootCA.ec.pem"),
        ),
        certificate_name=DEFAULT_ETCD_CERTIFICATE_NAME,
        startup_timeout=60.0,
    )
    await supervisor.start()
    try:
        yield f"{BIND_ADDRESS}:{ports[0][0]}"
    finally:
        await supervisor.stop()


async def test_a_client_chaining_to_the_second_root_is_accepted(
    member_trusting_two_roots: str,
) -> None:
    """Both roots reach etcd, not just the first one in the list.

    The member's own certificate is RSA and the client's is ECDSA, so this
    connection succeeds only if the member trusts the ECDSA root -- the second
    entry it was given. Before the fix it trusted the first entry alone and
    refused this client, while the registry's own client channel, built by
    ``build_credentials`` from the same list, accepted it.
    """
    pool = _pool([member_trusting_two_roots], ec_identity(0))
    try:
        assert await _member_count(pool) == 1
    finally:
        await pool.close()


async def test_a_client_chaining_to_the_first_root_still_works(
    member_trusting_two_roots: str,
) -> None:
    """The other half of the pair: combining must not drop the first root."""
    pool = _pool([member_trusting_two_roots], etcd_identity(0))
    try:
        assert await _member_count(pool) == 1
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Peer mutual TLS
# ---------------------------------------------------------------------------

async def test_three_secured_members_form_a_cluster(
    secured_cluster: tuple[list[Any], list[str]],
) -> None:
    """Peer mutual TLS, which is a different handshake from the client one.

    Peers authenticate each other with --peer-client-cert-auth AND have their
    certificates checked against the address they connect from. A cluster that
    elects a leader is proof that both passed for every pair.
    """
    _, endpoints = secured_cluster
    pool = _pool(endpoints, etcd_identity(0))
    try:
        assert await _member_count(pool) == 3
    finally:
        await pool.close()


async def test_the_cluster_keeps_serving_when_one_member_dies(
    secured_cluster: tuple[list[Any], list[str]],
) -> None:
    """Three members tolerate one failure -- over mutual TLS like any other."""
    from nmos.registry.etcd_backend import EtcdRegistryBackend

    supervisors, endpoints = secured_cluster
    registry = build_registry()
    backend = EtcdRegistryBackend(
        registry, _secured_config(endpoints, NAMESPACE),
    )
    await backend.start()
    try:
        await supervisors[-1].stop()

        node = make_node()
        await backend.register(ResourceType.NODE, Body.from_data(node))
        await _eventually(
            lambda: registry.store.get(ResourceType.NODE, node["id"]) is not None,
        )
    finally:
        await backend.close()
