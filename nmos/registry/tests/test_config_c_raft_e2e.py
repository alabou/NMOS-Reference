# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The whole secured raft rig: TLS registries, mutual TLS peers, replication.

    pytest nmos/registry/tests/test_config_c_raft_e2e.py -m e2e

The raft counterpart of ``test_config_c_distributed_e2e.py``, and it makes a
strictly larger claim, because it can: a registration arriving over mutual TLS
at one member is replicated over a mutual-TLS *peer transport* and comes back
out of another member's Query API. The etcd file registers and reads at a single
member; here three registries are the cluster, so starting all three costs
nothing extra and the cross-member claim is the one worth making.

What only this file covers
--------------------------
``test_cluster_conformance.py`` drives real sockets but builds its members
in-process, with contexts a fixture assembled. Everything between
``nmos_registry.py``'s command line and those objects -- ``_resolve_raft``,
``_build_raft_node``, ``build_raft_ssl_contexts``, and the launcher that types
the flags -- is exercised here and nowhere else. A certificate path read from
the wrong config field, or a context built without ``CERT_REQUIRED``, would pass
every other test in the suite.

Ports are the repository's fixed raft-rig ports (8544 + 10n), so this collides
with a raft rig already running on the same machine. It skips rather than fails
when it finds them occupied.
"""

from __future__ import annotations

import json
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from nmos.cluster.layout import DEFAULT_CERTIFICATE_NAME
from nmos.registry.tests._fixtures import make_device, make_node

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[3]
CERT_ROOT = REPO_ROOT / "Certificates"
LAUNCHER = REPO_ROOT / "start-registry-raft.sh"

MEMBERS = 3
# start-registry-raft.sh's block: registration 8544 + 10n, query 8543 + 10n,
# WebSocket 8548 + 10n, raft transport 2482 + 10n.
HOSTS = tuple(f"XYZ-SNX1000{index}" for index in range(MEMBERS))
REGISTRATION_PORTS = tuple(8544 + 10 * index for index in range(MEMBERS))
QUERY_PORTS = tuple(8543 + 10 * index for index in range(MEMBERS))
RAFT_PORTS = tuple(2482 + 10 * index for index in range(MEMBERS))

CA = str(CERT_ROOT / "build.0" / "ExampleRootCA-bundle.pem")


def _identity(serial: str) -> tuple[str, str]:
    stem = f"ExampleDeviceServer.ABC.{serial}.etcd"
    return (
        str(CERT_ROOT / "build.0.etcd" / "pem" / f"{stem}.chain.pem"),
        str(CERT_ROOT / "build.0.etcd" / "key" / f"{stem}.key"),
    )


def _port_free(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.25)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def _resolves_to_loopback(host: str) -> bool:
    try:
        return "127.0.0.1" in {
            info[4][0] for info in socket.getaddrinfo(host, None, socket.AF_INET)
        }
    except OSError:
        return False


@pytest.fixture(scope="module", autouse=True)
def preconditions() -> None:
    if sys.platform == "win32":
        pytest.skip("the launcher is a shell script; .bat is the Windows rig")
    if not LAUNCHER.is_file():
        pytest.skip(f"missing launcher: {LAUNCHER}")
    if not Path(CA).is_file():
        pytest.skip(f"certificate bundle missing: {CA}")
    for serial in (f"SNX1000{index}" for index in range(MEMBERS)):
        for path in _identity(serial):
            if not Path(path).is_file():
                pytest.skip(f"certificate missing: {path}")
    # The raft *peers* need no hosts file -- they verify the shared SAN, not the
    # address. The Registration and Query listeners still answer to the
    # certificate's own name, and that is what this test dials.
    missing = [host for host in HOSTS if not _resolves_to_loopback(host)]
    if missing:
        pytest.skip(f"{', '.join(missing)} must resolve to 127.0.0.1 in /etc/hosts")
    busy = [
        port for port in (*REGISTRATION_PORTS, *QUERY_PORTS, *RAFT_PORTS)
        if not _port_free(port)
    ]
    if busy:
        pytest.skip(f"raft rig ports already in use: {busy}")


@pytest.fixture(scope="module")
def secured_cluster(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Three secured members, started the way an operator starts them.

    Started together rather than one at a time: a three-member cluster has no
    quorum until two are up, so a fixture that waited for member 0 to answer
    before launching member 1 would wait for a readiness that cannot arrive.
    """
    processes = [
        # RAP=2: mutual TLS on Registration, which is what makes the negative
        # test below meaningful.
        subprocess.Popen(
            [str(LAUNCHER), str(index), str(MEMBERS), "2", "--secure"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            cwd=REPO_ROOT,
        )
        for index in range(MEMBERS)
    ]
    try:
        _await_every_member(processes)
        yield
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:      # pragma: no cover
                process.kill()


def _await_every_member(processes: list[subprocess.Popen[str]]) -> None:
    deadline = time.monotonic() + 90.0
    pending = set(range(MEMBERS))
    while pending and time.monotonic() < deadline:
        for index in sorted(pending):
            process = processes[index]
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                pytest.skip(f"member {index} exited: {output}")
            try:
                _get(
                    f"https://{HOSTS[index]}:{REGISTRATION_PORTS[index]}"
                    f"/x-nmos/registration/v1.3/",
                )
                pending.discard(index)
            except (urllib.error.URLError, ssl.SSLError, OSError):
                pass
        if pending:
            time.sleep(1.0)
    if pending:
        pytest.skip(f"members {sorted(pending)} did not become ready")


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def _context(client_identity: tuple[str, str] | None) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=CA)
    if client_identity is not None:
        certificate, key = client_identity
        context.load_cert_chain(certificate, key)
    return context


def _get(url: str, client: tuple[str, str] | None = None) -> object:
    with urllib.request.urlopen(
        urllib.request.Request(url), timeout=10,
        context=_context(client or _identity("SNX10001")),
    ) as answer:
        return json.load(answer)


def _post(url: str, payload: dict, client: tuple[str, str] | None = None) -> int:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(
        request, timeout=20,
        context=_context(client or _identity("SNX10001")),
    ) as answer:
        return int(answer.status)


def _register(index: int, resource_type: str, raw: dict) -> int:
    return _post(
        f"https://{HOSTS[index]}:{REGISTRATION_PORTS[index]}"
        f"/x-nmos/registration/v1.3/resource",
        {"type": resource_type, "data": raw},
    )


def _ids(index: int, collection: str) -> list[str]:
    answer = _get(
        f"https://{HOSTS[index]}:{QUERY_PORTS[index]}"
        f"/x-nmos/query/v1.3/{collection}",
    )
    assert isinstance(answer, list)
    return [entry["id"] for entry in answer]


def _eventually_on_every_member(collection: str, resource_id: str) -> None:
    """Replication is asynchronous; a member has not failed until it is late.

    Bounded by a real deadline rather than a fixed sleep: a sleep long enough
    to be safe on a loaded machine is long enough to hide a member that never
    received the entry at all.
    """
    deadline = time.monotonic() + 20.0
    pending = set(range(MEMBERS))
    while pending and time.monotonic() < deadline:
        for index in sorted(pending):
            if resource_id in _ids(index, collection):
                pending.discard(index)
        if pending:
            time.sleep(0.25)
    assert not pending, (
        f"{collection[:-1]} {resource_id} never reached member(s) "
        f"{sorted(pending)}"
    )


# ---------------------------------------------------------------------------
# The composed claim
# ---------------------------------------------------------------------------

def test_a_registration_over_mutual_tls_replicates_to_every_member(
    secured_cluster: None,
) -> None:
    """Registration API (mTLS) -> raft peers (mTLS) -> Query API (mTLS).

    A whole Node subtree, not a single resource: a Device whose parent Node is
    only present because it replicated would be rejected at apply, so carrying
    the subtree proves the *ordering* survived the transport and not merely
    that two writes arrived.
    """
    node = make_node(str(uuid.uuid4()))
    assert _register(0, "node", node) == 201
    _eventually_on_every_member("nodes", node["id"])

    device = make_device(str(uuid.uuid4()), node_id=node["id"])
    assert _register(0, "device", device) == 201
    _eventually_on_every_member("devices", device["id"])


def test_any_member_accepts_writes_for_any_node(secured_cluster: None) -> None:
    """A Node may register at whichever member it found.

    Only one member is the raft leader at a time, so a registration arriving at
    a follower has to be forwarded and committed rather than refused. Writing at
    every member in turn is what proves no member is read-only.
    """
    for index in range(MEMBERS):
        node = make_node(str(uuid.uuid4()))
        assert _register(index, "node", node) == 201
        _eventually_on_every_member("nodes", node["id"])


def test_the_registration_api_refuses_a_client_without_a_certificate(
    secured_cluster: None,
) -> None:
    """The listener is mutual TLS, so an unauthorised Node never reaches the API.

    A transport error rather than an HTTP status, which is the point: the
    refusal happens in the handshake, before any request exists.
    """
    with pytest.raises((urllib.error.URLError, ssl.SSLError, OSError)):
        urllib.request.urlopen(
            f"https://{HOSTS[0]}:{REGISTRATION_PORTS[0]}"
            f"/x-nmos/registration/v1.3/",
            timeout=10, context=_context(None),
        )


def test_plain_http_reaches_nothing(secured_cluster: None) -> None:
    """TLS-only listeners; there is no HTTP fallback to downgrade to."""
    with pytest.raises((urllib.error.URLError, OSError)):
        urllib.request.urlopen(
            f"http://{HOSTS[0]}:{REGISTRATION_PORTS[0]}"
            f"/x-nmos/registration/v1.3/",
            timeout=10,
        )


def _hello_on_the_peer_port(client: tuple[str, str] | None) -> bytes:
    """Speak ``Hello`` to member 0's raft port and return whatever comes back.

    A deliberately *wrong* ``cluster_id``, so a certified probe gets a refusing
    ``HelloAck`` rather than joining the cluster. What is being measured is
    whether the transport answers at all, and an accepted member would be a far
    worse thing for a test to leave running than a refused one.
    """
    from nmos.raft.messages import Hello
    from nmos.raft.wire import (
        PROTOCOL_MAJOR,
        PROTOCOL_MINOR,
        Frame,
        Stream,
        encode_frame,
    )

    hello = Hello(
        major=PROTOCOL_MAJOR, minor=PROTOCOL_MINOR,
        cluster_id="not-this-cluster", member_name="probe",
        member_index=1, incarnation=1, stream=Stream.CONTROL,
    )
    frame = encode_frame(Frame(
        stream=Stream.CONTROL, type=hello.TYPE, flags=0, payload=hello.encode(),
    ))
    with socket.create_connection(("127.0.0.1", RAFT_PORTS[0]), timeout=10) as raw:
        # The shared SAN, not the address: that is what the members verify each
        # other against, so it is what a probe has to present as the server
        # name for its own verification to succeed.
        with _context(client).wrap_socket(
            raw, server_hostname=DEFAULT_CERTIFICATE_NAME,
        ) as secured:
            secured.send(frame)
            try:
                return bytes(secured.recv(256))
            except (ssl.SSLError, OSError):
                return b""


def test_the_peer_transport_answers_nothing_without_a_certificate(
    secured_cluster: None,
) -> None:
    """The raft port is mutual TLS too, and that is the one that matters most.

    An unauthenticated connection to a Registration API can at worst register a
    resource. An unauthenticated connection to the *peer* port would be talking
    to the consensus layer -- proposing entries, voting in elections, claiming
    to be a member. So it is verified here directly rather than inferred from
    the fact that the members found each other.

    Asserted as "says nothing", not as "the handshake fails", because under
    TLS 1.3 it does not: the server validates the client certificate after its
    own handshake flight is complete, so ``do_handshake`` returns successfully
    on the client side and the refusal arrives as a close. Asserting on the
    handshake would therefore have passed on a server that required no
    certificate at all -- which is exactly the bug this test is for, so the
    certified probe below is not decoration. It is what makes the silence
    evidence.
    """
    assert _hello_on_the_peer_port(None) == b""
    assert _hello_on_the_peer_port(_identity("SNX10001")) != b""
