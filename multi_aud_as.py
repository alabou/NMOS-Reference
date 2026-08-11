#!/usr/bin/env python3
"""Run the vendored Authorization Server with a MULTI-DEVICE audience.

Why this exists
---------------
``fake-as/ipmx_fake_as.py`` accepts ``--default-aud`` as a single string and
``ipmx_security_tokens.mint_token`` writes ``"aud": [aud_value]`` — exactly one
entry. Every token it issues is therefore scoped to exactly one Node, so the
most interesting authorization rig cannot be built at all: one where the
Controller is authorised for *some* of the Nodes it can see and must report the
rest as inaccessible.

That is a limitation of the test fixture, not of the implementation under test.
Both sides already handle a multi-entry audience:

* ``nmos/oauth2/__init__.py`` iterates every ``aud`` entry when it decides
  whether a token covers this Node, and
* ``nmos/controller/handlers.py::_aud_covers_serial`` does the same when it
  decides whether to grey a device out in advance.

``fake-as/`` is a verbatim copy of the TR-10-SEC security validator's
Authorization Server. Keeping it byte-identical is what makes a validator
finding reproducible here, so this wrapper does not edit it. Instead it loads
the module and rebinds ``mint_token`` in that module's namespace — both call
sites live in ``ipmx_fake_as.py`` (it does ``from ipmx_security_tokens import
... mint_token ...``), so one rebind covers every token the server issues.

Usage
-----
The command line is exactly ``ipmx_fake_as.py``'s, except that
``--default-aud`` may be repeated — deliberately the same flag name rather than
a new one, so there is a single concept in the rig:

    multi_aud_as.py --host XYZ-SNX00000 --port 9443 \
        --cert <chain.pem> --key <key.pem> \
        --default-aud XYZ-SNX00001 --default-aud XYZ-SNX00002 ...

The first value is also forwarded to the vendored server, so its own notion of
``aud[0]`` agrees with the token contents. Given a single ``--default-aud``
this behaves exactly like running the vendored server directly.

Each value must be an exact CN / DNS-SAN identity of the Node it is meant to
cover: the Node's serial-number audience rule requires the entry to appear in
``tls_server_cert_names`` after the substring test, so an arbitrary label
(``node2``) authorises nothing.
"""
from __future__ import annotations

import asyncio
import enum
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

REPO = Path(__file__).resolve().parent
"""This checkout. All material is sourced from here — never from a wider
workspace — so a standalone clone runs the rig unchanged."""

FAKE_AS_DIR = REPO / "fake-as"
FAKE_AS_MAIN = FAKE_AS_DIR / "ipmx_fake_as.py"

AUD_FLAG = "--default-aud"

ClaimMutator = Callable[[dict[str, Any]], None]


class Exit(enum.IntEnum):
    """``sysexits.h`` codes, matching what the start-*.sh scripts return."""

    OK = 0
    USAGE = 64      # EX_USAGE — bad invocation
    NOINPUT = 66    # EX_NOINPUT — a required input file is missing


def _load_module(path: Path, name: str) -> ModuleType:
    """Import ``path`` under ``name`` without requiring it to be a package.

    ``fake-as`` contains a hyphen, so it can never be a package name and the
    normal import machinery cannot reach it.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _split_audiences(argv: list[str]) -> tuple[list[str], list[str]]:
    """Pull every ``--default-aud`` out of ``argv``.

    Returns the audience list and the remaining arguments. Both spellings
    argparse accepts are handled, because callers legitimately use either.
    """
    audiences: list[str] = []
    passthrough: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == AUD_FLAG:
            if index + 1 >= len(argv):
                raise ValueError(f"{AUD_FLAG} requires a value")
            audiences.append(argv[index + 1])
            index += 2
            continue
        if argument.startswith(f"{AUD_FLAG}="):
            audiences.append(argument.split("=", 1)[1])
            index += 1
            continue
        passthrough.append(argument)
        index += 1
    return audiences, passthrough


def _widen_audience(fake_as: ModuleType, audiences: list[str]) -> None:
    """Rebind ``mint_token`` so every issued token carries ``audiences``.

    The vendored ``mint_token`` builds its claim dict first and applies the
    caller's ``mutate`` afterwards, so overwriting ``aud`` inside a wrapping
    mutator is what wins — and it composes with any mutator the server itself
    passes rather than discarding it.
    """
    # getattr/setattr rather than attribute syntax: the module is loaded at
    # runtime from a path, so a checker cannot know its attributes.
    original: Any = getattr(fake_as, "mint_token")

    def mint_with_full_audience(
        template: Any,
        key: Any,
        *,
        mutate: ClaimMutator | None = None,
        **kwargs: Any,
    ) -> str:
        def widen(claims: dict[str, Any]) -> None:
            if mutate is not None:
                mutate(claims)
            claims["aud"] = list(audiences)

        return str(original(template, key, mutate=widen, **kwargs))

    setattr(fake_as, "mint_token", mint_with_full_audience)


def main() -> int:
    if not FAKE_AS_MAIN.is_file():
        print(f"multi_aud_as.py: no Authorization Server at {FAKE_AS_MAIN}",
              file=sys.stderr)
        return Exit.NOINPUT

    try:
        audiences, passthrough = _split_audiences(sys.argv[1:])
    except ValueError as exc:
        print(f"multi_aud_as.py: {exc}", file=sys.stderr)
        return Exit.USAGE

    if not audiences:
        print(f"multi_aud_as.py: at least one {AUD_FLAG} is required — with "
              f"none, run fake-as/ipmx_fake_as.py directly", file=sys.stderr)
        return Exit.USAGE

    # The vendored server imports its siblings by plain module name, so its
    # directory has to be importable before the module body executes.
    sys.path.insert(0, str(FAKE_AS_DIR))
    fake_as = _load_module(FAKE_AS_MAIN, "ipmx_fake_as")

    _widen_audience(fake_as, audiences)

    # Forward one --default-aud so the server's own default_aud_entry (which
    # it also uses to build token templates) matches aud[0].
    sys.argv = [str(FAKE_AS_MAIN), *passthrough, AUD_FLAG, audiences[0]]

    print(f"multi_aud_as: every token will carry aud={audiences}",
          file=sys.stderr)
    return int(asyncio.run(fake_as._amain()))


if __name__ == "__main__":
    sys.exit(main())
