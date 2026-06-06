# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Seed the controller's :class:`ResourceCache` from the local Node.

When the embedded controller starts up without an RDS configured
(``--rdsHost`` empty — typical for dev / single-Node demos), the
controller's cache stays empty and the senders/receivers pages have
nothing to show. This module fills that gap by serializing the
Node's own resource stores (the same data the IS-04 endpoint serves)
and bulking them into the cache via :meth:`ResourceCache.replace_all`.

The module is a pure-cache seeder — no HTTP, no auth, no network — so
it works the same regardless of TLS / OAuth2 / mTLS posture and runs
in milliseconds. When an RDS bootstrap follows it, the registry's
``replace_all`` overwrites whatever this module wrote, so seeding
locally first is harmless even with RDS connected.
"""

from __future__ import annotations

import json
from typing import Any

from nmos.controller.cache import ResourceCache
from nmos.json.engine import JsonEngine


def _serialize(resource: Any) -> dict[str, Any]:
    """Serialize a Node-side resource (wrapped or inner) to its IS-04
    dict form.

    Mirrors the path :func:`nmos.api.handlers_node._encode` uses for
    the IS-04 HTTP endpoint, then `json.loads` the result so the
    cache (which expects dicts) gets the same shape as registry-sourced
    resources. Polymorphic wrappers (``NSourceValue``, ``NFlowValue``
    et al) are unwrapped via ``.get()`` before encoding.
    """
    inner: Any = resource
    if hasattr(resource, "get") and callable(resource.get):
        try:
            got = resource.get()
        except Exception:
            got = None
        if got is not None:
            inner = got
    engine = JsonEngine()
    raw = engine.encode(inner)
    parsed: dict[str, Any] = json.loads(raw)
    return parsed


async def bootstrap_local_node(node: Any, cache: ResourceCache) -> None:
    """Populate ``cache`` with the local Node's IS-04 resources.

    Pulls all six IS-04 resource kinds (``node``, ``device``,
    ``source``, ``flow``, ``sender``, ``receiver``) from the Node's
    own state, serializes each, and bulks the result into the cache
    via ``replace_all``. Safe to call repeatedly (replace_all is
    idempotent).

    Resources that haven't been initialized yet (e.g. ``node_value``
    / ``device_value`` left as ``None`` because ``Node.init`` hasn't
    finished) are skipped silently.
    """
    if node.node_value is not None:
        await cache.replace_all("node", [_serialize(node.node_value)])
    if node.device_value is not None:
        await cache.replace_all("device", [_serialize(node.device_value)])

    await cache.replace_all(
        "source", [_serialize(s) for _, s in node.sources],
    )
    await cache.replace_all(
        "flow", [_serialize(f) for _, f in node.flows],
    )
    await cache.replace_all(
        "sender", [_serialize(s) for _, s in node.senders],
    )
    await cache.replace_all(
        "receiver", [_serialize(r) for _, r in node.receivers],
    )
