# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Hold the type model against the published IS-04 schemas.

``test_schema_conformance.py`` validates *responses* against the schemas. This
validates the *model* against them, which catches a different and earlier class
of mistake: a descriptor that requires a member the specification says is
optional rejects a conformant Node's registration outright, and no amount of
response validation will show it, because the resource never gets stored.

The schemas in ``nmos/registry/specs/schemas/`` are AMWA IS-04 ``v1.3.3``,
vendored verbatim. They are the independent authority here -- the descriptors
in ``nmos/codegen/definitions/`` are this project's reading of them, and the
two are compared rather than assumed to agree.

Why this is an inventory and not a pass/fail
--------------------------------------------
Divergence from a schema is not a defect, and the clearest case here is the one
that looks worst. ``NFlowVideoCoded`` requires ``components`` while
``flow_video_coded.json`` does not mention it -- because the requirement is
**BCP-006's**, not core IS-04's, and a BCP has no schema in this directory to be
compared against. Reading the schema alone would call that a bug and relaxing it
would break BCP-006 conformance.

So the schemas are an authority, not *the* authority, and the useful question is
never "does this match" but "is each difference one someone decided". What must
not happen is a difference appearing *silently*: the list below is exact, so
adding a member, changing an ``optional`` flag or vendoring a newer schema fails
this test until someone writes down which way it went and why.

The same inventory serves the Rust port, because both type trees are rendered
from these descriptors. A model that diverges from the spec diverges in both
languages identically, which is the intended behaviour -- the port reproduces
Python, including where Python is wrong -- and this is where that wrongness is
recorded instead of being rediscovered.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from pathlib import Path
from typing import Any

import nmos.codegen.definitions as definitions

SCHEMAS = Path(__file__).resolve().parents[2] / "registry" / "specs" / "schemas"

# Which schema describes which descriptor. Not derivable: the schemas are named
# for the specification's document structure and the types for the model's, and
# the two only mostly coincide.
#
# ``source_generic.json`` is the one genuine one-to-many -- it is
# ``source_core`` plus ``format``, which is exactly what the video, data and mux
# Sources are -- so it is checked against all three under a disambiguating key.
SCHEMA_TO_TYPE: dict[str, str] = {
    "resource_core.json": "NResourceCore",
    "node.json": "NNode",
    "device.json": "NDevice",
    "sender.json": "NSender",
    "source_core.json": "NSourceCore",
    "source_audio.json": "NSourceAudio",
    "source_generic.json": "NSourceVideo",
    "source_generic.json#data": "NSourceData",
    "source_generic.json#mux": "NSourceMux",
    "flow_video_raw.json": "NFlowVideoRaw",
    "flow_video_coded.json": "NFlowVideoCoded",
    "flow_audio_raw.json": "NFlowAudioRaw",
    "flow_audio_coded.json": "NFlowAudioCoded",
    "flow_data.json": "NFlowData",
    "flow_sdianc_data.json": "NFlowDataSdianc",
    "flow_json_data.json": "NFlowDataJson",
    "flow_mux.json": "NFlowMux",
    "receiver_core.json": "NReceiverCore",
    "receiver_video.json": "NReceiverVideo",
    "receiver_audio.json": "NReceiverAudio",
    "receiver_data.json": "NReceiverData",
    "receiver_mux.json": "NReceiverMux",
}

# Every place the model and the specification disagree, with the direction and
# the consequence. Anything not listed here is a new divergence and fails.
#
# STRICTER  the descriptor requires what the schema leaves optional, so a
#           conformant body is REJECTED -- a live interoperability failure.
# UNMODELLED the schema defines a property the descriptor has no member for.
#           Harmless for the registry, which stores and returns bodies as the
#           bytes that arrived and never rebuilds them from the typed form, but
#           a real gap for any code that *constructs* a resource from the types.
KNOWN_DIVERGENCES: dict[tuple[str, str], str] = {
    ("flow_video_coded.json", "STRICTER:components"): (
        "Deliberate, and required. flow_video_coded.json is core IS-04 and asks "
        "only for media_type, but the BCP-006 documents layer the real "
        "requirement on top of it: at IS-04 v1.3 a coded video Flow MUST carry "
        "'components'. The AMWA suites enforce it identically for all three -- "
        "BCP0060101Test.py:104 (JPEG XS), BCP0060201Test.py:146 (H.264) and "
        "BCP0060301Test.py:145 (H.265) each FAIL with \"Flow {} MUST indicate "
        "the color (sub-)sampling using the 'components' attribute\". Below "
        "v1.3 they only warn, and this registry serves v1.3. So the descriptor "
        "is not stricter than the specification -- it is stricter than the one "
        "schema file, which is where the difference has to be recorded because "
        "a BCP requirement has no schema of its own to compare against."
    ),
    ("node.json", "UNMODELLED:hostname"): (
        "node.json defines an optional 'hostname', described by the schema "
        "itself as deprecated. Not modelled. A body carrying it is still "
        "accepted -- NNode is not sealed -- and still returned verbatim."
    ),
    ("flow_json_data.json", "UNMODELLED:event_type"): (
        "flow_json_data.json defines an optional 'event_type'. Not modelled, "
        "accepted, and returned verbatim, as above."
    ),
}


def _resolve(name: str, seen: frozenset[str] = frozenset()) -> tuple[set[str], set[str]]:
    """Flatten a schema's ``allOf``/``$ref`` chain to (required, properties).

    The IS-04 schemas are built by composition -- ``flow_video_raw.json`` is
    ``flow_video.json`` is ``flow_core.json`` is ``resource_core.json`` -- so
    nothing meaningful is visible without following the chain to the bottom.
    ``seen`` guards against a cycle rather than expecting one.
    """
    if name in seen:
        return set(), set()
    seen = seen | {name}

    required: set[str] = set()
    properties: set[str] = set()

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        reference = node.get("$ref")
        if isinstance(reference, str):
            inherited_required, inherited_properties = _resolve(reference, seen)
            required.update(inherited_required)
            properties.update(inherited_properties)
            return
        required.update(node.get("required", []))
        properties.update(node.get("properties", {}))
        for branch in node.get("allOf", []):
            walk(branch)

    walk(json.loads((SCHEMAS / name).read_text()))
    return required, properties


def _descriptors() -> dict[str, Any]:
    """Every ``TypeDesc`` in the model, by name.

    Collected by walking the package rather than from a manifest, so a new
    definitions module is covered the moment it exists.
    """
    found: dict[str, Any] = {}
    for module in pkgutil.iter_modules(definitions.__path__):
        loaded = importlib.import_module(f"{definitions.__name__}.{module.name}")
        for value in vars(loaded).values():
            if type(value).__name__ == "TypeDesc":
                found[value.name] = value
    return found


def _wire_members(name: str, descs: dict[str, Any], seen: frozenset[str] = frozenset()) -> list[Any]:
    """The members of a type that appear in JSON, embedded ones flattened.

    An embedded member has no key of its own -- its type's members are written
    into the parent's object -- which is exactly how the schemas compose too, so
    flattening is what makes the two comparable. Members with ``json_key="-"``
    are internal (pointers, back-references) and are not on the wire at all.
    """
    if name in seen or name not in descs:
        return []
    seen = seen | {name}

    members: list[Any] = []
    for member in descs[name].members:
        if getattr(member, "embedded", False):
            members.extend(_wire_members(member.type_name, descs, seen))
        elif member.json_key and member.json_key != "-":
            members.append(member)
    return members


def _divergences() -> dict[tuple[str, str], None]:
    """Compare every mapped schema with its descriptor."""
    descs = _descriptors()
    found: dict[tuple[str, str], None] = {}

    for key, type_name in SCHEMA_TO_TYPE.items():
        schema_name = key.split("#")[0]
        assert type_name in descs, f"{key} maps to {type_name}, which no longer exists"

        schema_required, schema_properties = _resolve(schema_name)
        members = _wire_members(type_name, descs)
        model_required = {
            m.json_key for m in members if not getattr(m, "optional", False)
        }
        model_all = {m.json_key for m in members}

        for member in sorted(model_required - schema_required):
            found[key, f"STRICTER:{member}"] = None
        for member in sorted(schema_required - model_required):
            found[key, f"LAXER:{member}"] = None
        for member in sorted(schema_properties - model_all):
            found[key, f"UNMODELLED:{member}"] = None

    return found


def test_the_model_diverges_from_the_schemas_only_where_recorded() -> None:
    """The inventory is exact in both directions.

    A *new* divergence means the model drifted or a newer schema was vendored.
    A *disappeared* one means it was fixed, and the entry should go with the
    fix rather than linger and make the next reader think it is still true.
    """
    found = set(_divergences())
    known = set(KNOWN_DIVERGENCES)

    appeared = sorted(found - known)
    resolved = sorted(known - found)

    report = []
    if appeared:
        report.append(
            "NEW divergences from AMWA IS-04 v1.3.3 -- add them to "
            "KNOWN_DIVERGENCES with the reason, or fix the descriptor:\n    "
            + "\n    ".join(f"{s}: {w}" for s, w in appeared),
        )
    if resolved:
        report.append(
            "These divergences no longer occur; remove them from "
            "KNOWN_DIVERGENCES:\n    "
            + "\n    ".join(f"{s}: {w}" for s, w in resolved),
        )
    assert not report, "\n\n".join(report)


def test_the_comparison_is_actually_looking_at_something() -> None:
    """Guard the guard.

    Every check above is a set difference, and a set difference over two empty
    sets is empty. If ``$ref`` resolution broke, or ``TypeDesc`` were renamed,
    or ``json_key`` changed spelling, this file would go quietly green while
    comparing nothing at all.
    """
    descs = _descriptors()
    assert len(descs) > 250, f"only {len(descs)} descriptors found"

    # A schema deep in the composition chain, to prove $ref following works.
    required, properties = _resolve("flow_video_raw.json")
    assert {"id", "version", "label"} <= required, (
        "resource_core.json's members did not come through the $ref chain"
    )
    assert {"components", "frame_width", "grain_rate"} <= properties

    # And the model side, to prove embedded flattening works.
    members = _wire_members("NFlowVideoRaw", descs)
    keys = {m.json_key for m in members}
    assert {"id", "version", "source_id", "components"} <= keys, (
        "embedded members were not flattened into NFlowVideoRaw"
    )
    assert "-" not in keys, "internal members leaked into the wire comparison"


def test_every_known_divergence_explains_itself() -> None:
    """An inventory without reasons decays into a list nobody dares change."""
    for key, reason in KNOWN_DIVERGENCES.items():
        assert len(reason) > 80, f"{key} needs a real explanation, not {reason!r}"


def test_a_coded_video_flow_must_carry_components() -> None:
    """The one divergence that changes what the registry accepts -- on purpose.

    This is not a defect being tolerated. BCP-006-01, -02 and -03 each require
    ``components`` on a coded video Flow at IS-04 v1.3, and the AMWA suites fail
    a Node that omits it; the core ``flow_video_coded.json`` says nothing about
    it because the requirement lives a layer up. Enforcing it here is what makes
    this registry agree with the test suites its Nodes are measured by.

    Kept as a live demonstration rather than prose so that relaxing the
    descriptor -- which would be a BCP-006 conformance regression, not a
    liberalisation -- fails here and takes the inventory entry with it.
    """
    from nmos.registry.decode import decode_resource
    from nmos.registry.tests._fixtures import make_flow
    from nmos.registry.types import ResourceType

    coded = make_flow(media_type="video/H264", bit_rate=25_000)
    del coded["components"]

    # Conformant to the core schema alone, and not to BCP-006. That gap is the
    # whole point of the case: the schema cannot express the requirement.
    schema_required, _ = _resolve("flow_video_coded.json")
    assert "components" not in schema_required
    assert schema_required <= set(coded), (
        "the fixture no longer satisfies flow_video_coded.json, so this proves "
        "nothing about the descriptor"
    )

    try:
        decode_resource(ResourceType.FLOW, coded)
    except Exception as exc:  # noqa: BLE001 - the message is the assertion
        assert "Components" in str(exc), f"rejected, but not for components: {exc}"
    else:
        raise AssertionError(
            "the registry now accepts a coded video Flow without components; "
            "remove the flow_video_coded.json entry from KNOWN_DIVERGENCES",
        )
