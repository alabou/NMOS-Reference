# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""A native in-process Raft consensus layer for the distributed registry.

The second of two distributed backends. ``nmos/etcd/`` remains supported and is
the right choice when the registry's state must survive the loss of every
member at once, or when a live cluster has to be resized. This one trades both
of those for speed, for running natively on Windows, and for needing nothing
installed beyond this checkout.

Why a second backend at all
---------------------------
etcd solves a strictly harder problem than an IS-04 registry has: durable,
general-purpose, MVCC key-value storage with history. A registration through
that layer costs **two to three network round trips plus an fsync** -- a
linearizable read to fence against, a compare-and-swap, and then a wait for the
change to come back down the watch stream before the answer can be given.

Registry state, by contrast, is *soft*. Every resource is re-registered by its
Node within the garbage-collection interval (12 s, ``Behaviour -
Registration.md:47``), and liveness is already a lease. So the durability that
costs an fsync per write is durability the registry can regenerate in seconds,
and the read-before-write exists only because no member owns anything.

What this package does differently
----------------------------------
* **Replicates operations, not key-value pairs.** The unit of replication is a
  registry operation -- register, unregister, expire, forget -- so applying a
  committed entry *is* the store mutation plus its grain publication, in one
  uninterrupted synchronous step. There is no envelope, no key layout, no
  read model catching up to a separate source of truth, and therefore no fence.
* **Keeps the log in memory.** No write-ahead log, no fsync per entry. The one
  thing that reaches the disk is ``{term, voted_for, incarnation}`` -- about 24
  bytes, written when the election term changes, which is rare. See
  ``persist.py`` for why that much is not optional.
* **Owns Node subtrees.** A Node's resources are owned by the member that
  received its registration, so that member's local state is authoritative for
  them and a rejection needs no round trip to be trustworthy.
* **Batches.** Everything proposed within one event-loop tick commits in a
  single quorum round, whether that is one registration or five hundred.

What is deliberately not implemented
------------------------------------
**Dynamic cluster membership change.** The member set is static, derived on
every member from the same list by ``nmos/cluster/layout.py``, and resizing is
a rolling restart with a new list. Live reconfiguration is genuinely hard to
get right, it is the feature etcd is kept for, and a registry does not need to
be resized while running.

The wire format is a specification
----------------------------------
``wire.py`` and ``messages.py`` define a versioned, self-describing peer
protocol, and ``tests/test_wire.py`` pins it with committed golden vectors.
That is not tidiness: the intent is that a second implementation in another
language can join the same cluster, and a mixed cluster is the strongest
conformance test available for "these two implementations agree".
"""
