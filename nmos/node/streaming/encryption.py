# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""PEP encryption wrapper for the streaming engine.

Bridges the streaming engine with pep/ipmx_pep.py for packet encryption and
decryption. Handles key derivation, substreamid selection, per-direction key
version, CMAC-64 authentication, and dynamic key rotation for _KV protocols.

Anchors to VSF TR-10-13 v1.4 (2026-02-17):
- §14: iv' = (iv + substreamid) mod 2^64; bidirectional streams use even
  substreamid for sender→receiver and odd for receiver→sender.
- §15: Privacy Cipher (AES-CTR); CMAC modes use mac-then-encrypt with MAC
  stored as the last 8 bytes of the encrypted payload.
- §20.3: Dynamic key_version — the decrypting side MUST use the key_version
  read from the clear header of each packet to derive its decryption key.

Test observability: each key derivation emits a machine-parseable
``[PEP-KDF] ...`` line on stdout so multi-process end-to-end tests can
verify that the sender and receiver derived the same privacy_key by
comparing log lines (no shared memory between processes).
"""

from __future__ import annotations

import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, cast

# Add pep/ to path for ipmx_pep
_PEP_PATH = str(Path(__file__).parent.parent.parent.parent / "pep")
if _PEP_PATH not in sys.path:
    sys.path.insert(0, _PEP_PATH)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Size of the clear header prepended to every encrypted packet on the wire.
#: Structure (big-endian): [8B pep_ctr] [4B key_version] — mirrors the RTP
#: CTR Full Extension Header payload (TR-10-13 §20.1).
CLEAR_HEADER_SIZE = 12

#: Size of the CMAC-64 authentication tag (TR-10-13 §15).
CMAC_TAG_SIZE = 8

#: Default key-version rotation period for _KV protocols (seconds).
DEFAULT_KV_ROTATION_SEC = 2.0

#: Maximum number of recent key_versions the receiver caches to tolerate
#: out-of-order packets across a rotation boundary.
_KV_CACHE_SIZE = 4


def _to_pep_protocol(proto: Any) -> Any:
    """Convert an NMOS EnumId or string to a PepProtocol enum value.

    The activation transport params store the protocol as an EnumId (NMOS enum
    with .s attribute).  The pep/ module's protocol_is_kv() expects a
    PepProtocol (Python Enum with .value).  This function bridges the two
    enum systems (same string values, different Python types).
    """
    if proto is None:
        return None
    try:
        from ipmx_pep import PepProtocol
        s = proto.s if hasattr(proto, 's') else (proto.value if hasattr(proto, 'value') else str(proto))
        return PepProtocol(s)
    except (ValueError, ImportError):
        return proto


def _to_pep_mode(mode: Any) -> Any:
    """Convert an NMOS EnumId or string to a PepMode enum value.

    Same cross-enum-system bridge as _to_pep_protocol, for PEP encryption
    modes (AES-128-CTR, ECDH_AES-256-CTR-CMAC-64, etc.).
    """
    if mode is None:
        return None
    try:
        from ipmx_pep import PepMode
        s = mode.s if hasattr(mode, 's') else (mode.value if hasattr(mode, 'value') else str(mode))
        return PepMode(s)
    except (ValueError, ImportError):
        return mode


def _hex(data: bytes) -> str:
    """Format bytes as hex string, or ``'00'`` when empty."""
    return data.hex() if data else "00"


# ---------------------------------------------------------------------------
# StreamEncryption
# ---------------------------------------------------------------------------

@dataclass
class StreamEncryption:
    """PEP encryption/decryption context for one direction of one streaming session.

    A unidirectional transport uses a single :class:`StreamEncryption` on each
    endpoint (both with ``substreamid=0``). A bidirectional transport uses two
    instances per endpoint (``substreamid=0`` for forward, ``substreamid=1``
    for reverse), each with its own ``key_version``.

    All encryption parameters except ``key_version`` are immutable for the
    lifetime of the session. For ``_KV`` protocols the ``key_version`` may
    rotate dynamically; :meth:`make_encrypt_fn` and :meth:`make_decrypt_fn`
    handle the rotation transparently.

    Attributes:
        privacy_key: Derived AES key (16 or 32 bytes). Recomputed whenever
            ``key_version`` changes.
        iv_prime: 64-bit effective ``iv' = (iv + substreamid) mod 2^64``.
        key_bits: 128 or 256.
        psk: Pre-Shared Key (kept for re-derivation on KV change).
        key_generator: 16-byte KG (kept for re-derivation on KV change).
        key_version: Current 4-byte key version. Initial value can be
            overridden via ``from_privacy(key_version_override=...)``; for
            ``_KV`` protocols the sender rotates this value.
        key_id: 8-byte KeyId.
        iv: 8-byte base IV (the ``iv`` parameter of the ``a=privacy``
            attribute / ``ext_privacy_iv``). ``iv_prime`` is derived from it.
        key_pfs: ECDH shared secret (empty for PSK-only modes).
        substreamid: 16-bit sub-stream identifier (0 for unidirectional,
            even for forward and odd for reverse in bidirectional flows).
        protocol: PepProtocol instance (optional; used to decide whether to
            schedule ``_KV`` rotation in :meth:`make_encrypt_fn`).
        mode: PepMode instance (optional; determines whether to apply
            mac-then-encrypt for CMAC modes and whether to include AAD).
        key_rotation_period_sec: Rotation period for ``_KV`` protocols.
    """

    privacy_key: bytes
    iv_prime: int
    key_bits: int
    psk: bytes
    key_generator: bytes
    key_version: bytes
    key_id: bytes
    iv: bytes
    key_pfs: bytes = b""
    key_xcl: bytes = b""           # Node Reservation exclusive key (16 bytes when active)
    substreamid: int = 0
    protocol: Any = None    # PepProtocol | None — avoids circular import at class def
    mode: Any = None        # PepMode | None
    key_rotation_period_sec: float = DEFAULT_KV_ROTATION_SEC

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_privacy(
        cls,
        privacy: Any,               # nmos.node.types.Privacy
        privacy_keys: Any,          # nmos.node.types.PrivacyPreSharedKeys
        resource_id: str,
        is_sender: bool,
        verbose: bool = True,
        *,
        substreamid: int = 0,
        key_version_override: bytes | None = None,
        direction: str = "tx",
        key_rotation_period_sec: float = DEFAULT_KV_ROTATION_SEC,
    ) -> StreamEncryption | None:
        """Build an encryption context from activation privacy state.

        Args:
            privacy: The :class:`Privacy` object populated from active transport
                parameters after IS-05 activation.
            privacy_keys: :class:`PrivacyPreSharedKeys` — used as a fallback
                PSK source when ``privacy.psk`` is empty.
            resource_id: Sender/receiver UUID (for log lines only).
            is_sender: Role flag (for log lines only).
            verbose: When True, print the derivation summary and the
                ``[PEP-KDF]`` structured line.
            substreamid: 0 for unidirectional, even for forward / odd for
                reverse in bidirectional transports per TR-10-13 §14.
            key_version_override: 4-byte initial key_version. Defaults to
                ``privacy.key_version``. A bidirectional endpoint supplies
                different values for its forward and reverse directions.
            direction: ``"tx"`` or ``"rx"`` (for log lines only).
            key_rotation_period_sec: Period for ``_KV`` rotation (default 2s).

        Returns:
            A :class:`StreamEncryption` instance, or ``None`` when encryption
            is not configured (no PSK).
        """
        from ipmx_pep import derive_privacy_key, compute_iv_prime, mode_key_bits

        # Resolve PSK: prefer the activation's specific PSK, fall back to the
        # first entry of the configured PSK list.
        psk = privacy.psk
        if not psk and privacy_keys and privacy_keys.keys:
            psk = privacy_keys.keys[0].psk
        if not psk:
            return None

        iv_bytes = privacy.iv
        key_generator = privacy.key_generator
        initial_kv = key_version_override if key_version_override is not None else privacy.key_version
        key_id = privacy.key_id

        # Determine key bits from the mode. Defaults to 128 when mode is
        # unspecified (preserves existing behavior).
        mode = _to_pep_mode(getattr(privacy, 'mode', None))
        try:
            key_bits = mode_key_bits(mode) if mode is not None else 128
        except Exception:
            key_bits = 128

        # Compute ECDH shared secret if the privacy context carries one.
        key_pfs = b""
        if privacy.pfs:
            key_pfs = privacy.pfs
        elif privacy.ecdh_sender_private or privacy.ecdh_receiver_private:
            from nmos.node.privacy import compute_ecdh_shared_secret
            key_pfs = compute_ecdh_shared_secret(privacy, is_sender)

        # Node Reservation exclusive key (Matrox "NMOS With Privacy Encryption" §"Node Reservation")
        key_xcl = getattr(privacy, 'xcl', b"") or b""

        # Derive the initial privacy_key from the initial key_version.
        privacy_key = derive_privacy_key(
            psk=psk,
            key_generator=key_generator,
            key_version=initial_kv,
            key_pfs=key_pfs,
            key_bits=key_bits,
            key_xcl=key_xcl,
        )

        # iv_prime = (iv + substreamid) mod 2^64  (TR-10-13 §14)
        iv_int = int.from_bytes(iv_bytes, "big") if iv_bytes else 0
        iv_prime = compute_iv_prime(iv_int, substreamid=substreamid)

        enc = cls(
            privacy_key=privacy_key,
            iv_prime=iv_prime,
            key_bits=key_bits,
            psk=psk,
            key_generator=key_generator,
            key_version=initial_kv,
            key_id=key_id,
            iv=iv_bytes,
            key_pfs=key_pfs,
            key_xcl=key_xcl,
            substreamid=substreamid,
            protocol=_to_pep_protocol(getattr(privacy, 'protocol', None)),
            mode=mode,
            key_rotation_period_sec=key_rotation_period_sec,
        )

        if verbose:
            enc.print_summary(resource_id, is_sender, direction=direction)

        return enc

    # ------------------------------------------------------------------
    # Console / log output
    # ------------------------------------------------------------------

    def print_summary(
        self,
        resource_id: str,
        is_sender: bool,
        *,
        direction: str = "tx",
    ) -> None:
        """Print the key derivation summary for human inspection and a
        single machine-parseable ``[PEP-KDF]`` line for multi-process tests.

        The ``[PEP-KDF]`` line is the observability hook relied upon by the
        Tier-C end-to-end harness: since sender and receiver run in different
        processes, the harness captures both stdouts and cross-checks that
        the same ``derived_key`` appears on both sides for the same
        ``(resource, direction, key_version)`` tuple.
        """
        role = "Sender" if is_sender else "Receiver"

        # Human-readable summary (unchanged)
        print(f"    PEP Key Derivation ({role} {resource_id} dir={direction}):")
        print(f"      PSK:           {_hex(self.psk)} ({len(self.psk) * 8} bits)")
        print(f"      KeyGenerator:  {_hex(self.key_generator)}")
        print(f"      KeyVersion:    {_hex(self.key_version)}")
        print(f"      KeyId:         {_hex(self.key_id)}")
        print(f"      IV:            {_hex(self.iv)}")
        print(f"      IV':           {self.iv_prime:016x}")
        print(f"      SubstreamId:   {self.substreamid}")
        if self.key_pfs:
            print(f"      PFS (ECDH):    {_hex(self.key_pfs)} ({len(self.key_pfs) * 8} bits)")
        else:
            print(f"      PFS (ECDH):    none (PSK-only mode)")
        print(f"      Key bits:      {self.key_bits}")
        print(f"      → Derived Key: {_hex(self.privacy_key)}")

        # Machine-parseable line — STABLE FORMAT (do not reorder fields;
        # the Tier-C harness parses this with a fixed regex).
        protocol_s = self.protocol.value if hasattr(self.protocol, 'value') else (str(self.protocol) if self.protocol else "n/a")
        mode_s = self.mode.value if hasattr(self.mode, 'value') else (str(self.mode) if self.mode else "n/a")
        role_s = "sender" if is_sender else "receiver"
        print(
            f"[PEP-KDF] role={role_s} resource={resource_id} dir={direction} "
            f"substreamid={self.substreamid} protocol={protocol_s} mode={mode_s} "
            f"key_id={_hex(self.key_id)} key_version={_hex(self.key_version)} "
            f"iv_prime={self.iv_prime:016x} pfs_len={len(self.key_pfs)} "
            f"derived_key={_hex(self.privacy_key)}",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Low-level encrypt/decrypt (bypass the rotation/auth envelope)
    # ------------------------------------------------------------------

    def _rekey_for_version(self, key_version: bytes) -> bytes:
        """Derive the AES key for a given 4-byte key_version value."""
        from ipmx_pep import derive_privacy_key
        return cast(
            bytes,
            derive_privacy_key(
                psk=self.psk,
                key_generator=self.key_generator,
                key_version=key_version,
                key_pfs=self.key_pfs,
                key_bits=self.key_bits,
                key_xcl=self.key_xcl,
            ),
        )

    # ------------------------------------------------------------------
    # Sender: make_encrypt_fn
    # ------------------------------------------------------------------

    def make_encrypt_fn(self) -> Callable[[bytes, int], bytes]:
        """Return a stateful encryption function for the sender.

        The returned callable accepts ``(plaintext, ctr)`` and produces
        wire-format bytes: ``[12B clear header] [encrypted payload]``.

        For ``_KV`` protocols (``RTP_KV``, ``UDP_KV``, ``USB_KV``, ``RTSP_KV``)
        the sender rotates ``key_version`` every
        :attr:`key_rotation_period_sec` seconds, re-deriving
        :attr:`privacy_key` on each rotation. The current ``key_version`` is
        stamped into the clear header of every packet so the receiver can
        re-derive its decryption key from the wire.

        For ``_CMAC-64`` / ``_CMAC-64-AAD`` modes the last
        :data:`CMAC_TAG_SIZE` bytes of the plaintext are replaced with the
        CMAC-64 tag (mac-then-encrypt per TR-10-13 §15). For ``-AAD`` modes
        the MAC is computed over ``aad || payload`` where ``aad`` is built
        from the clear-header counter and key_version per §20.
        """
        try:
            from ipmx_pep import (
                protocol_is_kv, mode_has_cmac, mode_has_gmac, mode_has_aad,
                pep_encrypt, pep_cmac64,
            )
        except ImportError:
            # Older pep module lacked protocol_is_kv and/or mode_has_gmac
            from ipmx_pep import mode_has_cmac, mode_has_aad, pep_encrypt, pep_cmac64

            def protocol_is_kv(p: Any) -> bool:
                return bool(getattr(p, 'value', str(p)).endswith("_KV"))

            def mode_has_gmac(mode: Any) -> bool:
                return bool("GMAC-128" in getattr(mode, 'value', str(mode)))

        is_kv = self.protocol is not None and protocol_is_kv(self.protocol)
        has_cmac = self.mode is not None and mode_has_cmac(self.mode)
        has_gmac = self.mode is not None and mode_has_gmac(self.mode)
        has_aad = self.mode is not None and mode_has_aad(self.mode)
        rotation_period = self.key_rotation_period_sec

        # Rotation state captured in closure so concurrent callers share it.
        state: dict[str, Any] = {
            "last_rotation_monotonic": time.monotonic(),
            "kv_bytes": self.key_version,
            "privacy_key": self.privacy_key,
        }

        def _maybe_rotate() -> None:
            if not is_kv:
                return
            now = time.monotonic()
            if now - state["last_rotation_monotonic"] < rotation_period:
                return
            # Increment key_version mod 2^32 and re-derive the key.
            kv_int = (int.from_bytes(state["kv_bytes"], "big") + 1) & 0xFFFFFFFF
            new_kv = kv_int.to_bytes(4, "big")
            state["kv_bytes"] = new_kv
            state["privacy_key"] = self._rekey_for_version(new_kv)
            state["last_rotation_monotonic"] = now
            # Persist for inspection / next re-derivation base.
            self.key_version = new_kv
            self.privacy_key = state["privacy_key"]

        def encrypt_fn(plaintext: bytes, ctr: int) -> bytes:
            _maybe_rotate()
            kv_bytes = state["kv_bytes"]
            key = state["privacy_key"]
            kv_int = int.from_bytes(kv_bytes, "big")
            clear_header = struct.pack(">QI", ctr, kv_int)

            if has_gmac:
                # GMAC-128 is SRT-library-applied (NMOS With Privacy
                # Encryption.md:294 — "The SRT encryption takes control of
                # the iv'_ctr value of the cipher"). This simplified
                # transport ships the packet through the clear-header
                # framing without a pep-layer cipher, matching the UDP
                # adaptation pattern (no pep-layer MAC on UDP either).
                # privacy_key is still correctly derived above and
                # available on StreamEncryption for an SRT-library
                # integration at deploy time.
                return clear_header + plaintext

            if has_cmac:
                # mac-then-encrypt: the last 8 bytes of plaintext are reserved
                # for the MAC; compute CMAC over the payload (and optional AAD)
                # and overwrite those 8 bytes before encrypting.
                if len(plaintext) < CMAC_TAG_SIZE:
                    raise ValueError(
                        f"plaintext too short for CMAC-64: {len(plaintext)}"
                    )
                payload = plaintext[:-CMAC_TAG_SIZE]
                aad = clear_header if has_aad else b""
                mac = pep_cmac64(key, payload, aad=aad)
                to_encrypt = payload + mac
                ciphertext = pep_encrypt(key, self.iv_prime, ctr, to_encrypt)
            else:
                ciphertext = pep_encrypt(key, self.iv_prime, ctr, plaintext)

            return clear_header + cast(bytes, ciphertext)

        return encrypt_fn

    # ------------------------------------------------------------------
    # Receiver: make_decrypt_fn
    # ------------------------------------------------------------------

    def make_decrypt_fn(self) -> Callable[[bytes], tuple[bytes, int]]:
        """Return a stateful decryption function for the receiver.

        Returned callable: ``decrypt_fn(data) -> (plaintext, ctr)``.

        Per TR-10-13 §20.3 the receiver derives its decryption key from the
        ``dynamic_key_version`` read in the clear header of each packet — it
        never pre-negotiates a value and never assumes the one configured at
        activation. A small LRU cache (size :data:`_KV_CACHE_SIZE`) keeps
        recently-derived keys so out-of-order packets from just before a
        rotation boundary still decrypt without a re-derivation penalty.

        For ``_CMAC-64`` / ``_CMAC-64-AAD`` modes the last 8 bytes of the
        decrypted plaintext are interpreted as the CMAC tag and verified
        against a freshly-computed CMAC over the payload (and optional AAD
        derived from the clear header). A mismatch raises ``ValueError``.
        """
        try:
            from ipmx_pep import (
                mode_has_cmac, mode_has_gmac, mode_has_aad,
                pep_decrypt, pep_cmac64,
            )
        except ImportError:
            from ipmx_pep import mode_has_cmac, mode_has_aad, pep_decrypt, pep_cmac64

            def mode_has_gmac(mode: Any) -> bool:
                return bool("GMAC-128" in getattr(mode, 'value', str(mode)))

        has_cmac = self.mode is not None and mode_has_cmac(self.mode)
        has_gmac = self.mode is not None and mode_has_gmac(self.mode)
        has_aad = self.mode is not None and mode_has_aad(self.mode)

        # Key cache keyed by the 4-byte key_version bytes. Insertion order
        # drives eviction: oldest entry drops when the cache grows past
        # _KV_CACHE_SIZE. Seeded with the initial derived key.
        cache: dict[bytes, bytes] = {self.key_version: self.privacy_key}

        def _key_for(kv_bytes: bytes) -> bytes:
            cached = cache.get(kv_bytes)
            if cached is not None:
                return cached
            new_key = self._rekey_for_version(kv_bytes)
            cache[kv_bytes] = new_key
            # Evict oldest entries once past the cache bound.
            while len(cache) > _KV_CACHE_SIZE:
                oldest = next(iter(cache))
                del cache[oldest]
            return new_key

        def decrypt_fn(data: bytes) -> tuple[bytes, int]:
            if len(data) < CLEAR_HEADER_SIZE:
                raise ValueError(f"encrypted packet too short: {len(data)}")
            ctr, kv_int = struct.unpack(">QI", data[:CLEAR_HEADER_SIZE])
            kv_bytes = kv_int.to_bytes(4, "big")
            ciphertext = data[CLEAR_HEADER_SIZE:]

            if has_gmac:
                # Mirror of the sender pass-through: no pep-layer cipher for
                # GMAC-128 modes (cipher belongs to the SRT library per
                # NMOS With Privacy Encryption.md:294). _key_for still runs
                # for side-effects — the kv-based cache remains populated
                # in case a _KV GMAC variant is added later.
                _key_for(kv_bytes)
                return ciphertext, ctr

            key = _key_for(kv_bytes)
            plaintext = pep_decrypt(key, self.iv_prime, ctr, ciphertext)

            if has_cmac:
                if len(plaintext) < CMAC_TAG_SIZE:
                    raise ValueError(
                        f"decrypted payload too short for CMAC-64: {len(plaintext)}"
                    )
                payload = plaintext[:-CMAC_TAG_SIZE]
                recv_mac = plaintext[-CMAC_TAG_SIZE:]
                aad = data[:CLEAR_HEADER_SIZE] if has_aad else b""
                expected = pep_cmac64(key, payload, aad=aad)
                # Constant-time compare to thwart timing attacks.
                import hmac as _hmac
                if not _hmac.compare_digest(recv_mac, expected):
                    raise ValueError("CMAC-64 authentication tag mismatch")
                # Preserve the overall packet length so downstream parsers
                # (which expect a fixed-size 1432-byte packet) are unaffected.
                plaintext = payload + b"\x00" * CMAC_TAG_SIZE

            return plaintext, ctr

        return decrypt_fn

    # ------------------------------------------------------------------
    # Deprecated / not-implemented passthroughs kept for backward API compat
    # ------------------------------------------------------------------

    def encrypt(self, plaintext: bytes, ctr: int) -> bytes:
        """Low-level AES-CTR encrypt without the clear header / CMAC envelope.

        Prefer :meth:`make_encrypt_fn` for full wire-format output.
        """
        from ipmx_pep import pep_encrypt
        return cast(bytes, pep_encrypt(self.privacy_key, self.iv_prime, ctr, plaintext))

    def decrypt(self, ciphertext: bytes) -> tuple[bytes, int]:
        """Use :meth:`make_decrypt_fn`; direct invocation is unsupported."""
        raise NotImplementedError(
            "Use make_decrypt_fn() to create a stateful decryptor"
        )
