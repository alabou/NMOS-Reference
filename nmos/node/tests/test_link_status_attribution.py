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

import pytest

from nmos.node.events import (
    AlertDomain, EngineEvent, EventId, emit_transport_error, is_link_down,
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
        """
        assert is_link_down("lo") is False

    def test_unknown_interface_is_undetermined_not_down(self) -> None:
        """``None``, never ``True``: claiming AllDown because the interface
        could not be inspected is the same false alarm as claiming it because
        a peer hung up."""
        assert is_link_down("definitely-not-an-interface") is None
        assert is_link_down("*") is None

    def test_an_administratively_down_interface_reads_as_down(self) -> None:
        """Uses whichever real interface is down, if the host has one.

        Skips rather than fabricates: sysfs is the thing under test, so a
        synthetic fixture would only test the fixture.
        """
        net = pathlib.Path("/sys/class/net")
        if not net.is_dir():
            pytest.skip("no sysfs network information on this host")
        down = [
            iface.name for iface in net.iterdir()
            if is_link_down(iface.name) is True
        ]
        if not down:
            pytest.skip("every interface on this host is up")
        assert is_link_down(down[0]) is True


class TestTransportErrorDoesNotClaimLinkDown:
    @pytest.mark.asyncio
    async def test_connection_fault_on_a_healthy_interface_raises_no_link_event(
        self,
    ) -> None:
        """A peer-level failure over a working interface: transport only."""
        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=20)
        emit_transport_error(
            queue, "resource-1", "lo", is_sender=False,
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
        net = pathlib.Path("/sys/class/net")
        if not net.is_dir():
            pytest.skip("no sysfs network information on this host")
        down = [i.name for i in net.iterdir() if is_link_down(i.name) is True]
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
