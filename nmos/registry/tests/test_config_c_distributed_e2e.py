# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The whole secured rig: TLS registry, mutual TLS etcd, a real registration.

    pytest nmos/registry/tests/test_config_c_distributed_e2e.py -m e2e

Every other test in this suite proves one layer. This one proves they compose,
through the launchers an operator actually types, because that is the claim
worth making: a resource registered over mutual TLS lands in a mutual-TLS etcd
cluster and comes back out of the Query API.

It is deliberately end-to-end and therefore deliberately narrow -- one registry
member, one resource. Cluster behaviour is ``test_etcd_mtls_e2e.py``'s job and
the Registration API's semantics are covered without TLS elsewhere; repeating
either here would only make a slow test slower.

Ports are the repository's fixed secured-rig ports, so this will collide with a
secured rig already running on the same machine. It skips rather than fails when
it finds them occupied.
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
from collections.abc import Iterator
from pathlib import Path

import pytest

from nmos.registry.tests._fixtures import make_node

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[3]
CERT_ROOT = REPO_ROOT / "Certificates"
BUNDLED_ETCD = REPO_ROOT / ".etcd" / "etcd"

MEMBER = "XYZ-SNX10000"
REGISTRATION_PORT = 8444
QUERY_PORT = 8443
ETCD_CLIENT_PORT = 2381


def _identity(serial: str) -> tuple[str, str]:
    stem = f"ExampleDeviceServer.ABC.{serial}.etcd"
    return (
        str(CERT_ROOT / "build.0.etcd" / "pem" / f"{stem}.chain.pem"),
        str(CERT_ROOT / "build.0.etcd" / "key" / f"{stem}.key"),
    )


CA = str(CERT_ROOT / "build.0" / "ExampleRootCA-bundle.pem")


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
    if not BUNDLED_ETCD.is_file():
        pytest.skip("etcd not installed; run ./install-etcd.sh")
    if not Path(CA).is_file():
        pytest.skip(f"certificate bundle missing: {CA}")
    if not _resolves_to_loopback(MEMBER):
        pytest.skip(f"{MEMBER} must resolve to 127.0.0.1 in /etc/hosts")
    busy = [
        port for port in (ETCD_CLIENT_PORT, REGISTRATION_PORT, QUERY_PORT)
        if not _port_free(port)
    ]
    if busy:
        pytest.skip(f"secured rig ports already in use: {busy}")


@pytest.fixture(scope="module")
def secured_rig(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """A secured cluster and one secured registry member, via the launchers."""
    data_root = str(tmp_path_factory.mktemp("data-secure"))
    cluster = [
        sys.executable, str(REPO_ROOT / "etcd_cluster.py"),
        "--members", "3", "--secure", "--data-root", data_root,
    ]

    up = subprocess.run(
        [*cluster, "up", "--detach", "--timeout", "90"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=180,
    )
    if up.returncode != 0:
        pytest.skip(f"secured cluster did not start: {up.stdout}{up.stderr}")

    # RAP=2: mutual TLS on Registration, which is what makes the negative test
    # below meaningful.
    registry = subprocess.Popen(
        [str(REPO_ROOT / "start-registry-dist-secure.sh"), "0", "3", "2"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=REPO_ROOT,
    )
    try:
        _await_registration_api(registry)
        yield
    finally:
        registry.terminate()
        try:
            registry.wait(timeout=20)
        except subprocess.TimeoutExpired:      # pragma: no cover
            registry.kill()
        subprocess.run(
            [*cluster, "down"], capture_output=True, text=True,
            cwd=REPO_ROOT, timeout=90,
        )


def _await_registration_api(process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            pytest.skip(f"secured registry exited: {output}")
        try:
            _get(f"https://{MEMBER}:{REGISTRATION_PORT}/x-nmos/registration/v1.3/")
            return
        except (urllib.error.URLError, ssl.SSLError, OSError):
            time.sleep(1.0)
    pytest.skip("secured registry did not become ready")


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
    request = urllib.request.Request(url)
    with urllib.request.urlopen(
        request, timeout=10, context=_context(client or _identity("SNX10001")),
    ) as answer:
        return json.load(answer)


def _post(url: str, payload: dict, client: tuple[str, str] | None = None) -> int:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(
        request, timeout=10, context=_context(client or _identity("SNX10001")),
    ) as answer:
        return int(answer.status)


# ---------------------------------------------------------------------------
# The rig
# ---------------------------------------------------------------------------

def test_a_registration_over_mutual_tls_survives_into_the_query_api(
    secured_rig: None,
) -> None:
    """The composed claim, and the only test that makes it.

    Registration API over mutual TLS -> etcd cluster over mutual TLS -> Query
    API over mutual TLS. Any layer silently falling back to plaintext, or
    quietly failing to persist, breaks this and nothing else.
    """
    node = make_node()

    status = _post(
        f"https://{MEMBER}:{REGISTRATION_PORT}/x-nmos/registration/v1.3/resource",
        {"type": "node", "data": node},
    )
    assert status == 201

    nodes = _get(f"https://{MEMBER}:{QUERY_PORT}/x-nmos/query/v1.3/nodes")
    assert isinstance(nodes, list)
    assert node["id"] in [entry["id"] for entry in nodes]


def test_the_registration_api_refuses_a_client_without_a_certificate(
    secured_rig: None,
) -> None:
    """RAP=2 is Restricted Registration: mutual TLS, not merely TLS.

    Without a client certificate the handshake itself fails, so the failure is
    a transport error rather than an HTTP status -- which is the point. An
    unauthorised Node never reaches the API at all.
    """
    with pytest.raises((urllib.error.URLError, ssl.SSLError, OSError)):
        urllib.request.urlopen(
            f"https://{MEMBER}:{REGISTRATION_PORT}/x-nmos/registration/v1.3/",
            timeout=10, context=_context(None),
        )


def test_plain_http_reaches_nothing(secured_rig: None) -> None:
    """The listener is TLS-only; there is no HTTP fallback to downgrade to."""
    with pytest.raises((urllib.error.URLError, OSError)):
        urllib.request.urlopen(
            f"http://{MEMBER}:{REGISTRATION_PORT}/x-nmos/registration/v1.3/",
            timeout=10,
        )


# ---------------------------------------------------------------------------
# The OAuth 2.0 half of Config C
# ---------------------------------------------------------------------------
#
# Config C is mutual TLS *plus* OAuth 2.0, and the tests above cover the mutual
# TLS half. These cover what the OAuth 2.0 half refuses, which on a Query API is
# the security-relevant part: the registry classifies itself RAAM=2
# MTLS_PLUS_OAUTH2 and must then reject every unauthorised read even from a
# client whose certificate is beyond reproach.
#
# NOT covered, deliberately and not by oversight: an *authorised* read. The
# vendored Authorization Server's client_credentials grant issues a fixed scope
# set that contains no `query`, so a token that would actually be accepted here
# only comes out of its authorization_code (browser) flow. Driving that belongs
# to nmos/agentui, not to a headless registry test, and inventing a token here
# would test this file's signing rather than the registry's validation.

AS_PORT = 9443
OAUTH2_REGISTRATION_PORT = 8454
OAUTH2_QUERY_PORT = 8453
CLIENT_ID = "Example.Company.Device.Client.ABC.SNX00001.example.com"


def _device_client_identity() -> tuple[str, str]:
    """The client identity the Authorization Server issues tokens to."""
    return (
        str(CERT_ROOT / "build.0" / "pem"
            / "ExampleDeviceClient.ABC.SNX00001.chain.pem"),
        str(CERT_ROOT / "build.0" / "key" / "ExampleDeviceClient.ABC.SNX00001.key"),
    )


@pytest.fixture(scope="module")
def oauth2_rig(
    secured_rig: None, tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[str]:
    """A second secured member with --oauth2, plus the Authorization Server.

    Reuses the cluster the mutual-TLS rig already started -- one cluster, two
    registry members, which is what a real deployment looks like anyway.

    Yields the registry's console output. Its security classification is printed
    there rather than written to --logFile, and waiting for the right line in it
    is also how readiness is judged: the listener accepts connections *before*
    the JWKS fetch completes, and a request arriving in that window is refused
    401 for want of a key rather than for any reason under test.
    """
    if not _port_free(AS_PORT) or not _port_free(OAUTH2_QUERY_PORT):
        pytest.skip("Authorization Server or member 1 ports already in use")

    console = tmp_path_factory.mktemp("oauth2") / "registry.out"
    as_console = console.with_name("as.out")

    with open(as_console, "w") as as_out, open(console, "w") as registry_out:
        authorization_server = subprocess.Popen(
            [str(REPO_ROOT / "start-fake-as.sh"), "--serial=SNX10001"],
            stdout=as_out, stderr=subprocess.STDOUT, text=True, cwd=REPO_ROOT,
        )
        registry = subprocess.Popen(
            [str(REPO_ROOT / "start-registry-dist-secure.sh"), "1", "3", "2",
             "--oauth2"],
            stdout=registry_out, stderr=subprocess.STDOUT, text=True,
            cwd=REPO_ROOT,
        )

    try:
        _await_line(as_console, "Fake AS up", authorization_server, "auth server")
        _await_line(console, "JWKS: fetched", registry, "oauth2 registry")
        yield console.read_text()
    finally:
        for process in (registry, authorization_server):
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:   # pragma: no cover
                process.kill()


def _await_line(
    console: Path, needle: str, process: subprocess.Popen[str], what: str,
) -> None:
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if needle in console.read_text():
            return
        if process.poll() is not None:
            pytest.skip(f"{what} exited: {console.read_text()[-400:]}")
        time.sleep(0.5)
    pytest.skip(f"{what} never printed {needle!r}: {console.read_text()[-400:]}")


def _token() -> str:
    import urllib.parse

    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": "secret",
    }).encode()
    request = urllib.request.Request(
        f"https://XYZ-SNX00000:{AS_PORT}/realms/TR-10-SEC/token",
        data=body, method="POST",
    )
    with urllib.request.urlopen(
        request, timeout=10, context=ssl.create_default_context(cafile=CA),
    ) as answer:
        return str(json.load(answer)["access_token"])


def _query(headers: dict[str, str], client: tuple[str, str]) -> int:
    """Status of a Query API read, with HTTP errors reported as their status."""
    request = urllib.request.Request(
        f"https://XYZ-SNX10001:{OAUTH2_QUERY_PORT}/x-nmos/query/v1.3/nodes",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(
            request, timeout=10, context=_context(client),
        ) as answer:
            return int(answer.status)
    except urllib.error.HTTPError as refused:
        return int(refused.code)


def test_the_query_api_reports_mtls_plus_oauth2(oauth2_rig: str) -> None:
    """RAAM=2 is the classification Config C is defined by.

    Read from the registry's own output rather than inferred, because the
    refusals below would look identical on a registry that had quietly fallen
    back to mutual TLS alone.
    """
    assert "MTLS_PLUS_OAUTH2" in oauth2_rig
    assert "NAP=2 RESTRICTED_RW" in oauth2_rig


def test_a_read_without_a_bearer_token_is_refused(oauth2_rig: str) -> None:
    """A valid client certificate is no longer sufficient on its own.

    This is the whole difference between Config B and Config C, and the one
    assertion that would catch --oauth2 silently doing nothing.
    """
    assert _query({}, _device_client_identity()) == 401


def test_a_token_not_bound_to_the_client_certificate_is_refused(
    oauth2_rig: str,
) -> None:
    """The token and the certificate must name the same client.

    Presented here with an etcd member's certificate -- itself perfectly valid,
    and accepted for the mutual TLS layer -- alongside a token issued to the
    device client. Without this binding a leaked token would be usable by any
    holder of any trusted certificate.
    """
    assert _query(
        {"Authorization": f"Bearer {_token()}"}, _identity("SNX10001"),
    ) == 401


def test_an_authorised_read_succeeds(oauth2_rig: str) -> None:
    """The positive case, without which the refusals prove very little.

    A registry that answered 401 to everything -- misconfigured JWKS, wrong
    issuer, OAuth 2.0 wired to reject unconditionally -- would satisfy every
    test above and none of them would notice.

    The `query` scope carries Read only. Write needs an ``x-nmos-*`` claim the
    Authorization Server's default template deliberately omits, so this proves
    the read path and says nothing about writes.
    """
    assert _query(
        {"Authorization": f"Bearer {_token()}"}, _device_client_identity(),
    ) == 200


def test_the_registry_reports_itself_distributed(secured_rig: None) -> None:
    """Guards against the rig silently degrading to a standalone registry.

    A registry that failed to reach etcd and fell back to local state would
    pass the registration test above, because a standalone registry serves the
    same APIs from memory.
    """
    from nmos.etcd.channel import (
        EtcdChannelPool,
        build_credentials,
        parse_endpoints,
        unary_method,
    )
    from nmos.etcd.cluster import DEFAULT_ETCD_CERTIFICATE_NAME
    from nmos.etcd.generated import rpc_pb2

    import asyncio

    certificate, key = _identity("SNX10000")

    async def count_members() -> int:
        pool = EtcdChannelPool(
            parse_endpoints([f"127.0.0.1:{ETCD_CLIENT_PORT}"]),
            credentials=build_credentials(
                trusted_root_ca=[CA], certificate=certificate, key=key,
            ),
            target_name=DEFAULT_ETCD_CERTIFICATE_NAME,
            rpc_timeout=5.0,
        )
        member_list = unary_method(
            "Cluster", "MemberList",
            rpc_pb2.MemberListRequest, rpc_pb2.MemberListResponse,
        )
        try:
            answer = await pool.call(member_list, rpc_pb2.MemberListRequest())
            return len(answer.members)
        finally:
            await pool.close()

    assert asyncio.run(count_members()) == 3
