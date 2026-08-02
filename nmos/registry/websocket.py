# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The Query API WebSocket endpoint.

Serves the ``ws_href`` handed out by ``POST /subscriptions``. There is no
mandated path for this — ``Behaviour - Querying.md:29``: "There is no mandated
URL base path for servers to use to provide WebSocket connections. Instead
clients SHOULD observe the value of ``ws_href``" — so the path mirrors the
subscription's HTTP path purely because that is the least surprising choice.

Each connection runs two coroutines:

* a **reader**, which exists to notice the client going away. The Query API
  WebSocket is server-to-client only; a client never sends anything
  meaningful. Without a reader, a closed socket would not be detected until
  the next write, which for an idle subscription could be indefinitely.
* a **sender**, which drains that connection's buffer and writes grains,
  respecting ``max_update_rate_ms``.

Rate limiting
-------------
``max_update_rate_ms`` is the minimum interval between grains for one
connection. The sender waits for work, sends, then sleeps out the remainder of
the window before looking again; anything that happens during the sleep
accumulates and is coalesced per resource, so a client asking for 100 ms
updates receives at most ten grains a second no matter how busy the registry
is, and each one carries the net change rather than a replay.

The AMWA mock does not implement this at all — it writes a grain per event.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import WSMsgType, web

from nmos.api.response import error_response
from nmos.registry.registry import Registry
from nmos.registry.subscriptions import SubscriptionConnection

log = logging.getLogger(__name__)

# How long the sender waits for its socket to be closed before giving up on a
# graceful shutdown. Short: the connection is being torn down anyway.
_CLOSE_TIMEOUT_S = 1.0


async def handle_subscription_websocket(
    request: web.Request,
) -> web.StreamResponse:
    """GET (upgrade) /x-nmos/query/v1.3/subscriptions/{subscriptionId}."""
    registry: Registry = request.app["registry"]
    subscription_id = request.match_info["subscriptionId"]

    subscription = registry.subscriptions.get(subscription_id)
    if subscription is None:
        # Answered as a plain HTTP error rather than an accepted-then-closed
        # WebSocket, so a client that mistyped a ws_href sees a 404 instead of
        # a socket that opens and immediately dies.
        return error_response(
            404,
            f"subscription {subscription_id} was not found",
            request=request,
        )

    websocket = web.WebSocketResponse(heartbeat=30.0)
    await websocket.prepare(request)

    # Attaching queues the synchronisation burst for THIS connection before
    # any live event can be enqueued, so a client cannot miss a change that
    # lands between its connect and its sync.
    connection = registry.subscriptions.connect(subscription)
    log.info(
        "registry: websocket connected to subscription %s (%s)",
        subscription_id, subscription.resource_path,
    )

    sender = asyncio.create_task(
        _send_grains(websocket, registry, connection),
    )
    # The socket can end from either side, and both have to be watched:
    #   * the client disconnects -- the reader observes the close frame;
    #   * the server drops the subscription -- DELETE of a persistent
    #     subscription must forcibly close its clients (``:19``), and garbage
    #     collection of a non-persistent one does the same.
    # Waiting only on the reader would leave a deleted subscription serving
    # its socket until the client happened to go away on its own.
    reader = asyncio.create_task(_await_close(websocket))
    shutdown = asyncio.create_task(connection.wait_closed())
    try:
        await asyncio.wait(
            {reader, shutdown}, return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        registry.subscriptions.disconnect(connection)
        for task in (sender, reader, shutdown):
            task.cancel()
        await asyncio.gather(
            sender, reader, shutdown, return_exceptions=True,
        )
        if not websocket.closed:
            await websocket.close()
        log.info(
            "registry: websocket disconnected from subscription %s",
            subscription_id,
        )

    return websocket


async def _await_close(websocket: web.WebSocketResponse) -> None:
    """Consume inbound frames until the client goes away.

    Nothing a client sends carries meaning in this protocol, so frames are
    read and dropped. The loop's purpose is to observe the close.
    """
    async for message in websocket:
        if message.type in (
            WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED,
            WSMsgType.ERROR,
        ):
            return


async def _send_grains(
    websocket: web.WebSocketResponse,
    registry: Registry,
    connection: SubscriptionConnection,
) -> None:
    """Drain the connection's buffer into grains, honouring the rate limit."""
    subscription = connection.subscription
    interval = subscription.max_update_rate_ms / 1000.0

    while not connection.closed and not websocket.closed:
        await connection.wait()
        if connection.closed or websocket.closed:
            return

        pending = connection.drain()
        if not pending:
            # Woken by a close, or by an event that another drain already
            # took. Nothing to send: an empty grain would violate
            # queryapi-subscriptions-websocket.json, whose data array has
            # minItems 1.
            continue

        message = registry.subscriptions.build_grain(subscription, pending)
        try:
            await websocket.send_str(message)
        except (ConnectionResetError, RuntimeError):
            # The peer vanished mid-write. The reader will observe the close
            # and run the teardown; nothing useful to do here.
            return

        if interval > 0:
            # Sleep out the rest of the window. Events arriving during it
            # accumulate in the buffer and are coalesced, which is what makes
            # max_update_rate_ms a real bound rather than a hint.
            await asyncio.sleep(interval)
