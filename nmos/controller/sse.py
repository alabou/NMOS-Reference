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

    # Receiver caps pages pass a sender↔receiver pairing
    # (``&pair=<sid>:<rid>,…``). For those senders the flow-match must be
    # computed against the RECEIVER-NARROWED CS the page rendered, not the
    # sender's full caps — otherwise the live green lands on the wrong row.
    # The narrowing depends only on the declared caps (not the flow), so we
    # narrow ONCE here and re-run only the inclusion check per flow change.
    from nmos.controller.flow_match import (
        flow_match_index_for_sender,
        narrowed_constraint_sets_for_pair,
    )
    pair_map: dict[str, str] = {}
    for tok in request.query.get("pair", "").split(","):
        tok = tok.strip()
        if ":" in tok:
            sid, rid = tok.split(":", 1)
            sid, rid = sid.strip(), rid.strip()
            if sid and rid:
                pair_map[sid] = rid
    narrowed_cs: dict[str, list | None] = {
        sid: narrowed_constraint_sets_for_pair(cache, sid, rid)
        for sid, rid in pair_map.items()
    }

    def _scoped_flow_match(resource_id: str, base: dict | None) -> dict | None:
        """Receiver-scope a flow-match payload for a paired sender:
        override ``matched_cs_index`` with the index into the NARROWED CS
        the page shows, while preserving ``matched_values`` (the flow's
        operating point — narrowing-independent, used by the configure
        page). Returns ``base`` unchanged when not a paired sender."""
        if resource_id not in pair_map:
            return base
        idx = flow_match_index_for_sender(
            cache, resource_id, narrowed_cs.get(resource_id),
        )
        out = dict(base or {})
        out["matched_cs_index"] = idx
        return out

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

    # Initial snapshot — push the current status for every subscribed
    # id BEFORE entering the change-event loop. Without this, anything
    # that changed between page render and SSE connect (or any drop +
    # reconnect) would be lost until the next change, leaving the
    # badges / dots stale until the user pressed F5. Listener is
    # already registered in StatusEventStream.__init__, so events
    # fired during the snapshot are enqueued and replayed below;
    # the queue draining last guarantees the latest state wins.
    if filter_ids:
        snapshot_ids = set(filter_ids)
    else:
        snapshot_ids = {s["id"] for s in cache.all_senders() if s.get("id")}
        snapshot_ids |= {r["id"] for r in cache.all_receivers() if r.get("id")}
    for rid in snapshot_ids:
        status = cache.get_status(rid)
        # Additive: also seed the last-known flow-match so a caps page
        # that connects (or reconnects) after the bound flow last changed
        # gets the current green-highlight target without a reload. Emit a
        # frame when either a status or a flow-match exists.
        # Paired (receiver-caps) senders use the narrowed-scoped match;
        # everyone else uses the cache's last full-caps match.
        fm = _scoped_flow_match(rid, cache.get_flow_match(rid))
        if not status and fm is None:
            continue
        payload = {"id": rid, "kind": "", "status": status}
        if fm is not None:
            payload["flow_match"] = fm
        frame = f"event: status\ndata: {json.dumps(payload)}\n\n"
        try:
            await response.write(frame.encode("utf-8"))
        except (ConnectionResetError, asyncio.CancelledError):
            stream.close()
            return response

    # Keepalive pings so the connection isn't killed by intermediaries.
    keepalive_task = asyncio.create_task(_keepalive_loop(response))

    try:
        async for event in stream.events():
            payload = {
                "id": event.resource_id,
                "kind": event.kind,
                "status": event.status,
            }
            # Additive: carry the flow-match payload only on events that
            # have one (the cache's flow branch). Status-only frames are
            # byte-for-byte unchanged. For paired (receiver-caps) senders,
            # the cache's event signals the flow changed; recompute the
            # index against the receiver-narrowed CS so it matches the rows
            # the page rendered.
            if event.flow_match is not None:
                payload["flow_match"] = _scoped_flow_match(
                    event.resource_id, event.flow_match,
                )
            frame = f"event: status\ndata: {json.dumps(payload)}\n\n"
            # TEMP flow-match instrumentation: record every frame this
            # subscriber forwards, so we can see whether a flow_match
            # frame actually reached a caps-page subscriber. Remove once
            # the live update is confirmed.
            _trace = request.app.get("controller_debug_trace")
            if _trace is not None and _trace.enabled:
                _trace.emit(
                    "sse_frame",
                    subscriber_ids=sorted(filter_ids) if filter_ids else "all",
                    id=event.resource_id,
                    has_flow_match="flow_match" in payload,
                    flow_match=payload.get("flow_match"),
                    scoped=event.resource_id in pair_map,
                )
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
