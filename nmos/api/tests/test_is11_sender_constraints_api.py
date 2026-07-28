# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IS-11 PUT /senders/{senderId}/constraints/active status-code semantics.

The IS-11 Stream Compatibility Management API distinguishes two rejection
reasons for active constraints (StreamCompatibilityManagementAPI.raml, PUT
/senders/{senderId}/constraints/active):

* **400** — "the Constraints are rejected due to schema validation failure or
  if the Sender doesn't support a Parameter Constraint URN used in any of the
  Constraint Sets."
* **422** — "the Sender isn't capable to adhere to the proposed Constraints
  although the request is valid and the Sender supports all the Parameter
  Constraint URNs used."

This drives the real PUT handler and asserts each code, plus the empty-array
reset (200). It is the API-level guard for the discrimination that a single
422-for-everything handler silently fails (AMWA IS-11 test_06_01).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from nmos.api import create_app
from nmos.node import Node
from nmos.node.config import ConfigBuilder
from nmos.node.updates import SenderUpdate

_SC = "/x-nmos/streamcompatibility/v1.0/senders"


@pytest.fixture
async def client(aiohttp_client):  # type: ignore
    node = Node()
    node.init(serial_number="TST00001")
    cfg = json.loads(
        (Path(__file__).parent.parent.parent
         / "node" / "config" / "builtin" / "config10.json").read_text())
    builder = ConfigBuilder(node, verbose=False)
    for sender_cfg in cfg.get("senders", []):
        builder._build_sender_pipeline(sender_cfg)
    return await aiohttp_client(create_app(node))


async def _video_sender_id(client) -> str:
    """The (non-mux) video sender id — IS-04 Senders carry no ``format``,
    so match on the "Video" label."""
    sj = await (await client.get("/x-nmos/node/v1.3/senders")).json()
    return next(s["id"] for s in sj
                if "Video" in s.get("label", "") and "Mux" not in s.get("label", ""))


async def _put(client, sid: str, body: dict):
    return await client.put(f"{_SC}/{sid}/constraints/active", json=body)


@pytest.mark.asyncio
async def test_unsupported_param_constraint_urn_is_400(client) -> None:
    """An unsupported Parameter Constraint URN → 400 (AMWA test_06_01)."""
    sid = await _video_sender_id(client)
    resp = await _put(client, sid, {
        "constraint_sets": [{"urn:x-nmos:cap:not:existing": {"enum": [""]}}]})
    assert resp.status == 400, await resp.text()


@pytest.mark.asyncio
async def test_supported_urns_unsatisfiable_values_is_422(client) -> None:
    """All URNs supported but no capability set can satisfy the values → 422."""
    sid = await _video_sender_id(client)
    resp = await _put(client, sid, {"constraint_sets": [{
        "urn:x-nmos:cap:meta:preference": 100,
        "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
        "urn:x-nmos:cap:format:profile": {"enum": ["NoSuchProfile"]},
    }]})
    assert resp.status == 422, await resp.text()


@pytest.mark.asyncio
async def test_empty_constraint_sets_reset_is_200(client) -> None:
    """An empty ``constraint_sets`` array resets to unconstrained → 200."""
    sid = await _video_sender_id(client)
    resp = await _put(client, sid, {"constraint_sets": []})
    assert resp.status == 200, await resp.text()

    status = await (await client.get(f"{_SC}/{sid}/status/")).json()
    assert status["state"] == "unconstrained"


@pytest.mark.asyncio
async def test_active_sender_is_423(client) -> None:
    """A Sender that is already active rejects an active-constraints PUT."""
    sid = await _video_sender_id(client)
    node = client.app["node"]
    node.update_sender(sid, SenderUpdate(subscription_active=True))

    resp = await _put(client, sid, {"constraint_sets": []})
    assert resp.status == 423, await resp.text()


@pytest.mark.asyncio
async def test_activation_while_body_is_read_is_still_423(
    client, monkeypatch,
) -> None:
    """An activation landing during the body read must still yield 423.

    ``await request.text()`` is the handler's only suspension point, so it is
    the only place another request can run. If it sat between the "is the
    Sender active?" check and the mutation that check guards, an IS-05
    activation completing in that window would leave active constraints being
    applied to a now-active Sender — precisely what 423 exists to reject.

    Patching ``text()`` to activate the Sender as it is awaited reproduces that
    interleaving deterministically, without needing two real concurrent
    requests. It passes only while the body read stays above the check.
    """
    sid = await _video_sender_id(client)
    node = client.app["node"]

    original_text = web.Request.text

    async def activating_text(self: web.Request) -> Any:
        body = await original_text(self)
        # Stand-in for a concurrent IS-05 activation completing right here.
        node.update_sender(sid, SenderUpdate(subscription_active=True))
        return body

    monkeypatch.setattr(web.Request, "text", activating_text)

    resp = await _put(client, sid, {"constraint_sets": []})
    assert resp.status == 423, await resp.text()

    status = await (await client.get(f"{_SC}/{sid}/status/")).json()
    assert status["state"] != "constrained"
