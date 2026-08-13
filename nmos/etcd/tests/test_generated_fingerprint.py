# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The guard that makes committing generated code safe.

``nmos/etcd/generated/`` is tracked in git, like ``nmos/types/generated/``, so
trying the distributed registry needs no codegen step. The cost of that is that
the stubs can drift from the protos they were built from, and the failure is
silent and expensive: a member writing records against a schema its peers no
longer use. These tests pin the fingerprint check that closes it.
"""

from __future__ import annotations

import pytest

from nmos.etcd.generate import (
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
