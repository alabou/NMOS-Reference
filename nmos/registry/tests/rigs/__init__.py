# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Cluster rigs: one distributed registry, however its storage layer is run.

The distributed suite grew three near-identical rigs for one backend --
``nmos/etcd/tests/etcd_server.py`` starts one member, the old
``test_etcd_outage.py`` started one it could kill, and the old
``test_etcd_cluster_e2e.py`` started three. They differed in size and in what
they let a test do to a member, not in kind.

With a second backend that stops being tidiness and becomes a correctness
problem: a suite that proves two backends behave alike can only do so if both
are driven through *one* interface. Three rigs per backend would be six, and
the first behavioural difference anyone found would be as likely to live in a
rig as in the registry.

So there is one protocol -- :class:`ClusterRig` -- with an implementation per
backend, and ``BACKENDS`` names the ones a parameterised test runs against. It
was shaped against etcd first, because that is the backend that already worked;
a protocol designed against the implementation that did not exist yet would
have encoded guesses.
"""

from pathlib import Path

from nmos.registry.tests.rigs.etcd_rig import EtcdRig
from nmos.registry.tests.rigs.protocol import ClusterRig, RigUnavailable
from nmos.registry.tests.rigs.raft_rig import RaftRig

# Which backends a parameterised conformance test runs against.
#
# raft is listed first so a failure shows up against the newer implementation
# before the established one, which is almost always where it is.
BACKENDS = ("raft", "etcd")


def make_rig(backend: str, *, size: int, root: Path) -> ClusterRig:
    """Build a rig of ``size`` members for ``backend``.

    Raises:
        RigUnavailable: The backend cannot run here -- a missing binary, a
            missing certificate set. Callers turn this into ``pytest.skip``
            rather than a failure, because "not installed" is not "broken".
    """
    if backend == "etcd":
        return EtcdRig(size=size, root=root)
    if backend == "raft":
        return RaftRig(size=size, root=root)
    raise RigUnavailable(f"unknown backend {backend!r}")


__all__ = [
    "BACKENDS",
    "ClusterRig",
    "EtcdRig",
    "RaftRig",
    "RigUnavailable",
    "make_rig",
]
