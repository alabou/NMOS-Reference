# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Decoding and validation of Registration API request bodies.

Decoding *is* validation here. ``APIs.md:22`` says a registry SHOULD validate
registered resources against the JSON schemas and SHOULD answer 400 when they
do not conform; rather than carry a second JSON-schema validator alongside the
type layer, this package obtains both at once by decoding into the generated
NMOS types. Their ``decode()`` runs every ``assert_valid()`` check — required
members present, enums recognised, ids well formed, polymorphic discriminators
resolvable — and raises when they fail.

That has a consequence worth stating plainly: the registry only accepts what
the generated types model. It is stricter than a bare JSON-schema check would
be for resource shapes the types cover, and it is the reason the raw JSON is
retained separately (see ``RegisteredResource.raw``) — validation is done on
the typed view, but what gets *served* is what was received, so attributes the
types do not model survive the round trip instead of being silently rewritten.
"""

from __future__ import annotations

from typing import Any, Callable, NoReturn

from nmos.errors import NmosError
from nmos.json.engine import JsonEngine
from nmos.json.spans import JsonSpanError, member_spans
from nmos.registry.types import Body, ResourceType


class DecodeFailure(Exception):
    """A request body did not decode into a valid NMOS resource.

    Carries a human-readable reason destined for the ``debug`` member of the
    NMOS error body, so the Node operator can see *why* the registration was
    refused — ``Behaviour - Registration.md:106`` explicitly points at those
    error responses as the debugging aid for 400s.
    """


# Lazily built map of resource type -> the generated value class to decode
# into. Built on first use rather than at import time because the generated
# modules import large parts of the type graph, and the registry's CLI should
# not pay for that before it knows it is going to serve anything.
_VALUE_FACTORIES: dict[ResourceType, Callable[[], Any]] | None = None


def _value_factories() -> dict[ResourceType, Callable[[], Any]]:
    global _VALUE_FACTORIES
    if _VALUE_FACTORIES is None:
        from nmos.types.generated.ndevice import NDeviceValue
        from nmos.types.generated.nflow import NFlowValue
        from nmos.types.generated.nnode import NNodeValue
        from nmos.types.generated.nreceiver import NReceiverValue
        from nmos.types.generated.nsender import NSenderValue
        from nmos.types.generated.nsource import NSourceValue

        _VALUE_FACTORIES = {
            ResourceType.NODE: NNodeValue,
            ResourceType.DEVICE: NDeviceValue,
            ResourceType.SOURCE: NSourceValue,
            ResourceType.FLOW: NFlowValue,
            ResourceType.SENDER: NSenderValue,
            ResourceType.RECEIVER: NReceiverValue,
        }
    return _VALUE_FACTORIES


def decode_resource(resource_type: ResourceType, raw: Any) -> Any:
    """Decode one resource body into its generated value type.

    Args:
        resource_type: Which of the six IS-04 types ``raw`` claims to be.
        raw: The parsed JSON body.

    Returns:
        The decoded value object (``NNodeValue``, ``NSourceValue``, …). For
        the polymorphic types — Source, Flow, Receiver — this is the
        polymorphic wrapper; ``.get()`` yields the concrete variant chosen by
        the discriminator.

    Raises:
        DecodeFailure: The body is not an object, is missing a required
            member, carries an unrecognised enum, or (for a polymorphic type)
            matches no variant.
    """
    if not isinstance(raw, dict):
        _fail(f"expected a JSON object for {resource_type.value}")

    value = _value_factories()[resource_type]()
    try:
        value.decode(JsonEngine(), raw)
    except NmosError as exc:
        # NmosError covers the whole decode failure surface: InvalidData for
        # structural problems, InvalidObject for missing required members and
        # failed assertions, NotMatching when no polymorphic variant applies.
        raise DecodeFailure(
            f"{resource_type.value} failed validation: {exc.msg or exc}",
        ) from exc
    except (TypeError, ValueError, KeyError) as exc:
        # A malformed body can reach the type layer in shapes its own error
        # types do not anticipate (a string where an object was expected, and
        # similar). Those must still be a 400, never a 500.
        raise DecodeFailure(
            f"{resource_type.value} failed validation: {exc}",
        ) from exc
    return value


def decode_post_envelope(source: str) -> tuple[ResourceType, Body]:
    """Validate a ``POST /resource`` request and return what the registry keeps.

    The request is the ``{"type": <singular>, "data": {...}}`` envelope of
    ``registrationapi-resource-post-request.json``.

    Takes the request **text**, not a parsed object, because the ``data`` value
    has to be sliced out of it verbatim: parsing normalises number spelling and
    string escaping irreversibly, and the registry promises to serve back what
    the Node registered rather than a re-rendering of it.

    This is the **only** place a resource is decoded against its generated
    type, and that decode is the ``APIs.md:22`` schema validation. The decoded
    object is not returned, because nothing downstream reads it — the Query
    API, the basic-query filters and the WebSocket grains all serve
    ``RegisteredResource.raw``.

    Returns:
        ``(resource_type, body)`` — the type named by the envelope, and the
        body carrying both the untouched source text and its parsed form.

    Raises:
        DecodeFailure: The envelope is malformed, names an unknown type, or
            its ``data`` does not validate.
    """
    # One pass, not two. ``raw_decode`` builds each member's value in order to
    # find where it ends, so the scan hands back the decoded values *and* the
    # spans together; parsing with ``json.loads`` and then locating the span
    # separately would parse every resource body twice.
    try:
        members = member_spans(source)
    except JsonSpanError as exc:
        # Distinguish "not JSON at all" from "valid JSON, wrong shape", so the
        # 400 says which. Failure path only, so the extra parse costs nothing
        # on any request that succeeds.
        try:
            JsonEngine.parse_any(source)
        except (ValueError, TypeError):
            _fail(f"invalid JSON body: {exc}")
        _fail("expected a JSON object")

    type_entry = members.get("type")
    type_name = type_entry[1] if type_entry is not None else None
    if not isinstance(type_name, str):
        _fail("missing or non-string 'type' in registration envelope")

    resource_type = ResourceType.from_singular(type_name)
    if resource_type is None:
        permitted = ", ".join(rt.value for rt in ResourceType)
        _fail(f"unknown resource type {type_name!r}; expected one of: {permitted}")

    data_entry = members.get("data")
    if data_entry is None or not isinstance(data_entry[1], dict):
        _fail("missing or non-object 'data' in registration envelope")
    data_text, data = data_entry

    # Called purely for its validation side effect. The decoded object is
    # discarded: nothing downstream of the Registration API reads it, and
    # retaining it cost roughly 3x the memory of the resource itself. This
    # call IS the ``APIs.md:22`` schema check, so it must stay.
    decode_resource(resource_type, data)

    # The span, not a re-encoding -- and it came from the same pass that
    # produced ``data``, so no second parse was needed to obtain it.
    return resource_type, Body(data_text, data)


def _fail(reason: str) -> NoReturn:
    """Raise a DecodeFailure.

    Declared ``NoReturn`` so the type checker knows each guard clause above
    terminates control flow, letting them read as one line each without a
    redundant ``raise`` at every call site.
    """
    raise DecodeFailure(reason)
