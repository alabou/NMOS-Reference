# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Backend-neutral cluster topology for the distributed registry.

One member list in, one canonical layout out -- member names, ordering, quorum
arithmetic and the cluster token -- computed identically and independently on
every host. Both distributed backends derive their topology from here, because
two implementations of this rule that drifted would form the split cluster the
rule exists to prevent, and would do it silently.

See ``nmos.cluster.layout`` for the derivation itself.
"""

from nmos.cluster.layout import (
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

__all__ = [
    "DEFAULT_CERTIFICATE_NAME",
    "DEFAULT_CLIENT_PORT",
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
