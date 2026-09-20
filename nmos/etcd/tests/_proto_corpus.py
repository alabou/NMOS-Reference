# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Export protobuf wire bytes so the Rust client can prove it agrees.

Two generated trees come from one set of vendored protos. The fingerprint guard
proves they were generated from the *same* protos; it does not prove the two
generators agree about what those protos mean. That gap is small and it is not
empty -- a field mapped to a different number, a ``bytes`` field the other side
treats as a string, an enum whose values shifted -- and every one of them is a
member writing records its peers read differently.

So this records, for each message the client actually uses, a populated value
and the exact bytes Python serialises it to. The Rust asserts it decodes those
bytes to the same field values, and re-encodes to the same bytes.

Only the messages ``nmos/etcd/`` and ``nmos/registry/etcd_backend.py`` name are
covered, and a check below fails if that list and this one drift apart. Covering
all of ``rpc.proto`` would be covering an API surface this client deliberately
does not have.

Regenerate with::

    python -m nmos.etcd.tests._proto_corpus
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nmos.etcd.generate import RUST_OUTPUT_DIR, proto_fingerprint
from nmos.etcd.generated import kv_pb2, rpc_pb2

OUTPUT = RUST_OUTPUT_DIR.parents[1] / "tests" / "message_vectors.json"

# Where the client's message usage is declared, for the drift check at the end.
_CLIENT_SOURCES = (
    Path(__file__).resolve().parents[1],                       # nmos/etcd/
    Path(__file__).resolve().parents[2] / "registry" / "etcd_backend.py",
)

# Messages named by the client that carry no fields worth a vector: they are
# empty requests, and an empty message serialises to zero bytes in both
# implementations whatever the generators did. Listed rather than skipped
# silently, so "why is this not covered" has an answer.
_EMPTY_BY_DESIGN = {
    "StatusRequest",
    "MemberListRequest",
    "WatchProgressRequest",
}


def _samples() -> list[tuple[str, Any]]:
    """One populated value per message, in a fixed order.

    Every field that the client reads or writes is set to something
    distinguishable -- not 0, not "", not the first enum value -- because a
    field left at its default cannot detect a decoder that drops it: the value
    read back is the same either way. That is the check ``test_messages.py``
    turned out to need and not have, and the lesson carried here.
    """
    header = rpc_pb2.ResponseHeader(
        cluster_id=0x1122_3344_5566_7788,
        member_id=0x99AA_BBCC_DDEE_FF00,
        revision=4_242_424_242,
        raft_term=7,
    )
    key_value = kv_pb2.KeyValue(
        key=b"/nmos/nodes/n1/self",
        create_revision=11,
        mod_revision=22,
        version=33,
        value=b'{"id": "n1", "label": "caf\xc3\xa9"}',
        lease=0x0BAD_C0DE_0BAD_C0DE,
    )
    previous = kv_pb2.KeyValue(
        key=b"/nmos/nodes/n1/self",
        create_revision=11,
        mod_revision=21,
        version=32,
        value=b'{"id": "n1"}',
        lease=0x0BAD_C0DE_0BAD_C0DE,
    )

    put = rpc_pb2.PutRequest(
        key=b"/nmos/ids/n1",
        value=b"node",
        lease=123456789,
        prev_kv=True,
        ignore_value=False,
        ignore_lease=False,
    )
    range_request = rpc_pb2.RangeRequest(
        key=b"/nmos/nodes/",
        range_end=b"/nmos/nodes0",
        limit=500,
        revision=99,
        serializable=True,
        keys_only=False,
        count_only=False,
        min_mod_revision=1,
        max_mod_revision=1000,
    )
    delete_request = rpc_pb2.DeleteRangeRequest(
        key=b"/nmos/nodes/n1/",
        range_end=b"/nmos/nodes/n10",
        prev_kv=True,
    )

    return [
        ("ResponseHeader", header),
        ("KeyValue", key_value),
        ("Event", kv_pb2.Event(
            type=kv_pb2.Event.EventType.DELETE,
            kv=key_value,
            prev_kv=previous,
        )),
        ("RangeRequest", range_request),
        ("RangeResponse", rpc_pb2.RangeResponse(
            header=header, kvs=[key_value, previous], more=True, count=2,
        )),
        ("PutRequest", put),
        ("DeleteRangeRequest", delete_request),
        ("DeleteRangeResponse", rpc_pb2.DeleteRangeResponse(
            header=header, deleted=7, prev_kvs=[previous],
        )),
        # The compare-and-set the whole mutation path is built on. Its oneofs
        # are where a generator disagreement would be both easy and invisible.
        ("Compare", rpc_pb2.Compare(
            result=rpc_pb2.Compare.CompareResult.EQUAL,
            target=rpc_pb2.Compare.CompareTarget.MOD,
            key=b"/nmos/nodes/n1/self",
            mod_revision=22,
            range_end=b"",
        )),
        ("Compare/version", rpc_pb2.Compare(
            result=rpc_pb2.Compare.CompareResult.GREATER,
            target=rpc_pb2.Compare.CompareTarget.VERSION,
            key=b"/nmos/ids/n1",
            version=3,
        )),
        ("Compare/create", rpc_pb2.Compare(
            result=rpc_pb2.Compare.CompareResult.NOT_EQUAL,
            target=rpc_pb2.Compare.CompareTarget.CREATE,
            key=b"/nmos/ids/n1",
            create_revision=5,
        )),
        ("Compare/value", rpc_pb2.Compare(
            result=rpc_pb2.Compare.CompareResult.LESS,
            target=rpc_pb2.Compare.CompareTarget.VALUE,
            key=b"/nmos/ids/n1",
            value=b"node",
        )),
        ("Compare/lease", rpc_pb2.Compare(
            result=rpc_pb2.Compare.CompareResult.EQUAL,
            target=rpc_pb2.Compare.CompareTarget.LEASE,
            key=b"/nmos/nodes/n1/self",
            lease=123456789,
        )),
        ("RequestOp/put", rpc_pb2.RequestOp(request_put=put)),
        ("RequestOp/range", rpc_pb2.RequestOp(request_range=range_request)),
        (
            "RequestOp/delete",
            rpc_pb2.RequestOp(request_delete_range=delete_request),
        ),
        ("ResponseOp/range", rpc_pb2.ResponseOp(
            response_range=rpc_pb2.RangeResponse(header=header, kvs=[key_value]),
        )),
        ("ResponseOp/put", rpc_pb2.ResponseOp(
            response_put=rpc_pb2.PutResponse(header=header, prev_kv=previous),
        )),
        ("TxnRequest", rpc_pb2.TxnRequest(
            compare=[rpc_pb2.Compare(
                result=rpc_pb2.Compare.CompareResult.EQUAL,
                target=rpc_pb2.Compare.CompareTarget.MOD,
                key=b"/nmos/nodes/n1/self",
                mod_revision=22,
            )],
            success=[rpc_pb2.RequestOp(request_put=put)],
            failure=[rpc_pb2.RequestOp(request_range=range_request)],
        )),
        ("TxnResponse", rpc_pb2.TxnResponse(
            header=header,
            succeeded=True,
            responses=[rpc_pb2.ResponseOp(
                response_put=rpc_pb2.PutResponse(header=header),
            )],
        )),
        ("WatchCreateRequest", rpc_pb2.WatchCreateRequest(
            key=b"/nmos/",
            range_end=b"/nmos0",
            start_revision=4242,
            progress_notify=True,
            prev_kv=True,
            watch_id=9,
            fragment=False,
            filters=[rpc_pb2.WatchCreateRequest.FilterType.NOPUT],
        )),
        ("WatchRequest/create", rpc_pb2.WatchRequest(
            create_request=rpc_pb2.WatchCreateRequest(
                key=b"/nmos/", range_end=b"/nmos0", start_revision=4242,
            ),
        )),
        ("WatchRequest/progress", rpc_pb2.WatchRequest(
            progress_request=rpc_pb2.WatchProgressRequest(),
        )),
        ("WatchResponse", rpc_pb2.WatchResponse(
            header=header,
            watch_id=9,
            created=False,
            canceled=True,
            compact_revision=4000,
            cancel_reason="etcdserver: mvcc: required revision has been compacted",
            fragment=False,
            events=[kv_pb2.Event(type=kv_pb2.Event.EventType.PUT, kv=key_value)],
        )),
        ("LeaseGrantRequest", rpc_pb2.LeaseGrantRequest(TTL=30, ID=777)),
        ("LeaseGrantResponse", rpc_pb2.LeaseGrantResponse(
            header=header, ID=777, TTL=30, error="",
        )),
        ("LeaseKeepAliveRequest", rpc_pb2.LeaseKeepAliveRequest(ID=777)),
        ("LeaseKeepAliveResponse", rpc_pb2.LeaseKeepAliveResponse(
            header=header, ID=777, TTL=29,
        )),
        ("LeaseRevokeRequest", rpc_pb2.LeaseRevokeRequest(ID=777)),
        ("LeaseRevokeResponse", rpc_pb2.LeaseRevokeResponse(header=header)),
        ("LeaseTimeToLiveRequest", rpc_pb2.LeaseTimeToLiveRequest(
            ID=777, keys=True,
        )),
        ("LeaseTimeToLiveResponse", rpc_pb2.LeaseTimeToLiveResponse(
            header=header,
            ID=777,
            TTL=25,
            grantedTTL=30,
            keys=[b"/nmos/nodes/n1/self"],
        )),
        ("CompactionRequest", rpc_pb2.CompactionRequest(
            revision=4000, physical=True,
        )),
        ("CompactionResponse", rpc_pb2.CompactionResponse(header=header)),
        ("StatusResponse", rpc_pb2.StatusResponse(
            header=header,
            version="3.6.14",
            dbSize=1_048_576,
            leader=0x99AA_BBCC_DDEE_FF00,
            raftIndex=555,
            raftTerm=7,
            raftAppliedIndex=554,
            errors=["a recorded error"],
            dbSizeInUse=524_288,
            isLearner=False,
        )),
        ("MemberListResponse", rpc_pb2.MemberListResponse(
            header=header,
            members=[rpc_pb2.Member(
                ID=0x99AA_BBCC_DDEE_FF00,
                name="member-0",
                peerURLs=["https://127.0.0.1:22380"],
                clientURLs=["https://127.0.0.1:22379"],
                isLearner=False,
            )],
        )),
    ]


def _messages_named_by_the_client() -> set[str]:
    """Message names appearing in the client and the backend."""
    pattern = re.compile(r"(?:rpc_pb2|kv_pb2|auth_pb2)\.([A-Za-z][A-Za-z0-9_]*)")
    names: set[str] = set()
    for source in _CLIENT_SOURCES:
        paths = sorted(source.glob("*.py")) if source.is_dir() else [source]
        for path in paths:
            names.update(pattern.findall(path.read_text(encoding="utf-8")))
    # `DESCRIPTOR` is the module's own descriptor object, not a message.
    return names - {"DESCRIPTOR"}


def build() -> dict[str, Any]:
    """Each sample, as the bytes it serialises to."""
    cases = []
    for name, message in _samples():
        encoded = message.SerializeToString(deterministic=True)
        # A sample that encodes to nothing cannot detect anything. Every
        # message here has fields set, so this catches a field name that was
        # silently accepted and dropped rather than raising.
        if not encoded:
            raise SystemExit(
                f"{name} serialises to zero bytes, so the vector proves "
                f"nothing. Set a distinguishable value on its fields.",
            )
        cases.append({
            "name": name,
            "message": type(message).__name__,
            "encoded_hex": encoded.hex(),
            # Round-tripping through Python is recorded so the Rust assertion
            # is not enforcing a Python bug: if this were False the vector
            # would be describing something broken on both sides.
            "round_trips": type(message)
            .FromString(encoded)
            .SerializeToString(deterministic=True) == encoded,
        })

    names = [case["name"] for case in cases]
    if len(set(names)) != len(names):
        raise SystemExit("two cases share a name")

    covered = {case["message"] for case in cases}
    named = _messages_named_by_the_client()
    missing = sorted(named - covered - _EMPTY_BY_DESIGN)
    if missing:
        raise SystemExit(
            f"the client names message(s) {missing} that no vector covers, so "
            f"the Rust side would be untested for them. Add a sample, or "
            f"record why not in _EMPTY_BY_DESIGN.",
        )

    return {
        # Tied to the protos, so a regeneration that changed the contract
        # without refreshing this recording fails rather than passing against
        # the old bytes.
        "proto_fingerprint": proto_fingerprint(),
        "cases": cases,
    }


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(corpus, indent=2, sort_keys=True) + "\n")
    print(f"{len(corpus['cases'])} message vectors -> {OUTPUT}")


if __name__ == "__main__":
    main()
