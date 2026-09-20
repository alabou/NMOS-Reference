# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Export derived cluster layouts for the Rust port.

``derive_cluster`` is a pure function of the configured member list, and that
is load-bearing rather than tidy: two members handed the same list must derive
the same names, the same canonical order and the same **token**, or they form
two clusters that each believe they are the whole thing.

The token is the part a second implementation cannot get almost right. It
travels in the transport handshake and a member whose token differs is refused,
so a mixed Python/Rust cluster needs the same SHA-256 over the same material
string -- not an equivalent identity, the same sixteen hex characters.

What is recorded
----------------
Each case is a member set plus a derivation, and the answer: the ordered member
list with its derived names, which member is local, and the token. Refusals are
recorded too, with their message, because "the two implementations accept the
same configurations" is half the property and "they refuse the same ones" is
the other half -- a Rust build that accepted a four-member cluster would form
one that tolerates no more failures than three and costs a machine.

Regenerate with::

    python -m nmos.cluster.tests._layout_corpus
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nmos.cluster.layout import (
    DEFAULT_CERTIFICATE_NAME,
    DEFAULT_CLIENT_PORT,
    DEFAULT_PEER_PORT,
    MEMBER_NAME_PREFIX,
    PERMITTED_SIZES,
    ClusterConfigError,
    MemberSpec,
    derive_cluster,
)

OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "rust" / "crates" / "nmos-cluster" / "tests" / "layout_cases.json"
)

# A member set, a derivation, and a name for the case. Ordinary deployments
# first, then the shapes that are easy to get subtly wrong.
_CASES: list[dict[str, Any]] = [
    {
        "name": "a single member",
        "specs": [{"host": "reg-a.example.com"}],
        "local_host": "reg-a.example.com",
        "namespace": "/nmos",
    },
    {
        "name": "three members, given out of order",
        # Deliberately unsorted: the caller may pass `[local] + neighbours`,
        # and the canonical order has to come from the contents.
        "specs": [
            {"host": "reg-c.example.com"},
            {"host": "reg-a.example.com"},
            {"host": "reg-b.example.com"},
        ],
        "local_host": "reg-b.example.com",
        "namespace": "/nmos",
    },
    {
        "name": "five members",
        "specs": [{"host": f"reg-{n}.example.com"} for n in range(5)],
        "local_host": "reg-3.example.com",
        "namespace": "/nmos",
    },
    {
        "name": "a raft flavoured token",
        # The same hosts and namespace as the three-member case above. The
        # tokens must differ, or an etcd cluster and a consensus cluster
        # deployed together mistake each other for peers.
        "specs": [
            {"host": "reg-a.example.com"},
            {"host": "reg-b.example.com"},
            {"host": "reg-c.example.com"},
        ],
        "local_host": "reg-b.example.com",
        "namespace": "/nmos",
        "flavour": "raft\n",
    },
    {
        "name": "a different namespace changes the token",
        "specs": [
            {"host": "reg-a.example.com"},
            {"host": "reg-b.example.com"},
            {"host": "reg-c.example.com"},
        ],
        "local_host": "reg-b.example.com",
        "namespace": "/other",
    },
    {
        "name": "three members co-located on one host",
        # The same-machine rig. Names gain the peer port, because the host
        # alone no longer identifies a member.
        "specs": [
            {"host": "127.0.0.1", "client_port": 2381, "peer_port": 2382},
            {"host": "127.0.0.1", "client_port": 2383, "peer_port": 2384},
            {"host": "127.0.0.1", "client_port": 2385, "peer_port": 2386},
        ],
        "local_host": "127.0.0.1",
        "local_peer_port": 2384,
        "namespace": "/nmos",
    },
    {
        "name": "a host needing sanitisation",
        # Everything outside [A-Za-z0-9._-] collapses to a single dash, and
        # leading and trailing dashes are trimmed.
        "specs": [
            {"host": "reg a/b:c.example.com", "name": None},
            {"host": "reg-b.example.com"},
            {"host": "reg-c.example.com"},
        ],
        "local_host": "reg-b.example.com",
        "namespace": "/nmos",
    },
    {
        "name": "explicit names override derivation",
        "specs": [
            {"host": "reg-a.example.com", "name": "alpha"},
            {"host": "reg-b.example.com", "name": "beta"},
            {"host": "reg-c.example.com", "name": "gamma"},
        ],
        "local_host": "reg-c.example.com",
        "namespace": "/nmos",
    },
    {
        "name": "a bind address distinct from the advertised host",
        "specs": [
            {"host": "reg-a.example.com", "bind_address": "127.0.0.1"},
            {"host": "reg-b.example.com"},
            {"host": "reg-c.example.com"},
        ],
        "local_host": "reg-a.example.com",
        "namespace": "/nmos",
    },
    # -- refusals -----------------------------------------------------------
    {
        "name": "no members at all",
        "specs": [],
        "local_host": "reg-a.example.com",
        "namespace": "/nmos",
    },
    {
        "name": "an even-sized cluster",
        "specs": [{"host": f"reg-{n}.example.com"} for n in range(4)],
        "local_host": "reg-0.example.com",
        "namespace": "/nmos",
    },
    {
        "name": "the local host is not a member",
        "specs": [
            {"host": "reg-a.example.com"},
            {"host": "reg-b.example.com"},
            {"host": "reg-c.example.com"},
        ],
        "local_host": "reg-z.example.com",
        "namespace": "/nmos",
    },
    {
        "name": "an ambiguous local host",
        "specs": [
            {"host": "127.0.0.1", "client_port": 2381, "peer_port": 2382},
            {"host": "127.0.0.1", "client_port": 2383, "peer_port": 2384},
            {"host": "127.0.0.1", "client_port": 2385, "peer_port": 2386},
        ],
        "local_host": "127.0.0.1",
        "namespace": "/nmos",
    },
    {
        "name": "a local peer port no member has",
        "specs": [
            {"host": "127.0.0.1", "client_port": 2381, "peer_port": 2382},
            {"host": "127.0.0.1", "client_port": 2383, "peer_port": 2384},
            {"host": "127.0.0.1", "client_port": 2385, "peer_port": 2386},
        ],
        "local_host": "127.0.0.1",
        "local_peer_port": 9999,
        "namespace": "/nmos",
    },
    {
        "name": "client and peer ports the same",
        "specs": [
            {"host": "reg-a.example.com", "client_port": 2382, "peer_port": 2382},
            {"host": "reg-b.example.com"},
            {"host": "reg-c.example.com"},
        ],
        "local_host": "reg-a.example.com",
        "namespace": "/nmos",
    },
    {
        "name": "a host with surrounding whitespace",
        "specs": [
            {"host": " reg-a.example.com "},
            {"host": "reg-b.example.com"},
            {"host": "reg-c.example.com"},
        ],
        "local_host": "reg-b.example.com",
        "namespace": "/nmos",
    },
    {
        "name": "an empty explicit name",
        "specs": [
            {"host": "reg-a.example.com", "name": ""},
            {"host": "reg-b.example.com"},
            {"host": "reg-c.example.com"},
        ],
        "local_host": "reg-b.example.com",
        "namespace": "/nmos",
    },
    {
        "name": "two members sharing a peer endpoint",
        "specs": [
            {"host": "127.0.0.1", "client_port": 2381, "peer_port": 2382, "name": "a"},
            {"host": "127.0.0.1", "client_port": 2383, "peer_port": 2382, "name": "b"},
            {"host": "127.0.0.1", "client_port": 2385, "peer_port": 2386, "name": "c"},
        ],
        "local_host": "127.0.0.1",
        "local_peer_port": 2386,
        "namespace": "/nmos",
    },
    {
        "name": "two members sharing a name",
        "specs": [
            {"host": "127.0.0.1", "client_port": 2381, "peer_port": 2382, "name": "same"},
            {"host": "127.0.0.1", "client_port": 2383, "peer_port": 2384, "name": "same"},
            {"host": "127.0.0.1", "client_port": 2385, "peer_port": 2386, "name": "c"},
        ],
        "local_host": "127.0.0.1",
        "local_peer_port": 2386,
        "namespace": "/nmos",
    },
]


def _spec(raw: dict[str, Any]) -> MemberSpec:
    return MemberSpec(
        host=raw["host"],
        client_port=raw.get("client_port", 2381),
        peer_port=raw.get("peer_port", 2382),
        name=raw.get("name"),
        bind_address=raw.get("bind_address"),
    )


def build() -> dict[str, Any]:
    """Each configuration, and what this implementation makes of it."""
    cases = []
    for raw in _CASES:
        specs = [_spec(spec) for spec in raw["specs"]]
        record: dict[str, Any] = {
            "name": raw["name"],
            "specs": raw["specs"],
            "local_host": raw["local_host"],
            "local_peer_port": raw.get("local_peer_port"),
            "namespace": raw["namespace"],
            "flavour": raw.get("flavour", ""),
            "tls": raw.get("tls", True),
        }
        try:
            layout = derive_cluster(
                specs,
                local_host=raw["local_host"],
                local_peer_port=raw.get("local_peer_port"),
                namespace=raw["namespace"],
                tls=raw.get("tls", True),
                flavour=raw.get("flavour", ""),
            )
        except ClusterConfigError as exc:
            record["refused"] = str(exc)
        else:
            record["refused"] = None
            record["token"] = layout.token
            record["local"] = layout.local.name
            record["quorum"] = layout.quorum
            record["failures_tolerated"] = layout.failures_tolerated
            record["members"] = [
                {
                    "name": m.name,
                    "host": m.host,
                    "client_port": m.client_port,
                    "peer_port": m.peer_port,
                    "bind_address": m.bind_address,
                }
                for m in layout.members
            ]
            record["initial_cluster"] = layout.initial_cluster()
            record["client_endpoints"] = list(layout.client_endpoints())
        cases.append(record)

    if len({case["name"] for case in cases}) != len(cases):
        raise SystemExit("two cases share a name")

    accepted = sum(1 for case in cases if case["refused"] is None)
    if accepted == 0 or accepted == len(cases):
        raise SystemExit(
            "the corpus must contain both accepted and refused configurations; "
            "one that only refuses would pass against an implementation that "
            "refuses everything",
        )
    return {
        # The topology constants, so the two implementations cannot disagree
        # about a value neither of them computes. `DEFAULT_CERTIFICATE_NAME` in
        # particular is one string doing three jobs -- the gRPC target-name
        # override, etcd's allowed-hostname check and the raft transport's --
        # and a drifted copy would leave one side accepting certificates the
        # other rejects, with nothing failing until a mixed cluster met one.
        "constants": {
            "default_client_port": DEFAULT_CLIENT_PORT,
            "default_peer_port": DEFAULT_PEER_PORT,
            "member_name_prefix": MEMBER_NAME_PREFIX,
            "default_certificate_name": DEFAULT_CERTIFICATE_NAME,
            "permitted_sizes": sorted(PERMITTED_SIZES),
        },
        "cases": cases,
    }


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(corpus, indent=2, sort_keys=True) + "\n")
    accepted = sum(1 for case in corpus["cases"] if case["refused"] is None)
    print(
        f"{len(corpus['cases'])} layout cases "
        f"({accepted} derived, {len(corpus['cases']) - accepted} refused) "
        f"-> {OUTPUT}",
    )


if __name__ == "__main__":
    main()
