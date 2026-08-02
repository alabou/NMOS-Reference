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
from nmos.registry.types import ResourceType


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


def decode_post_envelope(body: Any) -> tuple[ResourceType, dict[str, Any], Any]:
    """Decode a ``POST /resource`` body.

    The body is the ``{"type": <singular>, "data": {...}}`` envelope of
    ``registrationapi-resource-post-request.json``.

    Returns:
        ``(resource_type, raw_data, typed_value)`` — the type named by the
        envelope, the untouched ``data`` object to be stored and served, and
        the decoded value object that proves it is valid.

    Raises:
        DecodeFailure: The envelope is malformed, names an unknown type, or
            its ``data`` does not validate.
    """
    if not isinstance(body, dict):
        _fail("expected a JSON object")

    type_name = body.get("type")
    if not isinstance(type_name, str):
        _fail("missing or non-string 'type' in registration envelope")

    resource_type = ResourceType.from_singular(type_name)
    if resource_type is None:
        permitted = ", ".join(rt.value for rt in ResourceType)
        _fail(f"unknown resource type {type_name!r}; expected one of: {permitted}")

    data = body.get("data")
    if not isinstance(data, dict):
        _fail("missing or non-object 'data' in registration envelope")

    typed = decode_resource(resource_type, data)
    return resource_type, data, typed


def _fail(reason: str) -> NoReturn:
    """Raise a DecodeFailure.

    Declared ``NoReturn`` so the type checker knows each guard clause above
    terminates control flow, letting them read as one line each without a
    redundant ``raise`` at every call site.
    """
    raise DecodeFailure(reason)
