# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Generate the float-formatting parity corpus.

``JsonEngine._write_float_value`` writes every float with ``repr`` -- the
shortest decimal that reads back as the same double -- and so does
``dump_any``. No Rust formatter spells that the same way (Rust writes
``1000000`` for ``1000000.0`` and ``1e-5`` for ``1e-05``), so
``nmos-json``'s ``format_repr`` is a re-implementation, and a
re-implementation needs evidence.

It used to be ``f"{value:g}"``, which truncated to six significant digits and
made 673 of the 811 values below come back as a *different number*. Fixed
during this port; the corpus records the corrected behaviour.

Hand-written expectations would only test the author's reading of C99. This
runs CPython over a wide spread of values and records what it actually printed.

The spread is chosen to cover where the algorithm changes its mind
---------------------------------------------------------------
* **every power of ten** from 1e-30 to 1e30, because the switch to exponent
  form happens at ``1e-4`` and ``1e16`` and the boundaries are the whole point;
* **values either side of those boundaries**, to catch an off-by-one;
* **more than six significant digits**, since that is where the old ``%g``
  truncated and where a regression would reappear;
* **denormals and the extremes**, where the exponent needs three digits;
* **negative zero**, which keeps its sign;
* **``inf`` and ``nan``**, which Python writes bare -- output that is not valid
  JSON, reproduced deliberately rather than corrected on one side only.

Regenerate with::

    python -m nmos.codegen.tests._float_corpus
"""

from __future__ import annotations

import json
import math
import random
import struct
import re
from pathlib import Path

OUTPUT = (
    Path(__file__).parent.parent.parent.parent
    / "rust" / "crates" / "nmos-json" / "tests" / "float_cases.json"
)

# Fixed so the corpus is reproducible. A regenerated corpus that differed only
# by sampling would make every diff unreadable.
SEED = 20260917


def _values() -> list[float]:
    values: list[float] = [
        0.0, -0.0, 1.0, -1.0, 0.5, 1.5, 2.5, 0.1, 0.2, 0.3,
        math.pi, math.e, math.tau,
        # Either side of the exponent-form boundaries.
        99999.0, 100000.0, 999999.0, 1000000.0, 1000001.0,
        0.0001, 0.00009999, 0.000123456789, 1e-5, 9.9999e-5,
        # More than six significant digits.
        1234567.0, 12345678.9, 1.23456789, 123.456789, 0.123456789,
        # Extremes and denormals.
        5e-324, 1e308, 1.7976931348623157e308, 2.2250738585072014e-308,
        float("inf"), float("-inf"), float("nan"),
    ]

    # Every power of ten, positive and negative, across the interesting range.
    for exponent in range(-30, 31):
        values.append(float(f"1e{exponent}"))
        values.append(float(f"-1e{exponent}"))
        values.append(float(f"9.999999e{exponent}"))

    # A reproducible spread of random magnitudes, including bit patterns that
    # a purely decimal generator would never reach.
    rng = random.Random(SEED)
    for _ in range(400):
        exponent = rng.randint(-40, 40)
        values.append(rng.uniform(-10, 10) * (10.0**exponent))
    for _ in range(200):
        bits = rng.getrandbits(64)
        candidate = struct.unpack("<d", struct.pack("<Q", bits))[0]
        if math.isfinite(candidate):
            values.append(candidate)

    return values


_NUMBER = re.compile(r'"minimum":(.*?)}')


def engine_float(value: float) -> str:
    """What ``JsonEngine`` actually writes for this float.

    Goes through the real encoder -- ``NConstraintFloat.minimum`` is a genuine
    ``NFloat`` member -- rather than recomputing the formatting here.

    That distinction is not pedantic. An earlier version of this module recorded
    ``repr(value)`` directly, which made the drift guard vacuous: reverting
    ``_write_float_value`` to ``f"{value:g}"`` changed the encoder and left the
    corpus untouched, so the guard passed while the bug was back. A corpus that
    does not consult the thing it is guarding is not guarding it.
    """
    from nmos.json.engine import JsonEngine
    from nmos.types.generated.nconstraint_float import NConstraintFloatValue

    obj = NConstraintFloatValue()
    obj.decode(JsonEngine(), {"minimum": value})
    text = JsonEngine().encode(obj, None)
    match = _NUMBER.search(text)
    return match.group(1) if match else text


def build() -> list[dict[str, object]]:
    """What Python printed, for every value, recorded exactly."""
    cases: list[dict[str, object]] = []
    seen: set[str] = set()
    for value in _values():
        # The bit pattern identifies the value unambiguously, which a decimal
        # literal in JSON would not -- and it is how the Rust side reconstructs
        # exactly the same float rather than one that merely prints the same.
        bits = struct.unpack("<Q", struct.pack("<d", value))[0]
        key = str(bits)
        if key in seen:
            continue
        seen.add(key)
        cases.append(
            {
                "bits": key,
                # What the encoder actually wrote, read back out of its output.
                "formatted": engine_float(value),
                # What `dump_any` emits, for synthesised responses. Since the
                # `%g` fix these agree for every finite value; they are kept
                # apart so a future divergence is visible rather than assumed.
                "dumped": json.dumps(value) if math.isfinite(value) else None,
            },
        )
    return cases


def main() -> None:
    cases = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n")
    print(f"{len(cases)} float cases -> {OUTPUT}")


if __name__ == "__main__":
    main()
