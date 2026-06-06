# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""End-to-end tests for the controller app.

Uses ``aiohttp.test_utils`` (no real TLS). A helper logs in through
the admin login form, yielding a ``TestClient`` whose cookie jar
carries the session token for all subsequent requests. A dedicated
``TestAdminAuth`` class exercises the login / logout gate itself.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient

from nmos.controller import create_controller_app
from nmos.controller.api_client import RemoteCallResult, RemoteNodeClient
from nmos.controller.auth import (
    SESSION_MAX_AGE_SECONDS,
    issue_session_token,
    verify_session_token,
)
from nmos.controller.cache import ResourceCache
from nmos.controller.grouping import GROUP_HINT_TAG
from nmos.node import Node


ADMIN_PASSWORD = "test-admin-pw"
PREFIX = "/controller"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_device(
    did: str, serial: str = "SNX00001",
    conn_href: str = "https://remote/x-nmos/connection/v1.1",
    compat_href: str | None = "https://remote/x-nmos/streamcompatibility/v1.0",
    node_id: str = "node-SNX00001",
) -> dict[str, Any]:
    """Build an IS-04 device dict.

    Device ``controls`` carry per-device APIs (IS-05, IS-11).
    Per-NODE APIs like the reservation service live on the Node,
    not the Device — see ``_make_node_resource``.

    ``compat_href=None`` omits the ``stream-compat`` control entry
    entirely, simulating a device that doesn't implement IS-11. Used
    by the Constrain-button gating tests.
    """
    controls: list[dict[str, Any]] = [
        {"type": "urn:x-nmos:control:sr-ctrl/v1.1", "href": conn_href},
    ]
    if compat_href is not None:
        controls.append({
            "type": "urn:x-nmos:control:stream-compat/v1.0",
            "href": compat_href,
        })
    return {
        "id": did,
        "label": f"device-{serial}",
        "description": f"serial {serial}",
        "node_id": node_id,
        "controls": controls,
    }


def _make_node_resource(
    nid: str, serial: str = "SNX00001",
    exclusive_href: str | None = None,
    exclusive_authorization: bool = False,
) -> dict[str, Any]:
    """Build an IS-04 Node resource dict.

    ``exclusive_href`` — when non-None, advertise the Node
    Reservation service (``urn:x-matrox:service:exclusive/v1.0``)
    in the Node's ``services`` array. Used by Privacy-flow tests
    that exercise the Exclusivity toggle. The reservation service
    is per-NODE (not per-device) per the NMOS Node Reservation
    spec.

    ``exclusive_authorization`` — published on the service entry as
    the NMOS IS-04 ``authorization`` flag. Controls which header
    the controller uses for the session bearer
    (``Authorization`` when False, ``PEP-Exclusive-Authorization``
    when True) per the NMOS With Node Reservation spec.
    """
    node: dict[str, Any] = {
        "id": nid,
        "label": f"node-{serial}",
        "description": f"serial {serial}",
    }
    if exclusive_href is not None:
        node["services"] = [
            {"type": "urn:x-matrox:service:exclusive/v1.0",
             "href": exclusive_href,
             "authorization": exclusive_authorization},
        ]
    return node


def _make_sender(
    sid: str, device_id: str, hint: str = "RTP 0:VIDEO 0",
    active: bool = False,
    format: str = "urn:x-nmos:format:video",
    transport: str = "urn:x-nmos:transport:rtp",
) -> dict[str, Any]:
    # ``format`` + ``transport`` are top-level IS-04 sender fields the
    # compatibility check now reads (format/transport guard).
    return {
        "id": sid,
        "device_id": device_id,
        "label": f"sender-{sid[:4]}",
        "description": "",
        "format": format,
        "transport": transport,
        "tags": {GROUP_HINT_TAG: [hint]},
        "subscription": {"active": active, "receiver_id": None},
    }


def _make_receiver(
    rid: str, device_id: str, hint: str = "RTP 0:VIDEO 0",
    active: bool = False,
    format: str = "urn:x-nmos:format:video",
    transport: str = "urn:x-nmos:transport:rtp",
) -> dict[str, Any]:
    return {
        "id": rid,
        "device_id": device_id,
        "label": f"receiver-{rid[:4]}",
        "description": "",
        "format": format,
        "transport": transport,
        "tags": {GROUP_HINT_TAG: [hint]},
        "subscription": {"active": active, "sender_id": None},
    }


def _make_node() -> Node:
    node = Node()
    node.init(serial_number="CTRLTEST")
    return node


async def _seed_cache(cache: ResourceCache) -> None:
    # Nodes first — devices reference them via ``node_id``. Default
    # seed does NOT advertise the reservation service; tests that
    # exercise Exclusivity re-upsert the Node with a service URL.
    await cache.upsert("node", _make_node_resource("node-SNX00001", "SNX00001"))
    await cache.upsert("node", _make_node_resource("node-SNX00002", "SNX00002"))
    await cache.upsert(
        "device",
        _make_device("dev1", "SNX00001", node_id="node-SNX00001"),
    )
    await cache.upsert(
        "device",
        _make_device("dev2", "SNX00002", node_id="node-SNX00002"),
    )
    await cache.upsert(
        "sender", _make_sender("11111111-1111-1111-1111-111111111111", "dev1"),
    )
    await cache.upsert(
        "sender",
        _make_sender("22222222-2222-2222-2222-222222222222", "dev1",
                     hint="RTP 0:VIDEO 1"),
    )
    await cache.upsert(
        "sender", _make_sender("33333333-3333-3333-3333-333333333333", "dev2"),
    )
    await cache.upsert(
        "receiver", _make_receiver("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "dev1"),
    )
    await cache.upsert(
        "receiver", _make_receiver("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "dev2"),
    )


async def _log_in(client: TestClient) -> None:
    """Post valid credentials; the cookie jar then carries the session."""
    resp = await client.post(
        f"{PREFIX}/login",
        data={"password": ADMIN_PASSWORD, "next": f"{PREFIX}/"},
        allow_redirects=False,
    )
    assert resp.status == 302, f"login failed: {resp.status}"


@pytest.fixture
async def raw_client(aiohttp_client: Any) -> TestClient:
    """Client WITHOUT a logged-in session. Used by the auth-gate tests.

    ``get_sender_active_constraints`` is mocked by default to return
    an unconstrained response — the configure handlers probe this on
    every render, and we don't want each test to pay 10s of real
    DNS/connection timeouts against the fake control URLs. Tests that
    need a specific constrained/unconstrained shape override the
    mock themselves.
    """
    cache = ResourceCache()
    await _seed_cache(cache)
    remote = RemoteNodeClient()
    remote.get_sender_active_constraints = AsyncMock(  # type: ignore[method-assign]
        return_value=RemoteCallResult(status=200, body={"constraint_sets": []}),
    )
    # IS-05 transport-parameter constraints — fetched by the Privacy
    # panel at configure-page render. Default: empty per-leg
    # constraints (no PEP declared) so the privacy panel stays hidden
    # for tests that don't care. Override in tests that exercise PEP.
    # Shape: the IS-05 /constraints/ endpoint returns a per-leg array
    # DIRECTLY (not wrapped in ``transport_params``) — matches what
    # the Python node at
    # handlers_connection.py:handle_get_sender_constraints emits via
    # ``_encode_constraints_raw``.
    remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
        return_value=RemoteCallResult(status=200, body=[{}]),
    )
    remote.get_receiver_constraints = AsyncMock(  # type: ignore[method-assign]
        return_value=RemoteCallResult(status=200, body=[{}]),
    )
    node = _make_node()
    app = create_controller_app(
        node, cache=cache, remote_client=remote,
        admin_password=ADMIN_PASSWORD,
    )
    app["_test_remote_stub"] = remote
    app["_test_cache"] = cache
    return await aiohttp_client(app)


@pytest.fixture
async def controller_client(raw_client: TestClient) -> TestClient:
    """Client WITH a logged-in session. Default for page / API tests."""
    await _log_in(raw_client)
    return raw_client


# ---------------------------------------------------------------------------
# Page handlers
# ---------------------------------------------------------------------------

class TestPages:
    @pytest.mark.asyncio
    async def test_index_renders(self, controller_client: TestClient) -> None:
        resp = await controller_client.get(f"{PREFIX}/")
        assert resp.status == 200
        text = await resp.text()
        assert "NMOS Controller" in text
        # Token-entry panel removed — no OAuth2 input on the home page.
        assert 'id="oauth2"' not in text

    @pytest.mark.asyncio
    async def test_senders_list_renders_groups(
        self, controller_client: TestClient,
    ) -> None:
        resp = await controller_client.get(f"{PREFIX}/senders")
        assert resp.status == 200
        text = await resp.text()
        i1 = text.find("SNX00001")
        i2 = text.find("SNX00002")
        assert 0 <= i1 < i2
        # Natural-group display name is "<transport> <group-index>",
        # e.g. "RTP 0" — format is a per-member attribute, not part
        # of the group identity.
        assert "RTP 0" in text
        assert "RTP 0:VIDEO" not in text

    @pytest.mark.asyncio
    async def test_senders_list_shows_device_address_and_transport(
        self, controller_client: TestClient,
    ) -> None:
        resp = await controller_client.get(f"{PREFIX}/senders")
        assert resp.status == 200
        text = await resp.text()
        # Device header carries ipaddr:port from the sr-ctrl control URL.
        assert "remote" in text  # from _make_device conn_href
        # Column headers for the facet dots.
        for heading in ("label", "status", "link", "sync", "conn", "media"):
            assert f">{heading}<" in text
        # Status badge starts as grey "idle" for inactive resources.
        assert "is-inactive" in text
        # Facet dots present.
        assert 'data-kind="link"' in text
        assert 'data-kind="media"' in text

    @pytest.mark.asyncio
    async def test_receivers_list_renders(
        self, controller_client: TestClient,
    ) -> None:
        resp = await controller_client.get(f"{PREFIX}/receivers")
        assert resp.status == 200
        text = await resp.text()
        assert "Find compatible senders" in text

    @pytest.mark.asyncio
    async def test_compatible_senders_single_mode_shows_members(
        self, controller_client: TestClient,
    ) -> None:
        """Single-receiver mode renders the senders-listing table shape
        (device title + group header + per-member rows) so the operator
        can pick individual senders via the member checkbox.
        """
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        resp = await controller_client.get(
            f"{PREFIX}/receivers/compatible-senders?"
            f"receiver_ids={rid}&mode=single",
        )
        assert resp.status == 200
        text = await resp.text()
        # Table skeleton matches the senders list.
        assert "devices-table" in text
        assert 'class="devices-header-row"' in text
        # Member rows are present — the operator can pick individual
        # senders.
        assert 'class="member-row"' in text
        assert 'class="member-check"' in text
        # Group radios are also present for the whole-group selection.
        assert 'name="_group"' in text
        # Mode directive names the exact count: one sender per receiver.
        # Whitespace-tolerant since Jinja may insert newlines/indents
        # between ``exactly`` and the count.
        import re
        assert re.search(r"pick\s+exactly\s+1\s+sender,", text) is not None
        # Submit button wires the required-count constraint into
        # ``submitSelection`` — 4th arg = receiver count (1 here).
        assert re.search(
            r"submitSelection\s*\([^)]*'sender_ids',\s*null,\s*1\s*\)",
            text, flags=re.DOTALL,
        ) is not None

    @pytest.mark.asyncio
    async def test_compatible_senders_group_mode_hides_members(
        self, controller_client: TestClient,
    ) -> None:
        """Group-of-receivers mode renders only group header rows — the
        per-member rows collapse so the operator only picks whole groups.
        The shape filter drops sender groups whose member roles don't
        match the receiver group.
        """
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        resp = await controller_client.get(
            f"{PREFIX}/receivers/compatible-senders?"
            f"receiver_ids={rid}&mode=group",
        )
        assert resp.status == 200
        text = await resp.text()
        # Table skeleton present.
        assert "devices-table" in text
        # Group radios present (whole-group selection).
        assert 'name="_group"' in text
        # Individual-member UI collapsed: no ``member-row`` / checkbox.
        assert 'class="member-row"' not in text
        assert 'class="member-check"' not in text
        # The shape filter kicked in: receiver group has 1 member
        # (role 0); only sender groups with matching shape survive.
        # dev1 has two senders in one group (roles 0 + 1) → dropped.
        # dev2 has one sender in one group (role 0) → kept.
        assert "SNX00002" in text
        assert "SNX00001" not in text

    @pytest.mark.asyncio
    async def test_compatible_senders_group_mode_no_matches(
        self, controller_client: TestClient,
    ) -> None:
        """Group-mode with a receiver whose group hint has no shape-
        matching sender group renders the "no match" alert.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # A receiver in a 3-member group has no matching sender group
        # in the default seed (all sender groups are size 1 or 2).
        rid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        await cache.upsert("receiver", {
            "id": rid, "device_id": "dev1",
            "label": "triple-receiver",
            "tags": {GROUP_HINT_TAG: ["RTP 9:VIDEO 0"]},
            "subscription": {"active": False, "sender_id": None},
        })
        rid2 = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        await cache.upsert("receiver", {
            "id": rid2, "device_id": "dev1",
            "label": "triple-receiver-2",
            "tags": {GROUP_HINT_TAG: ["RTP 9:AUDIO 1"]},
            "subscription": {"active": False, "sender_id": None},
        })
        rid3 = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        await cache.upsert("receiver", {
            "id": rid3, "device_id": "dev1",
            "label": "triple-receiver-3",
            "tags": {GROUP_HINT_TAG: ["RTP 9:AUDIO 2"]},
            "subscription": {"active": False, "sender_id": None},
        })
        resp = await controller_client.get(
            f"{PREFIX}/receivers/compatible-senders?"
            f"receiver_ids={rid},{rid2},{rid3}&mode=group",
        )
        assert resp.status == 200
        text = await resp.text()
        assert "No sender natural groups match" in text
        assert "Back to receivers" in text

    @pytest.mark.asyncio
    async def test_compatible_senders_subset_mode_matches_mux_audio_legs(
        self, controller_client: TestClient,
    ) -> None:
        """Subset mode: operator tickled 2 of 3 receivers from a
        V+A+A MUX group. The compatible-senders page should include
        a sender natural group whose leaf signature is a superset of
        the subset — in this case the V+A+A MUX sender itself, with
        only its audio legs offered as candidate rows (the video leg
        is hidden because subset mode restricts each rendered group
        to its matched legs).
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # Receiver V+A+A natural group on dev1.
        rid_v = "11111111-2222-3333-4444-555555555555"
        rid_a0 = "22222222-3333-4444-5555-666666666666"
        rid_a1 = "33333333-4444-5555-6666-777777777777"
        await cache.upsert("receiver", _make_receiver(
            rid_v, "dev1", hint="RTP 5:VIDEO 0",
            format="urn:x-nmos:format:video",
        ))
        await cache.upsert("receiver", _make_receiver(
            rid_a0, "dev1", hint="RTP 5:AUDIO 0",
            format="urn:x-nmos:format:audio",
        ))
        await cache.upsert("receiver", _make_receiver(
            rid_a1, "dev1", hint="RTP 5:AUDIO 1",
            format="urn:x-nmos:format:audio",
        ))
        # Sender V+A+A natural group on dev2 (the MUX candidate).
        sid_v = "aaaaaaaa-1111-2222-3333-444444444444"
        sid_a0 = "bbbbbbbb-2222-3333-4444-555555555555"
        sid_a1 = "cccccccc-3333-4444-5555-666666666666"
        await cache.upsert("sender", _make_sender(
            sid_v, "dev2", hint="RTP 7:VIDEO 0",
            format="urn:x-nmos:format:video",
        ))
        await cache.upsert("sender", _make_sender(
            sid_a0, "dev2", hint="RTP 7:AUDIO 0",
            format="urn:x-nmos:format:audio",
        ))
        await cache.upsert("sender", _make_sender(
            sid_a1, "dev2", hint="RTP 7:AUDIO 1",
            format="urn:x-nmos:format:audio",
        ))

        # Tick only the two audio receivers (subset of the V+A+A group).
        resp = await controller_client.get(
            f"{PREFIX}/receivers/compatible-senders?"
            f"receiver_ids={rid_a0},{rid_a1}&mode=subset",
        )
        assert resp.status == 200
        text = await resp.text()
        # Mode indicator names subset and lists the subset's leaves.
        assert "subset of receiver group" in text
        # Audio legs are present as member rows; the video leg is NOT
        # (subset mode hides non-matching sender legs).
        assert sid_a0 in text
        assert sid_a1 in text
        assert sid_v not in text
        # The MUX sender's device (dev2) is present.
        assert "SNX00002" in text
        # Submit-count constraint: 2 senders required (one per subset
        # leaf). Whitespace-tolerant.
        import re
        assert re.search(
            r"submitSelection\s*\([^)]*'sender_ids',\s*null,\s*2\s*\)",
            text, flags=re.DOTALL,
        ) is not None

    @pytest.mark.asyncio
    async def test_compatible_senders_subset_rejects_cross_group(
        self, controller_client: TestClient,
    ) -> None:
        """Hand-crafted URL: ``mode=subset`` with receivers from two
        different natural groups → 400. The UI's confine-to-one-group
        rule prevents this in the browser, but the server must defend
        against tampered URLs.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        rid_a = "44444444-aaaa-bbbb-cccc-dddddddddddd"
        rid_b = "55555555-bbbb-cccc-dddd-eeeeeeeeeeee"
        # Two receivers in DIFFERENT natural groups (hint group_index
        # differs: RTP 1 vs RTP 2).
        await cache.upsert("receiver", _make_receiver(
            rid_a, "dev1", hint="RTP 1:AUDIO 0",
            format="urn:x-nmos:format:audio",
        ))
        await cache.upsert("receiver", _make_receiver(
            rid_b, "dev1", hint="RTP 2:AUDIO 0",
            format="urn:x-nmos:format:audio",
        ))
        resp = await controller_client.get(
            f"{PREFIX}/receivers/compatible-senders?"
            f"receiver_ids={rid_a},{rid_b}&mode=subset",
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_receivers_caps_pairs_by_identity_not_url_order(
        self, controller_client: TestClient,
    ) -> None:
        """``receivers_caps`` pairs senders ↔ receivers by
        ``(format, role_index)`` leaf identity, regardless of URL
        order. Supplying senders in the opposite order from the
        receivers must still render the page.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # Two audio receivers, two audio senders — both in distinct
        # role-indexed leaves.
        rid_0 = "66666666-0000-0000-0000-000000000000"
        rid_1 = "77777777-1111-1111-1111-111111111111"
        sid_0 = "88888888-0000-0000-0000-000000000000"
        sid_1 = "99999999-1111-1111-1111-111111111111"
        await cache.upsert("receiver", _make_receiver(
            rid_0, "dev1", hint="RTP 3:AUDIO 0",
            format="urn:x-nmos:format:audio",
        ))
        await cache.upsert("receiver", _make_receiver(
            rid_1, "dev1", hint="RTP 3:AUDIO 1",
            format="urn:x-nmos:format:audio",
        ))
        await cache.upsert("sender", _make_sender(
            sid_0, "dev2", hint="RTP 4:AUDIO 0",
            format="urn:x-nmos:format:audio",
        ))
        await cache.upsert("sender", _make_sender(
            sid_1, "dev2", hint="RTP 4:AUDIO 1",
            format="urn:x-nmos:format:audio",
        ))
        # Deliberately reversed sender order. ``mode=subset``
        # activates identity pairing so the server corrects the
        # order; default (single) mode would zip-pair
        # ``rid_0 ↔ sid_1`` which isn't what this test asserts.
        resp = await controller_client.get(
            f"{PREFIX}/receivers/caps?"
            f"receiver_ids={rid_0},{rid_1}"
            f"&sender_ids={sid_1},{sid_0}"
            f"&mode=subset",
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_receivers_caps_rejects_unmatched_leaf(
        self, controller_client: TestClient,
    ) -> None:
        """``receivers_caps`` with ``mode=subset`` rejects a selection
        where a receiver leaf has no matching sender leaf — pair-by-
        identity cannot produce a coherent pairing. Returns 400.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # Build an AUDIO receiver + AUDIO sender at different role
        # indices so the format-compat + transport checks pass, and
        # only the ``pair_by_identity`` role-index match can reject.
        # (A format mismatch would also cause 400, but via the
        # single-mode format guard, not via identity pairing.)
        rid_a0 = "aaaaaaaa-0000-1111-2222-333333333333"
        rid_a1 = "aaaaaaaa-1111-2222-3333-444444444444"
        sid_a0 = "bbbbbbbb-0000-1111-2222-333333333333"
        await cache.upsert("receiver", _make_receiver(
            rid_a0, "dev1", hint="RTP 6:AUDIO 0",
            format="urn:x-nmos:format:audio",
        ))
        await cache.upsert("receiver", _make_receiver(
            rid_a1, "dev1", hint="RTP 6:AUDIO 1",
            format="urn:x-nmos:format:audio",
        ))
        await cache.upsert("sender", _make_sender(
            sid_a0, "dev2", hint="RTP 6:AUDIO 0",
            format="urn:x-nmos:format:audio",
        ))
        # Two receivers (leaf signature {AUDIO 0, AUDIO 1}) but only
        # one sender at AUDIO 0 — the AUDIO 1 receiver has no
        # identity-matching sender.
        resp = await controller_client.get(
            f"{PREFIX}/receivers/caps?"
            f"receiver_ids={rid_a0},{rid_a1}"
            f"&sender_ids={sid_a0},{sid_a0}"
            f"&mode=subset",
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_single_mode_lists_senders_at_any_role_index(
        self, controller_client: TestClient,
    ) -> None:
        """Single mode (K=1 receiver) lists senders of the same
        format regardless of role_index — e.g. receiver ``AUDIO 0``
        can be routed from sender ``AUDIO 0`` OR ``AUDIO 1`` OR
        ``AUDIO 2``. This relaxes the strict
        ``senderLayer == layer`` rule.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # Receiver at audio role 0.
        rid = "11110000-2222-3333-4444-555555555555"
        await cache.upsert("receiver", _make_receiver(
            rid, "dev1", hint="RTP 11:AUDIO 0",
            format="urn:x-nmos:format:audio",
        ))
        # Senders at audio role 0, 1, 2 — all should be candidates.
        sid_a0 = "aaaa0000-1111-2222-3333-444444444444"
        sid_a1 = "aaaa1111-2222-3333-4444-555555555555"
        sid_a2 = "aaaa2222-3333-4444-5555-666666666666"
        # Also a VIDEO sender that must NOT show up (format mismatch).
        sid_v0 = "bbbb0000-1111-2222-3333-444444444444"
        await cache.upsert("sender", _make_sender(
            sid_a0, "dev2", hint="RTP 12:AUDIO 0",
            format="urn:x-nmos:format:audio",
        ))
        await cache.upsert("sender", _make_sender(
            sid_a1, "dev2", hint="RTP 12:AUDIO 1",
            format="urn:x-nmos:format:audio",
        ))
        await cache.upsert("sender", _make_sender(
            sid_a2, "dev2", hint="RTP 12:AUDIO 2",
            format="urn:x-nmos:format:audio",
        ))
        await cache.upsert("sender", _make_sender(
            sid_v0, "dev2", hint="RTP 12:VIDEO 0",
            format="urn:x-nmos:format:video",
        ))
        resp = await controller_client.get(
            f"{PREFIX}/receivers/compatible-senders?"
            f"receiver_ids={rid}&mode=single",
        )
        assert resp.status == 200
        text = await resp.text()
        # All three audio senders appear — role indices 1 and 2 are
        # no longer excluded.
        assert sid_a0 in text
        assert sid_a1 in text
        assert sid_a2 in text
        # The video sender is still excluded — format check stands.
        assert sid_v0 not in text

    @pytest.mark.asyncio
    async def test_single_mode_caps_accepts_cross_role_pair(
        self, controller_client: TestClient,
    ) -> None:
        """``receivers_caps?mode=single`` accepts a sender ↔ receiver
        pair whose role indices differ (receiver ``AUDIO 0`` +
        sender ``AUDIO 1``). Caps must still intersect (guarded by
        ``filter_sender_cs_by_receiver``), format + transport must
        match (guarded by ``_reject_incompatible_single_pair``).
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        rid = "22220000-3333-4444-5555-666666666666"
        sid = "33330000-4444-5555-6666-777777777777"
        await cache.upsert("receiver", _make_receiver(
            rid, "dev1", hint="RTP 13:AUDIO 0",
            format="urn:x-nmos:format:audio",
        ))
        await cache.upsert("sender", _make_sender(
            sid, "dev2", hint="RTP 13:AUDIO 1",
            format="urn:x-nmos:format:audio",
        ))
        resp = await controller_client.get(
            f"{PREFIX}/receivers/caps?"
            f"receiver_ids={rid}&sender_ids={sid}&mode=single",
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_single_mode_caps_rejects_format_mismatch(
        self, controller_client: TestClient,
    ) -> None:
        """Single mode still enforces format compatibility — a
        VIDEO receiver paired with AUDIO sender at
        ``receivers_caps`` → 400. Same for transport mismatch.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        rid = "44440000-5555-6666-7777-888888888888"
        sid = "55550000-6666-7777-8888-999999999999"
        await cache.upsert("receiver", _make_receiver(
            rid, "dev1", hint="RTP 14:VIDEO 0",
            format="urn:x-nmos:format:video",
        ))
        await cache.upsert("sender", _make_sender(
            sid, "dev2", hint="RTP 14:AUDIO 0",
            format="urn:x-nmos:format:audio",
        ))
        resp = await controller_client.get(
            f"{PREFIX}/receivers/caps?"
            f"receiver_ids={rid}&sender_ids={sid}&mode=single",
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_receivers_list_has_view_capabilities_button(
        self, controller_client: TestClient,
    ) -> None:
        """Receivers list page exposes a secondary ``View capabilities``
        button that routes to ``/receivers/view-caps`` via
        ``formaction``, next to the primary compatible-senders button.
        """
        resp = await controller_client.get(f"{PREFIX}/receivers")
        assert resp.status == 200
        text = await resp.text()
        assert "View capabilities" in text
        assert 'formaction="/controller/receivers/view-caps"' in text

    @pytest.mark.asyncio
    async def test_receivers_view_caps_renders_receiver_cs_list(
        self, controller_client: TestClient,
    ) -> None:
        """``/receivers/view-caps`` renders the selected receivers' own
        constraint-set list with no form / no selection / no follow-up
        flow — a read-only variant of the senders caps page.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        await cache.upsert("receiver", {
            "id": rid, "device_id": "dev1", "label": "rx-PCM",
            "tags": {GROUP_HINT_TAG: ["RTP 0:AUDIO 0"]},
            "subscription": {"active": False, "sender_id": None},
            "caps": {
                "constraint_sets": [{
                    "urn:x-nmos:cap:meta:label": "Receiver-Native-PCM",
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-nmos:cap:meta:preference": 100,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
                    "urn:x-matrox:cap:meta:layer": 0,
                    "urn:x-nmos:cap:format:media_type":
                        {"enum": ["audio/L24"]},
                }],
            },
        })
        resp = await controller_client.get(
            f"{PREFIX}/receivers/view-caps?receiver_ids={rid}",
        )
        assert resp.status == 200
        text = await resp.text()
        # CS label + media type render.
        assert "Receiver-Native-PCM" in text
        assert "audio/L24" in text
        # No form / no radio / no configure link (read-only).
        assert 'name="conset' not in text
        assert 'type="radio"' not in text
        assert 'action="/controller/receivers/configure"' not in text
        assert 'Continue to configuration' not in text
        # Back link present.
        assert "Back to receivers" in text

    @pytest.mark.asyncio
    async def test_receivers_caps_drops_sender_cs_incompatible_with_receiver(
        self, controller_client: TestClient,
    ) -> None:
        """On the receivers caps page, sender constraint sets that the
        receiver can't accept are dropped — the operator only sees
        choices that are actually negotiable.

        Scenario: receiver supports PCM only; sender advertises both
        AAC and PCM. The AAC CS must NOT appear.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        sid = "11111111-1111-1111-1111-111111111111"
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        # Receiver: PCM only. The receiver's CS label is what the
        # intersection result will carry (X side of the algorithm).
        await cache.upsert("receiver", {
            "id": rid, "device_id": "dev1", "label": "rx-PCM",
            "format": "urn:x-nmos:format:audio",
            "transport": "urn:x-nmos:transport:rtp",
            "tags": {GROUP_HINT_TAG: ["RTP 0:AUDIO 0"]},
            "subscription": {"active": False, "sender_id": None},
            "caps": {
                # Trunk CS on both sides — the narrowing in
                # ``compat.filter_sender_cs_by_receiver`` requires at
                # least one (format=None, layer=None) CS in the
                # intersection or it treats the whole pair as an
                # invalid mux and drops it.
                "constraint_sets": [
                    {
                        "urn:x-nmos:cap:meta:label": "Receiver-Trunk",
                        "urn:x-nmos:cap:meta:enabled": True,
                        "urn:x-nmos:cap:meta:preference": 100,
                    },
                    {
                        "urn:x-nmos:cap:meta:label": "Receiver-PCM-Only",
                        "urn:x-nmos:cap:meta:enabled": True,
                        "urn:x-nmos:cap:meta:preference": 100,
                        "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
                        "urn:x-matrox:cap:meta:layer": 0,
                        "urn:x-nmos:cap:format:media_type": {"enum": ["audio/L24"]},
                    },
                ],
            },
        })
        # Sender: AAC + PCM. Matched AUDIO leaf so pair-by-identity
        # at ``receivers_caps`` pairs this sender with the audio
        # receiver above.
        sender = _make_sender(
            sid, "dev1",
            hint="RTP 0:AUDIO 0",
            format="urn:x-nmos:format:audio",
        )
        sender["caps"] = {
            "constraint_sets": [
                # Trunk CS — required so the narrowing's mux-validity
                # guard is satisfied on the resulting intersection.
                {
                    "urn:x-nmos:cap:meta:label": "Sender-Trunk",
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-nmos:cap:meta:preference": 100,
                },
                {
                    "urn:x-nmos:cap:meta:label": "Native-AAC",
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-nmos:cap:meta:preference": 100,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
                    "urn:x-matrox:cap:meta:layer": 0,
                    "urn:x-nmos:cap:format:media_type":
                        {"enum": ["audio/mpeg4-generic"]},
                },
                {
                    "urn:x-nmos:cap:meta:label": "Native-PCM",
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-nmos:cap:meta:preference": 50,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
                    "urn:x-matrox:cap:meta:layer": 0,
                    "urn:x-nmos:cap:format:media_type":
                        {"enum": ["audio/L24"]},
                },
            ],
        }
        await cache.upsert("sender", sender)

        resp = await controller_client.get(
            f"{PREFIX}/receivers/caps?receiver_ids={rid}&sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        # Result carries the receiver's label (X side). The PCM
        # pair produces a non-empty intersection so the picker shows
        # one CS labeled from the receiver.
        assert "Receiver-PCM-Only" in text
        # Sender labels NEVER appear in the narrowed output — the
        # intersection's identity is the receiver capset, not the
        # sender's. The AAC sender CS is dropped (empty overlap);
        # the sender's own label is suppressed anyway by design.
        assert "Native-AAC" not in text
        assert "audio/mpeg4-generic" not in text

    @pytest.mark.asyncio
    async def test_receivers_caps_renders_constraint_set_picker(
        self, controller_client: TestClient,
    ) -> None:
        """When a sender with declared caps reaches ``/receivers/caps``
        alongside a compatible receiver, the page must render the
        per-sender CS picker — NOT the "no overlapping" empty-state
        message (which was firing regardless of actual compat because
        the old handler/template used mismatched keys).
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        sid = "11111111-1111-1111-1111-111111111111"
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        sender = _make_sender(sid, "dev1")
        sender["caps"] = {
            "constraint_sets": [
                {
                    "urn:x-nmos:cap:meta:label": "Native",
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-nmos:cap:meta:preference": 100,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:video",
                    "urn:x-matrox:cap:meta:layer": 0,
                    "urn:x-nmos:cap:format:media_type": {"enum": ["video/raw"]},
                },
            ],
        }
        await cache.upsert("sender", sender)

        resp = await controller_client.get(
            f"{PREFIX}/receivers/caps?receiver_ids={rid}&sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        # Picker table rendered with the sender's CS label.
        assert "Native" in text
        assert "caps-row" in text
        assert f'name="conset_{sid}"' in text
        # Hidden fields thread both selections to the configure page.
        assert f'name="receiver_ids" value="{rid}"' in text
        assert f'name="sender_ids" value="{sid}"' in text
        # Empty-state message must NOT fire when real overlap exists.
        assert "No overlapping constraint sets" not in text

    @pytest.mark.asyncio
    async def test_receivers_caps_rejects_mismatched_counts(
        self, controller_client: TestClient,
    ) -> None:
        """Direct-linking / URL-tampering with ``#sender_ids !=
        #receiver_ids`` is rejected 400. The compatible-senders page
        enforces equal counts in the browser; the server re-validates
        so the config flow downstream can safely pair-by-index.
        """
        resp = await controller_client.get(
            f"{PREFIX}/receivers/caps"
            f"?receiver_ids=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            f"&sender_ids=11111111-1111-1111-1111-111111111111,"
            f"22222222-2222-2222-2222-222222222222",
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_receivers_configure_rejects_mismatched_counts(
        self, controller_client: TestClient,
    ) -> None:
        """Same 400 guard on the configure page."""
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            f"&sender_ids=",
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_receivers_caps_group_mode_pairs_by_index(
        self, controller_client: TestClient,
    ) -> None:
        """Group-of-receivers mode: sender[i] must be compatible with
        receiver[i] (paired by role). An all-to-all filter would drop
        every candidate because no single sender is compatible with
        every receiver in a natural group — e.g. a stereo L sender
        matches only the L receiver, not R.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # Left / right receivers on dev1 with distinct caps.
        r_left_id = "10101010-0000-0000-0000-000000000001"
        r_right_id = "10101010-0000-0000-0000-000000000002"
        # Result labels now come from the receiver side (the X of the
        # intersection), so the receiver capsets carry the labels the
        # test asserts against.
        await cache.upsert("receiver", {
            "id": r_left_id, "device_id": "dev1", "label": "rx-L",
            "tags": {GROUP_HINT_TAG: ["RTP 9:AUDIO 0"]},
            "subscription": {"active": False, "sender_id": None},
            "caps": {
                "constraint_sets": [{
                    "urn:x-nmos:cap:meta:label": "RX-L-Mono",
                    "urn:x-nmos:cap:format:channel_count": {"enum": [1]},
                }],
            },
        })
        await cache.upsert("receiver", {
            "id": r_right_id, "device_id": "dev1", "label": "rx-R",
            "tags": {GROUP_HINT_TAG: ["RTP 9:AUDIO 1"]},
            "subscription": {"active": False, "sender_id": None},
            "caps": {
                "constraint_sets": [{
                    "urn:x-nmos:cap:meta:label": "RX-R-Mono",
                    "urn:x-nmos:cap:format:channel_count": {"enum": [1]},
                }],
            },
        })
        # L + R senders with matching caps, different roles.
        s_left_id = "20202020-0000-0000-0000-000000000001"
        s_right_id = "20202020-0000-0000-0000-000000000002"
        await cache.upsert("sender", {
            "id": s_left_id, "device_id": "dev2", "label": "tx-L",
            "tags": {GROUP_HINT_TAG: ["RTP 9:AUDIO 0"]},
            "subscription": {"active": False, "receiver_id": None},
            "caps": {
                "constraint_sets": [{
                    "urn:x-nmos:cap:meta:label": "L-Mono",
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-nmos:cap:format:channel_count": {"enum": [1]},
                }],
            },
        })
        await cache.upsert("sender", {
            "id": s_right_id, "device_id": "dev2", "label": "tx-R",
            "tags": {GROUP_HINT_TAG: ["RTP 9:AUDIO 1"]},
            "subscription": {"active": False, "receiver_id": None},
            "caps": {
                "constraint_sets": [{
                    "urn:x-nmos:cap:meta:label": "R-Mono",
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-nmos:cap:format:channel_count": {"enum": [1]},
                }],
            },
        })

        # The ``receivers_caps`` handler's ``mode`` branch selects
        # pair-by-identity vs URL-order zip. Group/subset modes use
        # identity pairing — that's the scenario under test here —
        # so the URL carries ``mode=group``.
        resp = await controller_client.get(
            f"{PREFIX}/receivers/caps"
            f"?receiver_ids={r_left_id},{r_right_id}"
            f"&sender_ids={s_left_id},{s_right_id}"
            f"&mode=group",
        )
        assert resp.status == 200
        text = await resp.text()
        # Both pairs produce a non-empty narrowed intersection —
        # picker renders the receiver-side labels (the X of the
        # intersection); the "no overlap" fallback does not fire.
        assert "RX-L-Mono" in text
        assert "RX-R-Mono" in text
        assert "No overlapping constraint sets" not in text

    @pytest.mark.asyncio
    async def test_receivers_configure_mirrors_senders_plus_receivers_toggle(
        self, controller_client: TestClient,
    ) -> None:
        """The receivers-path configure page renders the same per-
        sender CS-editor layout as ``/senders/configure`` (Senders
        Constrain + Activate toggles, Reset, filter dropdowns, per-
        sender result cells) plus a single ``activate_receivers``
        toggle for driving receiver activation pair-by-index.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        sid = "11111111-1111-1111-1111-111111111111"
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        sender = _make_sender(sid, "dev1")
        sender["caps"] = {
            "constraint_sets": [{
                "urn:x-nmos:cap:meta:label": "Native",
                "urn:x-nmos:cap:meta:enabled": True,
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:video",
                "urn:x-matrox:cap:meta:layer": 0,
                "urn:x-nmos:cap:format:media_type": {"enum": ["video/raw"]},
            }],
        }
        await cache.upsert("sender", sender)

        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={rid}&sender_ids={sid}&conset_{sid}=0",
        )
        assert resp.status == 200
        text = await resp.text()

        # Shared sender-side toggles from /senders/configure.
        assert 'data-action="constrain"' in text
        assert 'data-action="activate"' in text
        assert 'class="btn btn-outline-secondary ml-3 btn-reset"' in text
        # New receivers-activate toggle present.
        assert 'data-action="activate_receivers"' in text
        # Per-sender CS editor rendered (label + result cell).
        assert "Native" in text
        assert f'data-result-for="{sid}"' in text
        # Separate receiver-result cell — sender-side and receiver-
        # side outcomes don't overwrite each other.
        assert f'data-result-for-receiver="{rid}"' in text
        # Two distinct result column headers — just "sender" /
        # "receiver"; the cell content (state badge / action result)
        # speaks for itself.
        import re
        headers = re.findall(r"<th>([^<]+)</th>", text)
        assert "sender" in headers
        assert "receiver" in headers
        # Paired receiver surfaced on the device-title row so the
        # operator sees which receiver each sender drives — shown
        # as ``<device_serial> <host:port>`` for unambiguous physical
        # identification (not the generic resource label).
        assert f'data-receiver-id="{rid}"' in text
        assert "SNX00001" in text     # receiver's device serial
        assert "remote" in text       # host component of sr-ctrl href
        # Form carries the pairing info for the JS pair-by-index logic.
        assert f'data-receiver-ids="{rid}"' in text
        assert f'data-sender-ids="{sid}"' in text
        # Live-status machinery: each state cell carries
        # ``data-live-active`` (source of truth for the top-row
        # Activate aggregate), and the page subscribes via
        # ``initStatusStream``.
        assert 'data-live-active' in text
        assert "controller.initStatusStream();" in text
        # The old stub UI is gone (no "Activate &amp; connect" button).
        assert "Activate &amp; connect" not in text

    @pytest.mark.asyncio
    async def test_configure_exposes_cs_meta_format_and_layer(
        self, controller_client: TestClient,
    ) -> None:
        """Configure templates MUST emit ``data-cs-meta-format`` and
        ``data-cs-meta-layer`` on every ``caps-row`` — the JS reads
        them to set the correct ``meta:preference=100`` +
        ``meta:enabled`` / ``layer_enabled`` + format/layer tuple on
        the IS-11 PUT body.

        This is the structural safeguard against recurrence of the
        'constraint violation' bug where the controller shipped a
        constraint_set without preference — the Node silently
        skipped the conset in ``force_flow_properties_compatibility``
        and then 500'd on activation. Rule:
        ``feedback_is11_constraint_meta`` memory.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        sid = "11111111-1111-1111-1111-111111111111"
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        sender = _make_sender(sid, "dev1")
        sender["caps"] = {
            "constraint_sets": [{
                "urn:x-nmos:cap:meta:label": "Native",
                "urn:x-nmos:cap:meta:enabled": True,
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:video",
                "urn:x-matrox:cap:meta:layer": 0,
                "urn:x-nmos:cap:format:media_type": {"enum": ["video/raw"]},
            }],
        }
        await cache.upsert("sender", sender)

        # Senders configure page — primary edit surface.
        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}&conset_{sid}=0",
        )
        assert resp.status == 200
        text = await resp.text()
        assert f'data-sender-id="{sid}"' in text
        assert 'data-cs-meta-format="video"' in text
        assert 'data-cs-meta-layer="0"' in text

        # Receivers configure page — same structural requirement.
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={rid}&sender_ids={sid}&conset_{sid}=0",
        )
        assert resp.status == 200
        text = await resp.text()
        assert 'data-cs-meta-format="video"' in text
        assert 'data-cs-meta-layer="0"' in text

    @pytest.mark.asyncio
    async def test_controller_js_emits_is11_meta_preference(
        self, controller_client: TestClient,
    ) -> None:
        """The shipped ``controller.js`` MUST set
        ``urn:x-nmos:cap:meta:preference = 100`` on every IS-11
        constraint_set it builds. If someone removes that, the live
        'constraint violation' bug returns — see
        ``feedback_is11_constraint_meta`` memory.
        """
        resp = await controller_client.get(
            f"{PREFIX}/static/controller.js",
        )
        assert resp.status == 200
        js = await resp.text()
        # Core meta fields must be present in the body-builder.
        assert "urn:x-nmos:cap:meta:preference" in js
        assert "urn:x-nmos:cap:meta:enabled" in js
        # Sub-layer meta must also be emitted when the CS is per-layer.
        assert "urn:x-matrox:cap:meta:layer_enabled" in js
        assert "urn:x-matrox:cap:meta:format" in js
        assert "urn:x-matrox:cap:meta:layer" in js

    @pytest.mark.asyncio
    async def test_configure_toggles_reflect_any_active_constrained(
        self, controller_client: TestClient,
    ) -> None:
        """Toggle buttons on the receivers-configure page reflect the
        any-wise OR of the live state: green if at least one
        sender/receiver is in that state. Pressing off then drives
        every resource off uniformly.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]

        # One sender active + constrained, one receiver active.
        sid = "11111111-1111-1111-1111-111111111111"
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        sender = _make_sender(sid, "dev1", active=True)
        sender["caps"] = {"constraint_sets": [{
            "urn:x-nmos:cap:meta:label": "Native",
            "urn:x-nmos:cap:meta:enabled": True,
        }]}
        await cache.upsert("sender", sender)
        await cache.upsert(
            "receiver",
            _make_receiver(rid, "dev1", active=True),
        )

        # Probe returns a non-empty constraint set → constrained=True.
        remote.get_sender_active_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body={"constraint_sets": [{"urn:x-nmos:cap:meta:label": "X"}]},
            ),
        )

        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={rid}&sender_ids={sid}&conset_{sid}=0",
        )
        assert resp.status == 200
        text = await resp.text()

        # All three toggles render in the ON state (green).
        import re
        def _btn(action: str) -> str:
            m = re.search(
                r"<button[^>]*data-action=\"" + re.escape(action) + r"\"[^>]*>",
                text,
            )
            return m.group(0) if m else ""

        constrain_btn = _btn("constrain")
        activate_btn = _btn("activate")
        receivers_btn = _btn("activate_receivers")
        assert "btn-toggle-on" in constrain_btn
        assert "btn-toggle-on" in activate_btn
        assert "btn-toggle-on" in receivers_btn
        assert 'aria-pressed="true"' in constrain_btn
        assert 'aria-pressed="true"' in activate_btn
        assert 'aria-pressed="true"' in receivers_btn
        # Per-row cells pre-fill with the state.
        assert "active" in text
        assert "constrained" in text

    @pytest.mark.asyncio
    async def test_senders_configure_toggles_reflect_live_state(
        self, controller_client: TestClient,
    ) -> None:
        """The senders-only configure page uses the same any-wise OR
        button initialisation as the receivers path: green if at least
        one selected sender is in that state. Column header is just
        ``sender`` — the cell content carries the state.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        sid = "11111111-1111-1111-1111-111111111111"
        sender = _make_sender(sid, "dev1", active=True)
        sender["caps"] = {"constraint_sets": [{
            "urn:x-nmos:cap:meta:label": "Native",
            "urn:x-nmos:cap:meta:enabled": True,
        }]}
        await cache.upsert("sender", sender)
        remote.get_sender_active_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body={"constraint_sets": [{"urn:x-nmos:cap:meta:label": "X"}]},
            ),
        )

        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}&conset_{sid}=0",
        )
        assert resp.status == 200
        text = await resp.text()

        import re
        def _btn(action: str) -> str:
            m = re.search(
                r"<button[^>]*data-action=\"" + re.escape(action) + r"\"[^>]*>",
                text,
            )
            return m.group(0) if m else ""
        assert "btn-toggle-on" in _btn("constrain")
        assert "btn-toggle-on" in _btn("activate")
        assert 'aria-pressed="true"' in _btn("constrain")
        # Column header renamed from "result" to "sender".
        headers = re.findall(r"<th>([^<]+)</th>", text)
        assert "sender" in headers
        assert "result" not in headers
        # Live-status subscription is wired on the senders configure
        # page too, with ``data-live-active="true"`` reflecting the
        # initially-active sender.
        assert "controller.initStatusStream();" in text
        assert 'data-live-active="true"' in text

    @pytest.mark.asyncio
    async def test_configure_toggles_off_when_nothing_active(
        self, controller_client: TestClient,
    ) -> None:
        """With every resource idle, every toggle stays in the OFF state
        (red) — and pressing ON drives the "activate" semantics.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        sid = "11111111-1111-1111-1111-111111111111"
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        # Default seeded sender/receiver are inactive; fixture mock
        # already returns an unconstrained probe.
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={rid}&sender_ids={sid}&conset_{sid}=0",
        )
        assert resp.status == 200
        text = await resp.text()
        import re
        def _btn(action: str) -> str:
            m = re.search(
                r"<button[^>]*data-action=\"" + re.escape(action) + r"\"[^>]*>",
                text,
            )
            return m.group(0) if m else ""
        assert "btn-toggle-off" in _btn("constrain")
        assert 'aria-pressed="false"' in _btn("constrain")
        assert "btn-toggle-off" in _btn("activate")
        assert "btn-toggle-off" in _btn("activate_receivers")

    @pytest.mark.asyncio
    async def test_constrain_button_disabled_when_sender_lacks_is11(
        self, controller_client: TestClient,
    ) -> None:
        """Track C: when the selected sender's owning device doesn't
        advertise ``urn:x-nmos:control:stream-compat`` (no IS-11),
        the master Constrain toggle must render with ``disabled`` so
        the operator can't submit a doomed PUT. Other toggles
        (Activate, Deactivate) are unaffected — IS-05 is independent.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # Rewrite dev1 to drop the stream-compat control entry.
        await cache.upsert(
            "device",
            _make_device("dev1", "SNX00001", compat_href=None,
                         node_id="node-SNX00001"),
        )
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()

        import re
        m = re.search(
            r'<button[^>]*data-action="constrain"[\s\S]*?>',
            text,
        )
        assert m is not None, "Constrain button missing"
        constrain_tag = m.group(0)
        assert "disabled" in constrain_tag, (
            "Constrain button must carry ``disabled`` when any selected "
            "sender's device lacks IS-11"
        )
        # The Activate button is untouched — IS-05 independence.
        m = re.search(
            r'<button[^>]*data-action="activate"[\s\S]*?>',
            text,
        )
        assert m is not None
        assert "disabled" not in m.group(0)

    @pytest.mark.asyncio
    async def test_constrain_button_enabled_when_all_senders_have_is11(
        self, controller_client: TestClient,
    ) -> None:
        """Baseline: when every selected sender's device exposes
        IS-11 (the default seeded state), the Constrain button does
        NOT carry ``disabled``.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        import re
        m = re.search(
            r'<button[^>]*data-action="constrain"[\s\S]*?>',
            text,
        )
        assert m is not None
        assert "disabled" not in m.group(0)

    # ---------------------------------------------------------------------
    # Track D — Cross-direction navigation (A/V/D ↔ USB ↔ TB)
    # ---------------------------------------------------------------------

    @staticmethod
    async def _seed_usb_pair(
        cache: ResourceCache,
        recv_side_device: str = "dev1",
        send_side_device: str = "dev2",
        recv_id: str = "uuuu1111-0000-4000-8000-000000000001",
        send_id: str = "uuuu2222-0000-4000-8000-000000000002",
        group_index: int = 0,
    ) -> tuple[str, str]:
        """Seed one USB receiver + one USB sender on opposite devices,
        each alone in its natural group (the USB shape rule).

        Returns ``(recv_id, send_id)``. Both sides carry the Matrox
        USB transport URN AND a unique ``natural_group_index`` so
        that no other same-direction resource on the target device
        shares the ``(USB, group_index)`` key.
        """
        usb_transport = "urn:x-matrox:transport:usb"
        hint = f"USB {group_index}:DATA 0"
        await cache.upsert("receiver", {
            "id": recv_id, "device_id": recv_side_device,
            "label": "usb-rx", "format": "urn:x-nmos:format:data",
            "transport": usb_transport,
            "tags": {GROUP_HINT_TAG: [hint]},
            "subscription": {"active": False, "sender_id": None},
        })
        await cache.upsert("sender", {
            "id": send_id, "device_id": send_side_device,
            "label": "usb-tx", "format": "urn:x-nmos:format:data",
            "transport": usb_transport,
            "tags": {GROUP_HINT_TAG: [hint]},
            "subscription": {"active": False, "receiver_id": None},
        })
        return recv_id, send_id

    @staticmethod
    async def _seed_tb_pair(
        cache: ResourceCache,
        recv_side_device: str = "dev1",
        send_side_device: str = "dev2",
        recv_id: str = "tbbb1111-0000-4000-8000-000000000001",
        send_id: str = "tbbb2222-0000-4000-8000-000000000002",
        group_index: int = 2,
    ) -> tuple[str, str]:
        """Seed one audio receiver + one audio sender on opposite
        devices, each alone in its natural group.

        The TB classifier uses a SHAPE rule ("audio resource alone in
        its natural group on its Node"), not a dedicated role label
        (role labels are fixed to VIDEO/AUDIO/DATA/MUX by NMOS). The
        seeded hint therefore uses the standard role "AUDIO" on a
        ``natural_group_index`` that holds no other same-direction
        resources on the target device.
        """
        hint = f"RTP {group_index}:AUDIO 0"
        await cache.upsert("receiver", {
            "id": recv_id, "device_id": recv_side_device,
            "label": "tb-rx", "format": "urn:x-nmos:format:audio",
            "transport": "urn:x-nmos:transport:rtp",
            "tags": {GROUP_HINT_TAG: [hint]},
            "subscription": {"active": False, "sender_id": None},
        })
        await cache.upsert("sender", {
            "id": send_id, "device_id": send_side_device,
            "label": "tb-tx", "format": "urn:x-nmos:format:audio",
            "transport": "urn:x-nmos:transport:rtp",
            "tags": {GROUP_HINT_TAG: [hint]},
            "subscription": {"active": False, "receiver_id": None},
        })
        return recv_id, send_id

    @pytest.mark.asyncio
    async def test_avd_selection_shows_usb_and_tb_links(
        self, controller_client: TestClient,
    ) -> None:
        """On the A/V/D receivers-configure page, with USB and TB
        pairs present on the reverse direction, both reverse-nav
        buttons render enabled with the correct hrefs.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        usb_rx, usb_tx = await self._seed_usb_pair(cache)
        tb_rx, tb_tx = await self._seed_tb_pair(cache)

        rid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"  # on dev2 / node-SNX00002
        sid = "11111111-1111-1111-1111-111111111111"  # on dev1 / node-SNX00001
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={rid}&sender_ids={sid}&mode=single",
        )
        assert resp.status == 200
        text = await resp.text()

        # Both reverse buttons present.
        assert "Configure USB capabilities" in text
        assert "Configure TB capabilities" in text
        # The A/V/D self-link must NOT appear (we're on that page).
        import re
        assert re.search(
            r'data-reverse-group="avd"', text,
        ) is None

        # Both enabled (rendered as <a>, not <button disabled>).
        usb_link = re.search(
            r'<[^>]*data-reverse-group="usb"[^>]*>[^<]*Configure USB',
            text,
        )
        assert usb_link is not None
        assert "disabled" not in usb_link.group(0)
        # The USB href carries the reverse-direction ids: USB receiver
        # is on the ORIGINAL sender's Node (dev1 → node-SNX00001);
        # USB sender is on the ORIGINAL receiver's Node (dev2 →
        # node-SNX00002). Jinja may emit attributes in either order
        # so the regex accepts data-reverse-group on either side of
        # the href attribute.
        tag_m = re.search(
            r'<a[^>]*data-reverse-group="usb"[^>]*>',
            text,
        )
        assert tag_m is not None, "USB reverse-link <a> tag missing"
        href_m = re.search(r'href="([^"]+)"', tag_m.group(0))
        assert href_m is not None
        href = href_m.group(1)
        # Must land on the CAPABILITIES picker (not skip ahead to the
        # configure page). The button says "Configure USB
        # capabilities …" — the operator still picks a constraint
        # set before the actual activation drives.
        assert "/receivers/caps?" in href, (
            f"reverse-direction link must target /receivers/caps, got {href!r}"
        )
        # USB receiver is on dev1 (sender's side reversed): ``usb_rx``.
        # USB sender is on dev2: ``usb_tx``.
        assert f"receiver_ids={usb_rx}" in href
        assert f"sender_ids={usb_tx}" in href

    @pytest.mark.asyncio
    async def test_usb_selection_shows_avd_and_tb_links(
        self, controller_client: TestClient,
    ) -> None:
        """Entering via a USB selection: the buttons point to A/V/D
        and TB on the same two Nodes. The USB self-link is absent.

        Critical: USB and TB both flow OPPOSITE to A/V/D, so they
        share the same direction. Going USB → TB needs NO swap of
        the Node ids; the TB resources live on the same Nodes as
        the USB selection (not the swapped ones). Going USB → A/V/D
        DOES swap because A/V/D is the other direction class.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # A/V/D pair (forward): senders on dev1, receivers on dev2.
        # USB pair (reverse): receivers on dev1, senders on dev2.
        usb_rx, usb_tx = await self._seed_usb_pair(cache)
        # TB pair (reverse — SAME direction as USB): receivers on
        # dev1, senders on dev2.
        tb_rx, tb_tx = await self._seed_tb_pair(
            cache, recv_side_device="dev1", send_side_device="dev2",
            recv_id="tbbb3333-0000-4000-8000-000000000003",
            send_id="tbbb4444-0000-4000-8000-000000000004",
        )
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={usb_rx}&sender_ids={usb_tx}&mode=single",
        )
        assert resp.status == 200
        text = await resp.text()

        # A/V/D + TB buttons present; USB self-link absent.
        assert "Configure A/V/D capabilities" in text
        assert "Configure TB capabilities" in text
        import re
        assert re.search(r'data-reverse-group="usb"', text) is None

        # Critical assertions: BOTH cross-direction buttons must be
        # ENABLED, not greyed. The bug we're guarding against is "from
        # USB → TB doesn't find candidates because the swap is wrong".
        avd_tag = re.search(
            r'<a[^>]*data-reverse-group="avd"[^>]*>',
            text,
        )
        assert avd_tag is not None, (
            "From USB page, A/V/D button must be enabled (the A/V/D pair "
            "lives between the same two Nodes)"
        )
        tb_tag = re.search(
            r'<a[^>]*data-reverse-group="tb"[^>]*>',
            text,
        )
        assert tb_tag is not None, (
            "From USB page, TB button must be enabled. Bug: an earlier "
            "version always swapped Node ids assuming the target group "
            "flowed the opposite direction, which broke USB → TB "
            "(both flow the same direction)."
        )
        # And the TB href carries the TB ids (not swapped).
        tb_href = re.search(r'href="([^"]+)"', tb_tag.group(0))
        assert tb_href is not None
        assert f"receiver_ids={tb_rx}" in tb_href.group(1)
        assert f"sender_ids={tb_tx}" in tb_href.group(1)

    @pytest.mark.asyncio
    async def test_tb_selection_shows_avd_and_usb_links(
        self, controller_client: TestClient,
    ) -> None:
        """Symmetric to USB → TB: from a TB page the operator must
        be able to reach both A/V/D (cross-direction) and USB
        (same-direction). Regression for the same swap bug viewed
        from the other side.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # USB pair (reverse): rx on dev1, tx on dev2.
        usb_rx, usb_tx = await self._seed_usb_pair(cache)
        # TB pair (reverse — same direction): rx on dev1, tx on dev2.
        tb_rx, tb_tx = await self._seed_tb_pair(
            cache, recv_side_device="dev1", send_side_device="dev2",
        )
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={tb_rx}&sender_ids={tb_tx}&mode=single",
        )
        assert resp.status == 200
        text = await resp.text()
        import re

        # TB self-link absent.
        assert re.search(r'data-reverse-group="tb"', text) is None

        # A/V/D + USB buttons both enabled.
        avd_tag = re.search(
            r'<a[^>]*data-reverse-group="avd"[^>]*>',
            text,
        )
        assert avd_tag is not None
        usb_tag = re.search(
            r'<a[^>]*data-reverse-group="usb"[^>]*>',
            text,
        )
        assert usb_tag is not None, (
            "From TB page, USB button must be enabled. Same regression "
            "as USB → TB."
        )
        usb_href = re.search(r'href="([^"]+)"', usb_tag.group(0))
        assert usb_href is not None
        # USB resources live on the same Nodes as the TB selection
        # (no swap — both reverse direction).
        assert f"receiver_ids={usb_rx}" in usb_href.group(1)
        assert f"sender_ids={usb_tx}" in usb_href.group(1)

    @pytest.mark.asyncio
    async def test_missing_reverse_group_is_disabled_with_tooltip(
        self, controller_client: TestClient,
    ) -> None:
        """Only A/V/D resources on both Nodes → USB + TB buttons
        render disabled with the "No pair found" tooltip.
        """
        rid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={rid}&sender_ids={sid}&mode=single",
        )
        assert resp.status == 200
        text = await resp.text()
        import re
        for group in ("usb", "tb"):
            m = re.search(
                rf'<button[^>]*data-reverse-group="{group}"[\s\S]*?>',
                text,
            )
            assert m is not None, f"{group} button missing"
            assert "disabled" in m.group(0)
            # Tooltip contains the "No ... pair found" wording.
            assert "No " in m.group(0)
            assert "pair found" in m.group(0)

    @pytest.mark.asyncio
    async def test_usb_two_in_same_group_disables_button(
        self, controller_client: TestClient,
    ) -> None:
        """Two USB receivers sharing the same natural group on the
        target Node → shape rule rejects ("alone in group" fails) →
        button disabled.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # Two USB receivers both in group 0 on dev1 (the reverse-recv Node).
        await self._seed_usb_pair(cache)
        await self._seed_usb_pair(
            cache,
            recv_id="uuuu5555-0000-4000-8000-000000000005",
            send_id="uuuu6666-0000-4000-8000-000000000006",
            group_index=0,
        )
        rid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={rid}&sender_ids={sid}&mode=single",
        )
        assert resp.status == 200
        text = await resp.text()
        import re
        m = re.search(
            r'<button[^>]*data-reverse-group="usb"[\s\S]*?>',
            text,
        )
        assert m is not None
        assert "disabled" in m.group(0)

    @pytest.mark.asyncio
    async def test_usb_multiple_groups_pick_smallest_group_index(
        self, controller_client: TestClient,
    ) -> None:
        """Two USB pairs, each alone in its own natural group → the
        USB button is ENABLED and the resolved href carries the ids
        of the pair with the smaller ``natural_group_index``.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # Pair #1: group 3 (larger index — should lose tie-break).
        big_rx, big_tx = await self._seed_usb_pair(
            cache,
            recv_id="uuuu7777-0000-4000-8000-000000000007",
            send_id="uuuu8888-0000-4000-8000-000000000008",
            group_index=3,
        )
        # Pair #2: group 1 (smaller index — should win tie-break).
        small_rx, small_tx = await self._seed_usb_pair(
            cache,
            recv_id="uuuu9999-0000-4000-8000-000000000009",
            send_id="uuuuAAAA-0000-4000-8000-00000000000A",
            group_index=1,
        )
        rid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={rid}&sender_ids={sid}&mode=single",
        )
        assert resp.status == 200
        text = await resp.text()
        import re
        tag = re.search(
            r'<a[^>]*data-reverse-group="usb"[^>]*>',
            text,
        )
        assert tag is not None, "USB button should be enabled"
        href = re.search(r'href="([^"]+)"', tag.group(0))
        assert href is not None
        # The winning pair has group_index=1 (smaller than 3).
        assert f"receiver_ids={small_rx}" in href.group(1)
        assert f"sender_ids={small_tx}" in href.group(1)
        assert big_rx not in href.group(1)
        assert big_tx not in href.group(1)

    @pytest.mark.asyncio
    async def test_tb_multiple_groups_prefer_transport_then_group_index(
        self, controller_client: TestClient,
    ) -> None:
        """Multiple TB audio candidates: first prefer the one whose
        transport matches the A/V/D selection's transport; within
        that, smallest ``natural_group_index`` wins.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # TB #1 on RTP (matches A/V/D's RTP transport), group 5.
        rtp5_rx, rtp5_tx = await self._seed_tb_pair(
            cache,
            recv_id="tbbb1111-0000-4000-8000-000000000011",
            send_id="tbbb2222-0000-4000-8000-000000000022",
            group_index=5,
        )
        # TB #2 on RTP, group 2 — smaller index, should win.
        rtp2_rx, rtp2_tx = await self._seed_tb_pair(
            cache,
            recv_id="tbbb3333-0000-4000-8000-000000000033",
            send_id="tbbb4444-0000-4000-8000-000000000044",
            group_index=2,
        )
        rid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={rid}&sender_ids={sid}&mode=single",
        )
        assert resp.status == 200
        text = await resp.text()
        import re
        tag = re.search(
            r'<a[^>]*data-reverse-group="tb"[^>]*>',
            text,
        )
        assert tag is not None
        href_m = re.search(r'href="([^"]+)"', tag.group(0))
        assert href_m is not None
        href = href_m.group(1)
        # Both candidates have RTP transport; the winner is the one
        # with the smaller group_index.
        assert f"receiver_ids={rtp2_rx}" in href
        assert f"sender_ids={rtp2_tx}" in href
        assert rtp5_rx not in href
        assert rtp5_tx not in href

    @pytest.mark.asyncio
    async def test_tb_two_in_same_group_disables_button(
        self, controller_client: TestClient,
    ) -> None:
        """Two audio receivers sharing the same natural group on the
        target Node → neither is "alone in group" → TB button
        disabled.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # Both pairs put their audio resources in group 2 on dev1.
        await self._seed_tb_pair(cache, group_index=2)
        await self._seed_tb_pair(
            cache,
            recv_id="tbbb5555-0000-4000-8000-000000000055",
            send_id="tbbb6666-0000-4000-8000-000000000066",
            group_index=2,
        )
        rid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={rid}&sender_ids={sid}&mode=single",
        )
        assert resp.status == 200
        text = await resp.text()
        import re
        m = re.search(
            r'<button[^>]*data-reverse-group="tb"[\s\S]*?>',
            text,
        )
        assert m is not None
        assert "disabled" in m.group(0)

    @pytest.mark.asyncio
    async def test_candidate_helpers_direct(
        self,
    ) -> None:
        """Unit test on the TB / USB candidate helpers directly — no
        HTTP layer involved. Pins the shape rule ("alone in group")
        and the tie-break ordering.
        """
        from nmos.controller.handlers import (
            _find_usb_candidates, _find_tb_candidates,
        )
        cache: ResourceCache = ResourceCache()
        await cache.upsert("node", _make_node_resource("n1"))
        await cache.upsert("device",
                           _make_device("d1", node_id="n1"))

        # One USB sender alone in group 4.
        await cache.upsert("sender", {
            "id": "u-alone", "device_id": "d1",
            "label": "usb-alone",
            "format": "urn:x-nmos:format:data",
            "transport": "urn:x-matrox:transport:usb",
            "tags": {GROUP_HINT_TAG: ["USB 4:DATA 0"]},
            "subscription": {"active": False, "receiver_id": None},
        })
        # Two USB senders sharing group 1 — NOT alone, must be skipped.
        await cache.upsert("sender", {
            "id": "u-shared-1", "device_id": "d1",
            "label": "usb-shared-1",
            "format": "urn:x-nmos:format:data",
            "transport": "urn:x-matrox:transport:usb",
            "tags": {GROUP_HINT_TAG: ["USB 1:DATA 0"]},
            "subscription": {"active": False, "receiver_id": None},
        })
        await cache.upsert("sender", {
            "id": "u-shared-2", "device_id": "d1",
            "label": "usb-shared-2",
            "format": "urn:x-nmos:format:data",
            "transport": "urn:x-matrox:transport:usb",
            "tags": {GROUP_HINT_TAG: ["USB 1:DATA 1"]},
            "subscription": {"active": False, "receiver_id": None},
        })

        usb = _find_usb_candidates(cache, "n1", is_sender=True)
        assert [r["id"] for r in usb] == ["u-alone"], (
            "shared-group USB senders must be filtered out"
        )

        # Three audio senders alone in their own RTP groups.
        for gi in (7, 3, 5):
            await cache.upsert("sender", {
                "id": f"audio-g{gi}", "device_id": "d1",
                "label": f"a{gi}",
                "format": "urn:x-nmos:format:audio",
                "transport": "urn:x-nmos:transport:rtp",
                "tags": {GROUP_HINT_TAG: [f"RTP {gi}:AUDIO 0"]},
                "subscription": {"active": False, "receiver_id": None},
            })
        tb = _find_tb_candidates(
            cache, "n1", is_sender=True,
            avd_transport="urn:x-nmos:transport:rtp",
        )
        # All three qualify (audio + alone); sorted by group_index.
        assert [r["id"] for r in tb] == [
            "audio-g3", "audio-g5", "audio-g7",
        ]

        # Now add an SRT-transport audio sender alone in group 1 and
        # re-run with avd_transport=RTP: the SRT one should sort
        # LAST because the primary sort key is "transport matches
        # avd_transport?".
        await cache.upsert("sender", {
            "id": "audio-srt-g1", "device_id": "d1",
            "label": "srt1",
            "format": "urn:x-nmos:format:audio",
            "transport": "urn:x-matrox:transport:srt",
            "tags": {GROUP_HINT_TAG: ["SRT 1:AUDIO 0"]},
            "subscription": {"active": False, "receiver_id": None},
        })
        tb = _find_tb_candidates(
            cache, "n1", is_sender=True,
            avd_transport="urn:x-nmos:transport:rtp",
        )
        # RTP ones win over SRT; within RTP sorted by group_index.
        assert [r["id"] for r in tb] == [
            "audio-g3", "audio-g5", "audio-g7", "audio-srt-g1",
        ]

    @pytest.mark.asyncio
    async def test_constrain_button_disabled_on_receivers_configure_when_sender_lacks_is11(
        self, controller_client: TestClient,
    ) -> None:
        """Same gate on the receivers-configure path: when any PAIRED
        sender's device doesn't support IS-11, the master Constrain
        toggle is disabled. This is the most common operator path —
        operators usually drive configuration from the receiver side.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        await cache.upsert(
            "device",
            _make_device("dev1", "SNX00001", compat_href=None,
                         node_id="node-SNX00001"),
        )
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={rid}&sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        import re
        m = re.search(
            r'<button[^>]*data-action="constrain"[\s\S]*?>',
            text,
        )
        assert m is not None
        assert "disabled" in m.group(0)

    @pytest.mark.asyncio
    async def test_receiver_deactivate_proxies_master_enable_false(
        self, controller_client: TestClient,
    ) -> None:
        """``POST /api/receivers/{id}/deactivate`` PATCHes the
        receiver's staged with ``master_enable=False`` and no
        sender-side transport-file fetch (the receiver just goes
        idle — no SDP needed).
        """
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.patch_receiver_staged = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body={"ok": True}),
        )
        # Also patch transportfile in case — if the handler wrongly
        # calls it, that'd be a bug the test should catch.
        remote.get_sender_transportfile = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=500, body="should not be called"),
        )
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        resp = await controller_client.post(
            f"{PREFIX}/api/receivers/{rid}/deactivate",
        )
        assert resp.status == 200
        args, _ = remote.patch_receiver_staged.call_args
        body = args[2]
        assert body["master_enable"] is False
        assert "transport_file" not in body
        # The SDP fetch must NOT have been called.
        assert remote.get_sender_transportfile.call_count == 0

    @pytest.mark.asyncio
    async def test_senders_caps_disabled_sets_hidden(
        self, controller_client: TestClient,
    ) -> None:
        """Enabled CS rendered; disabled CS dropped entirely."""
        cache: ResourceCache = controller_client.app["_test_cache"]
        sid = "11111111-1111-1111-1111-111111111111"
        sender = _make_sender(sid, "dev1")
        sender["caps"] = {
            "constraint_sets": [
                {
                    "urn:x-nmos:cap:meta:label": "Native",
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-nmos:cap:meta:preference": 100,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:video",
                    "urn:x-matrox:cap:meta:layer": 0,
                    "urn:x-nmos:cap:format:media_type": {"enum": ["video/raw"]},
                    "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
                    "urn:x-nmos:cap:format:frame_height": {"enum": [1080]},
                },
                {
                    "urn:x-nmos:cap:meta:label": "OffVariant",
                    "urn:x-nmos:cap:meta:enabled": False,
                    "urn:x-nmos:cap:meta:preference": 50,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:video",
                    "urn:x-matrox:cap:meta:layer": 0,
                },
            ],
        }
        await cache.upsert("sender", sender)

        resp = await controller_client.get(
            f"{PREFIX}/senders/caps?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        assert "Native" in text
        assert "OffVariant" not in text
        # Filter dropdown for Format is present.
        assert "caps-filter" in text or 'class="dropdown-menu"' in text
        # Media type surfaced in the expanded details.
        assert "video/raw" in text

    @pytest.mark.asyncio
    async def test_senders_caps_trunk_cs_derives_format_and_layer(
        self, controller_client: TestClient,
    ) -> None:
        """A constraint set without explicit cap:meta:format /
        cap:meta:layer inherits them from the sender's group-hint
        role and role index.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        sid = "11111111-1111-1111-1111-111111111111"
        sender = _make_sender(sid, "dev1", hint="RTP 0:AUDIO 2")
        sender["caps"] = {
            "constraint_sets": [
                {
                    # No meta_format / meta_layer — trunk CS.
                    "urn:x-nmos:cap:meta:label": "TrunkNative",
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-nmos:cap:meta:preference": 75,
                },
            ],
        }
        await cache.upsert("sender", sender)

        resp = await controller_client.get(
            f"{PREFIX}/senders/caps?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        assert "TrunkNative" in text
        # Derived format "audio" + derived layer 2 from the group
        # hint "RTP 0:AUDIO 2". Whitespace-tolerant assertion.
        import re
        assert re.search(r">\s*audio\s*<", text) is not None
        assert re.search(r">\s*2\s*<", text) is not None

    @pytest.mark.asyncio
    async def test_senders_caps_filter_by_format(
        self, controller_client: TestClient,
    ) -> None:
        cache: ResourceCache = controller_client.app["_test_cache"]
        sid = "11111111-1111-1111-1111-111111111111"
        sender = _make_sender(sid, "dev1")
        sender["caps"] = {
            "constraint_sets": [
                {
                    "urn:x-nmos:cap:meta:label": "VideoSet",
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:video",
                    "urn:x-matrox:cap:meta:layer": 0,
                },
                {
                    "urn:x-nmos:cap:meta:label": "AudioSet",
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
                    "urn:x-matrox:cap:meta:layer": 0,
                },
            ],
        }
        await cache.upsert("sender", sender)

        # No filter → both shown.
        resp = await controller_client.get(
            f"{PREFIX}/senders/caps?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        assert "VideoSet" in text and "AudioSet" in text

        # byFormat=video → only VideoSet.
        resp = await controller_client.get(
            f"{PREFIX}/senders/caps?sender_ids={sid}&byFormat=video",
        )
        assert resp.status == 200
        text = await resp.text()
        assert "VideoSet" in text
        assert "AudioSet" not in text
        # Dropdown still lists "audio" because the filter is applied
        # *after* option collection. Whitespace-tolerant.
        import re
        assert re.search(r">\s*audio\s*<", text) is not None


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

class TestJsonApi:
    @pytest.mark.asyncio
    async def test_list_senders(self, controller_client: TestClient) -> None:
        resp = await controller_client.get(f"{PREFIX}/api/senders")
        assert resp.status == 200
        body = await resp.json()
        devices = body["senders"]
        assert len(devices) == 2

    @pytest.mark.asyncio
    async def test_compatible_senders(self, controller_client: TestClient) -> None:
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        resp = await controller_client.get(
            f"{PREFIX}/api/receivers/{rid}/compatible-senders",
        )
        assert resp.status == 200
        body = await resp.json()
        assert len(body["senders"]) == 3

    @pytest.mark.asyncio
    async def test_compatible_senders_unknown_receiver(
        self, controller_client: TestClient,
    ) -> None:
        resp = await controller_client.get(
            f"{PREFIX}/api/receivers/does-not-exist/compatible-senders",
        )
        assert resp.status == 404


# ---------------------------------------------------------------------------
# Proxy endpoints (stub remote)
# ---------------------------------------------------------------------------

class TestProxy:
    @pytest.mark.asyncio
    async def test_activate_sender_calls_remote(
        self, controller_client: TestClient,
    ) -> None:
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.patch_sender_staged = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body={"ok": True}),
        )
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/activate",
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == 200
        args, _ = remote.patch_sender_staged.call_args
        assert args[2]["master_enable"] is True

    @pytest.mark.asyncio
    async def test_sender_not_found_returns_404(
        self, controller_client: TestClient,
    ) -> None:
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/deadbeef-ffff-ffff-ffff-000000000000/activate",
        )
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_constrain_sender_uses_streamcompat_base(
        self, controller_client: TestClient,
    ) -> None:
        """Active-constraints PUT routes through the IS-11
        streamcompatibility control URL, not the IS-05 sr-ctrl URL.

        Body carries a properly-formed constraint_set with the meta
        fields the Node requires for the fix-the-flow path — see
        ``feedback_is11_constraint_meta`` memory and
        ``controller.js::_collectConstraintSetForSender``. The proxy
        forwards the body verbatim, so those meta fields arrive on
        the far side intact.
        """
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.put_sender_active_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body={"ok": True}),
        )
        sid = "11111111-1111-1111-1111-111111111111"
        body = {"constraint_sets": [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:format:constant_bit_rate": {"enum": [True]},
        }]}
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/constrain", json=body,
        )
        assert resp.status == 200
        # First positional arg is ``base_url`` — must be the stream-compat
        # href, not the sr-ctrl one.
        args, _ = remote.put_sender_active_constraints.call_args
        assert "streamcompatibility" in args[0]
        assert "connection" not in args[0]
        # Third positional arg is the IS-11 body — booleans stay booleans,
        # AND meta fields are forwarded unchanged.
        forwarded_cs = args[2]["constraint_sets"][0]
        assert forwarded_cs[
            "urn:x-nmos:cap:format:constant_bit_rate"]["enum"] == [True]
        assert forwarded_cs["urn:x-nmos:cap:meta:preference"] == 100
        assert forwarded_cs["urn:x-nmos:cap:meta:enabled"] is True

    @pytest.mark.asyncio
    async def test_activate_does_not_forward_browser_authorization(
        self, controller_client: TestClient,
    ) -> None:
        """The browser's ``Authorization`` header — whether a stale
        ``Basic`` from a former HTTP-Basic deployment, or any other
        scheme — MUST NOT leak to remote Nodes. An upstream PATCH
        carrying a non-Bearer ``Authorization`` is rejected by the
        reservation middleware (observed on a production node) as
        ``"the bearer token is not owner of the device…"`` even when
        no bearer was intended.
        """
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.patch_sender_staged = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body={"ok": True}),
        )
        sid = "11111111-1111-1111-1111-111111111111"
        # Simulate a browser that still holds a cached Basic credential
        # from a prior HTTP-Basic version of the app.
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/activate",
            headers={"Authorization": "Basic YWRtaW46czNjcmV0"},
        )
        assert resp.status == 200
        # 4th positional arg of patch_sender_staged is ``forwarded_headers``.
        args, _ = remote.patch_sender_staged.call_args
        forwarded = args[3]
        assert "Authorization" not in forwarded
        assert "PEP-Exclusive-Authorization" not in forwarded

    @pytest.mark.asyncio
    async def test_activate_401_surfaces_remote_error_message(
        self, controller_client: TestClient,
    ) -> None:
        """When the remote Node returns 401 + an NError body, the
        controller's envelope carries a human-readable ``message``
        built from status + the body's ``error`` field so the browser
        can render ``HTTP 401: <detail>`` directly in the result cell.
        """
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        nerror = {
            "code": 401,
            "error": "client certificate required",
            "debug": "no peer cert presented",
        }
        remote.patch_sender_staged = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=401, body=nerror),
        )
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/activate",
        )
        # Non-200 remote → local 502, per convention.
        assert resp.status == 502
        body = await resp.json()
        assert body["status"] == 401
        assert body["message"] == "HTTP 401: client certificate required"
        # Full remote body preserved for the browser's tooltip.
        assert body["body"] == nerror

    @pytest.mark.asyncio
    async def test_activate_connect_failure_reports_transport_error(
        self, controller_client: TestClient,
    ) -> None:
        """A connect-level failure (status=0, error set) surfaces as
        ``message == <transport error>`` — no ``HTTP 0`` noise.
        """
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.patch_sender_staged = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=0, body=None, error="Cannot connect to host",
            ),
        )
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/activate",
        )
        assert resp.status == 502
        body = await resp.json()
        assert body["message"] == "Cannot connect to host"

    @pytest.mark.asyncio
    async def test_constrain_sender_409_when_compat_control_missing(
        self, controller_client: TestClient,
    ) -> None:
        """Device lacking a stream-compat control URL → 409, not a
        misdirected PUT to the sr-ctrl URL."""
        cache: ResourceCache = controller_client.app["_test_cache"]
        # Rewrite dev1 to drop the stream-compat control.
        await cache.upsert("device", {
            "id": "dev1", "label": "d", "description": "",
            "controls": [{
                "type": "urn:x-nmos:control:sr-ctrl/v1.1",
                "href": "https://remote/x-nmos/connection/v1.1",
            }],
        })
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/constrain",
            json={"constraint_sets": []},
        )
        assert resp.status == 409
        body = await resp.json()
        assert "streamcompat" in body["error"]


# ---------------------------------------------------------------------------
# Admin login/logout gate
# ---------------------------------------------------------------------------

class TestAdminAuth:
    @pytest.mark.asyncio
    async def test_page_request_redirects_to_login_when_unauthenticated(
        self, raw_client: TestClient,
    ) -> None:
        # A bare page fetch is a browser navigation → redirect to login.
        resp = await raw_client.get(
            f"{PREFIX}/senders",
            headers={"Accept": "text/html"},
            allow_redirects=False,
        )
        assert resp.status == 302
        location = resp.headers["Location"]
        assert location.startswith(f"{PREFIX}/login")
        assert "next=" in location

    @pytest.mark.asyncio
    async def test_api_request_returns_401_when_unauthenticated(
        self, raw_client: TestClient,
    ) -> None:
        # JSON endpoints get a clean 401, not a redirect.
        resp = await raw_client.get(f"{PREFIX}/api/senders")
        assert resp.status == 401
        body = await resp.json()
        assert body["error"] == "unauthenticated"

    @pytest.mark.asyncio
    async def test_login_page_renders(self, raw_client: TestClient) -> None:
        resp = await raw_client.get(f"{PREFIX}/login")
        assert resp.status == 200
        text = await resp.text()
        assert "Administrator sign-in" in text
        assert 'name="password"' in text

    @pytest.mark.asyncio
    async def test_login_wrong_password_returns_401_and_renders_error(
        self, raw_client: TestClient,
    ) -> None:
        resp = await raw_client.post(
            f"{PREFIX}/login",
            data={"password": "nope"},
        )
        assert resp.status == 401
        text = await resp.text()
        assert "Incorrect password" in text

    @pytest.mark.asyncio
    async def test_login_success_sets_cookie_and_redirects(
        self, raw_client: TestClient,
    ) -> None:
        resp = await raw_client.post(
            f"{PREFIX}/login",
            data={"password": ADMIN_PASSWORD, "next": f"{PREFIX}/senders"},
            allow_redirects=False,
        )
        assert resp.status == 302
        assert resp.headers["Location"] == f"{PREFIX}/senders"
        # aiohttp's cookie jar retains the cookie.
        assert any(
            c.key == "nmos_controller_session" for c in raw_client.session.cookie_jar
        )

    @pytest.mark.asyncio
    async def test_login_rejects_open_redirect_in_next(
        self, raw_client: TestClient,
    ) -> None:
        resp = await raw_client.post(
            f"{PREFIX}/login",
            data={"password": ADMIN_PASSWORD, "next": "https://evil.example/"},
            allow_redirects=False,
        )
        assert resp.status == 302
        # next sanitised back to the controller root.
        assert resp.headers["Location"] == f"{PREFIX}/"

    @pytest.mark.asyncio
    async def test_logout_clears_cookie(
        self, raw_client: TestClient,
    ) -> None:
        await _log_in(raw_client)
        resp = await raw_client.get(
            f"{PREFIX}/logout", allow_redirects=False,
        )
        assert resp.status == 302
        assert resp.headers["Location"] == f"{PREFIX}/login"

        # Subsequent protected request must be gated again.
        resp2 = await raw_client.get(f"{PREFIX}/api/senders")
        assert resp2.status == 401

    @pytest.mark.asyncio
    async def test_static_files_public(
        self, raw_client: TestClient,
    ) -> None:
        resp = await raw_client.get(f"{PREFIX}/static/controller.css")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_missing_admin_password_rejected_at_construct_time(
        self,
    ) -> None:
        node = _make_node()
        with pytest.raises(ValueError, match="admin_password"):
            create_controller_app(node, admin_password="")


# ---------------------------------------------------------------------------
# Session token (unit tests on nmos.controller.auth)
# ---------------------------------------------------------------------------

class TestSessionToken:
    def test_roundtrip_success(self) -> None:
        token = issue_session_token(ADMIN_PASSWORD)
        assert verify_session_token(token, ADMIN_PASSWORD) is True

    def test_wrong_password_fails(self) -> None:
        token = issue_session_token(ADMIN_PASSWORD)
        assert verify_session_token(token, "different") is False

    def test_malformed_token(self) -> None:
        assert verify_session_token("", ADMIN_PASSWORD) is False
        assert verify_session_token("no-dot", ADMIN_PASSWORD) is False
        assert verify_session_token(".sig", ADMIN_PASSWORD) is False
        assert verify_session_token("ts.", ADMIN_PASSWORD) is False
        assert verify_session_token("abc.def", ADMIN_PASSWORD) is False

    def test_expired_token(self) -> None:
        # Issue in the far past; verification must reject as stale.
        past = 1  # 1970
        token = issue_session_token(ADMIN_PASSWORD, issued_at=past)
        assert verify_session_token(
            token, ADMIN_PASSWORD, max_age=SESSION_MAX_AGE_SECONDS,
        ) is False

    def test_future_token_rejected(self) -> None:
        future = 2**31  # 2038
        token = issue_session_token(ADMIN_PASSWORD, issued_at=future)
        assert verify_session_token(token, ADMIN_PASSWORD) is False


# ---------------------------------------------------------------------------
# SSE live badges
# ---------------------------------------------------------------------------

class TestSse:
    @pytest.mark.asyncio
    async def test_status_events_delivers_change(
        self, controller_client: TestClient,
    ) -> None:
        cache: ResourceCache = controller_client.app["_test_cache"]
        sid = "11111111-1111-1111-1111-111111111111"

        resp = await controller_client.get(
            f"{PREFIX}/api/status-events?ids={sid}",
        )
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/event-stream")

        async def trigger() -> None:
            await asyncio.sleep(0.05)
            await cache.upsert("sender", _make_sender(sid, "dev1", active=True))

        task = asyncio.create_task(trigger())

        # The SSE handler sends a current-state snapshot for every
        # subscribed id BEFORE the change-event loop, so the first
        # event will reflect the initial (active=False) state. Drain
        # events until we see the active=True one delivered by the
        # ``trigger()`` task.
        received: bytes = b""
        active_true_seen = False
        try:
            async for chunk in resp.content.iter_any():
                received += chunk
                for line in received.decode("utf-8", errors="replace").splitlines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        payload = json.loads(line[len("data: "):])
                    except json.JSONDecodeError:
                        continue
                    if (payload.get("id") == sid
                            and payload.get("status", {}).get("active") is True):
                        active_true_seen = True
                        break
                if active_true_seen:
                    break
        finally:
            task.cancel()
            resp.close()

        assert active_true_seen, (
            "expected an active=True status event for the upserted sender; "
            f"raw stream was: {received!r}"
        )


# ---------------------------------------------------------------------------
# API-base resolver (device controls → href)
# ---------------------------------------------------------------------------

class TestApiBaseResolution:
    def test_connection_prefers_v11_over_v12(self) -> None:
        """When a device advertises both v1.1 and v1.2 connection APIs,
        the controller picks v1.1. v1.2 adds only schema headroom and
        some servers have stricter path parsing for it.
        """
        device = {
            "controls": [
                {"type": "urn:x-nmos:control:sr-ctrl/v1.2",
                 "href": "https://h/v1.2"},
                {"type": "urn:x-nmos:control:sr-ctrl/v1.1",
                 "href": "https://h/v1.1"},
            ],
        }
        assert RemoteNodeClient.connection_api_base(device) == "https://h/v1.1"

    def test_connection_picks_v12_when_v11_absent(self) -> None:
        device = {
            "controls": [
                {"type": "urn:x-nmos:control:sr-ctrl/v1.2",
                 "href": "https://h/v1.2"},
            ],
        }
        assert RemoteNodeClient.connection_api_base(device) == "https://h/v1.2"

    def test_connection_falls_back_to_future_version(self) -> None:
        """Unknown v1.3+ still resolves — the generic prefix match
        kicks in after the exact-preference pass misses.
        """
        device = {
            "controls": [
                {"type": "urn:x-nmos:control:sr-ctrl/v1.3",
                 "href": "https://h/v1.3"},
            ],
        }
        assert RemoteNodeClient.connection_api_base(device) == "https://h/v1.3"

    def test_connection_returns_none_when_no_control(self) -> None:
        assert RemoteNodeClient.connection_api_base({"controls": []}) is None

    def test_exclusive_service_reads_from_NODE_services(self) -> None:
        """Reservation service lives on ``node.services`` (per NMOS),
        NOT on ``device.controls``. Regression: the controller previously
        walked ``device.services`` and returned None for every Node
        even when the endpoint was live.
        """
        node = {
            "services": [
                {"type": "urn:x-matrox:service:exclusive/v1.0",
                 "href": "https://h/x-manufacturer/exclusive/v1.0/"},
            ],
        }
        assert RemoteNodeClient.exclusive_service_base(node) == (
            "https://h/x-manufacturer/exclusive/v1.0/"
        )

    def test_exclusive_service_ignores_DEVICE_services(self) -> None:
        """A DEVICE (not a Node) with a services array must NOT
        resolve — the resolver is Node-scoped. This pins the
        distinction so any future refactor that accidentally walks
        device.services trips the test.
        """
        # Same ``services`` shape but semantically a device (has
        # ``node_id``, no top-level Node URN). resolver only knows
        # whether the resource has a matching ``services[]`` entry,
        # so this test guards the CALL SITE (handlers must pass a
        # Node dict, not a device dict) — see
        # ``nmos/controller/handlers.py:_nodes_for_devices`` and
        # ``_headers_with_reservation``.
        device_misused_as_node = {
            "node_id": "some-node",
            "controls": [
                {"type": "urn:x-nmos:control:sr-ctrl/v1.1",
                 "href": "https://h/c/"},
            ],
            # A device that WRONGLY carried a services array would
            # still resolve — but that's a config bug, not the
            # resolver's problem. We document that with this assert:
            # resolvers work off ``services`` regardless of resource
            # type, so the caller is responsible for passing the
            # right resource. The guard is at the call site.
        }
        assert RemoteNodeClient.exclusive_service_base(
            device_misused_as_node,
        ) is None

    def test_exclusive_service_returns_none_when_node_missing_service(self) -> None:
        """A Node without the reservation service advertised → None.
        This is the exact path the controller hit in the field: the
        Python Node's init never touched ``nv.Services`` and every
        acquire attempt failed with 'node does not advertise the
        reservation service'. The unit-level catch is the pair of
        ``test_init_publishes_exclusive_service`` on the Node side
        and this test on the controller side.
        """
        node_without_service: dict[str, Any] = {"id": "some-node"}
        assert RemoteNodeClient.exclusive_service_base(
            node_without_service,
        ) is None

    def test_cache_node_for_device_resolves_owning_node(self) -> None:
        """``cache.node_for_device`` returns the Node that owns the
        given device_id — the controller uses this to find where to
        acquire when the operator ticks Exclusivity on a configure
        page. Regression for the refactor from device-scoped to
        node-scoped reservation keying.
        """
        import asyncio as _asyncio
        cache = ResourceCache()

        async def _seed() -> None:
            await cache.upsert("node", {"id": "node-A"})
            await cache.upsert("device", {"id": "dev-A", "node_id": "node-A"})

        _asyncio.run(_seed())
        owning = cache.node_for_device("dev-A")
        assert owning is not None
        assert owning["id"] == "node-A"
        assert cache.node_for_device("unknown-device") is None


# ---------------------------------------------------------------------------
# Reservation-bearer header selection (NMOS With Node Reservation §"Using
# Reservation along with OAuth2.0 authorizations")
# ---------------------------------------------------------------------------

class TestExclusiveHeaderSelection:
    """Per spec: OAuth2 OFF on remote → session bearer in ``Authorization``;
    OAuth2 ON on remote → session bearer in ``PEP-Exclusive-Authorization``.

    Reference tests: the Node's own suite —
    [test_reservation.py](../../api/tests/test_reservation.py) sends
    ``Authorization`` (OAuth2 off),
    [test_oauth2_reservation.py](../../api/tests/test_oauth2_reservation.py)
    sends both headers (OAuth2 on).
    """

    def test_exclusive_service_info_captures_authorization_flag(self) -> None:
        node_no_oauth2 = {
            "services": [
                {"type": "urn:x-matrox:service:exclusive/v1.0",
                 "href": "http://h/exclusive/v1.0/",
                 "authorization": False},
            ],
        }
        node_oauth2 = {
            "services": [
                {"type": "urn:x-matrox:service:exclusive/v1.0",
                 "href": "https://h/exclusive/v1.0/",
                 "authorization": True},
            ],
        }
        info_no = RemoteNodeClient.exclusive_service_info(node_no_oauth2)
        info_yes = RemoteNodeClient.exclusive_service_info(node_oauth2)
        assert info_no == ("http://h/exclusive/v1.0/", False)
        assert info_yes == ("https://h/exclusive/v1.0/", True)

    def test_exclusive_service_info_defaults_authorization_false(self) -> None:
        """When the service entry omits ``authorization``, default to
        ``False`` — the safe no-OAuth2 reading that matches the
        Python Node's default (no ``--oauth2`` flag).
        """
        node = {
            "services": [
                {"type": "urn:x-matrox:service:exclusive/v1.0",
                 "href": "http://h/e/"},
            ],
        }
        info = RemoteNodeClient.exclusive_service_info(node)
        assert info == ("http://h/e/", False)

    def test_header_name_picks_authorization_when_oauth2_off(self) -> None:
        from nmos.controller.api_client import exclusive_header_name
        assert exclusive_header_name(False) == "Authorization"

    def test_header_name_picks_pep_when_oauth2_on(self) -> None:
        from nmos.controller.api_client import exclusive_header_name
        assert exclusive_header_name(True) == "PEP-Exclusive-Authorization"

    @pytest.mark.asyncio
    async def test_renew_sends_authorization_when_oauth2_off(self) -> None:
        """Regression for the 401-loop bug: a renew against a remote
        running without OAuth2 must put the session bearer in
        ``Authorization`` — not ``PEP-Exclusive-Authorization``.
        """
        client = RemoteNodeClient()
        captured: dict[str, Any] = {}

        async def _fake_request(
            method: str, url: str, forwarded: dict[str, str], **kw: Any,
        ) -> RemoteCallResult:
            captured["headers"] = dict(forwarded)
            return RemoteCallResult(status=200, body="new-token")
        client._request = _fake_request  # type: ignore[method-assign]

        await client.renew_exclusive(
            "http://h/e/", session_token="TOK", forwarded_headers={},
            oauth2_on_remote=False,
        )
        assert captured["headers"].get("Authorization") == "Bearer TOK"
        assert "PEP-Exclusive-Authorization" not in captured["headers"]

    @pytest.mark.asyncio
    async def test_renew_sends_pep_when_oauth2_on(self) -> None:
        client = RemoteNodeClient()
        captured: dict[str, Any] = {}

        async def _fake_request(
            method: str, url: str, forwarded: dict[str, str], **kw: Any,
        ) -> RemoteCallResult:
            captured["headers"] = dict(forwarded)
            return RemoteCallResult(status=200, body="new-token")
        client._request = _fake_request  # type: ignore[method-assign]

        await client.renew_exclusive(
            "https://h/e/", session_token="TOK", forwarded_headers={},
            oauth2_on_remote=True,
        )
        assert (
            captured["headers"].get("PEP-Exclusive-Authorization")
            == "Bearer TOK"
        )
        assert "Authorization" not in captured["headers"]

    @pytest.mark.asyncio
    async def test_keepalive_and_release_honor_oauth2_flag(self) -> None:
        """Both keepalive and release must route the bearer to the
        header the remote expects — the polling task and the shutdown
        path both hit this code path.
        """
        client = RemoteNodeClient()
        seen: list[dict[str, str]] = []

        async def _fake_request(
            method: str, url: str, forwarded: dict[str, str], **kw: Any,
        ) -> RemoteCallResult:
            seen.append(dict(forwarded))
            return RemoteCallResult(status=200, body=None)
        client._request = _fake_request  # type: ignore[method-assign]

        await client.keepalive_exclusive(
            "http://h/e/", session_token="K", forwarded_headers={},
            oauth2_on_remote=False,
        )
        await client.release_exclusive(
            "http://h/e/", session_token="K", forwarded_headers={},
            oauth2_on_remote=False,
        )
        assert all(h.get("Authorization") == "Bearer K" for h in seen)
        assert all("PEP-Exclusive-Authorization" not in h for h in seen)


# ---------------------------------------------------------------------------
# Privacy (PEP) + Node Reservation flow
# ---------------------------------------------------------------------------

class TestPrivacyFlow:
    """End-to-end coverage of the Privacy panel + activate integration
    and the reservation endpoints. Mocks the remote Node calls via
    the ``_test_remote_stub`` fixture so the tests are hermetic.
    """

    @staticmethod
    def _transport_params_with_privacy(
        *,
        protocols: list[str] = ["RTP"],
        modes: list[str] = ["AES-128-CTR", "ECDH_AES-128-CTR"],
        curves: list[str] = ["secp521r1"],
    ) -> list[dict[str, Any]]:
        """IS-05 ``/constraints/`` response shape — a per-leg array
        where each entry is the constraint set for one active leg.
        Single-leg IS-05 senders/receivers return a one-element list.
        """
        return [{
            "ext_privacy_protocol": {"enum": protocols},
            "ext_privacy_mode": {"enum": modes},
            "ext_privacy_ecdh_curve": {"enum": curves},
        }]

    @pytest.mark.asyncio
    async def test_options_endpoint_returns_intersection(
        self, controller_client: TestClient,
    ) -> None:
        """GET /api/privacy/options returns the sorted intersection of
        protocols/modes/curves across every selected resource.
        """
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        # Sender: both AES modes. Receiver: only AES-128-CTR.
        remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body=self._transport_params_with_privacy(
                    modes=["AES-128-CTR", "AES-256-CTR"],
                ),
            ),
        )
        remote.get_receiver_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body=self._transport_params_with_privacy(
                    modes=["AES-128-CTR"],
                ),
            ),
        )
        sid = "11111111-1111-1111-1111-111111111111"
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        resp = await controller_client.get(
            f"{PREFIX}/api/privacy/options?ids={sid},{rid}",
        )
        assert resp.status == 200
        data = await resp.json()
        # Intersection is the single common mode.
        assert data["modes"] == ["AES-128-CTR"]
        assert data["protocols"] == ["RTP"]
        assert data["curves"] == ["secp521r1"]
        # dev1 / dev2 don't advertise the exclusive service (default
        # fixture), so exclusivity_ok is False.
        assert data["exclusivity_ok"] is False

    @pytest.mark.asyncio
    async def test_options_empty_when_no_pep_declared(
        self, controller_client: TestClient,
    ) -> None:
        """Default seed has empty ext_privacy_* enums → no PEP
        surface. The endpoint returns empty lists.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.get(
            f"{PREFIX}/api/privacy/options?ids={sid}",
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["protocols"] == []
        assert data["modes"] == []
        assert data["curves"] == []

    @pytest.mark.asyncio
    async def test_acquire_and_release(
        self, controller_client: TestClient,
    ) -> None:
        """POST /api/privacy/acquire drives remote acquire; POST
        /api/privacy/release drives remote release. The NODE (not
        Device) needs to advertise the reservation service."""
        cache: ResourceCache = controller_client.app["_test_cache"]
        # Re-upsert the Node with the reservation service advertised.
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="https://remote/x-manufacturer/exclusive/v1.0/",
        ))
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.acquire_exclusive = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body="bearer-xyz"),
        )
        remote.release_exclusive = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body=None),
        )

        # Acquire.
        resp = await controller_client.post(
            f"{PREFIX}/api/privacy/acquire",
            json={"node_ids": ["node-SNX00001"]},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["acquired"] == ["node-SNX00001"]
        assert data["failed"] == []
        remote.acquire_exclusive.assert_awaited_once()
        # Body of the acquire POST — hex exclusive_key + owner.
        call_args = remote.acquire_exclusive.call_args
        assert call_args.kwargs["owner"] == "administrator"
        # 16-byte hex = 32 chars.
        assert len(call_args.kwargs["exclusive_key_hex"]) == 32
        int(call_args.kwargs["exclusive_key_hex"], 16)  # must parse as hex

        # Release.
        resp = await controller_client.post(
            f"{PREFIX}/api/privacy/release",
            json={"node_ids": ["node-SNX00001"]},
        )
        assert resp.status == 200
        remote.release_exclusive.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_activate_with_reservation_against_no_oauth2_remote(
        self, controller_client: TestClient,
    ) -> None:
        """End-to-end regression for the 401-"exclusive session
        active, bearer token required" loop.

        Given: a Node advertising the reservation service with
        ``authorization=False`` (no OAuth2 on the remote — the
        default deployment). When the admin acquires a session and
        then activates a sender, the outbound PATCH /staged/ MUST
        carry the session bearer in ``Authorization`` (NOT in
        ``PEP-Exclusive-Authorization``). That's what the remote
        checks per spec §"Using Reservation along with OAuth2.0
        authorizations" and what the Python Node's own
        [test_reservation.py](../../api/tests/test_reservation.py)
        uses.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        # Re-upsert the Node with the reservation service advertised
        # and ``authorization=False``.
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="http://remote/x-manufacturer/exclusive/v1.0/",
            exclusive_authorization=False,
        ))
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.acquire_exclusive = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body="RSV-TOK"),
        )
        captured_patch: dict[str, Any] = {}

        async def _capture_patch(
            base_url: str, sender_id: str, body: dict[str, Any],
            forwarded: dict[str, str], **kw: Any,
        ) -> RemoteCallResult:
            captured_patch["forwarded"] = dict(forwarded)
            return RemoteCallResult(status=200, body={"master_enable": True})
        remote.patch_sender_staged = _capture_patch  # type: ignore[method-assign]

        # Acquire first.
        resp = await controller_client.post(
            f"{PREFIX}/api/privacy/acquire",
            json={"node_ids": ["node-SNX00001"]},
        )
        assert resp.status == 200

        # Activate a sender owned by the acquired Node.
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/activate",
        )
        assert resp.status == 200
        fwd = captured_patch["forwarded"]
        # The whole point of this test: bearer in Authorization, not PEP.
        assert fwd.get("Authorization") == "Bearer RSV-TOK"
        assert "PEP-Exclusive-Authorization" not in fwd

    @pytest.mark.asyncio
    async def test_constrain_forwards_reservation_bearer(
        self, controller_client: TestClient,
    ) -> None:
        """IS-11 PUT ``/senders/{id}/constraints/active/`` is
        state-changing; the reservation bearer MUST travel on it.
        Regression for the "clicking Constrain returns Unauthorized"
        bug where the handler routed through ``_forwarded_auth``
        (empty dict) instead of ``_headers_with_reservation``.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="http://remote/x-manufacturer/exclusive/v1.0/",
            exclusive_authorization=False,
        ))
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.acquire_exclusive = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body="RSV-TOK"),
        )
        captured: dict[str, Any] = {}

        async def _capture(
            base_url: str, sender_id: str, body: dict[str, Any],
            forwarded: dict[str, str], **kw: Any,
        ) -> RemoteCallResult:
            captured["forwarded"] = dict(forwarded)
            return RemoteCallResult(status=200, body=None)
        remote.put_sender_active_constraints = _capture  # type: ignore[method-assign]

        # Acquire the reservation first.
        resp = await controller_client.post(
            f"{PREFIX}/api/privacy/acquire",
            json={"node_ids": ["node-SNX00001"]},
        )
        assert resp.status == 200

        # Constrain.
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/constrain",
            json={"constraint_sets": []},
        )
        assert resp.status == 200
        fwd = captured["forwarded"]
        assert fwd.get("Authorization") == "Bearer RSV-TOK"
        assert "PEP-Exclusive-Authorization" not in fwd

    @pytest.mark.asyncio
    async def test_receiver_deactivate_forwards_reservation_bearer(
        self, controller_client: TestClient,
    ) -> None:
        """``api_receiver_deactivate`` is state-changing (PATCH
        /staged/) — the reservation bearer MUST travel on it too.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="http://remote/x-manufacturer/exclusive/v1.0/",
            exclusive_authorization=False,
        ))
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.acquire_exclusive = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body="RSV-TOK"),
        )
        captured: dict[str, Any] = {}

        async def _capture(
            base_url: str, receiver_id: str, body: dict[str, Any],
            forwarded: dict[str, str], **kw: Any,
        ) -> RemoteCallResult:
            captured["forwarded"] = dict(forwarded)
            return RemoteCallResult(status=200, body={"master_enable": False})
        remote.patch_receiver_staged = _capture  # type: ignore[method-assign]

        resp = await controller_client.post(
            f"{PREFIX}/api/privacy/acquire",
            json={"node_ids": ["node-SNX00001"]},
        )
        assert resp.status == 200

        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        resp = await controller_client.post(
            f"{PREFIX}/api/receivers/{rid}/deactivate",
        )
        assert resp.status == 200
        fwd = captured["forwarded"]
        assert fwd.get("Authorization") == "Bearer RSV-TOK"
        assert "PEP-Exclusive-Authorization" not in fwd

    @pytest.mark.asyncio
    async def test_activate_with_reservation_against_oauth2_remote(
        self, controller_client: TestClient,
    ) -> None:
        """Symmetric case: ``authorization=True`` on the service
        entry → bearer lands in ``PEP-Exclusive-Authorization``.
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="https://remote/x-manufacturer/exclusive/v1.0/",
            exclusive_authorization=True,
        ))
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.acquire_exclusive = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body="RSV-TOK"),
        )
        captured_patch: dict[str, Any] = {}

        async def _capture_patch(
            base_url: str, sender_id: str, body: dict[str, Any],
            forwarded: dict[str, str], **kw: Any,
        ) -> RemoteCallResult:
            captured_patch["forwarded"] = dict(forwarded)
            return RemoteCallResult(status=200, body={"master_enable": True})
        remote.patch_sender_staged = _capture_patch  # type: ignore[method-assign]

        resp = await controller_client.post(
            f"{PREFIX}/api/privacy/acquire",
            json={"node_ids": ["node-SNX00001"]},
        )
        assert resp.status == 200

        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/activate",
        )
        assert resp.status == 200
        fwd = captured_patch["forwarded"]
        assert fwd.get("PEP-Exclusive-Authorization") == "Bearer RSV-TOK"
        # OAuth2 bearer (Authorization) is NOT populated by the
        # controller in this flow — the admin is gated by the
        # controller's own session cookie; OAuth2 on the remote is
        # the Node's concern, not ours.
        assert "Authorization" not in fwd

    @pytest.mark.asyncio
    async def test_acquire_fails_when_service_missing(
        self, controller_client: TestClient,
    ) -> None:
        """A Node without the reservation service yields a failed
        entry per node; overall status is 409 when every node
        fails. Regression for the bug where the service
        wasn't advertised on the Python Node's ``services`` at all
        (the Python Node's earlier init never touched ``nv.Services``,
        so controllers couldn't discover the endpoint even though the
        routes were live)."""
        resp = await controller_client.post(
            f"{PREFIX}/api/privacy/acquire",
            json={"node_ids": ["node-SNX00001"]},  # default seed has no service
        )
        assert resp.status == 409
        data = await resp.json()
        assert data["acquired"] == []
        assert data["failed"][0]["node_id"] == "node-SNX00001"
        assert "reservation service" in data["failed"][0]["reason"]

    @pytest.mark.asyncio
    async def test_acquire_partial_success_returns_207(
        self, controller_client: TestClient,
    ) -> None:
        """One Node succeeds, one has no service → 207 Multi-Status-
        ish (prior successes left held)."""
        cache: ResourceCache = controller_client.app["_test_cache"]
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="https://remote/exclusive/v1.0/",
        ))
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.acquire_exclusive = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body="tok"),
        )
        resp = await controller_client.post(
            f"{PREFIX}/api/privacy/acquire",
            # node-SNX00002 has no service per default seed.
            json={"node_ids": ["node-SNX00001", "node-SNX00002"]},
        )
        assert resp.status == 207
        data = await resp.json()
        assert data["acquired"] == ["node-SNX00001"]
        assert len(data["failed"]) == 1
        assert data["failed"][0]["node_id"] == "node-SNX00002"

    @pytest.mark.asyncio
    async def test_release_all_beacon(
        self, controller_client: TestClient,
    ) -> None:
        """POST /api/privacy/release?all=true releases every held
        session for this admin (used by the browser-unload beacon).
        """
        cache: ResourceCache = controller_client.app["_test_cache"]
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="https://remote/exclusive/v1.0/",
        ))
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.acquire_exclusive = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body="t"),
        )
        remote.release_exclusive = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body=None),
        )
        # Hold a session first.
        await controller_client.post(
            f"{PREFIX}/api/privacy/acquire",
            json={"node_ids": ["node-SNX00001"]},
        )
        # Beacon release.
        resp = await controller_client.post(
            f"{PREFIX}/api/privacy/release?all=true",
        )
        assert resp.status == 200
        remote.release_exclusive.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sender_activate_with_ecdh_reads_receiver_pub_key(
        self, controller_client: TestClient,
    ) -> None:
        """ECDH sender activation: server GETs receiver's /active/,
        extracts ``ext_privacy_ecdh_receiver_public_key``, injects it
        into the sender's PATCH body alongside protocol/mode/curve.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.get_receiver_active = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body={
                "transport_params": [{
                    "ext_privacy_ecdh_receiver_public_key": "0xRXPUBKEY",
                }],
            }),
        )
        remote.patch_sender_staged = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body={"ok": True}),
        )
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/activate",
            json={
                "privacy": {
                    "protocol": "RTP",
                    "mode": "ECDH_AES-256-CTR",
                    "curve": "secp521r1",
                },
                "receiver_id": rid,
            },
        )
        assert resp.status == 200
        remote.get_receiver_active.assert_awaited_once()
        patch_call = remote.patch_sender_staged.call_args
        body = patch_call.args[2]
        leg = body["transport_params"][0]
        assert leg["ext_privacy_protocol"] == "RTP"
        assert leg["ext_privacy_mode"] == "ECDH_AES-256-CTR"
        assert leg["ext_privacy_ecdh_curve"] == "secp521r1"
        assert leg["ext_privacy_ecdh_receiver_public_key"] == "0xRXPUBKEY"

    @pytest.mark.asyncio
    async def test_sender_activate_ecdh_requires_receiver_id(
        self, controller_client: TestClient,
    ) -> None:
        """ECDH mode without ``receiver_id`` in body → 400."""
        sid = "11111111-1111-1111-1111-111111111111"
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/activate",
            json={
                "privacy": {
                    "protocol": "RTP",
                    "mode": "ECDH_AES-128-CTR",
                    "curve": "secp521r1",
                },
            },
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_sender_activate_non_ecdh_skips_receiver_fetch(
        self, controller_client: TestClient,
    ) -> None:
        """Non-ECDH modes don't fetch the receiver's /active/ — the
        PATCH body carries only protocol + mode.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.get_receiver_active = AsyncMock()  # type: ignore[method-assign]
        remote.patch_sender_staged = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body={"ok": True}),
        )
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/activate",
            json={
                "privacy": {
                    "protocol": "RTP",
                    "mode": "AES-128-CTR",
                },
            },
        )
        assert resp.status == 200
        remote.get_receiver_active.assert_not_awaited()
        body = remote.patch_sender_staged.call_args.args[2]
        leg = body["transport_params"][0]
        assert leg["ext_privacy_mode"] == "AES-128-CTR"
        assert "ext_privacy_ecdh_receiver_public_key" not in leg
        assert "ext_privacy_ecdh_curve" not in leg

    @pytest.mark.asyncio
    async def test_sender_activate_without_privacy_uses_legacy_path(
        self, controller_client: TestClient,
    ) -> None:
        """No ``privacy`` in body → legacy PATCH (just master_enable).
        Backward-compatible for existing non-PEP clients.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.patch_sender_staged = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body={"ok": True}),
        )
        resp = await controller_client.post(
            f"{PREFIX}/api/senders/{sid}/activate",
        )
        assert resp.status == 200
        body = remote.patch_sender_staged.call_args.args[2]
        assert body["master_enable"] is True
        assert "transport_params" not in body

    @pytest.mark.asyncio
    async def test_receiver_activate_with_privacy_forwards_key_fields(
        self, controller_client: TestClient,
    ) -> None:
        """Receiver activate with privacy: GETs sender /active/,
        extracts key_generator/version/id + (ECDH only) sender pub key,
        threads them into the receiver's PATCH body.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.get_sender_active = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body={
                "transport_params": [{
                    "ext_privacy_key_generator": "prng-v2",
                    "ext_privacy_key_version": "3",
                    "ext_privacy_key_id": "deadbeef",
                    "ext_privacy_ecdh_sender_public_key": "0xTXPUBKEY",
                }],
            }),
        )
        remote.get_sender_transportfile = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body="v=0\r\n"),
        )
        remote.patch_receiver_staged = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body={"ok": True}),
        )
        resp = await controller_client.post(
            f"{PREFIX}/api/receivers/{rid}/activate",
            json={
                "sender_id": sid,
                "privacy": {
                    "protocol": "RTP",
                    "mode": "ECDH_AES-256-CTR",
                    "curve": "secp521r1",
                },
            },
        )
        assert resp.status == 200
        body = remote.patch_receiver_staged.call_args.args[2]
        leg = body["transport_params"][0]
        assert leg["ext_privacy_key_generator"] == "prng-v2"
        assert leg["ext_privacy_key_version"] == "3"
        assert leg["ext_privacy_key_id"] == "deadbeef"
        assert leg["ext_privacy_ecdh_sender_public_key"] == "0xTXPUBKEY"
        assert leg["ext_privacy_protocol"] == "RTP"
        assert leg["ext_privacy_mode"] == "ECDH_AES-256-CTR"
        # SDP still attached (receiver always needs it).
        assert body["transport_file"]["data"] == "v=0\r\n"

    @pytest.mark.asyncio
    async def test_configure_page_renders_privacy_panel(
        self, controller_client: TestClient,
    ) -> None:
        """Configure page shows the Privacy panel (dropdowns + lock
        icon) when the intersection is non-empty.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body=self._transport_params_with_privacy(),
            ),
        )
        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        assert "privacy-panel" in text
        assert "Privacy Encryption" in text
        assert "privacy-protocol" in text
        assert "privacy-mode" in text
        # ECDH mode in the intersection → Curve dropdown rendered too.
        assert "privacy-curve" in text

    @pytest.mark.asyncio
    async def test_configure_page_renders_cannot_negotiate_banner(
        self, controller_client: TestClient,
    ) -> None:
        """When the Privacy intersection is empty across selected
        resources that each declare some PEP, the panel shows the
        red banner instead of dropdowns.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        # Declare disjoint enums for the single selected sender so
        # the intersection collapses to empty.
        remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body=[{
                    "ext_privacy_protocol": {"enum": ["NULL"]},
                    "ext_privacy_mode": {"enum": ["NULL"]},
                }],
            ),
        )
        # Seed the NODE (not the device) with the exclusive service so
        # the panel renders (requires at least one non-empty signal;
        # here it's the service advertisement).
        cache: ResourceCache = controller_client.app["_test_cache"]
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="https://remote/exclusive/v1.0/",
        ))
        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        assert "privacy-panel" in text
        assert "cannot be negotiated" in text.lower()

    @pytest.mark.asyncio
    async def test_configure_page_disables_exclusivity_when_service_missing(
        self, controller_client: TestClient,
    ) -> None:
        """No reservation service on the device → Exclusivity checkbox
        is rendered disabled with a tooltip.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body=self._transport_params_with_privacy(),
            ),
        )
        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        # The Privacy section is rendered (there's a PEP intersection).
        assert "privacy-exclusivity" in text
        # And the checkbox carries ``disabled`` because dev1 in the
        # default seed has no reservation service.
        import re
        m = re.search(
            r'id="privacy-exclusivity"[^>]*?disabled', text,
        )
        assert m is not None, (
            "Exclusivity checkbox should be disabled when the device "
            "doesn't advertise the reservation service"
        )

    @pytest.mark.asyncio
    async def test_privacy_panel_has_two_axis_indicators_when_negotiable(
        self, controller_client: TestClient,
    ) -> None:
        """Regression for the "yellow reads as insecure" fix.

        When PEP is negotiable AND no reservation is held, the panel
        must render:

          * a PEP dot with the ``is-ok`` class → green ("Encrypted");
          * a lock indicator whose initial state is ``is-open``
            (open padlock, grey) because reservation is an optional
            extra guarantee, not a missing security requirement.

        Neither indicator should use amber for a resting state.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body=self._transport_params_with_privacy(),
            ),
        )
        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        # Two distinct indicators in the header.
        assert 'data-role="privacy-pep-indicator"' in text
        assert 'data-role="privacy-lock-indicator"' in text
        # PEP axis is green ("is-ok"), labelled "Encrypted".
        assert "privacy-pep-indicator\n                   is-ok" in text \
            or "privacy-pep-indicator is-ok" in text \
            or "is-ok" in text  # rendered across lines by the template
        assert "Encrypted" in text
        # Lock paths for both open/closed are present; CSS picks which
        # one to show based on ``.is-reserved`` (default: open).
        assert "pl-shackle-open" in text
        assert "pl-shackle-closed" in text
        # Panel does NOT carry ``is-reserved`` on initial render —
        # reservation requires an explicit user acquire.
        import re
        panel_open = re.search(r'class="privacy-panel[^"]*"', text)
        assert panel_open is not None
        assert "is-reserved" not in panel_open.group(0)
        # The old "Ready"/"Blocked" single-chip phrasing is gone.
        # (Guard so anyone reintroducing it trips this assertion.)
        assert "privacy-status-chip" not in text
        # "Blocked" alone must not be part of the labels when PEP is ok.
        # (Still allowed as a substring elsewhere — this asserts the
        # specific label rendered inside the PEP indicator.)
        label_re = re.search(
            r'data-role="privacy-pep-indicator"[^>]*>.*?<span[^>]*class="privacy-indicator-label"[^>]*>\s*([^\s<]+)',
            text, re.DOTALL,
        )
        assert label_re is not None
        assert label_re.group(1) == "Encrypted"

    @pytest.mark.asyncio
    async def test_privacy_controls_locked_when_any_sender_active(
        self, controller_client: TestClient,
    ) -> None:
        """When any selected sender is active, the Privacy dropdowns
        AND the Exclusivity toggle MUST be disabled — toggling either
        while a stream runs is a no-op on the running stream (PEP
        parameters + exclusive_key only enter the key-derivation at
        ``master_enable=true`` activation). The UI surfaces this via
        ``disabled`` attrs + an amber "Locked while active" note.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        # Mark the sender as active in the cache.
        cache: ResourceCache = controller_client.app["_test_cache"]
        await cache.upsert("sender", _make_sender(sid, "dev1", active=True))
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body=self._transport_params_with_privacy(),
            ),
        )
        # Node advertises the reservation service — we want to prove
        # that Exclusivity is locked by the active-state rule, NOT
        # by the "service missing" rule.
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="https://remote/exclusive/v1.0/",
        ))
        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()

        import re
        # Every Privacy select carries ``disabled``. The regex walks
        # past the newline+indent whitespace that Jinja emits between
        # attributes, so a real ``disabled`` is matched whether it
        # sits on the same line or the next.
        for role in ("privacy-protocol", "privacy-mode", "privacy-curve"):
            m = re.search(
                rf'data-role="{role}"[\s\S]*?\bdisabled\b[\s\S]*?>',
                text,
            )
            assert m is not None, (
                f"{role} dropdown should be disabled when the sender is active"
            )
        # Exclusivity carries ``disabled``. No "(deactivate to
        # change)" / "(not available)" label text — the active-state
        # lock is communicated only by ``disabled`` + the footer
        # notice + the tooltip, so the row width never shifts on a
        # live state flip.
        excl = re.search(
            r'id="privacy-exclusivity"[\s\S]*?\bdisabled\b[\s\S]*?>', text,
        )
        assert excl is not None
        assert "deactivate to change" not in text
        assert "(not available)" not in text
        # Locked-note span is present AND visible (no ``hidden``
        # attribute) when a resource is active.
        note = re.search(
            r'data-role="privacy-locked-note"[\s\S]*?>', text,
        )
        assert note is not None
        assert "hidden" not in note.group(0), (
            "locked-note span must not carry ``hidden`` when a "
            "resource is active"
        )
        assert "Locked while active" in text
        # The form advertises the locked state via a data attribute
        # so any future JS can read it without re-deriving.
        assert 'data-privacy-locked="1"' in text

    @pytest.mark.asyncio
    async def test_privacy_panel_publishes_exclusivity_availability_flag(
        self, controller_client: TestClient,
    ) -> None:
        """The panel carries ``data-exclusivity-available`` so the
        browser-side live reconciler can distinguish the two
        disable reasons: "active" (transient) vs "no service
        advertised" (fixed for the page's lifetime). A live event
        flipping the panel back to inactive must not re-enable the
        toggle when the service was never there.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body=self._transport_params_with_privacy(),
            ),
        )
        # Default seed: no reservation service advertised on any Node.
        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        assert 'data-exclusivity-available="0"' in text

        # Now with the service present, the attribute flips to "1".
        cache: ResourceCache = controller_client.app["_test_cache"]
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="https://remote/exclusive/v1.0/",
        ))
        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        assert 'data-exclusivity-available="1"' in text

    @pytest.mark.asyncio
    async def test_privacy_controls_unlocked_when_all_inactive(
        self, controller_client: TestClient,
    ) -> None:
        """Baseline: with every selected resource inactive, the
        Privacy dropdowns + Exclusivity toggle are enabled and no
        locked-note banner renders. Pins the default "editable"
        state so a regression to always-locked trips the test.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        # Default seeded sender is inactive (`active=False`).
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body=self._transport_params_with_privacy(),
            ),
        )
        cache: ResourceCache = controller_client.app["_test_cache"]
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="https://remote/exclusive/v1.0/",
        ))
        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()

        import re
        # None of the Privacy selects is disabled. Match across
        # Jinja whitespace up to the tag close so attribute ordering
        # doesn't matter.
        for role in ("privacy-protocol", "privacy-mode", "privacy-curve"):
            tag = re.search(
                rf'data-role="{role}"[\s\S]*?>',
                text,
            )
            assert tag is not None
            assert "disabled" not in tag.group(0), (
                f"{role} dropdown must NOT be disabled when no "
                f"selected resource is active"
            )
        # Exclusivity is enabled too.
        excl_tag = re.search(r'id="privacy-exclusivity"[\s\S]*?>', text)
        assert excl_tag is not None
        assert "disabled" not in excl_tag.group(0)
        # Locked-note span is always rendered (so show/hide via the
        # ``hidden`` attribute doesn't reflow the panel). When no
        # resource is active it MUST carry ``hidden``.
        note = re.search(
            r'data-role="privacy-locked-note"[\s\S]*?>', text,
        )
        assert note is not None
        assert "hidden" in note.group(0), (
            "locked-note span must be hidden when no resource is active"
        )
        assert 'data-privacy-locked="0"' in text

    @pytest.mark.asyncio
    async def test_privacy_controls_locked_when_receiver_active(
        self, controller_client: TestClient,
    ) -> None:
        """On the receivers_configure path, an active RECEIVER in the
        selection locks the Privacy controls — same rule, symmetric.
        """
        rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        sid = "11111111-1111-1111-1111-111111111111"
        cache: ResourceCache = controller_client.app["_test_cache"]
        await cache.upsert("receiver", _make_receiver(rid, "dev1", active=True))
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="https://remote/exclusive/v1.0/",
        ))
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body=self._transport_params_with_privacy(),
            ),
        )
        remote.get_receiver_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body=self._transport_params_with_privacy(),
            ),
        )
        resp = await controller_client.get(
            f"{PREFIX}/receivers/configure"
            f"?receiver_ids={rid}&sender_ids={sid}&mode=single",
        )
        assert resp.status == 200
        text = await resp.text()
        import re
        m = re.search(
            r'id="privacy-exclusivity"[\s\S]*?\bdisabled\b[\s\S]*?>', text,
        )
        assert m is not None, (
            "Exclusivity must be disabled when the selected receiver "
            "is active"
        )
        assert "Locked while active" in text

    @pytest.mark.asyncio
    async def test_privacy_panel_pep_indicator_red_when_cannot_negotiate(
        self, controller_client: TestClient,
    ) -> None:
        """When PEP cannot be negotiated, the PEP indicator turns red
        (``is-blocked``) and the label reads "Blocked". The lock
        indicator stays open/grey — a failed negotiation doesn't
        imply anything about reservation.
        """
        sid = "11111111-1111-1111-1111-111111111111"
        remote: RemoteNodeClient = controller_client.app["_test_remote_stub"]
        remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200,
                body=[{
                    "ext_privacy_protocol": {"enum": ["NULL"]},
                    "ext_privacy_mode": {"enum": ["NULL"]},
                }],
            ),
        )
        # Node advertises the reservation service so the panel renders
        # at all (otherwise the whole panel is hidden).
        cache: ResourceCache = controller_client.app["_test_cache"]
        await cache.upsert("node", _make_node_resource(
            "node-SNX00001", "SNX00001",
            exclusive_href="https://remote/exclusive/v1.0/",
        ))
        resp = await controller_client.get(
            f"{PREFIX}/senders/configure?sender_ids={sid}",
        )
        assert resp.status == 200
        text = await resp.text()
        # PEP axis is red.
        assert "is-blocked" in text
        import re
        label_re = re.search(
            r'data-role="privacy-pep-indicator"[^>]*>.*?<span[^>]*class="privacy-indicator-label"[^>]*>\s*([^\s<]+)',
            text, re.DOTALL,
        )
        assert label_re is not None
        assert label_re.group(1) == "Blocked"
        # Lock indicator is present and in its default (open) state —
        # no ``is-reserved`` on the panel.
        assert 'data-role="privacy-lock-indicator"' in text
        panel_open = re.search(r'class="privacy-panel[^"]*"', text)
        assert panel_open is not None
        assert "is-reserved" not in panel_open.group(0)


# ---------------------------------------------------------------------------
# Debug-in-depth facility
# ---------------------------------------------------------------------------

class TestDebugFacility:
    """Covers the ``--debug-in-depth`` instrumentation:

    * default (off): debug endpoints 404, ``<html>`` has no
      ``data-debug`` attribute, no ``X-Trace-Id`` response header;
    * on: endpoints respond, debug middleware stamps trace ids,
      ``client-event`` appends JSONL to the rotating log,
      snapshot returns populated state.
    """

    @pytest.mark.asyncio
    async def test_debug_off_endpoints_404(
        self, controller_client: TestClient,
    ) -> None:
        resp = await controller_client.get(f"{PREFIX}/api/debug/snapshot")
        assert resp.status == 404
        resp = await controller_client.post(
            f"{PREFIX}/api/debug/client-event",
            json={"kind": "probe"},
        )
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_debug_off_no_trace_header(
        self, controller_client: TestClient,
    ) -> None:
        """When tracing is off the response must not echo X-Trace-Id.

        The header is meant as a server→client debug signal; leaking
        trace ids in the non-debug case would only pollute response
        headers on every API hit.
        """
        resp = await controller_client.get(f"{PREFIX}/api/senders")
        assert resp.status == 200
        assert "X-Trace-Id" not in resp.headers

    @pytest.mark.asyncio
    async def test_debug_off_html_has_no_data_debug(
        self, controller_client: TestClient,
    ) -> None:
        resp = await controller_client.get(f"{PREFIX}/")
        text = await resp.text()
        assert 'data-debug="1"' not in text

    @pytest.mark.asyncio
    async def test_debug_on_endpoints_work(
        self, aiohttp_client: Any, tmp_path: Any,
    ) -> None:
        """Snapshot + client-event round-trip against a trace-enabled app."""
        cache = ResourceCache()
        await _seed_cache(cache)
        remote = RemoteNodeClient()
        remote.get_sender_active_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200, body={"constraint_sets": []},
            ),
        )
        remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body=[{}]),
        )
        remote.get_receiver_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body=[{}]),
        )
        log_path = str(tmp_path / "controller-debug.log")
        app = create_controller_app(
            _make_node(), cache=cache, remote_client=remote,
            admin_password=ADMIN_PASSWORD,
            debug_log_path=log_path,
        )
        tc = await aiohttp_client(app)
        await _log_in(tc)

        snap = await tc.get(f"{PREFIX}/api/debug/snapshot")
        assert snap.status == 200
        body = await snap.json()
        assert body["log_path"] == log_path
        # Seeded cache has at least one of each resource kind.
        assert body["nodes"]
        assert body["devices"]
        assert body["senders"]
        assert body["receivers"]
        # One admin session was minted by _log_in.
        assert body["admin_sessions"] == 1

        # Client-event posts an arbitrary kind with a caller-supplied
        # trace id. The server must echo the same trace id back.
        post = await tc.post(
            f"{PREFIX}/api/debug/client-event",
            json={
                "kind": "click",
                "trace_id": "deadbeefcafe",
                "target": "btn-activate",
            },
        )
        assert post.status == 200
        echo = await post.json()
        assert echo["logged"] is True
        assert echo["trace_id"] == "deadbeefcafe"

        # And the log file contains the two events + the session-start
        # marker, one JSON object per line.
        with open(log_path, "r", encoding="utf-8") as fp:
            lines = [json.loads(line) for line in fp if line.strip()]
        kinds = [rec["kind"] for rec in lines]
        assert "session_start" in kinds
        assert "client.click" in kinds
        click_rec = next(r for r in lines if r["kind"] == "client.click")
        assert click_rec["trace_id"] == "deadbeefcafe"
        assert click_rec["target"] == "btn-activate"

    @pytest.mark.asyncio
    async def test_debug_on_middleware_stamps_trace_id(
        self, aiohttp_client: Any, tmp_path: Any,
    ) -> None:
        """A ``fetch`` with X-Trace-Id: abc must echo abc back on the
        response, AND the log must carry a ``request_in`` entry with
        that same id.
        """
        cache = ResourceCache()
        await _seed_cache(cache)
        remote = RemoteNodeClient()
        remote.get_sender_active_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200, body={"constraint_sets": []},
            ),
        )
        remote.get_sender_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body=[{}]),
        )
        remote.get_receiver_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(status=200, body=[{}]),
        )
        log_path = str(tmp_path / "controller-debug.log")
        app = create_controller_app(
            _make_node(), cache=cache, remote_client=remote,
            admin_password=ADMIN_PASSWORD,
            debug_log_path=log_path,
        )
        tc = await aiohttp_client(app)
        await _log_in(tc)

        resp = await tc.get(
            f"{PREFIX}/api/senders", headers={"X-Trace-Id": "feedface1234"},
        )
        assert resp.status == 200
        assert resp.headers.get("X-Trace-Id") == "feedface1234"

        with open(log_path, "r", encoding="utf-8") as fp:
            lines = [json.loads(line) for line in fp if line.strip()]
        req_ins = [r for r in lines if r["kind"] == "request_in"
                   and r.get("trace_id") == "feedface1234"]
        assert req_ins, (
            "expected a request_in entry with the caller's trace id; "
            f"got kinds={[r['kind'] for r in lines]}"
        )

    @pytest.mark.asyncio
    async def test_debug_on_html_has_data_debug(
        self, aiohttp_client: Any, tmp_path: Any,
    ) -> None:
        cache = ResourceCache()
        await _seed_cache(cache)
        remote = RemoteNodeClient()
        remote.get_sender_active_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200, body={"constraint_sets": []},
            ),
        )
        log_path = str(tmp_path / "controller-debug.log")
        app = create_controller_app(
            _make_node(), cache=cache, remote_client=remote,
            admin_password=ADMIN_PASSWORD,
            debug_log_path=log_path,
        )
        tc = await aiohttp_client(app)
        await _log_in(tc)
        resp = await tc.get(f"{PREFIX}/")
        assert resp.status == 200
        text = await resp.text()
        assert 'data-debug="1"' in text

    @pytest.mark.asyncio
    async def test_client_event_payload_size_limit(
        self, aiohttp_client: Any, tmp_path: Any,
    ) -> None:
        """Oversized client-event payloads are rejected with 413.

        Prevents a runaway browser from flooding the rotating log
        with one giant event per tick.
        """
        cache = ResourceCache()
        await _seed_cache(cache)
        remote = RemoteNodeClient()
        remote.get_sender_active_constraints = AsyncMock(  # type: ignore[method-assign]
            return_value=RemoteCallResult(
                status=200, body={"constraint_sets": []},
            ),
        )
        log_path = str(tmp_path / "controller-debug.log")
        app = create_controller_app(
            _make_node(), cache=cache, remote_client=remote,
            admin_password=ADMIN_PASSWORD,
            debug_log_path=log_path,
        )
        tc = await aiohttp_client(app)
        await _log_in(tc)

        bloated = {"kind": "ui", "filler": "x" * 8000}
        resp = await tc.post(
            f"{PREFIX}/api/debug/client-event", json=bloated,
        )
        assert resp.status == 413
