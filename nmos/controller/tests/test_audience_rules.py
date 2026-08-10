# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The controller's audience prediction must accept both of the spec's rules.

``aud_entry_allows_current_node`` in :mod:`nmos.oauth2` — the authority, and
what the Node actually runs — accepts an ``aud`` entry under **either** the
serial-number rule or the DNS-name rule, and its own docstring says a Node
"MUST NOT require operators to choose a single mode globally". The
controller's mirror of that check implemented only the first, so any
Authorization Server minting DNS-mode audiences made the UI disagree with
every Node: a spec-valid ``aud: ["*.local"]`` is accepted by the reference
rig's Nodes, yet every Device rendered ``READS_BLOCKED`` with its controls
disabled.

The asymmetry in these tests is deliberate and is the point of the design:
a match must be accepted, but a non-match is *not* asserted to be a refusal,
because the controller cannot see the Node's full certificate SAN list.
"""

from __future__ import annotations

from nmos.controller.handlers import _aud_covers_serial, _device_hostnames
from nmos.oauth2 import aud_entry_allows_current_node


#: What the reference rig's Node certificates carry, in SAN order.
CERT_NAMES = [
    "Example.Company.Device.Server.ABC.SNX00001.example.com",
    "Example.Company.Device.example.com",
    "Example.Company.Device.Server.example.com",
    "Example.Company.Device.Server.ABC.example.com",
    "XYZ-SNX00001.local",
    "XYZ-SNX00001",
]
SERIAL = "SNX00001"
HOSTNAMES = ("xyz-snx00001",)


def _device(*hrefs: str) -> dict:
    return {"controls": [{"href": h} for h in hrefs]}


class TestDeviceHostnames:
    def test_extracts_hosts_from_control_hrefs(self) -> None:
        device = _device(
            "https://xyz-snx00001:7051/x-nmos/connection/v1.1/",
            "https://xyz-snx00001:7051/x-nmos/streamcompatibility/v1.0/",
        )
        assert _device_hostnames(device) == ("xyz-snx00001",)

    def test_deduplicates_and_tolerates_junk(self) -> None:
        device = {"controls": [
            {"href": "https://a:1/x"}, {"href": "https://a:2/y"},
            {"href": None}, {"no_href": True}, "not-a-dict",
            {"href": "https://b:3/z"},
        ]}
        assert _device_hostnames(device) == ("a", "b")

    def test_no_controls_yields_nothing(self) -> None:
        assert _device_hostnames({}) == ()


class TestSerialRule:
    def test_bare_serial_entry(self) -> None:
        assert _aud_covers_serial([SERIAL], SERIAL, HOSTNAMES) is True

    def test_hostname_entry_containing_the_serial(self) -> None:
        assert _aud_covers_serial(["XYZ-SNX00001"], SERIAL, HOSTNAMES) is True

    def test_wildcard_covers_everything(self) -> None:
        assert _aud_covers_serial(["*"], SERIAL, ()) is True

    def test_unrelated_serial_is_not_covered(self) -> None:
        assert _aud_covers_serial(["XYZ-SNX00002"], SERIAL, HOSTNAMES) is False

    def test_empty_serial_never_matches(self) -> None:
        """An empty serial is a substring of everything; it must not read as
        covered just because the aud list is non-empty."""
        assert _aud_covers_serial(["anything"], "", HOSTNAMES) is False

    def test_non_string_entries_are_ignored(self) -> None:
        assert _aud_covers_serial([None, 7, {}], SERIAL, HOSTNAMES) is False


class TestDnsNameRule:
    """The rule that was missing."""

    def test_single_label_wildcard_matching_the_advertised_host(self) -> None:
        """``*.local`` matches ``XYZ-SNX00001.local`` per RFC 4592, and the
        Node accepts it — so the controller must not pre-emptively block."""
        entry = "*.local"
        assert aud_entry_allows_current_node(entry, SERIAL, CERT_NAMES, True) is True
        assert _aud_covers_serial([entry], SERIAL, ("xyz-snx00001.local",)) is True

    def test_exact_hostname_without_the_serial_in_it(self) -> None:
        """A cert identity shared across devices carries no serial, so the
        serial rule cannot see it; the DNS rule can."""
        entry = "Example.Company.Device.Server.ABC.example.com"
        assert aud_entry_allows_current_node(entry, SERIAL, CERT_NAMES, True) is True
        assert _aud_covers_serial(
            [entry], SERIAL, ("example.company.device.server.abc.example.com",),
        ) is True

    def test_wildcard_must_still_respect_one_label(self) -> None:
        """RFC 4592: ``*.local`` does not match a multi-label prefix, so this
        must not be treated as coverage."""
        assert _aud_covers_serial(
            ["*.local"], SERIAL, ("a.b.local",),
        ) is False

    def test_matching_is_delegated_not_reimplemented(self) -> None:
        """Both sides must agree on the same RFC 4592 semantics.

        Iterating a set of patterns through both implementations catches a
        divergence introduced by editing either one.
        """
        patterns = ["*.local", "*.example.com", "xyz-snx00001", "*",
                    "XYZ-SNX00002", "*.other"]
        for pattern in patterns:
            node_side = aud_entry_allows_current_node(
                pattern, SERIAL, CERT_NAMES, True)
            controller_side = _aud_covers_serial(
                [pattern], SERIAL, tuple(n.lower() for n in CERT_NAMES))
            if node_side:
                assert controller_side is True, (
                    f"the Node accepts {pattern!r} but the controller would "
                    f"paint the device unreachable"
                )


class TestPermissiveByDesign:
    def test_serial_bearing_entry_is_allowed_through(self) -> None:
        """An entry naming the serial but not equal to any cert identity is
        refused by the Node yet allowed here on purpose.

        The controller cannot see the cert identities, so tightening this
        would refuse audiences the Node would honour. A wrong 'covered' costs
        one honest 403 carrying the Node's reason; a wrong 'not covered'
        removes the control and misreports a working device.
        """
        entry = "XYZ-SNX00001,XYZ-SNX00002"
        assert aud_entry_allows_current_node(entry, SERIAL, CERT_NAMES, True) is False
        assert _aud_covers_serial([entry], SERIAL, HOSTNAMES) is True

    def test_hostnames_are_optional(self) -> None:
        """Callers without hostname information keep the old behaviour rather
        than failing closed."""
        assert _aud_covers_serial(["XYZ-SNX00001"], SERIAL) is True
        assert _aud_covers_serial(["*.local"], SERIAL) is False
