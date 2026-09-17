# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""etcd's view of the canonical cluster derivation.

The derivation itself lives in :mod:`nmos.cluster.layout` and is shared with
``nmos/raft/``. It moved there when the second distributed backend arrived, for
the reason its own docstring gives: determinism across members is a correctness
property, and two copies of that rule that drifted would form the split cluster
it exists to prevent -- silently, with each copy's own tests still passing.

Nothing about the derivation changed in the move. This module re-exports it so
every existing import path, every error string and every derived token stay
exactly as they were, and adds the one constant that is genuinely etcd
vocabulary rather than topology.
"""

from __future__ import annotations

from nmos.cluster.layout import (  # noqa: F401
    DEFAULT_CERTIFICATE_NAME,
    DEFAULT_CLIENT_PORT,
    DEFAULT_PEER_PORT,
    MEMBER_NAME_PREFIX,
    PERMITTED_SIZES,
    ClusterConfigError,
    ClusterLayout,
    Member,
    MemberSpec,
    cluster_token,
    derive_cluster,
)

# The SAN every member's etcd certificate shares, and therefore the single
# string that is both the gRPC target-name override and etcd's
# --client-cert-allowed-hostname / --peer-cert-allowed-hostname.
#
# An alias rather than a second literal: the raft backend verifies against the
# same shipped certificate set under its own flag name, and two literals that
# drifted would leave one side accepting certificates the other rejects.
DEFAULT_ETCD_CERTIFICATE_NAME = DEFAULT_CERTIFICATE_NAME

__all__ = [
    "DEFAULT_CERTIFICATE_NAME",
    "DEFAULT_CLIENT_PORT",
    "DEFAULT_ETCD_CERTIFICATE_NAME",
    "DEFAULT_PEER_PORT",
    "MEMBER_NAME_PREFIX",
    "PERMITTED_SIZES",
    "ClusterConfigError",
    "ClusterLayout",
    "Member",
    "MemberSpec",
    "cluster_token",
    "derive_cluster",
]
