# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The etcd key layout and value envelope.

Layout
------
::

    <ns>/meta/config
    <ns>/ids/<resource-id>
    <ns>/nodes/<node-id>/self
    <ns>/nodes/<node-id>/devices/<device-id>/self
    <ns>/nodes/<node-id>/devices/<device-id>/<plural>/<resource-id>

The shape is chosen so that **a Node's entire subtree is one prefix**. That
single property does most of the work in this design:

* deleting a Node is one ranged delete, not a walk;
* every key belonging to a Node hangs off that Node's lease, so expiry collects
  the subtree atomically on every member at once;
* two Nodes registering concurrently touch disjoint prefixes, so unrelated
  registrations never contend -- which is why there is no global generation key
  and no hot key anywhere in the mutation path.

``<ns>/ids/<resource-id>`` is the one deliberate exception to the tree. It is a
flat claim used to detect the cross-type id collision of
``Behaviour - Registration.md:101``, which cannot be answered from the tree
because the tree is keyed by *where a resource is*, and the question is whether
an id exists *anywhere*. Keeping it flat is what avoids a global index.

Envelope
--------
Values are JSON carrying the resource exactly as the Node sent it, plus the
registry-assigned paging cursors and a schema version. The raw form is stored
verbatim for the same reason ``RegisteredResource.raw`` exists: the Query API
serves what was registered, byte for byte, including attributes the generated
types do not model.

The cursors are in the envelope because they must be *authoritative*. If each
member allocated its own, the same resource would page differently on different
members. Storing them makes the value the single source of truth for ordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from nmos.json.engine import JsonEngine
from nmos.json.spans import JsonSpanError, member_spans
from nmos.registry.types import Body, ResourceType, TaiCursor

# Bumped only for a change that a previous version could not read. The preload
# refuses an envelope from the future rather than guessing, because a registry
# silently ignoring fields it does not understand is how two members end up
# serving different content for the same resource.
ENVELOPE_VERSION = 1

_META = "meta"
_CONFIG = "config"
_IDS = "ids"
_NODES = "nodes"
_DEVICES = "devices"
_SELF = "self"


class KeyError_(Exception):
    """A key or envelope did not have the expected shape."""


@dataclass(frozen=True)
class Namespace:
    """The configured key prefix, and every key derived from it.

    All key construction goes through here rather than being formatted at call
    sites: a single mismatched separator between the writer and the watcher
    would produce a resource that is stored but never materialised, which is
    both silent and extremely hard to see.
    """

    prefix: str

    def __post_init__(self) -> None:
        if not self.prefix.startswith("/"):
            raise KeyError_(f"namespace must start with '/': {self.prefix!r}")
        if self.prefix.endswith("/"):
            raise KeyError_(
                f"namespace must not end with '/': {self.prefix!r}",
            )

    # -- roots ----------------------------------------------------------

    @property
    def root(self) -> bytes:
        """Everything the watch covers."""
        return f"{self.prefix}/".encode()

    @property
    def meta_config(self) -> bytes:
        return f"{self.prefix}/{_META}/{_CONFIG}".encode()

    @property
    def ids_root(self) -> bytes:
        return f"{self.prefix}/{_IDS}/".encode()

    @property
    def nodes_root(self) -> bytes:
        return f"{self.prefix}/{_NODES}/".encode()

    # -- resource keys --------------------------------------------------

    def id_claim(self, resource_id: str) -> bytes:
        return f"{self.prefix}/{_IDS}/{resource_id}".encode()

    def node_subtree(self, node_id: str) -> bytes:
        """Prefix covering a Node and everything under it.

        The prefix a Node delete ranges over, and the prefix every key on that
        Node's lease shares.
        """
        return f"{self.prefix}/{_NODES}/{node_id}/".encode()

    def node(self, node_id: str) -> bytes:
        return f"{self.prefix}/{_NODES}/{node_id}/{_SELF}".encode()

    def device_subtree(self, node_id: str, device_id: str) -> bytes:
        return (
            f"{self.prefix}/{_NODES}/{node_id}/{_DEVICES}/{device_id}/"
        ).encode()

    def device(self, node_id: str, device_id: str) -> bytes:
        return (
            f"{self.prefix}/{_NODES}/{node_id}/{_DEVICES}/{device_id}/{_SELF}"
        ).encode()

    def child(
        self,
        resource_type: ResourceType,
        node_id: str,
        device_id: str,
        resource_id: str,
    ) -> bytes:
        """Key for a Source, Flow, Sender or Receiver."""
        if resource_type in (ResourceType.NODE, ResourceType.DEVICE):
            raise KeyError_(
                f"{resource_type.value} has its own key function",
            )
        return (
            f"{self.prefix}/{_NODES}/{node_id}/{_DEVICES}/{device_id}/"
            f"{resource_type.plural}/{resource_id}"
        ).encode()

    # -- parsing --------------------------------------------------------

    def parse(self, key: bytes) -> ParsedKey | None:
        """Decode a key back into what it identifies.

        Returns None for keys the Query view does not materialise -- the meta
        config and the id claims. They are not errors: the watch sees every key
        under the namespace, and these two are bookkeeping the local store has
        no representation for. Returning None rather than raising keeps the
        watch loop's ordinary path free of exception handling.
        """
        text = key.decode("utf-8", errors="replace")
        if not text.startswith(f"{self.prefix}/"):
            raise KeyError_(f"key outside namespace {self.prefix!r}: {text!r}")

        parts = text[len(self.prefix) + 1:].split("/")

        if parts[0] in (_META, _IDS):
            return None

        if parts[0] != _NODES:
            raise KeyError_(f"unrecognised key section {parts[0]!r}: {text!r}")

        # nodes/<id>/self
        if len(parts) == 3 and parts[2] == _SELF:
            return ParsedKey(
                resource_type=ResourceType.NODE,
                resource_id=parts[1],
                node_id=parts[1],
                device_id=None,
            )

        # nodes/<id>/devices/<id>/self
        if len(parts) == 5 and parts[2] == _DEVICES and parts[4] == _SELF:
            return ParsedKey(
                resource_type=ResourceType.DEVICE,
                resource_id=parts[3],
                node_id=parts[1],
                device_id=parts[3],
            )

        # nodes/<id>/devices/<id>/<plural>/<id>
        if len(parts) == 6 and parts[2] == _DEVICES:
            resource_type = ResourceType.from_plural(parts[4])
            if resource_type is None:
                raise KeyError_(
                    f"unknown collection {parts[4]!r} in key {text!r}",
                )
            return ParsedKey(
                resource_type=resource_type,
                resource_id=parts[5],
                node_id=parts[1],
                device_id=parts[3],
            )

        raise KeyError_(f"malformed resource key: {text!r}")


@dataclass(frozen=True)
class ParsedKey:
    """What a resource key identifies."""

    resource_type: ResourceType
    resource_id: str
    node_id: str
    device_id: str | None

    @property
    def is_node(self) -> bool:
        return self.resource_type is ResourceType.NODE

    @property
    def depth(self) -> int:
        """Tree depth, used to apply parents before children within a revision.

        Node 0, Device 1, everything else 2. Registration order is normative
        (``Behaviour - Registration.md:57-64``), and a revision that creates a
        Device and its Senders together has to be applied in that order or the
        store's referential-integrity check rejects the children.
        """
        if self.resource_type is ResourceType.NODE:
            return 0
        if self.resource_type is ResourceType.DEVICE:
            return 1
        return 2


@dataclass(frozen=True)
class Envelope:
    """The stored value for one resource."""

    version: int
    resource_type: ResourceType
    body: Body
    created: TaiCursor
    updated: TaiCursor
    health: int

    @property
    def raw(self) -> dict[str, Any]:
        """The body's parsed form, for the checks that need field access."""
        return self.body.data

    def encode(self) -> bytes:
        """Serialise for etcd.

        The metadata is encoded normally; the body is **spliced in as text**.
        Re-encoding it here would normalise the Node's spelling -- ``1e3`` to
        ``1000.0``, ``\\u00e9`` to ``é`` -- and then the member that accepted the
        registration would serve different bytes from every member that
        materialised it from storage. Splicing keeps all members byte-identical
        and costs nothing, since the text is what we already hold.

        Safe because ``Body.text`` is always a value some JSON parser has
        already accepted, so the result is well-formed by construction.
        """
        head = {
            "v": self.version,
            "type": self.resource_type.value,
            "created": str(self.created),
            "updated": str(self.updated),
            "health": self.health,
        }
        prefix = JsonEngine.dump_any(head)
        assert prefix.endswith("}")
        spliced: str = prefix[:-1] + ', "data": ' + self.body.text + "}"
        return spliced.encode("utf-8")

    @classmethod
    def decode(cls, value: bytes) -> Envelope:
        """Parse a stored value, or raise.

        Every failure here is fatal to the preload rather than survivable: a
        value that cannot be read is a resource whose existence this member
        cannot agree on, and continuing would mean serving a view that
        silently differs from its peers'.
        """
        # One pass yields both the decoded metadata and the body's exact span;
        # parsing and then locating the span separately would parse every
        # resource body twice on the watch path.
        try:
            text = value.decode("utf-8")
            members = member_spans(text)
        except UnicodeDecodeError as exc:
            raise KeyError_(f"envelope is not valid JSON: {exc}") from exc
        except JsonSpanError as exc:
            # Distinguish "not JSON at all" from "valid JSON, wrong shape".
            # Only reachable on the failure path, so the extra parse is free
            # in every case that matters.
            try:
                JsonEngine.parse_any(text)
            except (ValueError, TypeError):
                raise KeyError_(f"envelope is not valid JSON: {exc}") from exc
            raise KeyError_("envelope is not a JSON object") from exc

        document = {name: parsed for name, (_span, parsed) in members.items()}

        version = document.get("v")
        if not isinstance(version, int):
            raise KeyError_("envelope has no integer 'v'")
        if version > ENVELOPE_VERSION:
            raise KeyError_(
                f"envelope schema version {version} is newer than this "
                f"registry understands ({ENVELOPE_VERSION}). Upgrade this "
                f"member rather than letting it serve a partial view.",
            )

        type_name = document.get("type")
        resource_type = (
            ResourceType.from_singular(type_name)
            if isinstance(type_name, str) else None
        )
        if resource_type is None:
            raise KeyError_(f"envelope has unknown type {type_name!r}")

        data_entry = members.get("data")
        if data_entry is None or not isinstance(data_entry[1], dict):
            raise KeyError_("envelope has no 'data' object")
        # The TEXT the writer stored, not a re-encoding, so this member serves
        # the same bytes as the one that accepted the registration.
        body = Body(data_entry[0], cast("dict[str, Any]", data_entry[1]))

        created = _cursor(document, "created")
        updated = _cursor(document, "updated")

        health = document.get("health")
        if not isinstance(health, int):
            raise KeyError_("envelope has no integer 'health'")

        return cls(
            version=version,
            resource_type=resource_type,
            body=body,
            created=created,
            updated=updated,
            health=health,
        )


def _cursor(document: dict[str, Any], field: str) -> TaiCursor:
    value = document.get(field)
    if not isinstance(value, str):
        raise KeyError_(f"envelope has no string {field!r}")
    cursor = TaiCursor.parse(value)
    if cursor is None:
        raise KeyError_(f"envelope {field!r} is not '<sec>:<nsec>': {value!r}")
    return cursor
