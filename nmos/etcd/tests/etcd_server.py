# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""A real single-member etcd for the client integration tests.

These tests deliberately run against a real server rather than a mock. The
whole point of this package is the parts of etcd's behaviour that are easy to
get wrong from the documentation alone -- revision grouping, when a progress
notification is actually sent, what a CAS failure branch returns, how
compaction surfaces -- and a mock would only ever assert what its author
already believed.

The server is skipped rather than failed when absent: etcd is an optional
component installed by ``./install-etcd.sh``, so a checkout without it must
still run the default test gate cleanly.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BUNDLED_ETCD = REPO_ROOT / ".etcd" / "etcd"


def _etcd_binary() -> str | None:
    if BUNDLED_ETCD.is_file():
        return str(BUNDLED_ETCD)
    return shutil.which("etcd")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture(scope="session")
def etcd_endpoint(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Run one etcd for the session and yield its client endpoint.

    No TLS: these tests exercise the KV/lease/watch semantics, and the mTLS
    path is covered separately where certificates are the subject rather than
    an obstacle.
    """
    binary = _etcd_binary()
    if binary is None:
        pytest.skip("etcd not installed; run ./install-etcd.sh")

    data_dir = tmp_path_factory.mktemp("etcd-data")
    client_port = _free_port()
    peer_port = _free_port()
    client_url = f"http://127.0.0.1:{client_port}"
    peer_url = f"http://127.0.0.1:{peer_port}"

    process = subprocess.Popen(
        [
            binary,
            "--name", "test",
            "--data-dir", str(data_dir / "member"),
            "--listen-client-urls", client_url,
            "--advertise-client-urls", client_url,
            "--listen-peer-urls", peer_url,
            "--initial-advertise-peer-urls", peer_url,
            "--initial-cluster", f"test={peer_url}",
            "--initial-cluster-state", "new",
            "--initial-cluster-token", "nmos-etcd-tests",
            "--log-level", "error",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"etcd exited during startup with {process.returncode}",
                )
            with socket.socket() as probe:
                probe.settimeout(0.25)
                if probe.connect_ex(("127.0.0.1", client_port)) == 0:
                    break
            time.sleep(0.1)
        else:
            raise RuntimeError("etcd did not become reachable within 30s")

        yield client_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
