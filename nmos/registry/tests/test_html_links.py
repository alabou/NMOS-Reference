# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the browsable HTML rendering of the registry APIs.

Both APIs answer ``Accept: text/html`` with a page a browser can navigate by
clicking. Two things have to hold for that to be useful, and both were
initially broken:

* Every entry of every index must be a link. A partially-linked index is worse
  than an unlinked one, because the reader cannot tell whether an unlinked
  entry is a dead end or just unstyled.
* Every link must resolve. A cross-reference such as a Sender's ``flow_id``
  has to point into ``/flows/``; pointing it at ``/senders/<flow id>`` yields
  a confident-looking link that 404s.
"""

from __future__ import annotations

import re

import pytest

from nmos.registry import (
    InterfaceSecurity,
    Registry,
    create_query_app,
    create_registration_app,
)
from nmos.registry.decode import decode_resource
from nmos.registry.handlers_query import BASE_PATH as QUERY_BASE
from nmos.registry.handlers_registration import BASE_PATH as REG_BASE
from nmos.registry.store import RegistryStore
from nmos.registry.subscriptions import SubscriptionManager
from nmos.registry.tests._fixtures import (
    DEVICE_ID,
    FLOW_ID,
    NODE_ID,
    SENDER_ID,
    SOURCE_ID,
    make_device,
    make_flow,
    make_node,
    make_receiver,
    make_sender,
    make_source,
)
from nmos.registry.types import ResourceType

HTML = {"Accept": "text/html"}


def build_registry() -> Registry:
    registry = Registry(
        RegistryStore(), query_id="8c4d1e70-6b3a-4f52-9d81-2e7c5a0b3f14",
    )
    registry.attach_subscriptions(SubscriptionManager(registry))
    return registry


def seed_tree(registry: Registry) -> None:
    for resource_type, raw in (
        (ResourceType.NODE, make_node()),
        (ResourceType.DEVICE, make_device()),
        (ResourceType.SOURCE, make_source()),
        (ResourceType.FLOW, make_flow()),
        (ResourceType.SENDER, make_sender()),
        (ResourceType.RECEIVER, make_receiver()),
    ):
        typed = decode_resource(resource_type, raw)
        assert registry.register(resource_type, dict(raw), typed).ok


@pytest.fixture
def registry() -> Registry:
    return build_registry()


@pytest.fixture
async def query(aiohttp_client, registry: Registry):  # type: ignore[no-untyped-def]
    return await aiohttp_client(
        create_query_app(registry, InterfaceSecurity(), ws_port=8448),
    )


@pytest.fixture
async def registration(aiohttp_client, registry: Registry):  # type: ignore[no-untyped-def]
    return await aiohttp_client(
        create_registration_app(registry, InterfaceSecurity()),
    )


def entries(body: str) -> list[tuple[str, str | None]]:
    """Extract ``(value, href|None)`` for every array entry on the page."""
    found: list[tuple[str, str | None]] = []
    for item in re.finditer(r"<li>(.*?)</li>", body, re.S):
        fragment = item.group(1)
        value = re.search(r'<span class="string">&quot;(.*?)&quot;', fragment)
        if value is None:
            continue
        href = re.search(r'<a href="([^"]*)"', fragment)
        found.append((value.group(1), href.group(1) if href else None))
    return found


def named_links(body: str) -> dict[str, str]:
    """Map ``field name -> href`` for every linked object member."""
    return {
        name: href
        for name, href in re.findall(
            r'<span class="name">&quot;(\w+)&quot;</span>: '
            r'<span class="value"><a href="([^"]*)"',
            body,
        )
    }


# ---------------------------------------------------------------------------
# Index pages
# ---------------------------------------------------------------------------

class TestIndexes:
    async def test_query_base_links_every_collection(self, query) -> None:  # type: ignore[no-untyped-def]
        """All seven entries must link, ``nodes/`` and ``subscriptions/`` too.

        Those two were plain text while the other five linked, because the
        shared renderer's segment allowlist knew the Node API's singular
        ``node`` but neither of the Query API's collection names.
        """
        response = await query.get(QUERY_BASE, headers=HTML)
        listed = entries(await response.text())

        assert len(listed) == 7
        for value, href in listed:
            assert href is not None, f"{value} is not a link"
            assert href == f"{QUERY_BASE}/{value}"

    async def test_query_discovery_ladder_links(self, query) -> None:  # type: ignore[no-untyped-def]
        for path, expected in (
            ("/x-nmos", "/x-nmos/query/"),
            ("/x-nmos/query", "/x-nmos/query/v1.3/"),
        ):
            response = await query.get(path, headers=HTML)
            listed = entries(await response.text())
            assert listed and listed[0][1] == expected, path

    async def test_registration_base_entries_are_not_linked(
        self, registration,
    ) -> None:  # type: ignore[no-untyped-def]
        """``resource/`` and ``health/`` are listed but not browsable.

        ``registrationapi-base.json`` mandates both entries, yet neither
        answers a GET: ``/resource`` is POST-and-OPTIONS only (405) and the
        health resource is ``/health/nodes/{id}`` (so ``/health`` is 404).
        Linking them would offer two clicks that cannot work, so they render
        as plain text -- an accurate index beats a complete-looking one.
        """
        response = await registration.get(REG_BASE, headers=HTML)
        listed = entries(await response.text())

        assert [value for value, _ in listed] == ["resource/", "health/"]
        for value, href in listed:
            assert href is None, f"{value} links to {href}, which is not GETtable"

    async def test_registration_version_ladder_links(
        self, registration,
    ) -> None:  # type: ignore[no-untyped-def]
        """The ladder down to the base IS browsable, so it stays linked."""
        for path, expected in (
            ("/x-nmos", "/x-nmos/registration/"),
            ("/x-nmos/registration", "/x-nmos/registration/v1.3/"),
        ):
            response = await registration.get(path, headers=HTML)
            listed = entries(await response.text())
            assert listed and listed[0][1] == expected, path


# ---------------------------------------------------------------------------
# Cross-references
# ---------------------------------------------------------------------------

class TestCrossReferences:
    async def test_sender_references_resolve_to_their_collections(
        self, query, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """``flow_id`` and ``device_id`` must not link into ``/senders/``.

        The generic renderer can only link a UUID into the collection being
        browsed, which makes every cross-reference a 404. This is what the
        per-field resolver exists to fix.
        """
        seed_tree(registry)
        response = await query.get(f"{QUERY_BASE}/senders/{SENDER_ID}", headers=HTML)
        links = named_links(await response.text())

        assert links["id"] == f"{QUERY_BASE}/senders/{SENDER_ID}"
        assert links["flow_id"] == f"{QUERY_BASE}/flows/{FLOW_ID}"
        assert links["device_id"] == f"{QUERY_BASE}/devices/{DEVICE_ID}"

    async def test_device_links_up_to_its_node(self, query, registry) -> None:  # type: ignore[no-untyped-def]
        seed_tree(registry)
        response = await query.get(f"{QUERY_BASE}/devices/{DEVICE_ID}", headers=HTML)
        links = named_links(await response.text())
        assert links["node_id"] == f"{QUERY_BASE}/nodes/{NODE_ID}"

    async def test_flow_links_to_source_and_device(self, query, registry) -> None:  # type: ignore[no-untyped-def]
        seed_tree(registry)
        response = await query.get(f"{QUERY_BASE}/flows/{FLOW_ID}", headers=HTML)
        links = named_links(await response.text())
        assert links["source_id"] == f"{QUERY_BASE}/sources/{SOURCE_ID}"
        assert links["device_id"] == f"{QUERY_BASE}/devices/{DEVICE_ID}"

    async def test_receiver_subscription_sender_id_resolves(
        self, query, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """A nested reference resolves by its own key, not its parent's."""
        seed_tree(registry)
        typed = decode_resource(
            ResourceType.RECEIVER,
            make_receiver(subscription={"sender_id": SENDER_ID, "active": True}),
        )
        registry.register(
            ResourceType.RECEIVER,
            make_receiver(subscription={"sender_id": SENDER_ID, "active": True}),
            typed,
        )

        response = await query.get(f"{QUERY_BASE}/receivers", headers=HTML)
        links = named_links(await response.text())
        assert links["sender_id"] == f"{QUERY_BASE}/senders/{SENDER_ID}"

    async def test_device_sender_arrays_link_elementwise(
        self, query, registry,
    ) -> None:
        """Array elements inherit the array's key.

        The Device ``senders`` / ``receivers`` arrays are deprecated but real
        Nodes still send them, so their UUIDs are linked rather than left as
        dead strings.
        """
        seed_tree(registry)
        typed = decode_resource(
            ResourceType.DEVICE, make_device(senders=[SENDER_ID], receivers=[]),
        )
        registry.register(
            ResourceType.DEVICE,
            make_device(senders=[SENDER_ID], receivers=[]),
            typed,
        )

        response = await query.get(f"{QUERY_BASE}/devices/{DEVICE_ID}", headers=HTML)
        body = await response.text()
        assert f'<a href="{QUERY_BASE}/senders/{SENDER_ID}"' in body

    async def test_collection_listing_links_each_resource(
        self, query, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """Browsing a collection must let you click through to a member."""
        seed_tree(registry)
        response = await query.get(f"{QUERY_BASE}/senders", headers=HTML)
        links = named_links(await response.text())
        assert links["id"] == f"{QUERY_BASE}/senders/{SENDER_ID}"

    async def test_every_internal_link_resolves(self, query, registry) -> None:  # type: ignore[no-untyped-def]
        """Follow every generated link and require a 200.

        The strongest form of the check: a link that renders but 404s is the
        exact failure this whole mechanism exists to prevent.
        """
        seed_tree(registry)
        for resource_type in ResourceType:
            listing = await query.get(
                f"{QUERY_BASE}/{resource_type.plural}", headers=HTML,
            )
            body = await listing.text()
            targets = set(re.findall(r'<a href="(/x-nmos[^"]*)"', body))
            assert targets, resource_type.plural
            for target in targets:
                followed = await query.get(target)
                assert followed.status == 200, f"{target} -> {followed.status}"

    async def test_registration_debug_get_links_into_the_query_api(
        self, registration, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """The Registration API has no collections to browse.

        It is write-only apart from the debug reads, so its cross-references
        point at the Query API rather than at dead ends under
        ``/registration/``.
        """
        seed_tree(registry)
        response = await registration.get(
            f"{REG_BASE}/resource/senders/{SENDER_ID}", headers=HTML,
        )
        links = named_links(await response.text())
        assert links["flow_id"] == f"{QUERY_BASE}/flows/{FLOW_ID}"
        assert links["device_id"] == f"{QUERY_BASE}/devices/{DEVICE_ID}"


# ---------------------------------------------------------------------------
# JSON responses are unaffected
# ---------------------------------------------------------------------------

class TestJsonUnaffected:
    async def test_no_html_without_the_accept_header(
        self, query, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """Link rendering is an HTML-only concern; JSON must stay verbatim."""
        seed_tree(registry)
        response = await query.get(f"{QUERY_BASE}/senders/{SENDER_ID}")
        assert response.headers["Content-Type"] == "application/json"

        body = await response.json()
        assert body["flow_id"] == FLOW_ID
        assert "<a href" not in str(body)
