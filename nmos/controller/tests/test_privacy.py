# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for nmos.controller.privacy."""

from __future__ import annotations

from typing import Any

from nmos.controller.privacy import (
    EXT_PRIVACY_ECDH_RECEIVER_PUBLIC_KEY,
    EXT_PRIVACY_ECDH_SENDER_PUBLIC_KEY,
    EXT_PRIVACY_KEY_GENERATOR,
    EXT_PRIVACY_KEY_ID,
    EXT_PRIVACY_KEY_VERSION,
    compute_privacy_options,
    is_ecdh_mode,
    receiver_to_sender_fields,
    sender_to_receiver_fields,
)


def _constraints(
    protocols: list[str] | None = None,
    modes: list[str] | None = None,
    curves: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build a single-leg IS-05 transport-parameter-constraints array."""
    leg: dict[str, Any] = {}
    if protocols is not None:
        leg["ext_privacy_protocol"] = {"enum": protocols}
    if modes is not None:
        leg["ext_privacy_mode"] = {"enum": modes}
    if curves is not None:
        leg["ext_privacy_ecdh_curve"] = {"enum": curves}
    return [leg]


class TestIsEcdhMode:
    def test_ecdh_prefix_detected(self) -> None:
        assert is_ecdh_mode("ECDH_AES-128-CTR") is True
        assert is_ecdh_mode("ECDH_AES-256-CTR") is True

    def test_non_ecdh_modes(self) -> None:
        assert is_ecdh_mode("AES-128-CTR") is False
        assert is_ecdh_mode("AES-256-CTR") is False
        assert is_ecdh_mode("NULL") is False

    def test_none_and_empty(self) -> None:
        assert is_ecdh_mode(None) is False
        assert is_ecdh_mode("") is False


class TestComputePrivacyOptions:
    def test_full_intersection(self) -> None:
        """Every sender + receiver declares overlapping enums; the
        intersection is the common subset."""
        opts = compute_privacy_options(
            sender_constraints=[
                _constraints(
                    protocols=["RTP", "UDP"],
                    modes=["AES-128-CTR", "AES-256-CTR"],
                    curves=["secp256r1", "secp521r1"],
                ),
            ],
            receiver_constraints=[
                _constraints(
                    protocols=["RTP"],
                    modes=["AES-256-CTR", "ECDH_AES-256-CTR"],
                    curves=["secp521r1"],
                ),
            ],
        )
        assert opts.protocols == ["RTP"]
        assert opts.modes == ["AES-256-CTR"]
        assert opts.curves == ["secp521r1"]
        assert opts.pep_available is True

    def test_null_only_enum_treated_as_no_support(self) -> None:
        """A resource whose only mode value is ``NULL`` offers no PEP
        — intersection collapses to empty."""
        opts = compute_privacy_options(
            sender_constraints=[
                _constraints(modes=["NULL"]),
            ],
            receiver_constraints=[
                _constraints(modes=["AES-128-CTR", "NULL"]),
            ],
        )
        # Sender has no usable modes → intersection empty.
        assert opts.modes == []
        assert opts.pep_available is False

    def test_missing_key_fails_closed(self) -> None:
        """A resource that omits ``ext_privacy_mode`` entirely is treated
        as 'no PEP for mode' — intersection empty, pep_available False."""
        opts = compute_privacy_options(
            sender_constraints=[{}],
            receiver_constraints=[_constraints(modes=["AES-128-CTR"])],
        )
        assert opts.modes == []
        assert opts.pep_available is False

    def test_disjoint_enums_yield_empty(self) -> None:
        """No overlap between the two sides produces empty intersection."""
        opts = compute_privacy_options(
            sender_constraints=[_constraints(modes=["AES-128-CTR"])],
            receiver_constraints=[_constraints(modes=["AES-256-CTR"])],
        )
        assert opts.modes == []
        assert opts.protocols == []
        assert opts.pep_available is False

    def test_curve_empty_when_no_ecdh_selected(self) -> None:
        """When no selected mode is ECDH, empty curves is fine — the
        UI just hides the Curve dropdown."""
        opts = compute_privacy_options(
            sender_constraints=[
                _constraints(
                    protocols=["RTP"],
                    modes=["AES-128-CTR"],
                    curves=[],    # declared but empty (all NULL → set() stripped)
                ),
            ],
            receiver_constraints=[
                _constraints(
                    protocols=["RTP"],
                    modes=["AES-128-CTR"],
                    curves=[],
                ),
            ],
        )
        assert opts.protocols == ["RTP"]
        assert opts.modes == ["AES-128-CTR"]
        assert opts.curves == []
        assert opts.pep_available is True

    def test_exclusivity_all_devices_advertise_service(self) -> None:
        """``exclusivity_ok`` is True iff every device of the selection
        advertises the reservation service (i.e. the resolver returns
        non-None for each)."""
        sender_dev = {"id": "dev1"}
        receiver_dev = {"id": "dev2"}

        def resolver(d: dict[str, Any]) -> str | None:
            return f"https://{d['id']}/exclusive/v1.0/"

        opts = compute_privacy_options(
            sender_constraints=[_constraints(modes=["AES-128-CTR"])],
            receiver_constraints=[_constraints(modes=["AES-128-CTR"])],
            sender_devices=[sender_dev],
            receiver_devices=[receiver_dev],
            device_service_resolver=resolver,
        )
        assert opts.exclusivity_ok is True

    def test_exclusivity_mixed_yields_false(self) -> None:
        """If even one device doesn't advertise the service,
        ``exclusivity_ok`` is False — the Exclusivity toggle is
        disabled in the UI."""
        def resolver(d: dict[str, Any]) -> str | None:
            return None if d["id"] == "dev2" else f"https://{d['id']}/"

        opts = compute_privacy_options(
            sender_constraints=[_constraints(modes=["AES-128-CTR"])],
            receiver_constraints=[_constraints(modes=["AES-128-CTR"])],
            sender_devices=[{"id": "dev1"}],
            receiver_devices=[{"id": "dev2"}],
            device_service_resolver=resolver,
        )
        assert opts.exclusivity_ok is False


class TestSenderToReceiverFields:
    def test_non_ecdh_forwards_key_triplet(self) -> None:
        active = [{
            "ext_privacy_key_generator": "prng-v2",
            "ext_privacy_key_version": "3",
            "ext_privacy_key_id": "abcd1234",
            "ext_privacy_iv": "should-not-be-forwarded",
        }]
        out = sender_to_receiver_fields(active, ecdh=False)
        assert out == {
            EXT_PRIVACY_KEY_GENERATOR: "prng-v2",
            EXT_PRIVACY_KEY_VERSION: "3",
            EXT_PRIVACY_KEY_ID: "abcd1234",
        }

    def test_ecdh_also_forwards_sender_public_key(self) -> None:
        active = [{
            "ext_privacy_key_generator": "prng-v2",
            "ext_privacy_key_version": "3",
            "ext_privacy_key_id": "abcd1234",
            "ext_privacy_ecdh_sender_public_key": "0xDEADBEEF",
        }]
        out = sender_to_receiver_fields(active, ecdh=True)
        assert out[EXT_PRIVACY_ECDH_SENDER_PUBLIC_KEY] == "0xDEADBEEF"
        assert out[EXT_PRIVACY_KEY_GENERATOR] == "prng-v2"

    def test_absent_fields_omitted(self) -> None:
        """Sender active params missing a field simply drop out — the
        receiver uses its own default. No 500 for partial data."""
        active = [{"ext_privacy_key_generator": "x"}]
        out = sender_to_receiver_fields(active, ecdh=False)
        assert out == {EXT_PRIVACY_KEY_GENERATOR: "x"}

    def test_malformed_input_yields_empty(self) -> None:
        assert sender_to_receiver_fields(None, ecdh=False) == {}
        assert sender_to_receiver_fields([], ecdh=False) == {}
        assert sender_to_receiver_fields([None], ecdh=False) == {}


class TestReceiverToSenderFields:
    def test_extracts_receiver_public_key(self) -> None:
        active = [{"ext_privacy_ecdh_receiver_public_key": "0xF00BAA"}]
        out = receiver_to_sender_fields(active)
        assert out == {EXT_PRIVACY_ECDH_RECEIVER_PUBLIC_KEY: "0xF00BAA"}

    def test_missing_returns_empty(self) -> None:
        """Receiver not regenerated its key yet → empty result signals
        the caller to deactivate+reactivate the receiver first."""
        assert receiver_to_sender_fields([{}]) == {}
        assert receiver_to_sender_fields([]) == {}
