# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""End-to-end IS-05 → IS-11 test through the real HTTP handlers.

A Receiver that accepts an IS-05 PATCH carrying a compliant SDP transport_file
MUST report IS-11 stream-compatibility ``compliant_stream``; deactivating
returns it to ``unknown`` (no stream to evaluate).

This drives the actual connection-API PATCH handler (which calls
``do_activation(..., has_sdp=False)``) and the streamcompatibility status
handler — the integration path a prior unit-level fix missed because it
exercised ``do_activation`` directly with ``has_sdp=True``.
"""

from __future__ import annotations

import json

import pytest

from nmos.api import create_app
from nmos.node import Node

_CONN = "/x-nmos/connection/v1.1/single"
_STATUS = "/x-nmos/streamcompatibility/v1.0/receivers"

# A compliant SDP for config10's video receiver (H.264 1920x1080@60, 10-bit,
# YCbCr-4:2:2, SDR, RTP privacy — config10's receiver requires PEP). The H.264
# format params (measuredpixclk/vtotal/htotal/profile) are needed for the
# SDP→caps extraction; the a=privacy line makes transport:privacy match.
_COMPLIANT_SDP = "\r\n".join([
    "v=0",
    "o=- 536887296 536887296 IN IP4 127.0.0.1",
    "s=Net Stream 0 Video 0",
    "t=0 0",
    "m=video 27500 RTP/AVP 96",
    "c=IN IP4 239.1.0.1/128",
    "b=AS:43200",
    "a=rtpmap:96 H264/90000",
    ("a=fmtp:96 width=1920; height=1080; depth=10; exactframerate=60; "
     "sampling=YCbCr-4:2:2; colorimetry=BT709; TP=2110TPW; TCS=SDR; "
     "RANGE=NARROW; measuredpixclk=148500000; vtotal=1125; htotal=2200; "
     "IPMX; profile-level-id=7a0028; packetization-mode=1"),
    "a=ts-refclk:localmac=00-00-00-00-00-00",
    "a=mediaclk:sender",
    ("a=privacy:protocol=RTP; mode=AES-128-CTR; iv=2870a245b85ccfcf; "
     "key_generator=ebe030ba73e16ed71127cb4e0fcfa918; key_version=81549802; "
     "key_id=0001020304050607"),
    "",
])


@pytest.fixture
async def client(aiohttp_client, monkeypatch):  # type: ignore
    # No real streaming engine in a unit test — only the IS-04/05/11 state
    # machine is under test here.
    import nmos.node.activation_engine as ae
    monkeypatch.setattr(ae, "_manage_engine_lifecycle", lambda *a, **k: None)

    node = Node()
    node.init(serial_number="TST00001")
    from pathlib import Path
    from nmos.node.config import ConfigBuilder
    cfg_path = (Path(__file__).parent.parent.parent
                / "node" / "config" / "builtin" / "config10.json")
    cfg = json.loads(cfg_path.read_text())
    builder = ConfigBuilder(node, verbose=False)
    for sender_cfg in cfg.get("senders", []):
        builder._build_sender_pipeline(sender_cfg)
    for receiver_cfg in cfg.get("receivers", []):
        builder._build_receiver_from_config(receiver_cfg)
    return await aiohttp_client(create_app(node))


async def _video_ids(client) -> tuple[str, str]:
    """(video_sender_id, video_receiver_id) discovered via the node API.

    IS-04 Senders carry no ``format`` (only Receivers/Flows do), so the
    sender is matched by its (non-mux) "Video" label.
    """
    sj = await (await client.get("/x-nmos/node/v1.3/senders")).json()
    rj = await (await client.get("/x-nmos/node/v1.3/receivers")).json()

    def _is_video_label(label: str) -> bool:
        return "Video" in label and "Mux" not in label

    sid = next(s["id"] for s in sj if _is_video_label(s.get("label", "")))
    rid = next(r["id"] for r in rj if r.get("format", "").endswith("video"))
    return sid, rid


async def _status(client, rid: str) -> str:
    resp = await client.get(f"{_STATUS}/{rid}/status/")
    assert resp.status == 200, await resp.text()
    return (await resp.json())["state"]


@pytest.mark.asyncio
async def test_is05_patch_with_sdp_sets_is11_compliant_then_unknown(client) -> None:
    sender_id, receiver_id = await _video_ids(client)

    # No stream yet → unknown.
    assert await _status(client, receiver_id) == "unknown"

    # Activate the receiver via IS-05 with a compliant SDP transport_file.
    patch = {
        "master_enable": True,
        "sender_id": sender_id,
        "transport_file": {"type": "application/sdp", "data": _COMPLIANT_SDP},
        "activation": {"mode": "activate_immediate"},
    }
    resp = await client.patch(
        f"{_CONN}/receivers/{receiver_id}/staged", json=patch)
    assert resp.status == 200, await resp.text()

    # IS-11: PATCH accepted + SDP verified → compliant_stream.
    assert await _status(client, receiver_id) == "compliant_stream"

    # Deactivate → no stream to evaluate → unknown.
    resp = await client.patch(
        f"{_CONN}/receivers/{receiver_id}/staged",
        json={"master_enable": False, "activation": {"mode": "activate_immediate"}})
    assert resp.status == 200, await resp.text()
    assert await _status(client, receiver_id) == "unknown"
