# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Background tasks: garbage collection and periodic status reporting.

Both run under the process ``DispatchGroup`` and exit when it is cancelled,
following the same shape as the Node's own long-lived tasks (``go_node_server``,
``run_status_monitor``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nmos.registry.registry import Registry

log = logging.getLogger(__name__)

# How often the collector looks for expired Nodes. Deliberately much shorter
# than the collection interval itself: the interval is the guarantee about
# when a silent Node *becomes* eligible, and the tick is the granularity with
# which that is noticed. One second keeps the overshoot below the resolution
# of the health clock without making the loop hot.
GC_TICK_S = 1.0


async def run_garbage_collection(dg: Any, registry: Registry) -> None:
    """Expire Nodes that have stopped heartbeating.

    ``Behaviour - Registration.md:47`` sets the default collection interval at
    12 seconds — "triggered just after two failed heartbeats at the default 5
    second interval" — and ``:51`` requires that when it fires "both the Node
    and all registered sub-resources SHOULD be removed from the registry
    automatically".

    Collection also drives the second stage of the resource lifecycle,
    dropping tombstoned records once their forget interval has elapsed. Both
    live in ``RegistryStore.collect_garbage``; this task only supplies the
    clock.

    The AMWA test-suite mock records heartbeat times but never expires
    anything, so a Node that is unplugged stays in its registry forever.
    """
    log.info("registry: garbage collection running every %.1fs", GC_TICK_S)
    while not dg.is_done:
        try:
            await asyncio.sleep(GC_TICK_S)
        except asyncio.CancelledError:
            return

        try:
            collected = registry.collect_garbage()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            # A failure here must not kill the task: garbage collection is the
            # only thing standing between an ungracefully-disconnected Node
            # and a permanently stale registry, so it has to survive a bad
            # pass and try again on the next tick.
            log.exception("registry: garbage collection pass failed: %s", exc)
            continue

        if collected:
            log.info(
                "registry: garbage collected %d resource(s); %s",
                collected, registry.status_line(),
            )


async def run_status_reporting(
    dg: Any, registry: Registry, interval: float,
) -> None:
    """Log the registry status line periodically.

    Reproduces nmos-cpp's status line, which it emits from its expiry thread
    and from its ``POST /resource`` handler. This registry logs it from the
    same two places — here, and in ``handlers_registration.handle_post_resource``
    — so the two implementations' logs are directly comparable when
    diagnosing a registration problem against one another.

    Args:
        interval: Seconds between lines. Zero or negative disables reporting.
    """
    if interval <= 0:
        log.info("registry: periodic status reporting disabled")
        return

    log.info("registry: status reporting every %.1fs", interval)
    while not dg.is_done:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        log.info("registry: %s", registry.status_line())
