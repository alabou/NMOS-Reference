# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Standalone demo for the embedded NMOS Controller UI.

Seeds an in-process ``ResourceCache`` with three realistic devices
(two Matrox-style senders-and-receivers, one pure sender) plus their
natural groups, then boots the controller aiohttp app on a local
port so you can exercise every page and JSON endpoint from a browser
or with ``curl``.

No registry, no TLS, no OAuth2 — pure walk-through of the controller
surfaces.

Usage:
    python3 demo_controller.py [--port 5051]

Then open:
    http://127.0.0.1:5051/controller/
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from aiohttp import web

from nmos.config import ALLOW_NON_TLS_FOR_TESTING
import nmos.config as nmos_config
from nmos.controller import create_controller_app
from nmos.controller.app import URL_PREFIX
from nmos.controller.cache import ResourceCache
from nmos.controller.grouping import GROUP_HINT_TAG
from nmos.node import Node


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------

def _tagged(hint: str) -> dict[str, list[str]]:
    return {GROUP_HINT_TAG: [hint]}


def _device(
    did: str, serial: str, label: str,
    conn_href: str = "https://demo-remote.local/x-nmos/connection/v1.1",
) -> dict[str, object]:
    return {
        "id": did,
        "label": label,
        "description": f"Example demo device {serial}",
        "controls": [
            {"type": "urn:x-nmos:control:sr-ctrl/v1.1", "href": conn_href},
        ],
    }


def _sender(
    sid: str, device_id: str, label: str, hint: str, active: bool = False,
) -> dict[str, object]:
    return {
        "id": sid,
        "device_id": device_id,
        "label": label,
        "description": f"Sender {label}",
        "tags": _tagged(hint),
        "subscription": {"active": active, "receiver_id": None},
    }


def _receiver(
    rid: str, device_id: str, label: str, hint: str, active: bool = False,
) -> dict[str, object]:
    return {
        "id": rid,
        "device_id": device_id,
        "label": label,
        "description": f"Receiver {label}",
        "tags": _tagged(hint),
        "subscription": {"active": active, "sender_id": None},
    }


async def seed(cache: ResourceCache) -> None:
    # Device 1 — SNX00001, one video group (L/R) + one audio group (L/R)
    await cache.upsert("device", _device("11111111-dev1-0000-0000-000000000001",
                                          "SNX00001", "Example SNX00001 (lab)"))
    await cache.upsert("sender", _sender(
        "11111111-snd0-0000-0000-000000000001",
        "11111111-dev1-0000-0000-000000000001",
        "xyz1-video-L", "RTP 0:VIDEO 0",
        active=True,
    ))
    await cache.upsert("sender", _sender(
        "11111111-snd0-0000-0000-000000000002",
        "11111111-dev1-0000-0000-000000000001",
        "xyz1-video-R", "RTP 0:VIDEO 1",
    ))
    await cache.upsert("sender", _sender(
        "11111111-snd0-0000-0000-000000000003",
        "11111111-dev1-0000-0000-000000000001",
        "xyz1-audio-L", "RTP 1:AUDIO 0",
    ))
    await cache.upsert("sender", _sender(
        "11111111-snd0-0000-0000-000000000004",
        "11111111-dev1-0000-0000-000000000001",
        "xyz1-audio-R", "RTP 1:AUDIO 1",
    ))
    await cache.upsert("receiver", _receiver(
        "11111111-rcv0-0000-0000-000000000001",
        "11111111-dev1-0000-0000-000000000001",
        "xyz1-rx-video-L", "RTP 0:VIDEO 0",
    ))
    await cache.upsert("receiver", _receiver(
        "11111111-rcv0-0000-0000-000000000002",
        "11111111-dev1-0000-0000-000000000001",
        "xyz1-rx-video-R", "RTP 0:VIDEO 1",
    ))

    # Device 2 — SNX00002, same shape
    await cache.upsert("device", _device("22222222-dev2-0000-0000-000000000002",
                                          "SNX00002", "Example SNX00002 (studio)"))
    await cache.upsert("sender", _sender(
        "22222222-snd0-0000-0000-000000000001",
        "22222222-dev2-0000-0000-000000000002",
        "xyz2-video-L", "RTP 0:VIDEO 0",
    ))
    await cache.upsert("sender", _sender(
        "22222222-snd0-0000-0000-000000000002",
        "22222222-dev2-0000-0000-000000000002",
        "xyz2-video-R", "RTP 0:VIDEO 1",
    ))
    await cache.upsert("receiver", _receiver(
        "22222222-rcv0-0000-0000-000000000001",
        "22222222-dev2-0000-0000-000000000002",
        "xyz2-rx-audio-L", "RTP 1:AUDIO 0",
    ))
    await cache.upsert("receiver", _receiver(
        "22222222-rcv0-0000-0000-000000000002",
        "22222222-dev2-0000-0000-000000000002",
        "xyz2-rx-audio-R", "RTP 1:AUDIO 1",
    ))

    # Device 3 — SNX00003, pure sender (no receivers)
    await cache.upsert("device", _device("33333333-dev3-0000-0000-000000000003",
                                          "SNX00003", "Example SNX00003 (encoder)"))
    await cache.upsert("sender", _sender(
        "33333333-snd0-0000-0000-000000000001",
        "33333333-dev3-0000-0000-000000000003",
        "xyz3-mux", "SRT 0:MUX 0",
    ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(port: int) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # The controller's middleware fails closed when no TLS is present
    # in production mode; flip the test flag on because we're running
    # over plain HTTP for the demo.
    nmos_config.ALLOW_NON_TLS_FOR_TESTING = True  # noqa: E501 — demo only

    node = Node()
    node.init(serial_number="DEMO")

    cache = ResourceCache()
    await seed(cache)

    app = create_controller_app(node, cache=cache, admin_password="demo")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    url = f"http://127.0.0.1:{port}{URL_PREFIX}/"
    print()
    print("=" * 72)
    print(f"  NMOS Controller demo running at:")
    print(f"    {url}")
    print("  Suggested walk-through:")
    print(f"    {url}senders")
    print(f"    {url}receivers")
    print(f"    {url}api/senders")
    print("  Press Ctrl-C to stop.")
    print("=" * 72)
    print()

    # Periodically flip a status to show SSE updates happening.
    async def flipper() -> None:
        sid = "11111111-snd0-0000-0000-000000000002"
        state = False
        while True:
            await asyncio.sleep(4)
            state = not state
            await cache.upsert("sender", _sender(
                sid, "11111111-dev1-0000-0000-000000000001",
                "xyz1-video-R", "RTP 0:VIDEO 1",
                active=state,
            ))

    flipper_task = asyncio.create_task(flipper())

    try:
        # Block forever
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        flipper_task.cancel()
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5051)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.port))
    except KeyboardInterrupt:
        pass
