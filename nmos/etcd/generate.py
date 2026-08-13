# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Generate the etcd protobuf message classes from the vendored ``.proto`` files.

Run it exactly as the NMOS type generator is run::

    python -m nmos.etcd.generate

Output goes to ``nmos/etcd/generated/`` and **is committed**, mirroring
``nmos/types/generated/`` which is also tracked. That is a deliberate choice:
someone trying the distributed registry then needs only
``pip install -r requirements-etcd.txt`` and ``./install-etcd.sh``, with no
codegen step and no need to understand the build.

Committed generated code can go stale against its source, so ``generate()``
stamps the package with a digest of the vendored protos and
``check_generated_current()`` verifies it at startup. Generated code is still
never hand-edited; if something is wrong with it, the fix belongs here.

What is generated, and what deliberately is not
-----------------------------------------------
Only the **message classes** (``--python_out``) and their type stubs
(``--pyi_out``). The gRPC service stubs (``--grpc_python_out``) are *not*
generated, and that is a design decision rather than an omission.

``protoc``'s Python service plugin emits a ``_pb2_grpc.py`` with no annotations
and no accompanying ``.pyi``. Importing it would make every RPC return ``Any``,
which under ``mypy --strict`` either fails at each call site or forces the
wrapper layer to be littered with casts. Instead ``nmos/etcd/channel.py``
constructs each method with grpc's own typed ``channel.unary_unary(...)`` /
``stream_stream(...)`` API, naming the method path explicitly:

    "/etcdserverpb.KV/Range", "/etcdserverpb.Watch/Watch", ...

grpcio ships ``py.typed``, so that path is fully checked. It also matches what
this client is supposed to be — a small, explicit, audited set of RPCs rather
than a generic passthrough — and it is the same shape the eventual Rust port
takes with tonic.

Stripping
---------
The vendored protos are byte-identical to upstream (see ``proto/PROVENANCE.md``),
so the annotations etcd carries for its own build must be removed here before
``protoc`` runs. Nothing structural is removed: no message, field, enum value or
service method. Only annotations *about* them.

The stripper is deliberately strict. Anything it does not recognise raises
``UnrecognisedProtoConstruct`` and stops generation, because the alternative —
skipping a line it did not understand — is how a client silently loses a field
and starts writing subtly wrong records into the registry database.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

PACKAGE_DIR = Path(__file__).resolve().parent
PROTO_DIR = PACKAGE_DIR / "proto"
OUTPUT_DIR = PACKAGE_DIR / "generated"

# Compiled in this order only for readable logs; protoc resolves imports itself.
PROTO_FILES = ("kv.proto", "auth.proto", "rpc.proto")

# --------------------------------------------------------------------------
# Stripping rules
# --------------------------------------------------------------------------

# Imports that carry annotations this client has no use for. Dropping one is
# only safe because the matching option lines are dropped too -- the two lists
# must stay in step, which is why they sit next to each other.
DROPPED_IMPORTS = frozenset({
    "gogoproto/gogo.proto",
    "google/api/annotations.proto",
    "protoc-gen-openapiv2/options/annotations.proto",
    "etcd/api/versionpb/version.proto",
})

# etcd's protos import each other by build-root-relative path; all three are
# compiled from one flat directory here.
REWRITTEN_IMPORTS = {
    "etcd/api/mvccpb/kv.proto": "kv.proto",
    "etcd/api/authpb/auth.proto": "auth.proto",
}

# Extension namespaces whose options are annotations about a declaration rather
# than part of it. Anything outside this set is a construct we have not
# considered, and generation stops.
DROPPED_OPTION_PREFIXES = (
    "gogoproto.",
    "versionpb.",
    "google.api.",
    "grpc.gateway.",
)

_IMPORT_RE = re.compile(r'^\s*import\s+(?:public\s+)?"(?P<path>[^"]+)"\s*;')
_OPTION_RE = re.compile(r"^\s*option\s+\((?P<name>[A-Za-z0-9_.]+)\)")
# A trailing field/enum-value annotation group: `= 10 [(versionpb.x)="3.1"];`
# The optional trailer captures a line comment after the semicolon --
# `CORRUPT = 2 [(versionpb...)="3.3"]; // kv store corruption detected` -- which
# is preserved, because these protos are the readable reference for the wire
# contract and the comments are half of what makes them readable.
_ANNOTATION_GROUP_RE = re.compile(
    r"\s*\[(?P<body>[^\]]*)\]\s*;(?P<trailer>\s*//.*)?\s*$",
)
_EXTENSION_NAME_RE = re.compile(r"\(\s*(?P<name>[A-Za-z0-9_.]+)\s*\)")


class UnrecognisedProtoConstruct(Exception):
    """The stripper met something it was not written to handle.

    Always fatal. A proto construct nobody has looked at must not be guessed
    about: silently dropping it can remove a field from the wire contract, and
    silently keeping it makes ``protoc`` fail with a far less obvious message.
    """


def _is_dropped_extension(name: str) -> bool:
    return name.startswith(DROPPED_OPTION_PREFIXES)


def _strip_import(line: str, path: str, source: str) -> str | None:
    """Resolve one ``import`` line: drop it, rewrite it, or keep it."""
    if path in DROPPED_IMPORTS:
        return None
    if path in REWRITTEN_IMPORTS:
        return line.replace(path, REWRITTEN_IMPORTS[path])
    if "/" in path and not path.startswith("google/protobuf/"):
        # A new cross-tree import means etcd grew a dependency this vendoring
        # does not cover. Better to stop than to emit a client missing whatever
        # it declared.
        raise UnrecognisedProtoConstruct(
            f"{source}: unvendored import {path!r}. Either vendor it into "
            f"nmos/etcd/proto/ and add it to REWRITTEN_IMPORTS, or add it to "
            f"DROPPED_IMPORTS if it carries annotations only.",
        )
    return line


def _strip_annotation_group(line: str, source: str, lineno: int) -> str:
    """Remove a trailing ``[...]`` group when every option in it is annotation.

    A group mixing an annotation with a real option (``[default = 5]``, say)
    must not be half-removed, so that case raises instead.
    """
    match = _ANNOTATION_GROUP_RE.search(line)
    if match is None:
        return line

    body = match.group("body")
    extensions = _EXTENSION_NAME_RE.findall(body)
    if not extensions:
        # A bracket group with no extension options at all -- a plain proto
        # option such as `[deprecated = true]`. Leave it alone.
        return line
    if not all(_is_dropped_extension(name) for name in extensions):
        raise UnrecognisedProtoConstruct(
            f"{source}:{lineno}: annotation group mixes dropped and kept "
            f"options: {body.strip()!r}",
        )
    return line[: match.start()] + ";" + (match.group("trailer") or "")


def strip_proto(text: str, source: str) -> str:
    """Return ``text`` with etcd's build-only annotations removed.

    Line-oriented, with brace counting for the multi-line option blocks that
    ``google.api.http`` and the OpenAPI extension use.
    """
    output: list[str] = []
    lines = text.splitlines()
    index = 0

    while index < len(lines):
        line = lines[index]
        lineno = index + 1
        index += 1

        import_match = _IMPORT_RE.match(line)
        if import_match is not None:
            kept = _strip_import(line, import_match.group("path"), source)
            if kept is not None:
                output.append(kept)
            continue

        option_match = _OPTION_RE.match(line)
        if option_match is not None:
            name = option_match.group("name")
            if not _is_dropped_extension(name):
                raise UnrecognisedProtoConstruct(
                    f"{source}:{lineno}: unrecognised extension option "
                    f"({name}). Add it to DROPPED_OPTION_PREFIXES only after "
                    f"confirming it is an annotation and not part of the wire "
                    f"contract.",
                )
            # Consume the whole statement. Single-line options end with ';' on
            # this line; block options run until braces balance.
            depth = line.count("{") - line.count("}")
            while depth > 0 and index < len(lines):
                depth += lines[index].count("{") - lines[index].count("}")
                index += 1
            continue

        output.append(_strip_annotation_group(line, source, lineno))

    return "\n".join(output) + "\n"


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

_INIT_TEMPLATE = '''"""Generated etcd protobuf message classes. DO NOT EDIT.

Produced by ``python -m nmos.etcd.generate`` from the vendored protos in
``nmos/etcd/proto/``.

Committed to git, like ``nmos/types/generated/``. That is what lets someone try
the distributed registry with only ``pip install -r requirements-etcd.txt`` and
``./install-etcd.sh`` -- no codegen step, and no need to understand the build to
run the thing.

The cost of committing generated code is that it can go stale against its
source. PROTO_FINGERPRINT is the guard: it is the digest of the vendored
``.proto`` files this package was built from, and ``check_generated_current()``
compares it against those files at startup, so a proto change that has not been
regenerated is a clear message rather than a subtly wrong wire contract.
"""

PROTO_FINGERPRINT = "{fingerprint}"
"""SHA-256 over the vendored .proto sources these stubs were generated from."""
'''


def proto_fingerprint() -> str:
    """Digest of the vendored protos, in a fixed order.

    Covers file names as well as contents, so adding or removing a proto is a
    change too -- not only editing one.
    """
    digest = hashlib.sha256()
    for name in sorted(PROTO_FILES):
        source = PROTO_DIR / name
        digest.update(name.encode())
        digest.update(source.read_bytes())
    return digest.hexdigest()


class GeneratedOutOfDate(Exception):
    """The committed stubs do not match the vendored protos."""


def check_generated_current() -> None:
    """Verify the committed stubs were built from the current protos.

    Cheap enough to run at startup: three file reads and a hash. The failure it
    prevents is expensive and quiet -- a registry writing records against a
    schema its peers no longer use.
    """
    try:
        from nmos.etcd.generated import PROTO_FINGERPRINT
    except ImportError as exc:
        raise GeneratedOutOfDate(
            "the etcd protobuf stubs are missing or predate fingerprinting.\n"
            "  python -m nmos.etcd.generate",
        ) from exc

    current = proto_fingerprint()
    if PROTO_FINGERPRINT != current:
        raise GeneratedOutOfDate(
            f"the etcd protobuf stubs are stale: they were generated from "
            f"protos with fingerprint {PROTO_FINGERPRINT[:12]}, but "
            f"nmos/etcd/proto/ now hashes to {current[:12]}.\n"
            f"  python -m nmos.etcd.generate",
        )


def _run_protoc(staged_dir: Path) -> None:
    """Invoke protoc through grpcio-tools, which bundles its own copy."""
    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={staged_dir}",
        f"--python_out={OUTPUT_DIR}",
        f"--pyi_out={OUTPUT_DIR}",
        *[str(staged_dir / name) for name in PROTO_FILES],
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"protoc failed ({result.returncode}):\n{result.stderr}\n"
            f"{result.stdout}",
        )


def _rewrite_imports_to_package(path: Path) -> None:
    """Point generated cross-file imports at the package.

    protoc emits ``import kv_pb2`` for a flat proto_path, which only resolves if
    the output directory is on ``sys.path``. Rewriting to
    ``from nmos.etcd.generated import kv_pb2`` makes the package importable the
    normal way, which is what every caller expects.
    """
    text = path.read_text(encoding="utf-8")
    rewritten = re.sub(
        r"^import (kv_pb2|auth_pb2|rpc_pb2)( as \w+)?$",
        lambda m: (
            f"from nmos.etcd.generated import {m.group(1)}{m.group(2) or ''}"
        ),
        text,
        flags=re.MULTILINE,
    )
    if rewritten != text:
        path.write_text(rewritten, encoding="utf-8")


def generate() -> None:
    """Strip the vendored protos and compile them into ``generated/``."""
    if not PROTO_DIR.is_dir():
        raise SystemExit(f"missing vendored protos: {PROTO_DIR}")

    # A stale file from a previous etcd version would keep importing cleanly and
    # silently shadow the new contract, so the directory is rebuilt each time.
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="nmos-etcd-proto-") as staging:
        staged_dir = Path(staging)
        for name in PROTO_FILES:
            source = PROTO_DIR / name
            if not source.is_file():
                raise SystemExit(f"missing vendored proto: {source}")
            stripped = strip_proto(
                source.read_text(encoding="utf-8"), source.name,
            )
            (staged_dir / name).write_text(stripped, encoding="utf-8")
        _run_protoc(staged_dir)

    (OUTPUT_DIR / "__init__.py").write_text(
        _INIT_TEMPLATE.format(fingerprint=proto_fingerprint()),
        encoding="utf-8",
    )
    for generated in sorted(OUTPUT_DIR.glob("*_pb2.py")):
        _rewrite_imports_to_package(generated)

    produced = sorted(p.name for p in OUTPUT_DIR.iterdir())
    print(f"generated {len(produced)} files in {OUTPUT_DIR}:")
    for name in produced:
        print(f"  {name}")


if __name__ == "__main__":
    generate()
