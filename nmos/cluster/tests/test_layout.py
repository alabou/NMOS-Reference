# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""What the move to a shared module added, and what it must not have changed.

``nmos/etcd/tests/test_cluster.py`` still owns the derivation rules themselves
and runs unmodified against the re-export shim -- that it passes untouched is
the evidence the move was behaviour-preserving. This file covers only the two
things that are new here:

* the ``flavour`` token separator, which keeps an etcd cluster and a raft
  cluster on the same hosts from deriving the same identity;
* the compatibility guarantees the shim owes its importers.
"""

from __future__ import annotations

import pytest

from nmos.cluster.layout import (
    DEFAULT_CLIENT_PORT,
    DEFAULT_PEER_PORT,
    ClusterConfigError,
    MemberSpec,
    cluster_token,
    derive_cluster,
)

NAMESPACE = "/nmos-reference/registry/v1"


def _hosts(*names: str) -> list[MemberSpec]:
    return [MemberSpec(host=name) for name in names]


class TestFlavourSeparatesTheBackends:
    """Two storage layers on one set of hosts must not look alike."""

    def test_the_default_flavour_reproduces_the_original_token(self) -> None:
        """An existing etcd deployment must keep its token, and its data dir.

        The token is part of a member's identity; changing it would make every
        existing cluster look like a new one to itself. So the default must
        hash byte-identically to what the pre-move code produced -- which is
        what the etcd path passes, by passing nothing.
        """
        import hashlib

        layout = derive_cluster(
            _hosts("h0", "h1", "h2"), local_host="h0", namespace=NAMESPACE,
        )
        material = "|".join(
            f"{m.name}={m.host}:{m.peer_port}" for m in layout.members
        )
        expected = hashlib.sha256(
            f"{NAMESPACE}\n{material}".encode(),
        ).hexdigest()[:16]
        assert layout.token == f"nmos-registry-{expected}"

    def test_a_flavour_changes_the_token(self) -> None:
        etcd = derive_cluster(
            _hosts("h0", "h1", "h2"), local_host="h0", namespace=NAMESPACE,
        )
        raft = derive_cluster(
            _hosts("h0", "h1", "h2"), local_host="h0", namespace=NAMESPACE,
            flavour="raft\n",
        )
        assert etcd.token != raft.token

    def test_the_flavour_reaches_the_token_through_derive_cluster(self) -> None:
        layout = derive_cluster(
            _hosts("h0", "h1", "h2"), local_host="h0", namespace=NAMESPACE,
            flavour="raft\n",
        )
        assert layout.token == cluster_token(
            layout.members, namespace=NAMESPACE, flavour="raft\n",
        )

    def test_everything_but_the_token_is_flavour_independent(self) -> None:
        """Only identity differs. Topology must not.

        If a flavour changed member names, ordering or quorum, the two backends
        would disagree about the shape of the cluster rather than merely about
        which cluster it is -- and that disagreement is the one this module's
        determinism argument is about.
        """
        etcd = derive_cluster(
            _hosts("h2", "h0", "h1"), local_host="h0", namespace=NAMESPACE,
        )
        raft = derive_cluster(
            _hosts("h2", "h0", "h1"), local_host="h0", namespace=NAMESPACE,
            flavour="raft\n",
        )
        assert [m.name for m in etcd.members] == [m.name for m in raft.members]
        assert etcd.local == raft.local
        assert etcd.size == raft.size
        assert etcd.quorum == raft.quorum
        assert etcd.failures_tolerated == raft.failures_tolerated

    @pytest.mark.parametrize("flavour", ["", "raft\n", "something-else"])
    def test_derivation_stays_deterministic_under_any_flavour(
        self, flavour: str,
    ) -> None:
        """Every member computes the same answer from the same list."""
        first = derive_cluster(
            _hosts("b", "a", "c"), local_host="a", namespace=NAMESPACE,
            flavour=flavour,
        )
        second = derive_cluster(
            _hosts("c", "b", "a"), local_host="a", namespace=NAMESPACE,
            flavour=flavour,
        )
        assert first.token == second.token
        assert first.members == second.members


class TestSharedModuleCompatibility:
    """Guarantees the etcd shim owes the code that still imports through it."""

    def test_the_error_type_is_importable_from_both_paths_and_identical(
        self,
    ) -> None:
        from nmos.etcd.cluster import ClusterConfigError as ViaEtcd

        assert ViaEtcd is ClusterConfigError

    def test_the_error_no_longer_depends_on_the_etcd_extra(self) -> None:
        """It is a configuration fault, raised before any backend exists.

        Basing it on the etcd error hierarchy would make it a lie in the raft
        path, and would drag an optional dependency into a module both
        backends need.
        """
        assert ClusterConfigError.__mro__[1] is Exception

    def test_port_defaults_are_the_repository_convention(self) -> None:
        # Clear of etcd's own 2379/2380, so a stock etcd and a managed one can
        # share a developer's machine. raft never takes these -- it passes its
        # own 2481/2482 explicitly -- but they must not move, because existing
        # deployments and the rigs depend on them.
        assert (DEFAULT_CLIENT_PORT, DEFAULT_PEER_PORT) == (2381, 2382)
        spec = MemberSpec(host="h0")
        assert spec.client_port == DEFAULT_CLIENT_PORT
        assert spec.peer_port == DEFAULT_PEER_PORT

    def test_the_certificate_name_is_one_literal_under_two_names(self) -> None:
        from nmos.cluster.layout import DEFAULT_CERTIFICATE_NAME
        from nmos.etcd.cluster import DEFAULT_ETCD_CERTIFICATE_NAME

        # Two literals that drifted would leave one side accepting
        # certificates the other rejects, which is exactly the failure the
        # original comment in nmos/etcd/cluster.py warned about.
        assert DEFAULT_ETCD_CERTIFICATE_NAME is DEFAULT_CERTIFICATE_NAME

    def test_initial_cluster_still_formats_etcds_flag(self) -> None:
        """It stays on the shared layout, so it must still work from there."""
        layout = derive_cluster(
            _hosts("h0", "h1", "h2"), local_host="h0", namespace=NAMESPACE,
        )
        rendered = layout.initial_cluster(tls=True)
        assert rendered.count(",") == 2
        for member in layout.members:
            assert f"{member.name}=https://{member.host}:{member.peer_port}" in (
                rendered
            )
