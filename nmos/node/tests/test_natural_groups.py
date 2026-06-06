# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.node.types — NaturalGroups and PoolOfIndices."""

from __future__ import annotations

import pytest

from nmos.errors import Full, InvalidOperation
from nmos.node.types import NaturalGroups, PoolOfIndices


class TestPoolOfIndices:
    """PoolOfIndices — 256-slot allocator."""

    def test_allocate_first(self) -> None:
        pool = PoolOfIndices()
        assert pool.get_index() == 0

    def test_allocate_sequential(self) -> None:
        pool = PoolOfIndices()
        assert pool.get_index() == 0
        assert pool.get_index() == 1
        assert pool.get_index() == 2

    def test_release_and_reuse(self) -> None:
        pool = PoolOfIndices()
        i0 = pool.get_index()
        i1 = pool.get_index()
        pool.put_index(i0)
        assert pool.get_index() == i0  # reuses released slot

    def test_exhaust_raises_full(self) -> None:
        pool = PoolOfIndices()
        for _ in range(256):
            pool.get_index()
        with pytest.raises(Full):
            pool.get_index()

    def test_is_used(self) -> None:
        pool = PoolOfIndices()
        assert not pool.is_used(0)
        pool.get_index()
        assert pool.is_used(0)


class TestNaturalGroups:
    """NaturalGroups — transport grouping with format-specific role pools."""

    def _get_format_enum(self, urn: str) -> object:
        """Get format enum, skip test if enums not available."""
        try:
            from nmos.enums import EnumRegistry
            e = EnumRegistry.get(urn)
            if e is None:
                pytest.skip(f"enum {urn} not registered")
            return e
        except ImportError:
            pytest.skip("nmos.enums not available")

    def _get_transport_enum(self, urn: str) -> object:
        return self._get_format_enum(urn)  # same mechanism

    def test_video_group_hint(self) -> None:
        fmt = self._get_format_enum("urn:x-nmos:format:video")
        transport = self._get_transport_enum("urn:x-nmos:transport:rtp")
        groups = NaturalGroups()
        hint, role = groups.get_group_hint(3, fmt, transport)
        assert hint == "RTP 3:VIDEO 0"
        assert role == 0

    def test_audio_group_hint(self) -> None:
        fmt = self._get_format_enum("urn:x-nmos:format:audio")
        transport = self._get_transport_enum("urn:x-nmos:transport:rtp")
        groups = NaturalGroups()
        hint, role = groups.get_group_hint(5, fmt, transport)
        assert hint == "RTP 5:AUDIO 0"
        assert role == 0

    def test_multiple_roles_in_group(self) -> None:
        """Multiple resources in the same group get sequential role indices."""
        fmt = self._get_format_enum("urn:x-nmos:format:video")
        transport = self._get_transport_enum("urn:x-nmos:transport:rtp")
        groups = NaturalGroups()
        _, r0 = groups.get_group_hint(0, fmt, transport)
        _, r1 = groups.get_group_hint(0, fmt, transport)
        assert r0 == 0
        assert r1 == 1

    def test_put_role_index(self) -> None:
        """Released role index can be reused."""
        fmt = self._get_format_enum("urn:x-nmos:format:video")
        transport = self._get_transport_enum("urn:x-nmos:transport:rtp")
        groups = NaturalGroups()
        _, r0 = groups.get_group_hint(0, fmt, transport)
        groups.put_role_index(0, fmt, r0)
        _, r_reused = groups.get_group_hint(0, fmt, transport)
        assert r_reused == r0

    def test_different_formats_independent(self) -> None:
        """Video and audio role pools within the same group are independent."""
        video = self._get_format_enum("urn:x-nmos:format:video")
        audio = self._get_format_enum("urn:x-nmos:format:audio")
        transport = self._get_transport_enum("urn:x-nmos:transport:rtp")
        groups = NaturalGroups()
        _, vr = groups.get_group_hint(0, video, transport)
        _, ar = groups.get_group_hint(0, audio, transport)
        assert vr == 0
        assert ar == 0  # independent pool

    def test_transport_name_in_hint(self) -> None:
        """Different transports produce different names in hints."""
        fmt = self._get_format_enum("urn:x-nmos:format:video")
        ndi = self._get_transport_enum("urn:x-nmos:transport:ndi")
        groups = NaturalGroups()
        hint, _ = groups.get_group_hint(1, fmt, ndi)
        assert hint.startswith("NDI ")

    def test_set_name(self) -> None:
        groups = NaturalGroups()
        groups.set_name(5, "Custom Name")
        assert groups.get_description(5) == ""  # description is separate
