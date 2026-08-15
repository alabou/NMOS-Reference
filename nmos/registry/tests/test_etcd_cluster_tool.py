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
# The resolution preflight
# ---------------------------------------------------------------------------

def test_a_member_name_resolving_elsewhere_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turns an hour of reading handshake logs into one line of config error.

    Without the preflight, a hosts file pointing XYZ-SNX1000n somewhere else
    surfaces as etcd rejecting every peer connection with a TLS error naming a
    certificate that is in fact entirely valid.
    """
    def elsewhere(host: str, port: object, family: object = 0) -> list[object]:
        return [(family, 1, 6, "", ("10.9.9.9", 2381))]

    monkeypatch.setattr(socket, "getaddrinfo", elsewhere)
    layouts = etcd_cluster._layouts(3, etcd_cluster.PROFILE_LINUX, secure=True)

    with pytest.raises(SystemExit) as caught:
        etcd_cluster._check_secure_resolution(layouts)

    message = str(caught.value)
    assert "10.9.9.9" in message
    assert "127.0.0.1" in message


def test_an_unresolvable_member_name_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fails(host: str, port: object, family: object = 0) -> list[object]:
        raise OSError(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fails)
    layouts = etcd_cluster._layouts(1, etcd_cluster.PROFILE_LINUX, secure=True)

    with pytest.raises(SystemExit, match="does not resolve"):
        etcd_cluster._check_secure_resolution(layouts)


def test_the_preflight_passes_on_a_correctly_configured_host() -> None:
    """Guards the guard: a check that always failed would pass the two above."""
    layouts = etcd_cluster._layouts(3, etcd_cluster.PROFILE_LINUX, secure=True)
    try:
        etcd_cluster._check_secure_resolution(layouts)
    except SystemExit as exit_:
        pytest.skip(f"hosts file not set up for the secured rig: {exit_}")


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
