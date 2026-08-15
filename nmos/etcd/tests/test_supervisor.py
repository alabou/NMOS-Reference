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


def test_a_single_trusted_root_is_passed_straight_through(
    tmp_path: Path,
) -> None:
    """The common case writes nothing: one root is already one file."""
    root = tmp_path / "root.pem"
    root.write_bytes(b"-----ROOT-----\n")

    supervisor = EtcdSupervisor(
        layout=_single_member_layout(_free_port(), _free_port()),
        binary="etcd",
        data_dir=tmp_path / "member",
        tls=True,
        certificate="/tmp/cert.pem",
        key="/tmp/key.pem",
        trusted_root_ca=(str(root),),
    )
    argv = supervisor._build_argv()

    assert argv[argv.index("--trusted-ca-file") + 1] == str(root)
    assert argv[argv.index("--peer-trusted-ca-file") + 1] == str(root)
    assert supervisor._generated_ca is None


def test_every_trusted_root_reaches_etcd_not_just_the_first(
    tmp_path: Path,
) -> None:
    """The two halves of mutual TLS must trust the same set of roots.

    ``--trusted-ca-file`` takes one path while ``--etcdTrustedRootCA`` is
    repeatable, so passing ``trusted_root_ca[0]`` silently split the trust
    store: ``build_credentials`` trusted both roots on the client side while
    this member trusted one, and the member then rejected peers the same
    process would have accepted — with a certificate error naming a valid
    certificate.
    """
    rsa = tmp_path / "root-rsa.pem"
    ec = tmp_path / "root-ec.pem"
    rsa.write_bytes(b"-----ROOT RSA-----\n")
    ec.write_bytes(b"-----ROOT EC-----\n")

    supervisor = EtcdSupervisor(
        layout=_single_member_layout(_free_port(), _free_port()),
        binary="etcd",
        data_dir=tmp_path / "member",
        tls=True,
        certificate="/tmp/cert.pem",
        key="/tmp/key.pem",
        trusted_root_ca=(str(rsa), str(ec)),
    )
    argv = supervisor._build_argv()

    bundle = Path(argv[argv.index("--trusted-ca-file") + 1])
    assert argv[argv.index("--peer-trusted-ca-file") + 1] == str(bundle)
    assert bundle.read_bytes() == b"-----ROOT RSA-----\n-----ROOT EC-----\n"


def test_the_combined_trust_store_stays_out_of_the_data_directory(
    tmp_path: Path,
) -> None:
    """Writing it inside would turn a first start into "already initialised".

    ``start()`` refuses to bootstrap a non-empty data directory, so a file
    placed there before that check would make every fresh secured member look
    like one that had already been initialised.
    """
    rsa = tmp_path / "root-rsa.pem"
    ec = tmp_path / "root-ec.pem"
    rsa.write_bytes(b"-----ROOT RSA-----\n")
    ec.write_bytes(b"-----ROOT EC-----\n")
    data_dir = tmp_path / "member"

    supervisor = EtcdSupervisor(
        layout=_single_member_layout(_free_port(), _free_port()),
        binary="etcd",
        data_dir=data_dir,
        tls=True,
        certificate="/tmp/cert.pem",
        key="/tmp/key.pem",
        trusted_root_ca=(str(rsa), str(ec)),
    )
    supervisor._build_argv()

    assert not data_dir.exists() or not any(data_dir.iterdir())


def test_a_root_without_a_trailing_newline_still_concatenates(
    tmp_path: Path,
) -> None:
    """Two PEM blocks run together parse as one, and etcd would trust neither.

    PEM files do not reliably end in a newline, so joining them verbatim can
    produce ``-----END CERTIFICATE----------BEGIN CERTIFICATE-----``.
    """
    first = tmp_path / "first.pem"
    second = tmp_path / "second.pem"
    first.write_bytes(b"-----ROOT ONE-----")      # no trailing newline
    second.write_bytes(b"-----ROOT TWO-----\n")

    supervisor = EtcdSupervisor(
        layout=_single_member_layout(_free_port(), _free_port()),
        binary="etcd",
        data_dir=tmp_path / "member",
        tls=True,
        certificate="/tmp/cert.pem",
        key="/tmp/key.pem",
        trusted_root_ca=(str(first), str(second)),
    )
    argv = supervisor._build_argv()

    bundle = Path(argv[argv.index("--trusted-ca-file") + 1])
    assert bundle.read_bytes() == b"-----ROOT ONE-----\n-----ROOT TWO-----\n"


def test_an_unreadable_trusted_root_names_the_path(tmp_path: Path) -> None:
    root = tmp_path / "root.pem"
    root.write_bytes(b"-----ROOT-----\n")

    supervisor = EtcdSupervisor(
        layout=_single_member_layout(_free_port(), _free_port()),
        binary="etcd",
        data_dir=tmp_path / "member",
        tls=True,
        certificate="/tmp/cert.pem",
        key="/tmp/key.pem",
        trusted_root_ca=(str(root), str(tmp_path / "absent.pem")),
    )
    with pytest.raises(SupervisorError, match="absent.pem"):
        supervisor._build_argv()


def test_revocation_lists_reach_both_listeners(tmp_path: Path) -> None:
    """--etcdClientCrlFile and --etcdPeerCrlFile are separate flags for a reason.

    A revoked certificate is the case where the allowed-hostname restriction
    stops helping: the certificate still carries the etcd SAN, so only the CRL
    keeps its holder out. The client and peer lists are independent because a
    deployment may revoke a registry's access without ejecting its etcd member.
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
        client_crl_file="/tmp/clients.crl",
        peer_crl_file="/tmp/peers.crl",
    )
    argv = supervisor._build_argv()

    assert argv[argv.index("--client-crl-file") + 1] == "/tmp/clients.crl"
    assert argv[argv.index("--peer-crl-file") + 1] == "/tmp/peers.crl"


def test_no_revocation_flags_when_no_lists_are_configured(
    tmp_path: Path,
) -> None:
    """Empty CRL paths must be omitted, not passed as empty strings.

    etcd refuses to start on an unreadable --client-crl-file, so passing "" for
    "no list" would turn the default configuration into a start-up failure.
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

    assert "--client-crl-file" not in argv
    assert "--peer-crl-file" not in argv


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
