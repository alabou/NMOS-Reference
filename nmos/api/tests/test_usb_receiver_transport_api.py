# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""End-to-end IS-05 test for the USB (TCP) receiver connect endpoint.

A USB receiver connects to the sender; the sender's endpoint travels in the
transport-file SDP (``c=`` connection address, ``m=`` media port). Driving a
receiver PATCH with that SDP through the real connection-API handler MUST map
the endpoint into the receiver's active ``source_ip`` / ``source_port`` (what
the TCP receiver dials), not drop it.

This is the integration guard for the bug where the SDP→params mapping used
the multicast-RTP rule (connection→MulticastIp, port→DestinationPort) and the
streaming layer read DestinationIp/DestinationPort — so a USB receiver ended
up dialing 0.0.0.0:0, failing to connect (link down, monitor idle). The
loopback transport test never caught it because it called tcp_receiver with
explicit dest values, bypassing this wiring.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from nmos.api import create_app
from nmos.node import Node
from nmos.node.config import ConfigBuilder

_CONN = "/x-nmos/connection/v1.1/single"


@pytest.fixture
async def client(aiohttp_client, monkeypatch):  # type: ignore
    # No real streaming engine — only the IS-05 state machine + SDP→params
    # mapping is under test here.
    import nmos.node.activation_engine as ae
    monkeypatch.setattr(ae, "_manage_engine_lifecycle", lambda *a, **k: None)

    node = Node()
    node.init(serial_number="TST00001")
    cfg = json.loads(
        (Path(__file__).parent.parent.parent
         / "node" / "config" / "builtin" / "config10.json").read_text())
    builder = ConfigBuilder(node, verbose=False)
    for sender_cfg in cfg.get("senders", []):
        builder._build_sender_pipeline(sender_cfg)
    for receiver_cfg in cfg.get("receivers", []):
        builder._build_receiver_from_config(receiver_cfg)
    return await aiohttp_client(create_app(node))


async def _usb_ids(client) -> tuple[str, str]:
    sj = await (await client.get("/x-nmos/node/v1.3/senders")).json()
    rj = await (await client.get("/x-nmos/node/v1.3/receivers")).json()
    sid = next(s["id"] for s in sj if "USB" in s.get("label", ""))
    rid = next(r["id"] for r in rj if "USB" in r.get("label", ""))
    return sid, rid


@pytest.mark.asyncio
async def test_usb_receiver_active_params_carry_sender_endpoint(client) -> None:
    sender_id, receiver_id = await _usb_ids(client)

    # Activate the sender, then read the transport-file SDP it advertises.
    resp = await client.patch(
        f"{_CONN}/senders/{sender_id}/staged",
        json={"master_enable": True, "activation": {"mode": "activate_immediate"}})
    assert resp.status == 200, await resp.text()
    sdp = await (await client.get(
        f"{_CONN}/senders/{sender_id}/transportfile/")).text()

    # The sender's connect endpoint, straight off the SDP.
    c_ip = next(ln.split()[-1] for ln in sdp.splitlines() if ln.startswith("c="))
    m = re.search(r"m=application (\d+) TCP usb", sdp)
    assert m is not None, sdp
    m_port = int(m.group(1))

    # PATCH the receiver with that SDP and activate.
    resp = await client.patch(f"{_CONN}/receivers/{receiver_id}/staged", json={
        "master_enable": True,
        "sender_id": sender_id,
        "transport_file": {"type": "application/sdp", "data": sdp},
        "activation": {"mode": "activate_immediate"},
    })
    assert resp.status == 200, await resp.text()

    # The receiver's connect endpoint MUST be the sender's c=/m= → source_ip/port.
    active = await (await client.get(
        f"{_CONN}/receivers/{receiver_id}/active/")).json()
    tp = active["transport_params"][0]
    assert tp.get("source_ip") == c_ip, tp
    assert tp.get("source_port") == m_port, tp
