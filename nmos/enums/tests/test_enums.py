# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.enums module."""

from __future__ import annotations

import threading

import pytest

from nmos.enums import EnumId, EnumRegistry


@pytest.fixture(autouse=True)
def _clean_registry() -> None:  # type: ignore[misc]
    """Save and restore the global registry around each test.

    We cannot use reset() because module-level constants (FormatVideo etc.)
    hold references to EnumId objects that must remain in the registry.
    Instead, save the registry state, let the test modify it, then restore.
    """
    saved = dict(EnumRegistry._entries)
    yield  # type: ignore[misc]
    EnumRegistry._entries.clear()
    EnumRegistry._entries.update(saved)


class TestEnumId:
    """EnumId wraps a string and uses identity comparison."""

    def test_str_returns_string(self) -> None:
        e = EnumId("source_id")
        assert str(e) == "source_id"

    def test_repr(self) -> None:
        e = EnumId("format")
        assert repr(e) == "EnumId('format')"

    def test_identity_vs_equality(self) -> None:
        a = EnumId("x")
        b = EnumId("x")
        # Two separately created EnumIds are different objects...
        assert a is not b
        # ...but == compares by string value (safety net for Python idiom)
        assert a == b

    def test_same_object_is_equal(self) -> None:
        a = EnumId("x")
        assert a is a
        assert a == a

    def test_hash_is_string_based(self) -> None:
        a = EnumId("x")
        b = EnumId("x")
        # Same string value -> same hash (required for dict/set correctness)
        assert hash(a) == hash(b)
        # Different string value -> different hash
        c = EnumId("y")
        assert hash(a) != hash(c)

    def test_usable_as_dict_key(self) -> None:
        a = EnumId("key")
        d: dict[EnumId, int] = {a: 42}
        assert d[a] == 42

    def test_empty_string_is_valid(self) -> None:
        e = EnumId("")
        assert str(e) == ""


class TestEnumRegistry:
    """EnumRegistry deduplicates EnumId instances by string."""

    def test_get_creates_entry(self) -> None:
        e = EnumRegistry.get("source_id")
        assert isinstance(e, EnumId)
        assert str(e) == "source_id"

    def test_get_returns_same_object(self) -> None:
        a = EnumRegistry.get("source_id")
        b = EnumRegistry.get("source_id")
        assert a is b

    def test_different_strings_different_objects(self) -> None:
        a = EnumRegistry.get("source_id")
        b = EnumRegistry.get("format")
        assert a is not b

    def test_empty_string_deduplication(self) -> None:
        a = EnumRegistry.get("")
        b = EnumRegistry.get("")
        assert a is b
        assert str(a) == ""

    def test_lookup_existing(self) -> None:
        original = EnumRegistry.get("format")
        found = EnumRegistry.lookup("format")
        assert found is original

    def test_lookup_missing_returns_none(self) -> None:
        result = EnumRegistry.lookup("nonexistent")
        assert result is None

    def test_lookup_auto_creates(self) -> None:
        result = EnumRegistry.lookup("new_one", auto=True)
        assert result is not None
        assert str(result) == "new_one"
        # Subsequent get returns the same object
        assert EnumRegistry.get("new_one") is result

    def test_auto_lookup_never_returns_none(self) -> None:
        result = EnumRegistry.auto_lookup("auto_test")
        assert result is not None
        assert str(result) == "auto_test"

    def test_count_increments(self) -> None:
        baseline = EnumRegistry.count()
        EnumRegistry.get("test_count_a")
        assert EnumRegistry.count() == baseline + 1
        EnumRegistry.get("test_count_b")
        assert EnumRegistry.count() == baseline + 2
        EnumRegistry.get("test_count_a")  # duplicate
        assert EnumRegistry.count() == baseline + 2

    def test_reset_clears_all(self) -> None:
        EnumRegistry.get("test_reset_x")
        EnumRegistry.get("test_reset_y")
        before = EnumRegistry.count()
        assert before >= 2
        EnumRegistry.reset()
        assert EnumRegistry.count() == 0
        # Old references are stale -- new get creates fresh objects
        assert EnumRegistry.lookup("test_reset_x") is None


class TestCrossModuleDeduplication:
    """Two separate generated files both create NewEnumMapEntry("source_id"),
    then init() retargets one to point at the other. In Python, both calling
    EnumRegistry.get("source_id") naturally returns the same object.
    """

    def test_two_modules_same_enum(self) -> None:
        # Simulates enums_NSource.py
        source_id_from_source = EnumRegistry.get("source_id")
        # Simulates enums_NFlowCore.py
        source_id_from_flow = EnumRegistry.get("source_id")
        # They are the same object -- identity comparison works
        assert source_id_from_source is source_id_from_flow

    def test_identity_in_switch(self) -> None:
        """Simulates the generated decoder's name dispatch."""
        SourceId = EnumRegistry.get("source_id")
        Format = EnumRegistry.get("format")
        Label = EnumRegistry.get("label")

        # Simulate decoding a JSON key
        incoming = EnumRegistry.auto_lookup("source_id")

        # Switch-like dispatch using identity
        if incoming is SourceId:
            matched = "source_id"
        elif incoming is Format:
            matched = "format"
        elif incoming is Label:
            matched = "label"
        else:
            matched = "unknown"

        assert matched == "source_id"


class TestThreadSafety:
    """EnumRegistry is safe for concurrent access."""

    def test_concurrent_get(self) -> None:
        results: list[EnumId] = []
        barrier = threading.Barrier(10)

        def worker() -> None:
            barrier.wait()
            results.append(EnumRegistry.get("shared"))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads got the same object
        assert len(results) == 10
        first = results[0]
        for r in results[1:]:
            assert r is first


# ---------------------------------------------------------------------------
# Namespace cross-check: MatroxCCF.py constants vs namespaces.py
# ---------------------------------------------------------------------------

class TestCcfNamespaceCrossCheck:
    """Verify that MatroxCCF.py capability constants match the namespaces
    defined in nmos.codegen.namespaces.

    The Python CCF (caps/MatroxCCF.py) defines the same constants as plain
    strings.  If a namespace changes in namespaces.py, the corresponding
    CCF constant MUST be updated.  This test catches mismatches at test time
    instead of at runtime (where they cause silent constraint matching failures).
    """

    def test_h26x_cap_namespace_caps(self) -> None:
        """CapFormatConstantBitRate and CapTransport*Mode use H26x_CAP_NAMESPACE."""
        from nmos.codegen.namespaces import H26x_CAP_NAMESPACE
        from caps.MatroxCCF import (
            CapFormatConstantBitRate,
            CapTransportParameterSetsFlowMode,
            CapTransportParameterSetsTransportMode,
        )

        assert CapFormatConstantBitRate == H26x_CAP_NAMESPACE + "cap:format:constant_bit_rate", \
            f"CapFormatConstantBitRate namespace mismatch: {CapFormatConstantBitRate}"
        assert CapTransportParameterSetsFlowMode == H26x_CAP_NAMESPACE + "cap:transport:parameter_sets_flow_mode", \
            f"CapTransportParameterSetsFlowMode namespace mismatch: {CapTransportParameterSetsFlowMode}"
        assert CapTransportParameterSetsTransportMode == H26x_CAP_NAMESPACE + "cap:transport:parameter_sets_transport_mode", \
            f"CapTransportParameterSetsTransportMode namespace mismatch: {CapTransportParameterSetsTransportMode}"

    def test_matrox_cap_namespace_caps(self) -> None:
        """Capabilities using other Matrox namespaces."""
        from nmos.codegen.namespaces import (
            SYNCMEDIA_CAP_NAMESPACE, CLOCKREF_CAP_NAMESPACE,
            CHANORDER_CAP_NAMESPACE, PRIVACY_CAP_NAMESPACE,
            USB_CAP_NAMESPACE, INFOBLOCK_CAP_NAMESPACE,
        )
        from caps.MatroxCCF import (
            CapTransportSynchronousMedia, CapTransportClockRefType,
            CapFormatVideoLayers, CapFormatAudioLayers, CapFormatDataLayers,
        )

        assert CapTransportSynchronousMedia == SYNCMEDIA_CAP_NAMESPACE + "cap:transport:synchronous_media", \
            f"CapTransportSynchronousMedia namespace mismatch: {CapTransportSynchronousMedia}"
        assert CapTransportClockRefType == CLOCKREF_CAP_NAMESPACE + "cap:transport:clock_ref_type", \
            f"CapTransportClockRefType namespace mismatch: {CapTransportClockRefType}"

    def test_nmos_standard_caps_unchanged(self) -> None:
        """Standard NMOS capabilities always use urn:x-nmos: namespace."""
        from caps.MatroxCCF import (
            CapFormatMediaType, CapFormatGrainRate,
            CapFormatFrameWidth, CapFormatFrameHeight,
            CapFormatChannelCount, CapFormatSampleRate, CapFormatSampleDepth,
            CapFormatBitRate, CapFormatProfile, CapFormatLevel,
            CapFormatInterlaceMode, CapFormatColorspace,
            CapFormatTransferCharacteristic, CapFormatColorSampling,
            CapFormatComponentDepth,
        )

        nmos_caps = {
            "CapFormatMediaType": CapFormatMediaType,
            "CapFormatGrainRate": CapFormatGrainRate,
            "CapFormatFrameWidth": CapFormatFrameWidth,
            "CapFormatFrameHeight": CapFormatFrameHeight,
            "CapFormatChannelCount": CapFormatChannelCount,
            "CapFormatSampleRate": CapFormatSampleRate,
            "CapFormatSampleDepth": CapFormatSampleDepth,
            "CapFormatBitRate": CapFormatBitRate,
            "CapFormatProfile": CapFormatProfile,
            "CapFormatLevel": CapFormatLevel,
            "CapFormatInterlaceMode": CapFormatInterlaceMode,
            "CapFormatColorspace": CapFormatColorspace,
            "CapFormatTransferCharacteristic": CapFormatTransferCharacteristic,
            "CapFormatColorSampling": CapFormatColorSampling,
            "CapFormatComponentDepth": CapFormatComponentDepth,
        }

        for name, value in nmos_caps.items():
            assert value.startswith("urn:x-nmos:cap:"), \
                f"{name} should use urn:x-nmos: namespace, got: {value}"
