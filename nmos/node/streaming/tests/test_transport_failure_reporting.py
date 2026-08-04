# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""A transport that cannot start must say so, in both channels.

Two independent regressions are covered here.

**The event channel.** Every transport coroutine used to let a socket setup
failure escape as a bare exception. The task handle was discarded by
``start_streaming``, so the exception went nowhere and no engine event was
emitted either: the Node advertised a healthy Sender / Receiver that owned no
socket. Each coroutine now emits a transport error before re-raising.

**The state channel.** ``engine_state`` is set to ACTIVE before the task first
runs and nothing moved it off ACTIVE afterwards. ``_on_streaming_done`` now
records the outcome — including a coroutine that *returns* early, which is how
the TCP transports report a dead connection.

The failures are provoked with a TEST-NET-3 address (RFC 5737), which cannot
be a local address, so binding it fails deterministically with EADDRNOTAVAIL
rather than depending on what else is running on the host.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from nmos.node.events import EngineEvent, EventId, EventState
from nmos.node.streaming import _on_streaming_done
from nmos.node.streaming.transport_srt import srt_sender
from nmos.node.streaming.transport_tcp import tcp_sender
from nmos.node.streaming.transport_udp import udp_receiver, udp_sender
from nmos.node.types import Activation, EngineState

# RFC 5737 TEST-NET-3 — guaranteed not to be a local address.
NONLOCAL_IP = "203.0.113.99"


def _transport_errors(queue: asyncio.Queue[EngineEvent]) -> list[str]:
    """Drain ``queue`` and return the info text of every transport error."""
    out: list[str] = []
    while not queue.empty():
        ev = queue.get_nowait()
        if (ev.event == EventId.TRANSPORT_STREAM_ERROR
                and ev.state == EventState.ERROR):
            out.append(ev.info)
    return out


class TestSetupFailureIsReported:
    """A setup failure emits a transport error AND propagates."""

    @pytest.mark.asyncio
    async def test_udp_sender_multicast_interface_failure(self) -> None:
        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=100)
        # Non-local source with a multicast destination: the bind falls back to
        # the wildcard, then IP_MULTICAST_IF rejects the non-local interface.
        with pytest.raises(OSError):
            await udp_sender(
                loop=asyncio.get_event_loop(),
                source_ip=NONLOCAL_IP, source_port=0,
                dest_ip="239.255.77.9", dest_port=17801,
                sender_id=str(uuid.uuid4()), interface_name="lo",
                event_queue=queue, stop_event=asyncio.Event(),
            )
        assert any("sender socket setup failed" in e
                   for e in _transport_errors(queue))

    @pytest.mark.asyncio
    async def test_udp_receiver_bind_failure(self) -> None:
        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=100)
        with pytest.raises(OSError):
            await udp_receiver(
                loop=asyncio.get_event_loop(),
                interface_ip=NONLOCAL_IP, multicast_ip="", source_ip="",
                dest_port=17802,
                receiver_id=str(uuid.uuid4()), interface_name="lo",
                event_queue=queue, stop_event=asyncio.Event(),
            )
        assert any("receiver socket setup failed" in e
                   for e in _transport_errors(queue))

    @pytest.mark.asyncio
    async def test_srt_sender_bind_failure(self) -> None:
        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=100)
        with pytest.raises(OSError):
            await srt_sender(
                loop=asyncio.get_event_loop(),
                listen_ip=NONLOCAL_IP, listen_port=17803,
                sender_id=str(uuid.uuid4()), interface_name="lo",
                event_queue=queue, stop_event=asyncio.Event(),
            )
        assert any("sender socket setup failed" in e
                   for e in _transport_errors(queue))

    @pytest.mark.asyncio
    async def test_tcp_sender_listen_failure(self) -> None:
        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=100)
        with pytest.raises(OSError):
            await tcp_sender(
                listen_ip=NONLOCAL_IP, listen_port=17804,
                sender_id=str(uuid.uuid4()), interface_name="lo",
                event_queue=queue, stop_event=asyncio.Event(),
            )
        assert any("sender listen failed" in e
                   for e in _transport_errors(queue))


class TestSenderClearsTransientFault:
    """A send that succeeds after a failure clears the error.

    Regression: the senders emitted the error and nothing else, so one
    transient fault pinned transmissionStatus at Error for the lifetime of the
    activation. Windows made this permanent-looking — sending to a loopback
    multicast group fails with WinError 1231 until a Receiver joins — but a
    Linux ``ENOBUFS`` or a route flap produced the same stuck state.
    """

    @pytest.mark.asyncio
    async def test_udp_sender_emits_recovery(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=200)
        stop = asyncio.Event()
        real_sendto = loop.sock_sendto
        calls = {"n": 0}

        async def flaky_sendto(sock: object, data: bytes, addr: object) -> int:
            calls["n"] += 1
            if calls["n"] == 1:
                # The shape of the Windows fault: unroutable, then routable.
                raise OSError(1231, "The network location cannot be reached")
            return await real_sendto(sock, data, addr)  # type: ignore[arg-type]

        monkeypatch.setattr(loop, "sock_sendto", flaky_sendto)

        task = asyncio.ensure_future(udp_sender(
            loop=loop,
            source_ip="127.0.0.1", source_port=0,
            dest_ip="127.0.0.1", dest_port=17805,
            sender_id=str(uuid.uuid4()), interface_name="lo",
            event_queue=queue, stop_event=stop,
        ))
        # The sender paces at DEFAULT_PERIOD_NS (1s), so the window has to span
        # two sends: the injected failure and the success that clears it.
        await asyncio.sleep(1.5)
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

        events: list[EngineEvent] = []
        while not queue.empty():
            events.append(queue.get_nowait())

        errors = [i for i, e in enumerate(events)
                  if e.event == EventId.TRANSPORT_STREAM_ERROR
                  and "send error" in e.info]
        # ``info`` distinguishes a recovery from the NORMAL TRANSPORT_OK the
        # activate / starting lifecycle already emits (``emit_recovery`` tags
        # its events "recovery").
        recoveries = [i for i, e in enumerate(events)
                      if e.event == EventId.TRANSPORT_OK
                      and e.state == EventState.NORMAL
                      and e.info == "recovery"]
        assert errors, "expected the injected send failure to be reported"
        assert recoveries, "expected a recovery once sending succeeded again"
        assert recoveries[-1] > errors[0], "recovery must follow the error"


class TestEngineStateOutcome:
    """``_on_streaming_done`` records what actually happened."""

    @staticmethod
    def _active() -> tuple[Activation, asyncio.Event]:
        stop = asyncio.Event()
        act = Activation()
        act.engine = stop
        act.engine_state = EngineState.ACTIVE
        return act, stop

    @pytest.mark.asyncio
    async def test_exception_sets_error(self) -> None:
        act, stop = self._active()

        async def boom() -> None:
            raise OSError("bind failed")

        task = asyncio.ensure_future(boom())
        await asyncio.gather(task, return_exceptions=True)
        _on_streaming_done(act, "res-1", stop, task)
        assert act.engine_state == EngineState.ERROR

    @pytest.mark.asyncio
    async def test_early_return_sets_error(self) -> None:
        """The TCP transports break out of their loop instead of raising."""
        act, stop = self._active()

        async def gives_up() -> None:
            return

        task = asyncio.ensure_future(gives_up())
        await task
        _on_streaming_done(act, "res-2", stop, task)
        assert act.engine_state == EngineState.ERROR

    @pytest.mark.asyncio
    async def test_stop_requested_is_not_an_error(self) -> None:
        act, stop = self._active()

        async def stopped() -> None:
            return

        task = asyncio.ensure_future(stopped())
        await task
        stop.set()  # deactivation asked it to stop
        _on_streaming_done(act, "res-3", stop, task)
        assert act.engine_state == EngineState.ACTIVE

    @pytest.mark.asyncio
    async def test_cancellation_is_not_an_error(self) -> None:
        act, stop = self._active()

        async def forever() -> None:
            await asyncio.Event().wait()

        task = asyncio.ensure_future(forever())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        _on_streaming_done(act, "res-4", stop, task)
        assert act.engine_state == EngineState.ACTIVE

    @pytest.mark.asyncio
    async def test_already_deactivated_is_not_an_error(self) -> None:
        """``stop_streaming`` clears ``engine`` and owns the state from then on."""
        act, stop = self._active()
        act.engine = None
        act.engine_state = EngineState.INACTIVE

        async def done() -> None:
            return

        task = asyncio.ensure_future(done())
        await task
        _on_streaming_done(act, "res-5", stop, task)
        assert act.engine_state == EngineState.INACTIVE
