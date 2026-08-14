# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the multi-registry target list and failover selection."""

from __future__ import annotations

import pytest

from nmos.rds_targets import (
    MAX_REGISTRIES,
    RdsSpecError,
    RegistrySelector,
    RegistryTarget,
    build_targets,
    parse_rds_spec,
    target_from_scalars,
)

DEFAULT = target_from_scalars(
    host="",
    registration_port=8447,
    query_port=8446,
    ws_port=8448,
    tls=True,
    certificate_name="Example.Company.Device.Server.example.com",
    trusted_root_ca=("GlobalRoot.pem",),
    client_certificate="client.chain.pem",
    client_key="client.key",
)


class TestSpecParsing:
    def test_host_only_inherits_every_scalar_default(self) -> None:
        """The common case: a homogeneous cluster differing only by address."""
        t = parse_rds_spec("host=192.0.2.7", DEFAULT)
        assert t.host == "192.0.2.7"
        assert (t.registration_port, t.query_port, t.ws_port) == (8447, 8446, 8448)
        assert t.tls is True
        assert t.certificate_name == DEFAULT.certificate_name
        assert t.trusted_root_ca == ("GlobalRoot.pem",)
        assert t.client_certificate == "client.chain.pem"

    def test_ports_override_individually(self) -> None:
        """The WSL2 rig: members share an address and differ by port block."""
        t = parse_rds_spec(
            "host=localhost,queryPort=8456,wsPort=8458,registrationPort=8457",
            DEFAULT,
        )
        assert (t.registration_port, t.query_port, t.ws_port) == (8457, 8456, 8458)

    def test_disable_tls_bare_and_explicit(self) -> None:
        assert parse_rds_spec("host=a,disableTLS", DEFAULT).tls is False
        assert parse_rds_spec("host=a,disableTLS=true", DEFAULT).tls is False
        assert parse_rds_spec("host=a,disableTLS=false", DEFAULT).tls is True

    def test_repeated_ca_accumulates(self) -> None:
        t = parse_rds_spec("host=a,ca=One.pem,ca=Two.pem", DEFAULT)
        assert t.trusted_root_ca == ("One.pem", "Two.pem")

    def test_first_ca_replaces_the_inherited_defaults(self) -> None:
        """An entry naming its own anchors describes a different PKI.

        Appending to the global list would make it trust more than was asked
        for, which is the wrong direction for a trust decision to fail in.
        """
        t = parse_rds_spec("host=a,ca=Only.pem", DEFAULT)
        assert t.trusted_root_ca == ("Only.pem",)

    def test_whitespace_is_tolerated(self) -> None:
        t = parse_rds_spec(" host = a , queryPort = 9000 ", DEFAULT)
        assert t.host == "a" and t.query_port == 9000

    @pytest.mark.parametrize(
        "spec,message",
        [
            ("", "at least host"),
            ("queryPort=8446", "requires host"),
            ("host=", "must not be empty"),
            ("host=a,bogus=1", "unknown --rds field"),
            ("host=a,queryPort=nope", "must be an integer"),
            ("host=a,queryPort=0", "must be 1-65535"),
            ("host=a,queryPort=70000", "must be 1-65535"),
            ("host=a,host=b", "more than once"),
            ("host=a,disableTLS=maybe", "must be true or false"),
        ],
    )
    def test_malformed_specs_are_rejected(self, spec: str, message: str) -> None:
        with pytest.raises(RdsSpecError, match=message):
            parse_rds_spec(spec, DEFAULT)


class TestTargetList:
    def test_no_specs_falls_back_to_the_scalar_flags(self) -> None:
        """An unchanged command line must keep working exactly as before."""
        scalar = target_from_scalars(
            host="registry.example.com", registration_port=8447,
            query_port=8446, ws_port=8448, tls=True,
        )
        assert build_targets(None, scalar) == (scalar,)

    def test_no_specs_and_no_host_is_no_registry(self) -> None:
        """``--rdsHost ""`` means standalone; it must not invent a target."""
        assert build_targets(None, DEFAULT) == ()

    def test_five_entries_are_allowed(self) -> None:
        specs = [f"host=10.0.0.{i}" for i in range(1, MAX_REGISTRIES + 1)]
        assert len(build_targets(specs, DEFAULT)) == MAX_REGISTRIES

    def test_more_than_five_is_refused(self) -> None:
        specs = [f"host=10.0.0.{i}" for i in range(1, MAX_REGISTRIES + 2)]
        with pytest.raises(RdsSpecError, match="at most 5"):
            build_targets(specs, DEFAULT)

    def test_duplicate_entries_are_refused(self) -> None:
        """Two entries for one member would fail over to a known-dead one."""
        with pytest.raises(RdsSpecError, match="duplicate"):
            build_targets(["host=a", "host=a"], DEFAULT)

    def test_same_host_different_port_is_not_a_duplicate(self) -> None:
        """The WSL2 rig is exactly this shape."""
        targets = build_targets(
            ["host=localhost,registrationPort=8447",
             "host=localhost,registrationPort=8457"],
            DEFAULT,
        )
        assert len(targets) == 2


class TestSelector:
    @staticmethod
    def _selector(n: int) -> RegistrySelector:
        return RegistrySelector(
            build_targets([f"host=10.0.0.{i}" for i in range(1, n + 1)], DEFAULT),
        )

    def test_starts_on_the_first_configured_registry(self) -> None:
        assert self._selector(3).current.host == "10.0.0.1"

    def test_failure_advances_to_the_next(self) -> None:
        s = self._selector(3)
        assert s.fail(s.current).host == "10.0.0.2"
        assert s.fail(s.current).host == "10.0.0.3"

    def test_advances_only_once_per_outage(self) -> None:
        """Seven clients notice one dead registry; the list must move once.

        The Node loop plus six WebSocket subscribers all report the same
        target. Advancing per report would skip the whole list on a single
        failure and land on a registry nobody has shown to be bad.
        """
        s = self._selector(3)
        dead = s.current
        results = [s.fail(dead) for _ in range(7)]
        assert all(r.host == "10.0.0.2" for r in results)
        assert s.failover_count == 1

    def test_reporting_a_stale_target_is_a_no_op(self) -> None:
        s = self._selector(3)
        first = s.current
        s.fail(first)
        assert s.fail(first).host == "10.0.0.2"
        assert s.failover_count == 1

    def test_it_wraps_around(self) -> None:
        """With every member down, keep cycling rather than giving up."""
        s = self._selector(2)
        s.fail(s.current)
        assert s.fail(s.current).host == "10.0.0.1"

    def test_no_failback_to_an_earlier_registry(self) -> None:
        """Nothing returns to a previous entry on its own.

        Each switch costs a full re-registration and a re-sync of six
        subscriptions, so a flapping member must not be able to drag every
        client back and forth.
        """
        s = self._selector(3)
        s.fail(s.current)
        for _ in range(50):
            assert s.current.host == "10.0.0.2"

    def test_a_single_registry_stays_put(self) -> None:
        """Nowhere to go: the caller retries the same one under its backoff."""
        s = self._selector(1)
        assert s.fail(s.current).host == "10.0.0.1"
        assert s.failover_count == 0
        assert s.has_alternatives is False

    def test_empty_is_refused(self) -> None:
        with pytest.raises(RdsSpecError, match="at least one registry"):
            RegistrySelector(())
