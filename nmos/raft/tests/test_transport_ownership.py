# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Who owns whom, between a node and its transport.

A node owns its transport -- ``RaftNode`` holds it as ``transport`` -- and
``start`` hands the transport the node straight back as its handler. Held
strongly that is a cycle: transport -> node -> transport.

CPython's collector would eventually reclaim such a cycle, which is why it
could sit here unnoticed; Rust's reference counting would not, and the two
implementations must agree on ownership. Worse, ``close`` here never cleared
the handler at all -- the Rust one did -- so nothing broke the cycle even in
principle.

Asserted as a *structural* property rather than through ``close``, because that
is the one a refactor can silently remove.
"""
from __future__ import annotations

import gc
import weakref
from typing import Any

from nmos.raft.messages import AppendEntries, RequestVote
from nmos.raft.transport import RaftTransport


class _Recorder:
    """Enough of ``PeerHandler`` to be installed, and weak-referenceable."""

    def on_request_vote(self, peer: int, message: RequestVote) -> Any:
        raise NotImplementedError

    def on_append_entries(self, peer: int, message: AppendEntries) -> Any:
        raise NotImplementedError

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


def _transport() -> RaftTransport:
    return RaftTransport(
        local=0,
        peers={},
        bind=("127.0.0.1", 0),
        cluster_id="ownership",
        member_name="member-0",
        incarnation=1,
        rpc_timeout=0.5,
    )


class TestTheTransportDoesNotOwnItsHandler:
    async def test_starting_takes_no_strong_reference(self) -> None:
        transport = _transport()
        handler = _Recorder()
        watch = weakref.ref(handler)

        await transport.start(handler)  # type: ignore[arg-type]
        assert watch() is handler, "the handler went early"

        # The node is what keeps itself alive; the transport only keeps a way
        # back to it.
        del handler
        gc.collect()
        assert watch() is None, (
            "the transport kept the handler alive, which with the node's own "
            "reference to the transport is a cycle nothing breaks -- `close` "
            "never cleared it"
        )
        await transport.close()

    async def test_a_live_handler_is_still_reachable(self) -> None:
        """The other half: weak must not mean unusable."""
        transport = _transport()
        handler = _Recorder()
        await transport.start(handler)  # type: ignore[arg-type]
        assert transport._handler is handler  # noqa: SLF001
        await transport.close()
