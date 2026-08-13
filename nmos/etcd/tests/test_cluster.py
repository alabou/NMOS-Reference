# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for canonical cluster derivation.

These need no etcd, so they run in the default gate: the derivation is what
replaces registry-to-registry negotiation, and if it is wrong the cluster
splits before any I/O happens.
"""

from __future__ import annotations

import pytest

from nmos.etcd.cluster import (
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


# ---------------------------------------------------------------------------
# Determinism -- the property the whole no-peer-channel design rests on
# ---------------------------------------------------------------------------

def test_derivation_is_independent_of_argument_order() -> None:
    """Every member is handed the same set in whatever order it likes.

    In practice each one passes [itself] + neighbours, so the orders genuinely
    differ per host. If the derivation depended on that, members would compute
    different names and tokens and form separate clusters.
    """
    order_a = derive_cluster(
        _hosts("a", "b", "c"), local_host="a", namespace=NAMESPACE,
    )
    order_b = derive_cluster(
        _hosts("b", "c", "a"), local_host="b", namespace=NAMESPACE,
    )
    order_c = derive_cluster(
        _hosts("c", "a", "b"), local_host="c", namespace=NAMESPACE,
    )

    assert order_a.members == order_b.members == order_c.members
    assert order_a.token == order_b.token == order_c.token
    assert (
        order_a.initial_cluster()
        == order_b.initial_cluster()
        == order_c.initial_cluster()
    )
    # Only the identity of "us" differs.
    assert [layout.local.host for layout in (order_a, order_b, order_c)] == [
        "a", "b", "c",
    ]


def test_token_is_stable_across_calls() -> None:
    """A restart must not invent a new cluster."""
    first = derive_cluster(_hosts("a", "b", "c"), local_host="a", namespace=NAMESPACE)
    second = derive_cluster(_hosts("a", "b", "c"), local_host="a", namespace=NAMESPACE)
    assert first.token == second.token


def test_token_separates_namespaces() -> None:
    """Two deployments on the same hosts must not merge."""
    one = derive_cluster(_hosts("a", "b", "c"), local_host="a", namespace="/one")
    two = derive_cluster(_hosts("a", "b", "c"), local_host="a", namespace="/two")
    assert one.token != two.token


def test_token_changes_with_membership() -> None:
    three = derive_cluster(_hosts("a", "b", "c"), local_host="a", namespace=NAMESPACE)
    five = derive_cluster(
        _hosts("a", "b", "c", "d", "e"), local_host="a", namespace=NAMESPACE,
    )
    assert three.token != five.token


# ---------------------------------------------------------------------------
# Sizes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("size", "tolerated"), [(1, 0), (3, 1), (5, 2)],
)
def test_permitted_sizes_and_failure_tolerance(size: int, tolerated: int) -> None:
    hosts = _hosts(*[f"h{i}" for i in range(size)])
    layout = derive_cluster(hosts, local_host="h0", namespace=NAMESPACE)
    assert layout.size == size
    assert layout.failures_tolerated == tolerated
    assert layout.quorum == size - tolerated


@pytest.mark.parametrize("size", [2, 4, 6, 7])
def test_even_and_oversized_clusters_are_refused(size: int) -> None:
    """4 members tolerate the same 1 failure as 3, for more machines."""
    hosts = _hosts(*[f"h{i}" for i in range(size)])
    with pytest.raises(ClusterConfigError, match="must have 1, 3, 5 members"):
        derive_cluster(hosts, local_host="h0", namespace=NAMESPACE)


def test_empty_member_list_is_refused() -> None:
    with pytest.raises(ClusterConfigError, match="no members configured"):
        derive_cluster([], local_host="a", namespace=NAMESPACE)


# ---------------------------------------------------------------------------
# Locating the local member
# ---------------------------------------------------------------------------

def test_local_host_must_be_in_the_member_list() -> None:
    """Otherwise the registry joins a cluster no one expects it in."""
    with pytest.raises(ClusterConfigError, match="not in the member list"):
        derive_cluster(_hosts("a", "b", "c"), local_host="d", namespace=NAMESPACE)


def test_ambiguous_local_host_requires_a_peer_port() -> None:
    """Only reachable in the same-machine rigs, where hosts repeat."""
    specs = [
        MemberSpec(host="127.0.0.1", client_port=2381, peer_port=2382, name="m0"),
        MemberSpec(host="127.0.0.1", client_port=2391, peer_port=2392, name="m1"),
        MemberSpec(host="127.0.0.1", client_port=2401, peer_port=2402, name="m2"),
    ]
    with pytest.raises(ClusterConfigError, match="matches 3 members"):
        derive_cluster(specs, local_host="127.0.0.1", namespace=NAMESPACE)

    layout = derive_cluster(
        specs, local_host="127.0.0.1", local_peer_port=2392,
        namespace=NAMESPACE,
    )
    assert layout.local.name == "m1"
    assert layout.local.client_port == 2391


def test_unknown_peer_port_for_local_host_is_refused() -> None:
    specs = [
        MemberSpec(host="127.0.0.1", client_port=2381, peer_port=2382, name="m0"),
        MemberSpec(host="127.0.0.1", client_port=2391, peer_port=2392, name="m1"),
        MemberSpec(host="127.0.0.1", client_port=2401, peer_port=2402, name="m2"),
    ]
    with pytest.raises(ClusterConfigError, match="peer port 9999"):
        derive_cluster(
            specs, local_host="127.0.0.1", local_peer_port=9999,
            namespace=NAMESPACE,
        )


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------

def test_duplicate_host_without_distinct_names_is_refused() -> None:
    specs = [
        MemberSpec(host="a"),
        MemberSpec(host="a"),
        MemberSpec(host="b"),
    ]
    with pytest.raises(ClusterConfigError, match="duplicate"):
        derive_cluster(specs, local_host="b", namespace=NAMESPACE)


def test_duplicate_explicit_name_is_refused() -> None:
    specs = [
        MemberSpec(host="a", name="same"),
        MemberSpec(host="b", name="same"),
        MemberSpec(host="c", name="other"),
    ]
    with pytest.raises(ClusterConfigError, match="duplicate member name"):
        derive_cluster(specs, local_host="a", namespace=NAMESPACE)


def test_client_and_peer_ports_must_differ() -> None:
    specs = [MemberSpec(host="a", client_port=2381, peer_port=2381)]
    with pytest.raises(ClusterConfigError, match="cannot share a port"):
        derive_cluster(specs, local_host="a", namespace=NAMESPACE)


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_out_of_range_ports_are_refused(port: int) -> None:
    specs = [MemberSpec(host="a", client_port=port)]
    with pytest.raises(ClusterConfigError, match="out of range"):
        derive_cluster(specs, local_host="a", namespace=NAMESPACE)


def test_whitespace_in_host_is_refused() -> None:
    """A stray space from a shell default must not become a hostname."""
    with pytest.raises(ClusterConfigError, match="whitespace"):
        derive_cluster([MemberSpec(host=" a")], local_host=" a", namespace=NAMESPACE)


# ---------------------------------------------------------------------------
# Derived strings
# ---------------------------------------------------------------------------

def test_initial_cluster_lists_every_peer() -> None:
    layout = derive_cluster(
        _hosts("c", "a", "b"), local_host="a", namespace=NAMESPACE,
    )
    assert layout.initial_cluster() == (
        "nmos-registry-a=https://a:2382,"
        "nmos-registry-b=https://b:2382,"
        "nmos-registry-c=https://c:2382"
    )


def test_initial_cluster_honours_disabled_tls() -> None:
    layout = derive_cluster(
        _hosts("a"), local_host="a", namespace=NAMESPACE, tls=False,
    )
    assert layout.initial_cluster() == "nmos-registry-a=http://a:2382"


def test_client_endpoints_put_the_local_member_first() -> None:
    """The channel pool tries them in order; local first costs no hop."""
    layout = derive_cluster(
        _hosts("a", "b", "c"), local_host="b", namespace=NAMESPACE,
    )
    assert layout.client_endpoints() == (
        f"b:{DEFAULT_CLIENT_PORT}",
        f"a:{DEFAULT_CLIENT_PORT}",
        f"c:{DEFAULT_CLIENT_PORT}",
    )


def test_member_names_are_derived_from_hosts() -> None:
    layout = derive_cluster(
        _hosts("XYZ-SNX10000", "XYZ-SNX10001", "XYZ-SNX10002"),
        local_host="XYZ-SNX10000", namespace=NAMESPACE,
    )
    assert [m.name for m in layout.members] == [
        "nmos-registry-XYZ-SNX10000",
        "nmos-registry-XYZ-SNX10001",
        "nmos-registry-XYZ-SNX10002",
    ]


def test_member_name_sanitises_unsafe_characters() -> None:
    """The name becomes part of a data-directory path."""
    layout = derive_cluster(
        [MemberSpec(host="host/with:odd chars")],
        local_host="host/with:odd chars", namespace=NAMESPACE,
    )
    assert layout.members[0].name == "nmos-registry-host-with-odd-chars"


def test_bind_address_defaults_to_the_advertised_host() -> None:
    layout = derive_cluster(_hosts("a"), local_host="a", namespace=NAMESPACE)
    assert layout.local.bind_address == "a"


def test_bind_address_can_differ_from_the_advertised_host() -> None:
    """The Linux rig advertises a name but binds a specific loopback address."""
    specs = [
        MemberSpec(host="rds-1.test", bind_address="127.0.0.11"),
        MemberSpec(host="rds-2.test", bind_address="127.0.0.12"),
        MemberSpec(host="rds-3.test", bind_address="127.0.0.13"),
    ]
    layout = derive_cluster(
        specs, local_host="rds-2.test", namespace=NAMESPACE,
    )
    assert layout.local.listen_client_url(tls=True) == (
        f"https://127.0.0.12:{DEFAULT_CLIENT_PORT}"
    )
    assert layout.local.advertise_client_url(tls=True) == (
        f"https://rds-2.test:{DEFAULT_CLIENT_PORT}"
    )


def test_wsl_rig_shares_one_address_and_separates_by_port() -> None:
    """Windows forwards localhost to WSL's 127.0.0.1 only, never 127.0.0.11."""
    specs = [
        MemberSpec(host="127.0.0.1", client_port=2381, peer_port=2382, name="m0"),
        MemberSpec(host="127.0.0.1", client_port=2391, peer_port=2392, name="m1"),
        MemberSpec(host="127.0.0.1", client_port=2401, peer_port=2402, name="m2"),
    ]
    layout = derive_cluster(
        specs, local_host="127.0.0.1", local_peer_port=2382,
        namespace=NAMESPACE,
    )
    assert layout.client_endpoints() == (
        "127.0.0.1:2381", "127.0.0.1:2391", "127.0.0.1:2401",
    )


def test_token_is_a_pure_function_of_members_and_namespace() -> None:
    layout = derive_cluster(
        _hosts("a", "b", "c"), local_host="a", namespace=NAMESPACE,
    )
    assert layout.token == cluster_token(layout.members, namespace=NAMESPACE)
    assert layout.token.startswith("nmos-registry-")


def test_defaults_match_the_repository_port_convention() -> None:
    """Clear of etcd's own 2379/2380 so a stock etcd can coexist."""
    assert (DEFAULT_CLIENT_PORT, DEFAULT_PEER_PORT) == (2381, 2382)
