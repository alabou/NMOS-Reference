# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""SDP bandwidth (b=AS) must equal the sender's transport bit_rate.

The sender's transport bit_rate is the encoded essence bitrate plus transport
overhead.  The SDP b=AS line carries that same transport bandwidth, so the two
must always match (a single source — the SDP reads the sender's transport
Bitrate attribute rather than re-deriving it).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from nmos.node import Node, _generate_sdp_from_params
from nmos.node.config import ConfigBuilder

BUILTIN_DIR = Path(__file__).parent.parent / "config" / "builtin"


def _build(config_name: str) -> Node:
    node = Node()
    node.init(serial_number="SDPTST")
    path = BUILTIN_DIR / f"{config_name}.json"
    if not path.exists():
        pytest.skip(f"{config_name}.json not found")
    cfg = json.load(open(path))
    builder = ConfigBuilder(node, verbose=False)
    for s in cfg.get("senders", []):
        try:
            builder._build_sender_pipeline(s)
        except Exception:
            pass
    return node


def test_sdp_b_as_matches_sender_transport_bitrate() -> None:
    """For every coded sender that declares a transport bit_rate, the generated
    SDP's b=AS must equal that bit_rate."""
    node = _build("config5")  # native H264 over RTP

    checked = 0
    for static_id, sender in node.senders:
        if not (hasattr(sender, "Bitrate") and sender.Bitrate.defined):
            continue
        sender_id = (sender.ResourceCore.Id.value
                     if hasattr(sender, "ResourceCore") else static_id)
        sdp = _generate_sdp_from_params(node, sender, sender_id)
        if not sdp:
            continue
        m = re.search(r"b=AS:(\d+)", sdp)
        assert m is not None, "coded sender SDP should carry a b=AS bandwidth line"
        assert int(m.group(1)) == sender.Bitrate.value, (
            f"SDP b=AS ({m.group(1)}) must equal the sender transport "
            f"bit_rate ({sender.Bitrate.value})"
        )
        checked += 1

    assert checked > 0, "expected at least one coded sender with a transport bit_rate"
