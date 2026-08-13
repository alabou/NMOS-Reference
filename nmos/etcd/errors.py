# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Errors raised by the etcd client, and the mapping from gRPC status codes.

The registry has to make three different decisions from an etcd failure, and
they are not interchangeable:

* **Retry, or fail the request?** A deadline or an unavailable member is worth
  retrying against another endpoint; a malformed request is not.
* **Answer 503, or answer 500?** Everything here that reaches a handler becomes
  a 503 with ``Retry-After`` — the registry is temporarily unable to serve, not
  broken. An unexpected error is a bug and must not be disguised as congestion.
* **Resnapshot, or resume?** ``EtcdCompacted`` is the one failure that cannot be
  recovered by resuming a watch, because the history the watcher needs is gone.
  It has its own class precisely so the watch loop cannot accidentally treat it
  as a transient reconnect and silently skip a range of revisions.

That last one is why this module exists as more than a single exception type.
The legacy dRDS collapsed these distinctions and its watch loop could not tell a
compaction from a dropped connection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import grpc


class EtcdError(Exception):
    """Base for every failure raised by ``nmos.etcd``."""


class EtcdUnavailable(EtcdError):
    """No member could serve the request, or the deadline expired.

    Retryable. The caller either tries another endpoint or, once the overall
    mutation deadline is spent, answers 503.
    """


class EtcdCompacted(EtcdError):
    """A required revision has been compacted away.

    Raised by a range read at a revision below the compaction point, and
    surfaced explicitly from the watch stream. Never retryable at the same
    revision: the only recovery is a fresh fixed-revision snapshot.
    """

    def __init__(self, message: str, *, compact_revision: int = 0) -> None:
        super().__init__(message)
        self.compact_revision = compact_revision
        """The revision history was compacted to, when etcd reported it.

        Zero when unknown. The resnapshot path does not depend on the value --
        it takes a fresh linearizable revision regardless -- but it is the first
        thing anyone diagnosing a compaction storm wants to see in the log.
        """


class EtcdLeaseNotFound(EtcdError):
    """A lease has expired or been revoked.

    Distinguished from a generic failure because it is *authoritative*: the Node
    that owned it is gone as far as the cluster is concerned, so a heartbeat
    against it answers 404 and the Node re-registers. Treating it as a transient
    error would keep a dead Node alive in the local view.
    """


class EtcdPermissionDenied(EtcdError):
    """The cluster rejected this client's credentials.

    Not retryable and not a 503 — the client certificate is wrong, revoked, or
    not permitted by ``--client-cert-allowed-hostname``. Retrying cannot fix it,
    and reporting congestion would hide a misconfiguration that only ever gets
    worse.
    """


def classify(error: grpc.aio.AioRpcError) -> EtcdError:
    """Convert a gRPC error into the class the registry can act on.

    Anything not explicitly listed becomes ``EtcdUnavailable``, which is the
    conservative choice: it is retryable and it degrades to 503 rather than to a
    wrong answer.
    """
    import grpc

    code = error.code()
    detail = error.details() or ""
    text = f"{code.name.lower()}: {detail}" if code is not None else detail

    if code is grpc.StatusCode.PERMISSION_DENIED or (
        code is grpc.StatusCode.UNAUTHENTICATED
    ):
        return EtcdPermissionDenied(text)

    # etcd reports both compaction and a missing lease as OUT_OF_RANGE /
    # NOT_FOUND with a message, so the message is the only discriminator. It is
    # matched on the documented etcd error strings rather than on the code
    # alone, and anything unmatched falls through to EtcdUnavailable.
    if "required revision has been compacted" in detail:
        return EtcdCompacted(text)
    if "requested lease not found" in detail or "lease not found" in detail:
        return EtcdLeaseNotFound(text)

    return EtcdUnavailable(text)
