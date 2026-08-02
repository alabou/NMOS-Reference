# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Hyperlink resolution for the browsable HTML rendering of the APIs.

Both registry APIs answer ``Accept: text/html`` with a navigable page, so a
browser can walk the whole registry by clicking. Making that work needs one
piece of knowledge the generic renderer does not have: which collection a
given reference attribute points at.

Without it, every UUID in a document can only be linked into the collection
currently being browsed. A Sender's ``flow_id`` would then link to
``/senders/<flow id>`` and 404 — the page looks indexed but the links are
wrong, which is worse than no links. ``device_id``, ``source_id`` and the rest
have the same problem.

The Node API solves this inside the JSON engine
(``nmos/api/handlers_node.py::_make_link_resolver``), but that path only
applies while encoding generated types. The registry serves each resource as
the raw JSON it was registered with — deliberately, so vendor extensions
survive — so it renders plain dicts and needs the equivalent mapping here.
"""

from __future__ import annotations

from nmos.api.response import LinkResolver
from nmos.registry.types import ResourceType

#: Reference attribute -> the collection its UUID lives in.
#:
#: ``id`` and ``parents`` map to the empty string, meaning "the collection
#: being browsed": a resource's own id addresses itself, and IS-04 constrains
#: ``parents`` to the same type as the resource holding it (a Flow's parents
#: are Flows, a Source's are Sources).
#:
#: ``senders`` / ``receivers`` are the deprecated Device arrays. They are
#: still linked because a registry will be handed them by real Nodes, and a
#: dead link is worse than a plain string.
_FIELD_TO_COLLECTION: dict[str, str] = {
    "id": "",
    "parents": "",
    "node_id": ResourceType.NODE.plural,
    "device_id": ResourceType.DEVICE.plural,
    "source_id": ResourceType.SOURCE.plural,
    "flow_id": ResourceType.FLOW.plural,
    "sender_id": ResourceType.SENDER.plural,
    "receiver_id": ResourceType.RECEIVER.plural,
    "senders": ResourceType.SENDER.plural,
    "receivers": ResourceType.RECEIVER.plural,
}


def make_link_resolver(request_path: str, api_base: str) -> LinkResolver:
    """Build a resolver for a document served at ``request_path``.

    Args:
        request_path: Path of the request being rendered. Used to work out the
            collection currently being browsed, which is where ``id`` and
            ``parents`` links point.
        api_base: Versioned API root, e.g. ``/x-nmos/query/v1.3``. Named
            references are resolved relative to this rather than to the
            request path, so a cross-reference from inside
            ``/senders/{id}`` still lands in ``/flows/``.

    Returns:
        A ``(field_name, value) -> href | None`` callback. It returns None for
        anything it does not recognise, which leaves the generic rules in
        ``nmos/api/response.py`` to decide.
    """
    trimmed = request_path.rstrip("/")
    segments = [segment for segment in trimmed.split("/") if segment]

    # A single-resource path ends in the resource's own id; the collection is
    # the path with that id removed. A collection path is already the
    # collection.
    if segments and _looks_like_uuid(segments[-1]):
        collection_base = "/" + "/".join(segments[:-1]) + "/"
    else:
        collection_base = trimmed + "/"

    base = api_base.rstrip("/")

    def resolve(field_name: str | None, value: str) -> str | None:
        if field_name is None or not _looks_like_uuid(value):
            return None
        collection = _FIELD_TO_COLLECTION.get(field_name)
        if collection is None:
            # Vendor extensions namespace their keys, e.g. Matrox's
            # ``urn:x-matrox:receiver_id``. The suffix after the last colon
            # carries the same meaning as the plain attribute, so it is worth
            # one more lookup before giving up.
            _, _, suffix = field_name.rpartition(":")
            collection = _FIELD_TO_COLLECTION.get(suffix)
        if collection is None:
            # Deliberately unresolved. The renderer treats this resolver as
            # authoritative for UUIDs, so returning None leaves the value as
            # plain text instead of guessing a collection. A BCP-008 monitor
            # Source's ``monitor_sibling_id`` is the motivating case: it names
            # a Sender or a Receiver depending on the sibling ``monitor_type``
            # field, which a per-field resolver cannot see.
            return None
        if collection == "":
            return collection_base + value
        return f"{base}/{collection}/{value}"

    return resolve


def _looks_like_uuid(value: str) -> bool:
    """Cheap UUID shape test.

    Deliberately permissive about the version and variant nibbles that the
    RAML pattern pins down: this only decides whether to render a hyperlink,
    and refusing to link a resource a Node actually registered would be the
    worse error.
    """
    if len(value) != 36:
        return False
    if value[8] != "-" or value[13] != "-" or value[18] != "-" or value[23] != "-":
        return False
    return all(
        character in "0123456789abcdefABCDEF-" for character in value
    )
