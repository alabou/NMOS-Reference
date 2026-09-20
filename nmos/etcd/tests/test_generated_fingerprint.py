# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The guard that makes committing generated code safe.

``nmos/etcd/generated/`` is tracked in git, like ``nmos/types/generated/``, so
trying the distributed registry needs no codegen step. The cost of that is that
the stubs can drift from the protos they were built from, and the failure is
silent and expensive: a member writing records against a schema its peers no
longer use. These tests pin the fingerprint check that closes it.

Since the Rust client exists there are **two** generated trees from these same
protos, and the guard has to cover the pair rather than each alone. A member
regenerating one and committing only that is a mixed cluster whose two halves
speak different wire contracts -- and nothing at runtime would say so, because
each half's own check passes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nmos.etcd.generate import (
    RUST_OUTPUT_DIR,
    GeneratedOutOfDate,
    check_generated_current,
    proto_fingerprint,
)


def test_the_committed_stubs_match_the_vendored_protos() -> None:
    """Fails the moment someone edits a proto without regenerating."""
    check_generated_current()


def test_fingerprint_is_stable() -> None:
    assert proto_fingerprint() == proto_fingerprint()
    assert len(proto_fingerprint()) == 64


def test_fingerprint_covers_file_names_not_just_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding or removing a proto is a change too, not only editing one."""
    from nmos.etcd import generate

    before = proto_fingerprint()
    monkeypatch.setattr(generate, "PROTO_FILES", ("kv.proto", "auth.proto"))
    assert generate.proto_fingerprint() != before


def test_a_changed_proto_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: stale stubs must be a clear message, not a subtle bug."""
    from nmos.etcd import generate

    monkeypatch.setattr(
        generate, "proto_fingerprint", lambda: "0" * 64,
    )
    with pytest.raises(GeneratedOutOfDate, match="stale"):
        generate.check_generated_current()


# ---------------------------------------------------------------------------
# The two trees
# ---------------------------------------------------------------------------

_RUST_STAMP = RUST_OUTPUT_DIR / "fingerprint.json"


def _rust_fingerprint() -> str:
    """The digest the Rust tree was generated from.

    Read from the JSON sidecar rather than by parsing ``fingerprint.rs`` or
    invoking cargo: this test must run in a checkout with no Rust toolchain,
    which is most of them.
    """
    document = json.loads(_RUST_STAMP.read_text(encoding="utf-8"))
    value = document["proto_fingerprint"]
    assert isinstance(value, str)
    return value


def test_the_rust_tree_was_generated_from_these_protos() -> None:
    """The Rust half of ``check_generated_current``.

    ``nmos-etcd/build.rs`` enforces this at build time, which catches it for
    anyone compiling the Rust. This catches it for everyone else -- including
    the common case of a Python-only contributor editing a proto, where no
    cargo command runs at all and the Rust tree would silently be left behind.
    """
    assert _RUST_STAMP.is_file(), (
        f"{_RUST_STAMP} is missing -- the Rust etcd client has no record of "
        f"which protos it was built from.\n"
        f"  python -m nmos.etcd.generate"
    )
    assert _rust_fingerprint() == proto_fingerprint(), (
        f"the generated Rust etcd types are stale: built from protos with "
        f"fingerprint {_rust_fingerprint()[:12]}, but nmos/etcd/proto/ now "
        f"hashes to {proto_fingerprint()[:12]}.\n"
        f"  python -m nmos.etcd.generate"
    )


def test_both_trees_speak_one_wire_contract() -> None:
    """The check neither tree can make about itself.

    Each tree's own guard proves it matches the protos *as they are now*. That
    is not the same as the two trees matching each other: regenerate one,
    commit it, and its guard passes while the other still carries the old
    stamp. This is the one assertion that fails in that case, and it is why it
    lives in the Python suite -- the suite that runs whether or not there is a
    Rust toolchain.
    """
    from nmos.etcd.generated import PROTO_FINGERPRINT

    assert PROTO_FINGERPRINT == _rust_fingerprint(), (
        f"the Python and Rust etcd clients were generated from different "
        f"protos -- Python {PROTO_FINGERPRINT[:12]}, "
        f"Rust {_rust_fingerprint()[:12]}. A mixed cluster would have two "
        f"halves speaking different wire contracts, and each half's own "
        f"check would pass.\n"
        f"  python -m nmos.etcd.generate"
    )


def test_the_rust_stamp_is_a_digest_and_not_a_placeholder() -> None:
    # A stamp of the wrong shape would make the comparisons above compare two
    # things that are equal and meaningless.
    value = _rust_fingerprint()
    assert len(value) == 64, value
    assert all(c in "0123456789abcdef" for c in value), value


def test_the_rust_tree_holds_the_modules_the_protos_declare() -> None:
    """Orphan detection, the etcd equivalent of the NMOS tree's.

    A proto package that disappears upstream leaves its module behind, and a
    module nothing includes keeps compiling. The generator rebuilds the
    directory each run, so this is really a check that it did -- and that the
    committed tree is the generator's output rather than something edited.
    """
    expected = {
        "mod.rs", "fingerprint.rs", "fingerprint.json",
        "mvccpb.rs", "authpb.rs", "etcdserverpb.rs",
    }
    on_disk = {p.name for p in Path(RUST_OUTPUT_DIR).iterdir() if p.is_file()}
    assert on_disk == expected, (
        f"the generated Rust etcd tree does not match what the generator "
        f"emits -- extra {sorted(on_disk - expected)}, "
        f"missing {sorted(expected - on_disk)}.\n"
        f"  python -m nmos.etcd.generate"
    )
