# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The rig itself: ``etcd_cluster.py --secure`` and the launcher scripts.

The tool is not library code, but it is the only thing that stands a secured
cluster up, so the properties a secured cluster depends on are asserted here
rather than left to whoever runs it next:

  * the topology -- one bind address, one port block per member -- because
    getting this wrong produces TLS rejections that look like bad certificates,
  * that ``--secure`` reaches every subcommand from either side of it, since a
    flag that works in only one position fails silently inside a shell wrapper,
  * that the secured and unsecured launchers cannot be confused for each other.

Most of it runs without starting anything. The one test that does start a
cluster is marked ``e2e``.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

import etcd_cluster  # noqa: E402


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

def test_secured_members_share_an_address_and_differ_by_port() -> None:
    """The shape that makes peer mutual TLS possible on one machine.

    etcd checks the certificate a peer presents against the address that peer's
    connection arrives from. Loopback connections are all sourced from 127.0.0.1
    whatever their destination, so members on separate loopback addresses can
    never satisfy that check with DNS-only certificates. Sharing the address and
    separating by port is what the Go rig did, and it is why it worked.
    """
    layouts = etcd_cluster._layouts(3, etcd_cluster.PROFILE_LINUX, secure=True)
    members = layouts[0].members

    assert {m.bind_address for m in members} == {"127.0.0.1"}
    assert [m.host for m in members] == [
        "XYZ-SNX10000", "XYZ-SNX10001", "XYZ-SNX10002",
    ]
    assert [m.client_port for m in members] == [2381, 2391, 2401]
    assert [m.peer_port for m in members] == [2382, 2392, 2402]


def test_secured_urls_are_https_on_both_listeners() -> None:
    layouts = etcd_cluster._layouts(3, etcd_cluster.PROFILE_LINUX, secure=True)
    local = layouts[0].local

    assert local.advertise_peer_url(tls=True).startswith("https://")
    assert local.advertise_client_url(tls=True).startswith("https://")
    assert "https://" in layouts[0].initial_cluster()


def test_the_plaintext_topology_is_untouched() -> None:
    """--secure must not change what the unsecured rig has always done.

    Member names become data-directory paths, so a topology change here would
    orphan the databases of anyone already running the development rig.
    """
    layouts = etcd_cluster._layouts(3, etcd_cluster.PROFILE_LINUX, secure=False)
    members = layouts[0].members

    assert [m.host for m in members] == ["127.0.0.11", "127.0.0.12", "127.0.0.13"]
    assert [m.name for m in members] == [
        "nmos-registry-0", "nmos-registry-1", "nmos-registry-2",
    ]


def test_secured_and_plaintext_clusters_use_separate_data_roots() -> None:
    """Their members advertise different peer URLs under the same names.

    etcd records a member's peer URL in its WAL, so one data directory shared
    between the two modes fails on the second run with a peer-URL mismatch --
    on a database that is otherwise perfectly healthy.
    """
    secure = etcd_cluster.parse_args(["--secure", "endpoints"])
    plain = etcd_cluster.parse_args(["endpoints"])

    assert etcd_cluster._data_root(secure) == etcd_cluster.SECURE_DATA_ROOT
    assert etcd_cluster._data_root(plain) == etcd_cluster.DEFAULT_DATA_ROOT
    assert etcd_cluster._data_root(secure) != etcd_cluster._data_root(plain)


def test_an_explicit_data_root_still_wins() -> None:
    args = etcd_cluster.parse_args(["--secure", "--data-root", "/tmp/x", "endpoints"])
    assert etcd_cluster._data_root(args) == Path("/tmp/x")


# ---------------------------------------------------------------------------
# Flag plumbing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["--secure", "endpoints"],
    ["endpoints", "--secure"],
])
def test_secure_is_accepted_on_either_side_of_the_subcommand(
    argv: list[str],
) -> None:
    """The wrapper scripts append their arguments after the subcommand.

    ``start-etcd-cluster.sh 3 --secure`` becomes ``... up --secure``, so a
    top-level-only flag would be rejected there for no reason the user can see.
    """
    assert etcd_cluster.parse_args(argv).secure is True


def test_secure_defaults_to_off() -> None:
    assert etcd_cluster.parse_args(["endpoints"]).secure is False


def test_the_certificate_type_reaches_the_identity() -> None:
    """--tct selects the RSA or ECDSA flavour, spelled as start-registry.sh."""
    rsa_cert, rsa_key, _ = etcd_cluster._secure_identity(0, etcd_cluster.TCT_RSA)
    ec_cert, ec_key, _ = etcd_cluster._secure_identity(0, etcd_cluster.TCT_ECDSA)

    assert rsa_cert.endswith(".etcd.chain.pem")
    assert ec_cert.endswith(".etcd.ec.chain.pem")
    assert rsa_key.endswith(".etcd.key")
    assert ec_key.endswith(".etcd.ec.key")


def test_a_missing_identity_names_the_member_and_the_file() -> None:
    """--members tops out at 5 because the certificate set does."""
    with pytest.raises(SystemExit, match="SNX10009"):
        etcd_cluster._secure_identity(9, etcd_cluster.TCT_RSA)


def test_endpoints_keep_the_member_names_when_secured() -> None:
    """Even on the wsl profile, which rewrites them to localhost otherwise."""
    layouts = etcd_cluster._layouts(3, etcd_cluster.PROFILE_WSL, secure=True)
    line = etcd_cluster._endpoint_string(
        layouts[0], etcd_cluster.PROFILE_WSL, secure=True,
    )
    assert line == "XYZ-SNX10000:2381,XYZ-SNX10001:2391,XYZ-SNX10002:2401"


# ---------------------------------------------------------------------------
# One member per machine
# ---------------------------------------------------------------------------

def test_index_selects_one_member_and_keeps_the_whole_cluster() -> None:
    """How a cluster spread over machines is started: same list, one member.

    The selected member is the only one this host runs, but --initial-cluster
    must still name all three -- a member that does not know its peers cannot
    join them.
    """
    layouts = etcd_cluster._layouts(3, etcd_cluster.PROFILE_LINUX, secure=True)
    args = etcd_cluster.parse_args(["--secure", "--index", "1", "up"])

    selected = etcd_cluster._selected(layouts, args)

    assert [index for index, _ in selected] == [1]
    started = selected[0][1]
    assert started.local.name == "nmos-registry-1"           # type: ignore[attr-defined]
    assert len(started.members) == 3                          # type: ignore[attr-defined]
    assert started.initial_cluster().count("nmos-registry") == 3  # type: ignore[attr-defined]


def test_without_index_every_member_is_this_machine_s() -> None:
    layouts = etcd_cluster._layouts(3, etcd_cluster.PROFILE_LINUX, secure=True)
    args = etcd_cluster.parse_args(["--secure", "up"])
    assert [index for index, _ in etcd_cluster._selected(layouts, args)] == [
        0, 1, 2,
    ]


def test_an_index_outside_the_cluster_is_refused() -> None:
    layouts = etcd_cluster._layouts(3, etcd_cluster.PROFILE_LINUX, secure=True)
    args = etcd_cluster.parse_args(["--secure", "--index", "7", "up"])
    with pytest.raises(SystemExit, match="indices 0..2"):
        etcd_cluster._selected(layouts, args)


def test_the_selected_member_carries_its_own_identity() -> None:
    """Certificates are per member, so --index has to pick the right serial."""
    certificate, _, _ = etcd_cluster._secure_identity(2, etcd_cluster.TCT_RSA)
    assert "SNX10002" in certificate


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------

def test_a_secured_member_binds_a_resolved_address_not_its_name() -> None:
    """etcd refuses a name in a listen URL: "expected IP in URL for binding".

    The member is still ADVERTISED by name -- that is what peers verify its
    certificate against -- so the two URLs deliberately differ.
    """
    layouts = etcd_cluster._layouts(3, etcd_cluster.PROFILE_LINUX, secure=True)
    local = layouts[0].local

    assert local.host == "XYZ-SNX10000"
    assert local.bind_address == "127.0.0.1"
    assert local.listen_peer_url(tls=True) == "https://127.0.0.1:2382"
    assert local.advertise_peer_url(tls=True) == "https://XYZ-SNX10000:2382"


def test_an_unsecured_cluster_is_refused_off_the_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rig may not put an unauthenticated database on the network.

    Same rule the registry enforces, in the tool that would otherwise be the
    easy way around it.
    """
    monkeypatch.setattr(etcd_cluster, "_resolved_address", lambda host: "10.1.2.3")
    monkeypatch.setattr(etcd_cluster, "_is_local_address", lambda address: True)
    layouts = etcd_cluster._layouts(1, etcd_cluster.PROFILE_LINUX, secure=False)

    with pytest.raises(SystemExit, match="unsecured cluster on a routable"):
        etcd_cluster._check_addressing(
            [layout.local for layout in layouts], secure=False,
        )


def test_a_secured_cluster_may_use_a_routable_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The multi-machine case: routable is exactly what it must be."""
    monkeypatch.setattr(etcd_cluster, "_resolved_address", lambda host: "10.1.2.3")
    monkeypatch.setattr(etcd_cluster, "_is_local_address", lambda address: True)
    layouts = etcd_cluster._layouts(1, etcd_cluster.PROFILE_LINUX, secure=True)

    etcd_cluster._check_addressing(
        [layout.local for layout in layouts], secure=True,
    )


def test_a_member_belonging_to_another_machine_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting all three on one host is the mistake --index exists to avoid.

    Without the check, a member whose name points at another machine binds
    nothing here and fails inside etcd; the message names the address and
    suggests --index instead.
    """
    monkeypatch.setattr(etcd_cluster, "_resolved_address", lambda host: "10.9.9.9")
    monkeypatch.setattr(etcd_cluster, "_is_local_address", lambda address: False)
    layouts = etcd_cluster._layouts(3, etcd_cluster.PROFILE_LINUX, secure=True)

    with pytest.raises(SystemExit, match="--index"):
        etcd_cluster._check_addressing(
            [layout.local for layout in layouts], secure=True,
        )


# ---------------------------------------------------------------------------
# Diagnosing the addressing rather than leaving it to etcd
# ---------------------------------------------------------------------------

def test_an_unresolvable_member_name_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named, but pointing nowhere.

    etcd's own failure for this is a bind error on a URL it prints back at
    you; naming the host that will not resolve is the difference between a
    minute and an afternoon.
    """
    monkeypatch.setattr(etcd_cluster, "_resolved_address", lambda host: None)
    layouts = etcd_cluster._layouts(1, etcd_cluster.PROFILE_LINUX, secure=True)

    with pytest.raises(SystemExit, match="does not resolve"):
        etcd_cluster._check_addressing(
            [layout.local for layout in layouts], secure=True,
        )


def test_the_check_passes_on_a_correctly_configured_host() -> None:
    """Guards the guard: a check that always failed would pass every test above."""
    layouts = etcd_cluster._layouts(3, etcd_cluster.PROFILE_LINUX, secure=True)
    try:
        etcd_cluster._check_addressing(
            [layout.local for layout in layouts], secure=True,
        )
    except SystemExit as refused:
        pytest.skip(f"hosts file not set up for the secured rig: {refused}")


def test_a_local_address_is_decided_by_binding_it() -> None:
    """Asked the same way etcd will ask it, rather than by parsing interfaces."""
    assert etcd_cluster._is_local_address("127.0.0.1") is True
    # A public address this machine certainly does not hold.
    assert etcd_cluster._is_local_address("203.0.113.7") is False


# ---------------------------------------------------------------------------
# The launchers
# ---------------------------------------------------------------------------

def _script(name: str) -> str:
    return (REPO_ROOT / name).read_text()


def test_the_secured_launcher_disables_nothing() -> None:
    """The one assertion that keeps the secured rig secured.

    --etcdDisableTLS is store_true, so it silently wins over any certificate
    passed alongside it. A stray copy in the secured launcher would produce a
    rig that looks secured, reports itself secured, and runs its database in
    the clear.
    """
    secured = _script("start-registry-dist-secure.sh")
    assert "--registryDisableTLS" not in secured
    assert "--etcdDisableTLS" not in secured
    for flag in ("--etcdCertificate", "--etcdKey", "--etcdTrustedRootCA"):
        assert flag in secured


def test_the_secured_launcher_can_manage_its_own_member() -> None:
    """--managed is the deployed shape, and had no launcher until now.

    Without it every rig script passed --etcdExternal, so the supervisor's
    ownership rule -- launch it, keep it alive, stop what you started -- was
    exercised only by tests and never by the rig it exists for.
    """
    secured = _script("start-registry-dist-secure.sh")

    assert "--managed" in secured
    assert "--etcdBootstrap" in secured, "a first start has to bootstrap once"
    assert "--etcdDataDir" in secured, "the production default needs root"


def test_managed_mode_passes_no_endpoints() -> None:
    """Endpoints and a managed member are two descriptions of one cluster.

    Accepting both would let a registry supervise a member of one cluster
    while talking to another, which is a split brain with a plausible-looking
    command line.
    """
    secured = _script("start-registry-dist-secure.sh")
    managed_branch = secured.split('if [ "$MANAGED" = "1" ]; then', 1)[1]
    managed_branch = managed_branch.split("else", 1)[0]

    assert "--etcdEndpoints" not in managed_branch
    assert "--etcdExternal" not in managed_branch


def test_the_unsecured_launcher_is_explicitly_unsecured() -> None:
    """It must keep disabling both, so the two rigs cannot be confused."""
    plain = _script("start-registry-dist.sh")
    assert "--registryDisableTLS" in plain
    assert "--etcdDisableTLS" in plain


def test_the_unsecured_launcher_passes_no_oauth2_to_the_registry() -> None:
    """Bearer tokens over plain HTTP is a NAP=0 deployment claiming compliance.

    Its usage line used to advertise --oauth2 while hard-coding
    --registryDisableTLS, so the flag was accepted, passed straight through,
    and produced exactly that -- silently, because nothing refuses the
    combination. Asserted against the command it execs rather than the file, so
    the header stays free to explain why the flag is absent.
    """
    plain = _script("start-registry-dist.sh")
    command = plain.split("exec ", 1)[1]

    assert "--oauth2" not in command
    assert "--oauth2)" not in plain, "no option branch should accept it either"


def test_the_launchers_point_at_each_other_not_at_the_standalone_one() -> None:
    """start-registry.sh rejects --distributed; it is not the secured rig."""
    plain = _script("start-registry-dist.sh")
    assert "start-registry-dist-secure.sh" in plain


@pytest.mark.parametrize("script", [
    "start-registry-dist-secure.sh", "start-registry-dist.sh",
])
def test_the_launchers_are_syntactically_valid(script: str) -> None:
    result = subprocess.run(
        ["bash", "-n", str(REPO_ROOT / script)], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("argv,expected", [
    ([], "member index"),
    (["9", "3"], "0..2"),
    (["0", "4"], "1, 3 or 5"),
    (["0", "3", "0"], "start-registry-dist.sh"),
    (["0", "3", "2", "--nap=1", "--oauth2"], "not allowed"),
])
def test_the_secured_launcher_refuses_bad_arguments(
    argv: list[str], expected: str,
) -> None:
    result = subprocess.run(
        [str(REPO_ROOT / "start-registry-dist-secure.sh"), *argv],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert result.returncode != 0
    assert expected in result.stderr


# ---------------------------------------------------------------------------
# The rig, actually run
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_a_secured_cluster_comes_up_reports_healthy_and_stops(
    tmp_path: Path,
) -> None:
    """up --detach, status, down -- the three the rig is used through.

    ``status`` is the reason this is worth an e2e test: it is a pure client
    operation, so against a secured cluster it has to present a client
    certificate of its own. Two separate channel pools inside it needed
    credentials, and missing either reports every member UNREACHABLE against a
    cluster the same command just started.
    """
    if not etcd_cluster.BUNDLED_ETCD.is_file() and shutil.which("etcd") is None:
        pytest.skip("etcd not installed; run ./install-etcd.sh")

    data_root = str(tmp_path / "data-secure")
    base = [
        sys.executable, str(REPO_ROOT / "etcd_cluster.py"),
        "--members", "3", "--secure", "--data-root", data_root,
    ]

    up = subprocess.run(
        [*base, "up", "--detach", "--timeout", "90"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=180,
    )
    if up.returncode != 0:
        pytest.skip(f"secured cluster did not start: {up.stdout}{up.stderr}")

    try:
        status = subprocess.run(
            [*base, "status"], capture_output=True, text=True,
            cwd=REPO_ROOT, timeout=60,
        )
        assert status.returncode == 0, status.stdout + status.stderr
        assert "3/3 healthy" in status.stdout
        assert "https://XYZ-SNX10000:2381" in status.stdout
    finally:
        down = subprocess.run(
            [*base, "down"], capture_output=True, text=True,
            cwd=REPO_ROOT, timeout=90,
        )
        assert "all members stopped" in down.stdout, down.stdout + down.stderr
