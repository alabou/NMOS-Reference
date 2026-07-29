# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IS-05 scheduled activation — activate_scheduled_relative / _absolute.

A scheduled activation is accepted now (202) and carried out later by a
background timer. These tests cover the whole life of that timer: arming it,
what the 202 reports, the activation actually happening, what /staged and
/active say afterwards, withdrawing it with a null mode, the 423 that protects
it from being displaced, and the ways a background timer can go wrong
(collected by the garbage collector, swallowing its own exception, outliving
the node that armed it).

Delays are deliberately tiny — long enough that the activation is genuinely
deferred, short enough not to slow the suite.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import time
from pathlib import Path
from typing import Any

import pytest

from nmos.api import create_app
from nmos.node import Node
from nmos.node.config import ConfigBuilder
from nmos.node.types import format_tai

_CONN = "/x-nmos/connection/v1.1/single/senders"

# Long enough for the PATCH to return with the activation still pending.
_DELAY_S = 0.20
# Comfortably past _DELAY_S, but far below the 37 s TAI offset, so a test that
# passes here proves the offset was applied rather than merely tolerated.
_SETTLE_S = 0.60


def _make_node() -> Node:
    node = Node()
    node.init(serial_number="TST00001")
    cfg = json.loads(
        (Path(__file__).parent.parent.parent
         / "node" / "config" / "builtin" / "config10.json").read_text())
    builder = ConfigBuilder(node, verbose=False)
    for sender_cfg in cfg.get("senders", []):
        builder._build_sender_pipeline(sender_cfg)
    for receiver_cfg in cfg.get("receivers", []):
        builder._build_receiver_from_config(receiver_cfg)
    return node


@pytest.fixture
async def client(aiohttp_client):  # type: ignore
    return await aiohttp_client(create_app(_make_node()))


async def _sender_ids(client) -> list[str]:
    sj = await (await client.get("/x-nmos/node/v1.3/senders")).json()
    return [s["id"] for s in sj]


async def _a_sender_id(client) -> str:
    return (await _sender_ids(client))[0]


def _relative(delay_s: float, *, master_enable: bool = True) -> dict[str, Any]:
    sec = int(delay_s)
    nsec = int(round((delay_s - sec) * 1_000_000_000))
    return {
        "master_enable": master_enable,
        "activation": {
            "mode": "activate_scheduled_relative",
            "requested_time": f"{sec}:{nsec}",
        },
    }


def _absolute(target_posix: float, *, master_enable: bool = True) -> dict[str, Any]:
    return {
        "master_enable": master_enable,
        "activation": {
            "mode": "activate_scheduled_absolute",
            "requested_time": format_tai(target_posix),
        },
    }


async def _patch(client, sid: str, body: dict[str, Any]):
    return await client.patch(f"{_CONN}/{sid}/staged", json=body)


async def _staged(client, sid: str) -> dict[str, Any]:
    return await (await client.get(f"{_CONN}/{sid}/staged/")).json()


async def _active(client, sid: str) -> dict[str, Any]:
    return await (await client.get(f"{_CONN}/{sid}/active/")).json()


def _pending(client) -> dict[str, Any]:
    return client.app["node"].dg_pending_activation


# ---------------------------------------------------------------------------
# Arming: what the 202 says, and that nothing has happened yet
# ---------------------------------------------------------------------------

class TestScheduledRelativeAccepted:

    @pytest.mark.asyncio
    async def test_returns_202(self, client) -> None:
        sid = await _a_sender_id(client)
        resp = await _patch(client, sid, _relative(_DELAY_S))
        assert resp.status == 202, await resp.text()

    @pytest.mark.asyncio
    async def test_202_body_describes_the_pending_activation(self, client) -> None:
        sid = await _a_sender_id(client)
        before = time.time()
        resp = await _patch(client, sid, _relative(_DELAY_S))
        body = await resp.json()

        act = body["activation"]
        assert act["mode"] == "activate_scheduled_relative"

        # activation_time is WHEN IT WILL HAPPEN, in TAI — not the time of this
        # request. It must therefore be at least the delay into the future.
        fire_tai = float(act["activation_time"].split(":")[0])
        assert fire_tai >= float(format_tai(before + _DELAY_S).split(":")[0])

        # requested_time echoes the offset the client asked for, as a duration,
        # so the TAI epoch offset must NOT appear in it.
        assert act["requested_time"].startswith("0:")

    @pytest.mark.asyncio
    async def test_sender_is_not_active_yet(self, client) -> None:
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))
        assert (await _active(client, sid))["master_enable"] is False

    @pytest.mark.asyncio
    async def test_timer_is_registered_against_the_resource(self, client) -> None:
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))
        assert len(_pending(client)) == 1


# ---------------------------------------------------------------------------
# Firing: the activation actually happens
# ---------------------------------------------------------------------------

class TestScheduledActivationFires:

    @pytest.mark.asyncio
    async def test_relative_activates_after_the_delay(self, client) -> None:
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))
        await asyncio.sleep(_SETTLE_S)

        assert (await _active(client, sid))["master_enable"] is True

    @pytest.mark.asyncio
    async def test_absolute_activates_at_the_target(self, client) -> None:
        """Also the regression test for the TAI offset.

        The target is given in TAI. Reading it as a POSIX instant would put the
        deadline 37 s further out, so the activation would still be pending when
        this assertion runs.
        """
        sid = await _a_sender_id(client)
        resp = await _patch(client, sid, _absolute(time.time() + _DELAY_S))
        assert resp.status == 202, await resp.text()

        await asyncio.sleep(_SETTLE_S)
        assert (await _active(client, sid))["master_enable"] is True

    @pytest.mark.asyncio
    async def test_absolute_requested_time_is_echoed_to_the_nanosecond(
        self, client,
    ) -> None:
        """requested_time is what the client asked for, byte for byte.

        A client may compare the echo against what it sent. Rebuilding the value
        from the parsed target silently rounds it: a float cannot hold nanosecond
        precision at present-day epoch values, and datetime resolves only to
        microseconds. Together those shifted the echo by a few hundred
        nanoseconds — enough to fail a conformance check.
        """
        sid = await _a_sender_id(client)
        # Nanosecond digits that survive no lossy round-trip.
        sent = f"{int(time.time()) + 37 + 30}:270357608"

        resp = await _patch(client, sid, {
            "master_enable": True,
            "activation": {
                "mode": "activate_scheduled_absolute",
                "requested_time": sent,
            },
        })
        assert resp.status == 202, await resp.text()

        act = (await resp.json())["activation"]
        assert act["requested_time"] == sent
        # For an absolute activation the requested instant IS the activation
        # instant, so this must be exact too.
        assert act["activation_time"] == sent

    @pytest.mark.asyncio
    async def test_relative_requested_time_is_echoed_exactly(self, client) -> None:
        sid = await _a_sender_id(client)
        resp = await _patch(client, sid, {
            "master_enable": True,
            "activation": {
                "mode": "activate_scheduled_relative",
                "requested_time": "0:123456789",
            },
        })
        assert resp.status == 202, await resp.text()
        assert (await resp.json())["activation"]["requested_time"] == "0:123456789"

    @pytest.mark.asyncio
    async def test_staged_stops_advertising_it_once_fired(self, client) -> None:
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))
        await asyncio.sleep(_SETTLE_S)

        act = (await _staged(client, sid))["activation"]
        assert act["mode"] is None
        assert act["requested_time"] is None
        assert act["activation_time"] is None

    @pytest.mark.asyncio
    async def test_active_reports_which_activation_produced_the_state(
        self, client,
    ) -> None:
        """A controller must be able to see that its scheduled activation ran."""
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))
        await asyncio.sleep(_SETTLE_S)

        act = (await _active(client, sid))["activation"]
        assert act["mode"] == "activate_scheduled_relative"
        assert act["activation_time"] is not None

    @pytest.mark.asyncio
    async def test_timer_deregisters_itself(self, client) -> None:
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))
        await asyncio.sleep(_SETTLE_S)

        assert _pending(client) == {}

    @pytest.mark.asyncio
    async def test_registry_snapshot_is_refreshed(self, client) -> None:
        """No HTTP response carries this activation, so the timer must publish.

        Without it the registry would keep serving the pre-activation snapshot
        until some unrelated request happened to publish.
        """
        sid = await _a_sender_id(client)
        node = client.app["node"]
        await _patch(client, sid, _relative(_DELAY_S))

        published: list[bool] = []
        original_publish = node.publish
        node.publish = lambda: (published.append(True), original_publish())[1]

        await asyncio.sleep(_SETTLE_S)
        assert published, "timer did not publish after activating"


# ---------------------------------------------------------------------------
# A target already in the past activates immediately
# ---------------------------------------------------------------------------

class TestPastDueTarget:

    @pytest.mark.asyncio
    async def test_relative_zero_delay_is_200_not_202(self, client) -> None:
        sid = await _a_sender_id(client)
        resp = await _patch(client, sid, _relative(0.0))
        assert resp.status == 200, await resp.text()

    @pytest.mark.asyncio
    async def test_absolute_in_the_past_is_200_not_202(self, client) -> None:
        sid = await _a_sender_id(client)
        resp = await _patch(client, sid, _absolute(time.time() - 10.0))
        assert resp.status == 200, await resp.text()

    @pytest.mark.asyncio
    async def test_activates_without_a_timer(self, client) -> None:
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(0.0))

        assert (await _active(client, sid))["master_enable"] is True
        assert _pending(client) == {}

    @pytest.mark.asyncio
    async def test_response_reports_no_timing(self, client) -> None:
        """Past-due is reported as a completed activation, not a scheduled one."""
        sid = await _a_sender_id(client)
        resp = await _patch(client, sid, _relative(0.0))
        act = (await resp.json())["activation"]

        assert act["mode"] is None
        assert act["requested_time"] is None
        assert act["activation_time"] is None


# ---------------------------------------------------------------------------
# Withdrawing a scheduled activation
# ---------------------------------------------------------------------------

class TestCancellation:

    @pytest.mark.asyncio
    async def test_null_mode_disarms_the_timer(self, client) -> None:
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))

        resp = await _patch(client, sid, {"activation": {"mode": None}})
        assert resp.status == 200, await resp.text()
        assert _pending(client) == {}

        await asyncio.sleep(_SETTLE_S)
        assert (await _active(client, sid))["master_enable"] is False

    @pytest.mark.asyncio
    async def test_cancel_clears_the_mode(self, client) -> None:
        """Withdrawing an activation is expressed by a null mode.

        A PATCH is a merge, so the requested_time the client set earlier is
        still there afterwards — it was not part of this request. It is inert:
        with no mode there is no activation to carry out, and any later
        scheduled PATCH supplies its own requested_time.
        """
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))
        await _patch(client, sid, {"activation": {"mode": None}})

        assert (await _staged(client, sid))["activation"]["mode"] is None


# ---------------------------------------------------------------------------
# 423: a pending activation cannot be displaced
# ---------------------------------------------------------------------------

class TestPendingActivationIsLocked:

    @pytest.mark.asyncio
    async def test_second_scheduled_patch_is_423(self, client) -> None:
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))

        resp = await _patch(client, sid, _relative(_DELAY_S))
        assert resp.status == 423, await resp.text()

    @pytest.mark.asyncio
    async def test_immediate_patch_while_pending_is_423(self, client) -> None:
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))

        resp = await _patch(client, sid, {
            "master_enable": True,
            "activation": {"mode": "activate_immediate"},
        })
        assert resp.status == 423, await resp.text()

    @pytest.mark.asyncio
    async def test_refused_patch_leaves_the_timer_alone(self, client) -> None:
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))
        await _patch(client, sid, _relative(_DELAY_S))       # refused, 423

        await asyncio.sleep(_SETTLE_S)
        assert (await _active(client, sid))["master_enable"] is True

    @pytest.mark.asyncio
    async def test_lock_lifts_once_the_activation_has_fired(self, client) -> None:
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))
        await asyncio.sleep(_SETTLE_S)

        resp = await _patch(client, sid, _relative(_DELAY_S))
        assert resp.status == 202, await resp.text()

    @pytest.mark.asyncio
    async def test_params_patch_while_pending_is_200(self, client) -> None:
        """The lock is on setting an activation, not on staging parameters.

        200, not 202: IS-05 reserves 202 for a request that *schedules* an
        activation, and this request schedules nothing. It reports a null
        activation_time for the same reason, while still showing the pending
        mode, because /staged does genuinely still have one pending.
        """
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))

        resp = await _patch(client, sid, {
            "transport_params": [{"destination_ip": "239.9.9.9"}]})
        assert resp.status == 200, await resp.text()

        act = (await resp.json())["activation"]
        assert act["activation_time"] is None
        assert act["mode"] == "activate_scheduled_relative"
        assert len(_pending(client)) == 1

    @pytest.mark.asyncio
    async def test_params_patch_does_not_move_the_deadline(self, client) -> None:
        """The deadline is anchored to the request that scheduled it.

        IS-05 defines the relative mode as firing when the clock reaches "time
        of message receipt + requested_time" — the message being the one that
        requested the activation. Re-measuring the delay from a later parameter
        PATCH would push the activation past the moment the client was promised.
        """
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(0.6))

        await asyncio.sleep(0.3)                   # half-way through the wait
        await _patch(client, sid, {
            "transport_params": [{"destination_ip": "239.9.9.9"}]})

        # Fires on the original schedule (0.6s). Had the countdown restarted from
        # the second request it would not fire until 0.9s, so it would still be
        # pending at this point.
        await asyncio.sleep(0.45)                  # t = 0.75s
        assert (await _active(client, sid))["master_enable"] is True

    @pytest.mark.asyncio
    async def test_params_patch_still_changes_what_is_applied(self, client) -> None:
        """Parameters are read when the activation fires, not when it is armed.

        So staging a parameter alongside a pending activation does change the
        configuration that goes live — it just cannot change when.
        """
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))
        await _patch(client, sid, {
            "transport_params": [{"destination_ip": "239.9.9.9"}]})

        await asyncio.sleep(_SETTLE_S)
        active = await _active(client, sid)
        assert active["master_enable"] is True
        assert active["transport_params"][0]["destination_ip"] == "239.9.9.9"

    @pytest.mark.asyncio
    async def test_master_enable_is_fixed_when_the_activation_is_scheduled(
        self, client,
    ) -> None:
        """A scheduled activation does what it was asked to do when scheduled.

        Deliberate design choice, not something IS-05 settles: the on/off intent
        is captured at the scheduling request, so a controller staging a salvo
        knows what each device will do at the appointed time. The consequence is
        that a later master_enable PATCH carrying no activation mode does not
        reach the pending activation — /staged reports the new value while the
        activation still applies the captured one.
        """
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))          # master_enable true

        resp = await _patch(client, sid, {"master_enable": False})
        assert resp.status == 200, await resp.text()
        assert (await _staged(client, sid))["master_enable"] is False

        await asyncio.sleep(_SETTLE_S)
        assert (await _active(client, sid))["master_enable"] is True


# ---------------------------------------------------------------------------
# Timers are per resource
# ---------------------------------------------------------------------------

class TestTimersAreIndependent:

    @pytest.mark.asyncio
    async def test_two_senders_both_fire(self, client) -> None:
        ids = await _sender_ids(client)
        first, second = ids[0], ids[1]

        await _patch(client, first, _relative(_DELAY_S))
        await _patch(client, second, _relative(_DELAY_S))
        assert len(_pending(client)) == 2

        await asyncio.sleep(_SETTLE_S)
        assert (await _active(client, first))["master_enable"] is True
        assert (await _active(client, second))["master_enable"] is True

    @pytest.mark.asyncio
    async def test_cancelling_one_leaves_the_other_armed(self, client) -> None:
        ids = await _sender_ids(client)
        first, second = ids[0], ids[1]

        await _patch(client, first, _relative(_DELAY_S))
        await _patch(client, second, _relative(_DELAY_S))

        await _patch(client, first, {"activation": {"mode": None}})
        await asyncio.sleep(_SETTLE_S)

        assert (await _active(client, first))["master_enable"] is False
        assert (await _active(client, second))["master_enable"] is True


# ---------------------------------------------------------------------------
# Receivers schedule the same way senders do
# ---------------------------------------------------------------------------

class TestReceiverScheduledActivation:
    """The activation pipeline is shared, so receivers must behave identically."""

    _RECV = "/x-nmos/connection/v1.1/single/receivers"

    async def _receiver_id(self, client) -> str:
        rj = await (await client.get("/x-nmos/node/v1.3/receivers")).json()
        return rj[0]["id"]

    @pytest.mark.asyncio
    async def test_relative_is_202_then_activates(self, client) -> None:
        rid = await self._receiver_id(client)

        resp = await client.patch(f"{self._RECV}/{rid}/staged",
                                  json=_relative(_DELAY_S))
        assert resp.status == 202, await resp.text()
        assert len(_pending(client)) == 1

        await asyncio.sleep(_SETTLE_S)
        active = await (await client.get(f"{self._RECV}/{rid}/active/")).json()
        assert active["master_enable"] is True
        assert _pending(client) == {}

    @pytest.mark.asyncio
    async def test_second_scheduled_patch_is_423(self, client) -> None:
        rid = await self._receiver_id(client)
        await client.patch(f"{self._RECV}/{rid}/staged", json=_relative(_DELAY_S))

        resp = await client.patch(f"{self._RECV}/{rid}/staged",
                                  json=_relative(_DELAY_S))
        assert resp.status == 423, await resp.text()

    @pytest.mark.asyncio
    async def test_null_mode_disarms_the_timer(self, client) -> None:
        rid = await self._receiver_id(client)
        await client.patch(f"{self._RECV}/{rid}/staged", json=_relative(_DELAY_S))

        await client.patch(f"{self._RECV}/{rid}/staged",
                           json={"activation": {"mode": None}})
        assert _pending(client) == {}

        await asyncio.sleep(_SETTLE_S)
        active = await (await client.get(f"{self._RECV}/{rid}/active/")).json()
        assert active["master_enable"] is False


# ---------------------------------------------------------------------------
# The ways a background timer can go wrong
# ---------------------------------------------------------------------------

class TestTimerRobustness:

    @pytest.mark.asyncio
    async def test_timer_survives_garbage_collection(self, client) -> None:
        """The event loop holds only a weak reference to a running task.

        If nothing keeps a reference to the timer, it can be collected part-way
        through its wait and the activation would simply never happen.
        """
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))

        gc.collect()

        await asyncio.sleep(_SETTLE_S)
        assert (await _active(client, sid))["master_enable"] is True

    @pytest.mark.asyncio
    async def test_failure_at_fire_time_is_logged(
        self, client, monkeypatch, caplog,
    ) -> None:
        """A background task has nobody to return an error to."""
        import nmos.node.activation_engine as ae

        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))

        def exploding(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(ae, "do_activation", exploding)

        with caplog.at_level(logging.ERROR):
            await asyncio.sleep(_SETTLE_S)

        assert any("scheduled activation failed" in r.message
                   for r in caplog.records), caplog.text
        # and it must not leave a dead entry behind
        assert _pending(client) == {}

    @pytest.mark.asyncio
    async def test_shutdown_disarms_pending_timers(self, client) -> None:
        """A timer must not fire into a node that is being torn down."""
        from nmos.node.activation_engine import cancel_pending_activations

        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))

        cancel_pending_activations(client.app["node"])
        assert _pending(client) == {}

        await asyncio.sleep(_SETTLE_S)
        assert (await _active(client, sid))["master_enable"] is False

    @pytest.mark.asyncio
    async def test_firing_does_not_interleave_with_a_patch(self, client) -> None:
        """A firing timer and a request must not both be half-applied.

        The timer's activation and the handler's mutate-and-respond are each
        free of awaits, so the loop cannot switch between them. Whichever runs
        first runs to completion, and the state left behind is coherent either
        way.
        """
        sid = await _a_sender_id(client)
        await _patch(client, sid, _relative(_DELAY_S))

        # Fire the timer and hit the same resource in the same breath.
        patch_task = asyncio.ensure_future(
            _patch(client, sid, {
                "transport_params": [{"destination_ip": "239.8.8.8"}]}))
        await asyncio.sleep(_SETTLE_S)
        resp = await patch_task

        assert resp.status in (200, 202, 423), await resp.text()

        active = await _active(client, sid)
        assert active["master_enable"] is True
        # The activation completed fully: params were flipped, not left half-way.
        assert active["transport_params"][0]["destination_ip"] is not None
        assert _pending(client) == {}
