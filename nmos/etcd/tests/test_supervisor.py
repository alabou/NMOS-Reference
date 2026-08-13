# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the local etcd member supervisor.

The ownership rule is tested in **both** directions, because each half protects
against a different real failure: terminating a self-launched child stops a
Ctrl-C'd run orphaning a process that holds the client port and the data-dir
lock, and *not* terminating an adopted one stops the registry killing a
service-managed etcd out from under systemd.
"""

from __future__ import annotations

import asyncio
import shutil
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from nmos.etcd.cluster import MemberSpec, derive_cluster
from nmos.etcd.supervisor import (
    EtcdSupervisor,
    ProcessOwnership,
    SupervisorError,
    parse_etcd_version,
)
from nmos.etcd.tests.etcd_server import BUNDLED_ETCD

NAMESPACE = "/nmos-reference/registry/v1"


# ---------------------------------------------------------------------------
# Version gate -- pure, so it runs in the default gate
# ---------------------------------------------------------------------------

def test_parse_etcd_version_ignores_suffixes() -> None:
    assert parse_etcd_version("3.6.14") == (3, 6, 14)
    assert parse_etcd_version("3.7.0-rc.1") == (3, 7, 0)
    assert parse_etcd_version("3.6") == (3, 6)


def test_parse_etcd_version_rejects_nonsense() -> None:
    with pytest.raises(SupervisorError, match="cannot parse"):
        parse_etcd_version("not-a-version")


# ---------------------------------------------------------------------------
# Data directory rules -- pure enough to run without etcd
# ---------------------------------------------------------------------------

def _single_member_layout(port: int, peer_port: int, name: str = "m0"):
    spec = MemberSpec(
        host="127.0.0.1",
        client_port=port,
        peer_port=peer_port,
        name=name,
        bind_address="127.0.0.1",
    )
    return derive_cluster(
        [spec],
        local_host="127.0.0.1",
        local_peer_port=peer_port,
        namespace=NAMESPACE,
        tls=False,
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def test_bootstrap_on_a_populated_data_dir_is_refused(tmp_path: Path) -> None:
    """Bootstrapping an existing member forks the cluster."""
    data_dir = tmp_path / "member"
    data_dir.mkdir()
    (data_dir / "member").mkdir()

    supervisor = EtcdSupervisor(
        layout=_single_member_layout(_free_port(), _free_port()),
        binary="etcd",
        data_dir=data_dir,
        bootstrap=True,
        tls=False,
    )
    with pytest.raises(SupervisorError, match="is not empty"):
        supervisor._validate_data_dir()


def test_empty_data_dir_without_bootstrap_is_allowed(tmp_path: Path) -> None:
    """A missing directory must NEVER be read as "make a new cluster".

    It means initial-cluster-state=existing: the member was added by an
    explicit membership operation and is starting for the first time.
    Inferring a new cluster here is how recovering one dead member ends up
    creating a second cluster.
    """
    supervisor = EtcdSupervisor(
        layout=_single_member_layout(_free_port(), _free_port()),
        binary="etcd",
        data_dir=tmp_path / "member",
        bootstrap=False,
        tls=False,
    )
    supervisor._validate_data_dir()
    assert (tmp_path / "member").is_dir()
    assert "--initial-cluster-state" in supervisor._build_argv()
    argv = supervisor._build_argv()
    assert argv[argv.index("--initial-cluster-state") + 1] == "existing"


def test_bootstrap_selects_cluster_state_new(tmp_path: Path) -> None:
    supervisor = EtcdSupervisor(
        layout=_single_member_layout(_free_port(), _free_port()),
        binary="etcd",
        data_dir=tmp_path / "member",
        bootstrap=True,
        tls=False,
    )
    argv = supervisor._build_argv()
    assert argv[argv.index("--initial-cluster-state") + 1] == "new"


def test_tls_argv_carries_the_identity_restriction(tmp_path: Path) -> None:
    """--client/peer-cert-allowed-hostname is a control, not hardening.

    Without it, any device certificate signed by the same Product CA is
    accepted as an etcd client and can write to the registry database.
    """
    supervisor = EtcdSupervisor(
        layout=_single_member_layout(_free_port(), _free_port()),
        binary="etcd",
        data_dir=tmp_path / "member",
        tls=True,
        certificate="/tmp/cert.pem",
        key="/tmp/key.pem",
        trusted_root_ca=("/tmp/ca.pem",),
        certificate_name="Example.Company.Device.Etcd.ABC.example.com",
    )
    argv = supervisor._build_argv()

    assert "--client-cert-auth" in argv
    assert "--peer-client-cert-auth" in argv
    assert argv[argv.index("--tls-min-version") + 1] == "TLS1.2"
    for flag in (
        "--client-cert-allowed-hostname", "--peer-cert-allowed-hostname",
    ):
        assert argv[argv.index(flag) + 1] == (
            "Example.Company.Device.Etcd.ABC.example.com"
        )
    # One certificate serves every role; that is what its dual EKU is for.
    assert argv[argv.index("--cert-file") + 1] == "/tmp/cert.pem"
    assert argv[argv.index("--peer-cert-file") + 1] == "/tmp/cert.pem"


def test_tls_without_a_certificate_is_refused(tmp_path: Path) -> None:
    supervisor = EtcdSupervisor(
        layout=_single_member_layout(_free_port(), _free_port()),
        binary="etcd",
        data_dir=tmp_path / "member",
        tls=True,
    )
    with pytest.raises(SupervisorError, match="--etcdCertificate"):
        supervisor._build_argv()


def test_never_touches_membership_or_deletes_data(tmp_path: Path) -> None:
    """The three things the legacy start scripts did on every start.

    `rm -f -r` the data dir, `member remove` + `member add`, and an EOL binary.
    None may appear in the command line this supervisor builds.
    """
    supervisor = EtcdSupervisor(
        layout=_single_member_layout(_free_port(), _free_port()),
        binary="etcd",
        data_dir=tmp_path / "member",
        tls=False,
    )
    argv = supervisor._build_argv()
    assert not any("member" == part for part in argv[1:])
    assert not any(part in {"remove", "add"} for part in argv)
    assert "--force-new-cluster" not in argv


# ---------------------------------------------------------------------------
# Live process management
# ---------------------------------------------------------------------------

pytest_e2e = pytest.mark.e2e


@pytest.fixture
def etcd_binary() -> Iterator[str]:
    binary = str(BUNDLED_ETCD) if BUNDLED_ETCD.is_file() else shutil.which("etcd")
    if binary is None:
        pytest.skip("etcd not installed; run ./install-etcd.sh")
    yield binary


def _supervisor(
    binary: str, data_dir: Path, *, bootstrap: bool, name: str = "m0",
    client_port: int | None = None, peer_port: int | None = None,
) -> EtcdSupervisor:
    return EtcdSupervisor(
        layout=_single_member_layout(
            client_port or _free_port(), peer_port or _free_port(), name=name,
        ),
        binary=binary,
        data_dir=data_dir,
        bootstrap=bootstrap,
        tls=False,
        startup_timeout=45.0,
    )


@pytest.mark.e2e
async def test_launched_member_is_stopped_on_shutdown(
    etcd_binary: str, tmp_path: Path,
) -> None:
    """Stop what you started: no orphan holding the port or the data-dir lock."""
    supervisor = _supervisor(etcd_binary, tmp_path / "m", bootstrap=True)
    member = supervisor.layout.local

    ownership = await supervisor.start()
    assert ownership is ProcessOwnership.LAUNCHED
    assert supervisor.owns_process is True

    await supervisor.stop()

    # The port must actually be free again, not merely reported closed.
    for _ in range(50):
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex((member.bind_address, member.client_port)) != 0:
                break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("etcd was still listening after stop()")


@pytest.mark.e2e
async def test_adopted_member_survives_shutdown(
    etcd_binary: str, tmp_path: Path,
) -> None:
    """Never stop what you adopted -- the systemd case."""
    client_port, peer_port = _free_port(), _free_port()
    data_dir = tmp_path / "m"

    owner = _supervisor(
        etcd_binary, data_dir, bootstrap=True,
        client_port=client_port, peer_port=peer_port,
    )
    assert await owner.start() is ProcessOwnership.LAUNCHED

    try:
        # A second supervisor, same configuration, finds it already running.
        adopter = _supervisor(
            etcd_binary, data_dir, bootstrap=False,
            client_port=client_port, peer_port=peer_port,
        )
        assert await adopter.start() is ProcessOwnership.ADOPTED
        assert adopter.owns_process is False

        await adopter.stop()

        # Still serving: the adopter did not start it, so it must not stop it.
        with socket.socket() as probe:
            probe.settimeout(1.0)
            assert probe.connect_ex(("127.0.0.1", client_port)) == 0
    finally:
        await owner.stop()


@pytest.mark.e2e
async def test_member_with_a_different_name_is_not_adopted(
    etcd_binary: str, tmp_path: Path,
) -> None:
    """Adopting another configuration's member would serve its data."""
    client_port, peer_port = _free_port(), _free_port()

    owner = _supervisor(
        etcd_binary, tmp_path / "m", bootstrap=True, name="m0",
        client_port=client_port, peer_port=peer_port,
    )
    await owner.start()

    try:
        stranger = _supervisor(
            etcd_binary, tmp_path / "other", bootstrap=False, name="different",
            client_port=client_port, peer_port=peer_port,
        )
        with pytest.raises(SupervisorError, match="calls itself"):
            await stranger.start()
    finally:
        await owner.stop()


@pytest.mark.e2e
async def test_foreign_listener_on_the_port_is_refused(
    etcd_binary: str, tmp_path: Path,
) -> None:
    """Refuse to interfere with an unrelated process."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    try:
        supervisor = _supervisor(
            etcd_binary, tmp_path / "m", bootstrap=True,
            client_port=port, peer_port=_free_port(),
        )
        with pytest.raises(SupervisorError, match="does not answer as an etcd"):
            await supervisor.start()
    finally:
        listener.close()


@pytest.mark.e2e
async def test_data_survives_a_restart_of_the_supervisor(
    etcd_binary: str, tmp_path: Path,
) -> None:
    """A routine restart must not lose the database.

    This is the regression the legacy start scripts could never pass: they deleted
    the data directory and re-added membership on every start.
    """
    from nmos.etcd.channel import EtcdChannelPool, parse_endpoints
    from nmos.etcd.kv import EtcdKV, put_op

    client_port, peer_port = _free_port(), _free_port()
    data_dir = tmp_path / "m"

    first = _supervisor(
        etcd_binary, data_dir, bootstrap=True,
        client_port=client_port, peer_port=peer_port,
    )
    await first.start()

    pool = EtcdChannelPool(
        parse_endpoints([f"127.0.0.1:{client_port}"]),
        credentials=None, target_name=None, rpc_timeout=5.0,
    )
    try:
        await EtcdKV(pool).txn(success=[put_op(b"/survive", b"yes")])
    finally:
        await pool.close()
    await first.stop()

    # Same data directory, no bootstrap -- the member rejoins itself.
    second = _supervisor(
        etcd_binary, data_dir, bootstrap=False,
        client_port=client_port, peer_port=peer_port,
    )
    await second.start()
    pool = EtcdChannelPool(
        parse_endpoints([f"127.0.0.1:{client_port}"]),
        credentials=None, target_name=None, rpc_timeout=5.0,
    )
    try:
        result = await EtcdKV(pool).range_at(b"/survive")
        assert [kv.value for kv in result.kvs] == [b"yes"]
    finally:
        await pool.close()
        await second.stop()
