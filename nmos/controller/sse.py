# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Server-Sent Events pump for live status badges.

Browsers on the top-level senders / receivers listing pages open an
``EventSource`` pointed at ``/api/status-events?ids=<csv>`` — this
module is the server-side endpoint. It registers a listener on the
shared ``ResourceCache`` that is scoped to the resource ids the client
asked for, then forwards ``StatusChanged`` events as SSE frames.

Protocol (all ASCII text per the SSE spec):

    event: status
    data: {"id": "abc…", "kind": "sender", "status": {...}}

    : keepalive   ← comment line every ~15 s so browsers don't time out

The listener auto-detaches when the connection drops.
"""

from __future__ import annotations

import asyncio
import json
import logging

from aiohttp import web

from nmos.controller.cache import ResourceCache, StatusEventStream

log = logging.getLogger(__name__)


KEEPALIVE_INTERVAL_S = 15.0


async def status_events_handler(request: web.Request) -> web.StreamResponse:
    """``GET /api/status-events?ids=a,b,c`` — SSE stream.

    When ``ids`` is omitted or empty, the stream receives every
    status-changed event (useful for listeners that want the full
    feed; the listing pages pass an explicit id set).
    """
    cache: ResourceCache | None = request.app.get("controller_cache")
    if cache is None:
        raise web.HTTPInternalServerError(reason="controller cache not configured")

    raw_ids = request.query.get("ids", "").strip()
    filter_ids: set[str] = {
        s for s in (p.strip() for p in raw_ids.split(",")) if s
    }

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering
        },
    )
    await response.prepare(request)

    stream = StatusEventStream(cache, filter_ids)

    # Keepalive pings so the connection isn't killed by intermediaries.
    keepalive_task = asyncio.create_task(_keepalive_loop(response))

    try:
        async for event in stream.events():
            payload = {
                "id": event.resource_id,
                "kind": event.kind,
                "status": event.status,
            }
            frame = f"event: status\ndata: {json.dumps(payload)}\n\n"
            try:
                await response.write(frame.encode("utf-8"))
            except (ConnectionResetError, asyncio.CancelledError):
                break
    finally:
        keepalive_task.cancel()
        stream.close()
        try:
            await response.write_eof()
        except Exception:
            pass

    return response


async def _keepalive_loop(response: web.StreamResponse) -> None:
    """Periodic SSE comment line to keep the TCP connection alive."""
    try:
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL_S)
            try:
                await response.write(b": keepalive\n\n")
            except (ConnectionResetError, asyncio.CancelledError):
                return
    except asyncio.CancelledError:
        return
