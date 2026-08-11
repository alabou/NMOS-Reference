# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Link status must describe the interface, not the peer.

BCP-008-01 §"Link Status": linkStatus exposes "the health of all the physical
links associated with the receiver", with AllUp / SomeDown / AllDown defined
over *interfaces*. So a peer refusing a connection, closing one, or going
quiet must not move it — those are connection faults, and connection/
transmission status is where they belong.

Every receiver in the streaming emulation used to pass ``link_down=True`` to
``emit_transport_error`` for any socket failure at all, so an ordinary end of
stream published linkStatus AllDown on a node whose Ethernet was fine. The
parameter no longer exists; the decision is made by asking the OS about the
interface.
"""

from __future__ import annotations

import asyncio
import inspect
import pathlib
import sys

import pytest

from nmos.node.events import (
    _IF_OPER_STATUS_DORMANT,
    _IF_OPER_STATUS_DOWN,
    _IF_OPER_STATUS_LOWER_LAYER_DOWN,
    _IF_OPER_STATUS_NOT_PRESENT,
    _IF_OPER_STATUS_TESTING,
    _IF_OPER_STATUS_UNKNOWN,
    _IF_OPER_STATUS_UP,
    _OPERSTATE_DOWN,
    _WINDOWS_OPER_STATUS_DOWN,
    AlertDomain,
    EngineEvent,
    EventId,
    emit_transport_error,
    interface_names,
    is_link_down,
    loopback_interface_name,
)


def _drain(queue: "asyncio.Queue[EngineEvent]") -> list[EngineEvent]:
    events: list[EngineEvent] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


class TestIsLinkDown:
    def test_loopback_reads_as_up(self) -> None:
        """The regression that made this worth testing.

        ``IFF_RUNNING`` is never present in sysfs ``flags``, so deriving the
        operational half from that file reported every interface — loopback
        included — as down. Loopback is always up on a running machine, which
        makes it the sharpest available check.

        The interface is named by :func:`loopback_interface_name` rather than
        hard-coded to ``lo``, which is a Linux name: asking for ``lo`` on
        Windows tests nothing, because no such interface exists there and the
        honest answer is ``None``. Each platform is asked about its own
        loopback, so the invariant being checked is the same one everywhere.
        """
        name = loopback_interface_name()
        assert name is not None, "this machine reports no loopback interface"
        assert is_link_down(name) is False

    def test_loopback_is_named_per_platform(self) -> None:
        # Guards the helper itself: a Windows GUID is not "lo", and returning
        # "lo" there would send every caller looking for something absent.
        name = loopback_interface_name()
        if sys.platform == "win32":
            assert name is None or name.startswith("{")
        else:
            assert name == "lo"

    def test_unknown_interface_is_undetermined_not_down(self) -> None:
        """``None``, never ``True``: claiming AllDown because the interface
        could not be inspected is the same false alarm as claiming it because
        a peer hung up."""
        assert is_link_down("definitely-not-an-interface") is None
        assert is_link_down("*") is None


class TestWindowsOperStatusMapping:
    """The Windows half must ask the same question sysfs is asked.

    ``OperStatus`` is MIB-II ``ifOperStatus``, which is what sysfs
    ``operstate`` is modelled on, so the two platforms can be held to one rule
    rather than two that drift. These run everywhere: the mapping is a table,
    and pinning it does not need Windows.
    """

    def test_only_the_three_down_conditions_are_down(self) -> None:
        assert _WINDOWS_OPER_STATUS_DOWN == {
            _IF_OPER_STATUS_DOWN,
            _IF_OPER_STATUS_NOT_PRESENT,
            _IF_OPER_STATUS_LOWER_LAYER_DOWN,
        }

    def test_it_mirrors_the_sysfs_down_set(self) -> None:
        # The same three conditions, spelled in the other platform's
        # vocabulary. If one list grows, this fails until the other does.
        assert _OPERSTATE_DOWN == {"down", "lowerlayerdown", "notpresent"}
        assert len(_WINDOWS_OPER_STATUS_DOWN) == len(_OPERSTATE_DOWN)

    @pytest.mark.parametrize("status", [
        _IF_OPER_STATUS_UP,
        _IF_OPER_STATUS_TESTING,
        _IF_OPER_STATUS_UNKNOWN,
        _IF_OPER_STATUS_DORMANT,
    ])
    def test_undetermined_states_are_not_down(self, status: int) -> None:
        # Mirrors the sysfs rule that "unknown" is not down: loopback reports
        # it, and it means the driver does not track operational state rather
        # than that the link is dead. Reporting these as down would raise
        # AllDown on a healthy node.
        assert status not in _WINDOWS_OPER_STATUS_DOWN

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows API path")
    def test_windows_reports_a_real_status_for_a_real_adapter(self) -> None:
        # The end-to-end shape of the Windows branch: at least one adapter is
        # enumerable, and every status it reports is a value the mapping knows.
        from nmos.node.events import _windows_adapters

        adapters = _windows_adapters()
        assert adapters, "GetAdaptersAddresses returned nothing"
        for _guid, _friendly, _description, _if_type, status in adapters:
            assert status in range(1, 8), f"unexpected OperStatus {status}"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows API path")
    def test_windows_matches_an_adapter_by_friendly_name(self) -> None:
        # The GUID is what netifaces yields, but an operator naming an
        # interface by hand writes what the Network Connections panel shows.
        from nmos.node.events import _windows_adapters, _windows_oper_status

        for guid, friendly, _description, _if_type, status in _windows_adapters():
            if friendly:
                assert _windows_oper_status(friendly) == status
                assert _windows_oper_status(guid) == status
                return
        pytest.skip("no adapter exposes a friendly name")

    def test_an_administratively_down_interface_reads_as_down(self) -> None:
        """Uses whichever real interface is down, if the host has one.

        Skips rather than fabricates: the OS query is the thing under test, so
        a synthetic fixture would only test the fixture.

        Enumerated through :func:`interface_names` rather than by listing
        sysfs, so this exercises whichever backend the platform actually uses.
        Reaching into ``/sys/class/net`` from the test meant the Windows path
        could never be covered by the very tests written to cover it.
        """
        names = interface_names()
        if not names:
            pytest.skip("this host reports no network interfaces")
        down = [name for name in names if is_link_down(name) is True]
        if not down:
            pytest.skip("every interface on this host is up")
        assert is_link_down(down[0]) is True


class TestTransportErrorDoesNotClaimLinkDown:
    @pytest.mark.asyncio
    async def test_connection_fault_on_a_healthy_interface_raises_no_link_event(
        self,
    ) -> None:
        """A peer-level failure over a working interface: transport only."""
        # The loopback by its platform name: passing "lo" on Windows names no
        # interface at all, so the link would read as undetermined and the test
        # would pass without ever exercising "the interface is fine".
        healthy = loopback_interface_name()
        assert healthy is not None, "this machine reports no loopback interface"
        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=20)
        emit_transport_error(
            queue, "resource-1", healthy, is_sender=False,
            info="connection lost",
        )
        events = _drain(queue)
        assert [e.event for e in events] == [EventId.TRANSPORT_STREAM_ERROR], (
            f"expected only a transport error; got "
            f"{[(e.domain, e.event, e.info) for e in events]}"
        )
        assert not any(e.domain == AlertDomain.LINK for e in events)

    @pytest.mark.asyncio
    async def test_link_event_still_raised_when_the_interface_is_really_down(
        self,
    ) -> None:
        names = interface_names()
        if not names:
            pytest.skip("this host reports no network interfaces")
        down = [name for name in names if is_link_down(name) is True]
        if not down:
            pytest.skip("every interface on this host is up")

        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=20)
        emit_transport_error(
            queue, "resource-1", down[0], is_sender=False, info="recv error",
        )
        kinds = [e.event for e in _drain(queue)]
        assert EventId.TRANSPORT_STREAM_ERROR in kinds
        assert EventId.LINK_DOWN in kinds, (
            "a genuinely down interface must still report a link failure"
        )

    def test_no_call_site_can_assert_link_down_by_argument(self) -> None:
        """The parameter is removed, not defaulted.

        Nine receiver call sites across the three transports used to pass
        ``link_down=True``. Deleting the parameter is what stops a tenth being
        added.
        """
        assert "link_down" not in inspect.signature(emit_transport_error).parameters

    def test_no_transport_passes_a_link_down_argument(self) -> None:
        streaming = pathlib.Path(__file__).resolve().parents[1] / "streaming"
        offenders = [
            f"{path.name}:{n}"
            for path in sorted(streaming.glob("transport_*.py"))
            for n, line in enumerate(path.read_text().splitlines(), 1)
            if "link_down=True" in line and not line.lstrip().startswith("#")
        ]
        assert not offenders, f"link_down asserted at {offenders}"
