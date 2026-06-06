# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Primary E2E PEP test matrix — every valid (protocol, mode, curve, transport) combination.

Each test runs a real sender→receiver encrypted streaming loopback on localhost
and verifies:
1. Sender and receiver derive the same privacy_key (direct attribute compare)
2. Zero decryption errors during the active streaming window (wire round-trip)
3. For ECDH modes: non-empty PFS shared secret on both sides

Protocol-dependent mode sets follow the Matrox "NMOS With Privacy Encryption" spec:
- RTP / RTP_KV / RTSP / RTSP_KV: all 12 modes (6 PSK + 6 ECDH, incl. CMAC variants)
- UDP / UDP_KV: 4 modes (CTR only, no CMAC/GMAC)
- USB / USB_KV: 4 modes (CMAC-64-AAD only)
- SRT: 4 modes (CTR only — GMAC-128 out of scope)

Curves: secp256r1, secp521r1, 25519, 448 (when ECDH mode).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

# Ensure pep/ importable
_PEP_PATH = str(Path(__file__).parent.parent.parent.parent.parent / "pep")
if _PEP_PATH not in sys.path:
    sys.path.insert(0, _PEP_PATH)

from ipmx_pep import PepMode, PepProtocol  # noqa: E402

from nmos.node.streaming.tests._pep_e2e_harness import (  # noqa: E402
    run_pep_e2e,
    assert_keys_agree,
    assert_clean_round_trip,
    assert_ecdh_pfs_present,
)


# ---------------------------------------------------------------------------
# Mode sets per protocol family (Matrox spec §"Mode")
# ---------------------------------------------------------------------------

MODES_RTP = [
    PepMode.AES_128_CTR, PepMode.AES_256_CTR,
    PepMode.AES_128_CTR_CMAC_64, PepMode.AES_256_CTR_CMAC_64,
    PepMode.AES_128_CTR_CMAC_64_AAD, PepMode.AES_256_CTR_CMAC_64_AAD,
]

MODES_UDP = [
    PepMode.AES_128_CTR, PepMode.AES_256_CTR,
]

MODES_USB = [
    PepMode.AES_128_CTR_CMAC_64_AAD, PepMode.AES_256_CTR_CMAC_64_AAD,
]

MODES_SRT = [
    PepMode.AES_128_CTR, PepMode.AES_256_CTR,
]

MODES_RTSP = MODES_RTP

# Protocol → applicable PSK modes
_PROTO_MODES: dict[PepProtocol, list[PepMode]] = {
    PepProtocol.RTP: MODES_RTP,
    PepProtocol.RTP_KV: MODES_RTP,
    PepProtocol.UDP: MODES_UDP,
    PepProtocol.UDP_KV: MODES_UDP,
    PepProtocol.USB: MODES_USB,
    PepProtocol.USB_KV: MODES_USB,
    PepProtocol.SRT: MODES_SRT,
    PepProtocol.RTSP: MODES_RTSP,
    PepProtocol.RTSP_KV: MODES_RTSP,
}

# Protocol → transport string for the harness
_PROTO_TRANSPORT: dict[PepProtocol, str] = {
    PepProtocol.RTP: "rtp_udp_ucast",
    PepProtocol.RTP_KV: "rtp_udp_ucast",
    PepProtocol.UDP: "udp_ucast",
    PepProtocol.UDP_KV: "udp_ucast",
    PepProtocol.USB: "tcp",        # USB over TCP
    PepProtocol.USB_KV: "tcp",
    PepProtocol.SRT: "srt",
    PepProtocol.RTSP: "tcp",       # RTSP over TCP
    PepProtocol.RTSP_KV: "tcp",
}

# ECDH curves to test
CURVES: list[str] = ["secp256r1", "secp521r1", "25519"]
# Note: curve "448" requires Curve448 support in privacy.py which may not be available.
# Include it only if the cryptography library supports it.
try:
    from cryptography.hazmat.primitives.asymmetric.x448 import X448PrivateKey
    CURVES.append("448")
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Combination generator
# ---------------------------------------------------------------------------

def _ecdh_mode(mode: PepMode) -> PepMode:
    """Return the ECDH variant of a PSK-only mode."""
    return PepMode("ECDH_" + mode.value)


def _all_psk_combinations() -> list[tuple[str, PepProtocol, str, PepMode]]:
    """Yield (test_id, protocol, transport, mode) for all PSK-only combinations."""
    combos = []
    for proto, modes in _PROTO_MODES.items():
        transport = _PROTO_TRANSPORT[proto]
        for mode in modes:
            tid = f"{proto.value}-{transport}-{mode.value}"
            combos.append(pytest.param(proto, transport, mode, id=tid))
    return combos


def _all_ecdh_combinations() -> list[tuple[str, PepProtocol, str, PepMode, str]]:
    """Yield (test_id, protocol, transport, mode, curve) for all ECDH combinations."""
    combos = []
    for proto, modes in _PROTO_MODES.items():
        transport = _PROTO_TRANSPORT[proto]
        for mode in modes:
            try:
                ecdh = _ecdh_mode(mode)
            except ValueError:
                continue
            for curve in CURVES:
                tid = f"{proto.value}-{transport}-{ecdh.value}-{curve}"
                combos.append(pytest.param(proto, transport, ecdh, curve, id=tid))
    return combos


# ---------------------------------------------------------------------------
# PSK-only E2E tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,transport,mode", _all_psk_combinations())
async def test_pep_psk_e2e(
    protocol: PepProtocol,
    transport: str,
    mode: PepMode,
) -> None:
    """PSK-only E2E: encrypt → send → receive → decrypt → verify key + wire."""
    from ipmx_pep import protocol_is_kv
    result = await run_pep_e2e(
        protocol=protocol,
        transport=transport,
        mode=mode,
        curve=None,
        duration=3.0,
    )
    # For _KV protocols the sender's privacy_key gets rotated during streaming,
    # so direct key comparison after streaming would fail. The wire round-trip
    # is the authoritative proof of correct key agreement.
    if not protocol_is_kv(protocol):
        assert_keys_agree(result)
    assert_clean_round_trip(result)


# ---------------------------------------------------------------------------
# ECDH E2E tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,transport,mode,curve", _all_ecdh_combinations())
async def test_pep_ecdh_e2e(
    protocol: PepProtocol,
    transport: str,
    mode: PepMode,
    curve: str,
) -> None:
    """ECDH E2E: generate keys, exchange, encrypt → send → receive → decrypt."""
    # Resolve curve to the enum the Privacy object expects
    from nmos.enums import EnumRegistry
    curve_enum = EnumRegistry.get(curve)

    from ipmx_pep import protocol_is_kv
    result = await run_pep_e2e(
        protocol=protocol,
        transport=transport,
        mode=mode,
        curve=curve_enum,
        duration=3.0,
    )
    if not protocol_is_kv(protocol):
        assert_keys_agree(result)
    assert_clean_round_trip(result)
    assert_ecdh_pfs_present(result)
