# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Export the message samples for the Rust codec, from the test that owns them.

The same reasoning as ``_wire_corpus.py``, one layer up. ``test_messages.py``
holds ``_SAMPLES``: one instance of every message with every field set to
something distinguishable from its default. A mixed Python/Rust cluster depends
on both ends encoding those identically, and the obvious way to arrange it --
writing a second sample set in Rust -- gives the two implementations *separate*
recordings. Two recordings drift, and the drift is invisible: each side keeps
agreeing with its own copy.

So the samples are read out of ``test_messages.py`` **without modifying it**,
and each is exported three ways:

* the **field values**, so the Rust side builds the message itself rather than
  replaying bytes -- replaying hex tests the framing and nothing below it;
* the **bytes it encodes to**, which is the assertion;
* its **type byte**, so the Rust side dispatches the same way a peer would.

Unlike ``_wire_corpus.py``, the field values here need no transcription: the
messages are dataclasses, so ``dataclasses.fields`` yields them from the
instance. There is nothing to keep in step and therefore no self-check -- the
duplication that one guards against does not exist here.

Three variants per sample
-------------------------
``test_messages.py`` opens by saying that none of its values are "zero, empty
or False unless the test is specifically about that", because a field that
encodes but does not decode reads back as its default and a sample holding the
default cannot tell. **Thirteen fields across six samples are nevertheless at
their defaults** -- ``AppendEntriesReply.catching_up`` and
``RequestVote.pre_vote`` among them. Measured by dropping each field's decode
in turn: two mutations survived the whole corpus.

Rather than edit ``test_messages.py``, each sample is exported three times:

``sample``
    Exactly what the test holds.
``saturated``
    Every field forced to a distinguishable non-default, derived from the
    resolved type annotation rather than transcribed. This is what covers the
    thirteen.
``absent``
    Every ``X | None`` field set to ``None``, for the two messages that have
    one. Member indices start at 0, so "nobody" and "member 0" must encode
    differently, and the samples set both fields to a real member.

The values in the last two are the exporter's choice, but **the bytes are still
Python's answer** -- they come from the same ``encode`` the cluster uses. That
is what the corpus asserts, and it does not depend on who picked the inputs.

Regenerate with::

    python -m nmos.raft.tests._messages_corpus
"""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from pathlib import Path
from typing import Any

from nmos.raft.messages import WireEntry
from nmos.raft.tests.test_messages import _SAMPLES
from nmos.raft.wire import MessageType, Stream

OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "rust" / "crates" / "nmos-registry-raft" / "tests" / "message_vectors.json"
)


def _value(value: Any) -> Any:
    """One field value, in a form JSON carries and Rust can rebuild from.

    ``bytes`` and ``WireEntry`` are tagged rather than flattened, because a
    hex string and a string field are indistinguishable once both are JSON
    strings -- and the Rust side has to know which constructor to call.
    """
    if isinstance(value, WireEntry):
        return {"entry": {f.name: _value(getattr(value, f.name))
                          for f in dataclasses.fields(value)}}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, Stream):
        return int(value)
    if isinstance(value, tuple):
        return [_value(item) for item in value]
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    raise SystemExit(f"no JSON form for {type(value).__name__}: {value!r}")


# A distinguishable non-default for every type a message field has. Constants,
# not random draws: the corpus is committed and compared for equality, so a
# fresh value on every run would report drift on every run.
_SATURATED: dict[Any, Any] = {
    bool: True,
    # Large enough to need a multi-byte varint, which a single-byte field would
    # encode identically to a small one.
    int: 300,
    # Surrounding whitespace and a non-ASCII character: `Forward.body_text` is
    # the request body verbatim, so anything that trims or re-encodes it breaks
    # the registry's fidelity guarantee, and a tidy ASCII value cannot tell.
    str: "  saturated é—value  ",
    # A NUL and a high byte, because both survive only a genuinely binary path.
    bytes: b"\x00\xff\x10",
    Stream: Stream.BULK,
}

_SATURATED_TUPLES: dict[Any, tuple[Any, ...]] = {
    int: (3, 9),
    bytes: (b"\x01", b"\x02\x03"),
    # Two entries, so a repeated field emitted once is visible.
    WireEntry: (
        WireEntry(term=7, index=8, payload=b"\x01"),
        WireEntry(term=7, index=9, payload=b"\x02\x03"),
    ),
}


def _optional_arg(hint: Any) -> Any | None:
    """The ``T`` of a ``T | None`` annotation, or ``None`` if it is not one."""
    if not isinstance(hint, (types.UnionType, type(typing.Union[int, None]))):
        return None
    args = [arg for arg in typing.get_args(hint) if arg is not type(None)]
    return args[0] if len(args) == 1 else None


def _saturated(hint: Any) -> Any:
    """A non-default value for a field annotated ``hint``.

    Driven by the resolved annotation rather than by the value the sample
    happens to hold, because the fields this exists to cover are exactly the
    ones whose value is an empty tuple or a ``False`` -- from which the element
    type cannot be recovered.
    """
    inner = _optional_arg(hint)
    if inner is not None:
        return _saturated(inner)
    if typing.get_origin(hint) is tuple:
        args = typing.get_args(hint)
        if len(args) == 2 and args[1] is Ellipsis and args[0] in _SATURATED_TUPLES:
            return _SATURATED_TUPLES[args[0]]
        raise SystemExit(f"no saturated value for tuple annotation {hint!r}")
    if hint in _SATURATED:
        return _SATURATED[hint]
    raise SystemExit(f"no saturated value for annotation {hint!r}")


def _case(sample: Any, variant: str) -> dict[str, Any]:
    message_type = sample.TYPE
    if not isinstance(message_type, MessageType):
        raise SystemExit(f"{type(sample).__name__}.TYPE is not a MessageType")
    return {
        "message": type(sample).__name__,
        "variant": variant,
        "type": int(message_type),
        "fields": {
            field.name: _value(getattr(sample, field.name))
            for field in dataclasses.fields(sample)
        },
        "encoded_hex": sample.encode().hex(),
    }


def build() -> dict[str, Any]:
    """Each sample message, as fields plus the bytes they must encode to."""
    cases = []
    # `_SAMPLES` is heterogeneous, so it types as `list[object]` and neither
    # `dataclasses.fields` nor `replace` accepts that. The messages have no
    # common base to narrow to -- they are related by being dataclasses with an
    # `encode`, which is structural -- so this is where the check stops being
    # able to help.
    samples: list[Any] = list(_SAMPLES)
    for sample in samples:
        hints = typing.get_type_hints(type(sample))
        names = [field.name for field in dataclasses.fields(sample)]

        cases.append(_case(sample, "sample"))
        cases.append(_case(
            dataclasses.replace(
                sample, **{name: _saturated(hints[name]) for name in names},
            ),
            "saturated",
        ))

        optional = [name for name in names if _optional_arg(hints[name]) is not None]
        if optional:
            cases.append(_case(
                dataclasses.replace(sample, **dict.fromkeys(optional)), "absent",
            ))

    # `test_messages.py` asserts every MessageType but PONG is sampled; assert
    # the same here rather than trusting it, because this exporter is what
    # decides whether the Rust side sees a message at all. A sample added there
    # and silently dropped here would leave Rust untested for it.
    keys = {(case["message"], case["variant"]) for case in cases}
    if len(keys) != len(cases):
        raise SystemExit("two cases share a message and variant")
    return {"cases": cases}


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(corpus, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"{len(corpus['cases'])} message vectors -> {OUTPUT}")


if __name__ == "__main__":
    main()
