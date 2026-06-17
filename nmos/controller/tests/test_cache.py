# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for nmos.controller.cache."""

from __future__ import annotations

from typing import Any

import pytest

from nmos.controller.cache import (
    ResourceCache,
    StatusChanged,
    StatusEventStream,
    decode_status_code,
    extract_monitor_state,
    extract_status,
)
from nmos.controller.grouping import GROUP_HINT_TAG


def _sender(
    sid: str, device_id: str = "d1", label: str = "s",
    hint: str | None = None, active: bool = False,
    receiver_id: str | None = None,
) -> dict[str, Any]:
    r: dict[str, Any] = {
        "id": sid,
        "device_id": device_id,
        "label": label,
        "description": "",
        "tags": {},
        "subscription": {"active": active, "receiver_id": receiver_id},
    }
    if hint is not None:
        r["tags"][GROUP_HINT_TAG] = [hint]
    return r


def _device(did: str, label: str = "dev", serial: str = "SNX00001") -> dict[str, Any]:
    return {
        "id": did,
        "label": label,
        "description": f"device {serial}",
        "controls": [],
    }


class TestExtractStatus:
    def test_sender_status_active_no_monitor_is_grey(self) -> None:
        # No BCP-008 monitor: ``active`` still reflects the subscription,
        # but every facet (and overall) is grey ``not-used`` — we never
        # synthesize ``healthy`` from the connection state.
        st = extract_status(
            "sender",
            {"subscription": {"active": True, "receiver_id": "r1"}},
        )
        assert st["active"] is True
        assert st["peer_id"] == "r1"
        assert st["monitored"] is False
        assert st["overall"] == "not-used"
        for facet in ("link", "sync", "conn", "media"):
            assert st[facet] == "not-used"

    def test_receiver_status_inactive_no_monitor_is_grey(self) -> None:
        st = extract_status(
            "receiver",
            {"subscription": {"active": False, "sender_id": None}},
        )
        assert st["active"] is False
        assert st["peer_id"] is None
        assert st["monitored"] is False
        assert st["overall"] == "not-used"
        for facet in ("link", "sync", "conn", "media"):
            assert st[facet] == "not-used"

    def test_other_kinds_empty(self) -> None:
        assert extract_status("device", {}) == {}


class TestDecodeStatusCode:
    """NMOS With Status Reporting §93: status integers 0..3 mapping."""

    def test_zero_is_inactive_for_most_facets(self) -> None:
        for facet in ("overall", "link", "conn", "media"):
            assert decode_status_code(0, facet) == "inactive"  # type: ignore[arg-type]

    def test_zero_is_not_used_for_sync(self) -> None:
        assert decode_status_code(0, "sync") == "not-used"

    def test_healthy_partially_unhealthy(self) -> None:
        assert decode_status_code(1, "overall") == "healthy"
        assert decode_status_code(2, "overall") == "partially-healthy"
        assert decode_status_code(3, "overall") == "unhealthy"

    def test_unknown_int_falls_back_to_facet_zero(self) -> None:
        assert decode_status_code(99, "link") == "inactive"
        assert decode_status_code(99, "sync") == "not-used"

    def test_non_integer_falls_back(self) -> None:
        assert decode_status_code(None, "link") == "inactive"
        assert decode_status_code("healthy", "link") == "inactive"  # string ignored
        assert decode_status_code("1", "link") == "healthy"          # str → int ok

    def test_float_accepted_as_int(self) -> None:
        assert decode_status_code(1.0, "overall") == "healthy"


class TestExtractMonitorState:
    """Project a monitoring Source resource into the UI status dict."""

    def _receiver_source(self, **state: Any) -> dict[str, Any]:
        return {
            "id": "00000000-0500-4003-ab00-4d5458005179",
            "format": "urn:x-nmos:format:data",
            "monitor_type": "receiver",
            "monitor_sibling_id": "00000000-0300-4000-ab00-4d5458005179",
            "monitor_state": {
                "overall_status": state.get("overall_status", 1),
                "link_status": state.get("link_status", 1),
                "synchronization_status": state.get("synchronization_status", 1),
                "connection_status": state.get("connection_status", 1),
                "stream_status": state.get("stream_status", 1),
            },
        }

    def _sender_source(self, **state: Any) -> dict[str, Any]:
        return {
            "id": "00000000-0500-4003-ab00-4d5458005100",
            "format": "urn:x-nmos:format:data",
            "monitor_type": "sender",
            "monitor_sibling_id": "00000000-0300-4000-ab00-4d5458005100",
            "monitor_state": {
                "overall_status": state.get("overall_status", 1),
                "link_status": state.get("link_status", 1),
                "synchronization_status": state.get("synchronization_status", 1),
                "transmission_status": state.get("transmission_status", 1),
                "essence_status": state.get("essence_status", 1),
            },
        }

    def test_receiver_all_healthy(self) -> None:
        out = extract_monitor_state(self._receiver_source())
        assert out is not None
        assert out["overall"] == "healthy"
        assert out["link"] == "healthy"
        assert out["sync"] == "healthy"
        assert out["conn"] == "healthy"
        assert out["media"] == "healthy"

    def test_receiver_facet_attribute_mapping(self) -> None:
        # conn ← connection_status (not transmission_status)
        # media ← stream_status (not essence_status)
        src = self._receiver_source(connection_status=2, stream_status=3)
        out = extract_monitor_state(src)
        assert out is not None
        assert out["conn"] == "partially-healthy"
        assert out["media"] == "unhealthy"

    def test_sender_facet_attribute_mapping(self) -> None:
        # conn ← transmission_status (not connection_status)
        # media ← essence_status (not stream_status)
        src = self._sender_source(transmission_status=2, essence_status=3)
        out = extract_monitor_state(src)
        assert out is not None
        assert out["conn"] == "partially-healthy"
        assert out["media"] == "unhealthy"

    def test_sync_zero_is_not_used(self) -> None:
        src = self._receiver_source(synchronization_status=0)
        out = extract_monitor_state(src)
        assert out is not None
        assert out["sync"] == "not-used"

    def test_rejects_non_monitor_source(self) -> None:
        # Wrong format.
        assert extract_monitor_state({
            "format": "urn:x-nmos:format:video",
            "monitor_type": "receiver",
            "monitor_state": {},
        }) is None
        # Missing monitor_type.
        assert extract_monitor_state({
            "format": "urn:x-nmos:format:data",
            "monitor_state": {},
        }) is None
        # monitor_type not sender/receiver.
        assert extract_monitor_state({
            "format": "urn:x-nmos:format:data",
            "monitor_type": "flow",
            "monitor_state": {},
        }) is None
        # Not a dict.
        assert extract_monitor_state(None) is None  # type: ignore[arg-type]

    def test_overall_message_preserved(self) -> None:
        # The serialized monitor_state attribute is ``overall_status_message``
        # (NMonitorState.MonitorOverallStatusMessage); the UI exposes it under
        # ``overall_message``.
        src = self._receiver_source()
        src["monitor_state"]["overall_status_message"] = "stream locked"
        out = extract_monitor_state(src)
        assert out is not None
        assert out["overall_message"] == "stream locked"


class TestResourceCacheUpsert:
    @pytest.mark.asyncio
    async def test_upsert_and_get(self) -> None:
        cache = ResourceCache()
        await cache.upsert("device", _device("d1"))
        await cache.upsert("sender", _sender("s1"))
        assert cache.get_sender("s1") is not None
        assert cache.get_device("d1") is not None

    @pytest.mark.asyncio
    async def test_status_change_fires_event(self) -> None:
        cache = ResourceCache()
        events: list[StatusChanged] = []
        cache.add_status_listener(events.append)

        await cache.upsert("sender", _sender("s1", active=False))
        await cache.upsert("sender", _sender("s1", active=True))
        await cache.upsert("sender", _sender("s1", active=True))  # no change

        assert len(events) == 2
        assert events[0].resource_id == "s1"
        assert events[0].status["active"] is False
        assert events[1].status["active"] is True

    @pytest.mark.asyncio
    async def test_replace_all(self) -> None:
        cache = ResourceCache()
        await cache.upsert("sender", _sender("s1", active=True))
        await cache.replace_all("sender", [_sender("s2", active=False)])
        assert cache.get_sender("s1") is None
        assert cache.get_sender("s2") is not None


class TestMonitorLinkage:
    """BCP-008 "NMOS With Status Reporting" — the cache must promote
    per-facet values from a sibling monitor Source into the
    sender/receiver's derived status."""

    @staticmethod
    def _monitor(
        mid: str, kind: str, sibling_id: str, link: int = 1,
        sync: int = 1, conn: int = 1, media: int = 1, overall: int = 1,
    ) -> dict[str, Any]:
        state: dict[str, Any] = {
            "overall_status": overall,
            "link_status":    link,
            "synchronization_status": sync,
        }
        if kind == "sender":
            state["transmission_status"] = conn
            state["essence_status"] = media
        else:
            state["connection_status"] = conn
            state["stream_status"] = media
        return {
            "id":                 mid,
            "device_id":          "d1",
            "format":             "urn:x-nmos:format:data",
            "monitor_type":       kind,
            "monitor_sibling_id": sibling_id,
            "monitor_state":      state,
        }

    @pytest.mark.asyncio
    async def test_sender_status_uses_monitor_when_present(self) -> None:
        cache = ResourceCache()
        await cache.upsert("sender", _sender("s1", active=True))
        await cache.upsert("source", self._monitor(
            "m1", "sender", "s1", link=1, sync=1, conn=2, media=3, overall=2,
        ))
        st = cache.get_status("s1")
        assert st["overall"] == "partially-healthy"
        assert st["link"]    == "healthy"
        assert st["sync"]    == "healthy"
        assert st["conn"]    == "partially-healthy"
        assert st["media"]   == "unhealthy"

    @pytest.mark.asyncio
    async def test_monitor_upsert_fires_sibling_status_event(self) -> None:
        """A monitor Source arriving AFTER its sender is what drives
        the SSE update to the listing page."""
        cache = ResourceCache()
        events: list[StatusChanged] = []
        cache.add_status_listener(events.append)

        await cache.upsert("sender", _sender("s1", active=True))
        events.clear()  # drop the sender's own upsert event
        await cache.upsert("source", self._monitor(
            "m1", "sender", "s1", conn=3,
        ))
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "sender"
        assert ev.resource_id == "s1"
        assert ev.status["conn"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_sender_upsert_picks_up_existing_monitor(self) -> None:
        """If the monitor Source is cached first (RDS may deliver
        Sources before their siblings), a subsequent sender upsert
        must still read the monitor values."""
        cache = ResourceCache()
        await cache.upsert("source", self._monitor(
            "m1", "sender", "s1", media=2,
        ))
        await cache.upsert("sender", _sender("s1", active=True))
        st = cache.get_status("s1")
        assert st["media"] == "partially-healthy"

    @pytest.mark.asyncio
    async def test_monitor_removal_falls_back_to_grey(self) -> None:
        """Removing the monitor Source drops the sibling's per-facet
        values back to grey ``not-used`` — no monitor means no telemetry,
        so we do NOT synthesize health from ``subscription.active``."""
        cache = ResourceCache()
        await cache.upsert("sender", _sender("s1", active=True))
        await cache.upsert("source", self._monitor(
            "m1", "sender", "s1", media=3,
        ))
        assert cache.get_status("s1")["media"] == "unhealthy"
        assert cache.get_status("s1")["monitored"] is True
        await cache.remove("source", "m1")
        st = cache.get_status("s1")
        # No monitor → grey, even though subscription.active is True.
        assert st["monitored"] is False
        assert st["active"] is True
        assert st["media"] == "not-used"
        assert st["overall"] == "not-used"

    @pytest.mark.asyncio
    async def test_receiver_uses_receiver_specific_attributes(self) -> None:
        """Receiver facets use ``connection_status`` / ``stream_status``
        (not sender's ``transmission_status`` / ``essence_status``).
        """
        cache = ResourceCache()
        await cache.upsert("receiver", {
            "id": "r1", "device_id": "d1", "label": "rx",
            "subscription": {"active": True, "sender_id": None},
        })
        await cache.upsert("source", self._monitor(
            "m1", "receiver", "r1", conn=2, media=3,
        ))
        st = cache.get_status("r1")
        assert st["conn"]  == "partially-healthy"
        assert st["media"] == "unhealthy"


class TestGroupedViews:
    @pytest.mark.asyncio
    async def test_sender_grouping_by_device_and_hint(self) -> None:
        # Per "NMOS With Natural Groups" §"Senders": the group identity
        # is (<group-name>, <group-index>). A single natural group
        # contains senders of multiple formats — AUDIO + VIDEO here all
        # live in the "RTP 0" group.
        cache = ResourceCache()
        await cache.upsert("device", _device("d1", label="A", serial="SNX00001"))
        await cache.upsert("device", _device("d2", label="B", serial="SNX00002"))
        await cache.upsert("sender", _sender("s1", device_id="d1", hint="RTP 0:VIDEO 0"))
        await cache.upsert("sender", _sender("s2", device_id="d1", hint="RTP 0:VIDEO 1"))
        await cache.upsert("sender", _sender("s3", device_id="d1", hint="RTP 0:AUDIO 0"))
        await cache.upsert("sender", _sender("s4", device_id="d2", hint="RTP 0:VIDEO 0"))

        views = cache.senders_grouped()
        assert len(views) == 2
        v1 = next(v for v in views if v.device_id == "d1")
        assert v1.device_serial == "SNX00001"
        # Only ONE natural group per device (RTP 0) — AUDIO + VIDEO
        # members coexist inside it.
        assert len(v1.groups) == 1
        g = v1.groups[0]
        assert g.hint_key == "RTP 0"
        assert g.display_name == "RTP 0"
        # Members sorted by (format, role, id): AUDIO 0, VIDEO 0, VIDEO 1.
        assert [(m.hint.format, m.hint.role) for m in g.members if m.hint] == [
            ("AUDIO", 0),
            ("VIDEO", 0),
            ("VIDEO", 1),
        ]

    @pytest.mark.asyncio
    async def test_ungrouped_resources_bucket(self) -> None:
        cache = ResourceCache()
        await cache.upsert("device", _device("d1"))
        await cache.upsert("sender", _sender("no-hint", device_id="d1", hint=None))
        views = cache.senders_grouped()
        assert len(views) == 1
        assert len(views[0].ungrouped) == 1

    @pytest.mark.asyncio
    async def test_non_groupable_hint_goes_to_ungrouped(self) -> None:
        # A hint whose role token isn't a recognised format (third-party
        # device) can't be grouped — it lands in ``ungrouped`` and keeps its
        # raw role text for display, NOT in a natural group.
        cache = ResourceCache()
        await cache.upsert("device", _device("d1"))
        await cache.upsert(
            "sender", _sender("odd", device_id="d1", hint="RTP 0:THERMAL 1"),
        )
        views = cache.senders_grouped()
        assert len(views) == 1
        assert views[0].groups == []
        assert len(views[0].ungrouped) == 1
        m = views[0].ungrouped[0]
        assert m.hint is not None and m.hint.groupable is False
        assert m.hint.role_label == "THERMAL 1"

    @pytest.mark.asyncio
    async def test_device_address_and_transports(self) -> None:
        # Transport labels come from each resource's IS-04 ``transport``
        # attribute (with the urn:x-nmos:transport: prefix stripped),
        # rolled up to a distinct sorted set per device.
        cache = ResourceCache()
        dev: dict[str, Any] = {
            "id": "d1",
            "label": "Dev",
            "description": "SNX00001 lab",
            "controls": [
                {
                    "type": "urn:x-nmos:control:sr-ctrl/v1.1",
                    "href": "http://10.0.0.5:5060/x-nmos/connection/v1.1/",
                },
            ],
        }
        await cache.upsert("device", dev)
        s1 = _sender("s1", device_id="d1", hint="RTP 0:VIDEO 0")
        s1["transport"] = "urn:x-nmos:transport:rtp.mcast"
        s2 = _sender("s2", device_id="d1", hint="SRT 0:MUX 0")
        s2["transport"] = "urn:x-nmos:transport:srt"
        await cache.upsert("sender", s1)
        await cache.upsert("sender", s2)
        views = cache.senders_grouped()
        assert len(views) == 1
        v = views[0]
        assert v.device_address == "10.0.0.5:5060"
        assert v.transports == ["rtp.mcast", "srt"]


class TestSenderFormatFromFlow:
    """The Python node doesn't publish a top-level ``format`` on sender
    resources (it's inherited from the flow per IS-04). ``_build_grouped``
    must resolve it via the ``flow_id`` lookup before downstream compat
    checks — otherwise fail-closed format comparison drops every
    sender with a null ``format`` and the controller shows "No senders
    are compatible" even for obvious matches.
    """

    @pytest.mark.asyncio
    async def test_null_sender_format_resolved_from_flow(self) -> None:
        cache = ResourceCache()
        await cache.upsert("device", {
            "id": "d1", "label": "dev", "description": "SNX00001",
            "controls": [],
        })
        await cache.upsert("flow", {
            "id":     "flow-audio-0",
            "format": "urn:x-nmos:format:audio",
        })
        # Sender published WITHOUT a top-level ``format`` — matches
        # what a live Python node produces.
        await cache.upsert("sender", {
            "id":        "sender-audio-0",
            "device_id": "d1",
            "label":     "Net Stream Audio 0",
            "flow_id":   "flow-audio-0",
            "transport": "urn:x-nmos:transport:rtp.mcast",
            "tags":      {"urn:x-nmos:tag:grouphint/v1.0": ["RTP 0:AUDIO 0"]},
            "subscription": {"active": False, "receiver_id": None},
        })
        devs = cache.senders_grouped()
        assert len(devs) == 1
        member = devs[0].groups[0].members[0]
        # GroupedResource.resource carries the resolved format so
        # downstream format-compatible checks succeed.
        assert member.resource.get("format") == "urn:x-nmos:format:audio"

    @pytest.mark.asyncio
    async def test_missing_flow_leaves_format_null(self) -> None:
        """If the sender's ``flow_id`` has no corresponding flow in
        cache (pre-registration race / dangling ref), format stays
        unset — callers see ``None`` rather than a silently wrong URN.
        """
        cache = ResourceCache()
        await cache.upsert("device", {
            "id": "d1", "label": "dev", "description": "SNX00001",
            "controls": [],
        })
        await cache.upsert("sender", {
            "id":        "sender-orphan",
            "device_id": "d1",
            "label":     "Orphan",
            "flow_id":   "does-not-exist",
            "transport": "urn:x-nmos:transport:rtp.mcast",
            "tags":      {"urn:x-nmos:tag:grouphint/v1.0": ["RTP 0:AUDIO 0"]},
            "subscription": {"active": False, "receiver_id": None},
        })
        devs = cache.senders_grouped()
        member = devs[0].groups[0].members[0]
        assert member.resource.get("format") in (None, "")

    @pytest.mark.asyncio
    async def test_get_sender_resolves_null_format_from_flow(self) -> None:
        """``get_sender`` (not just ``_build_grouped``) must resolve
        a null sender format via the referenced flow — the caps /
        configure handlers call ``get_sender`` directly and format-
        compat checks there would otherwise reject a real sender as
        if its format were missing.
        """
        cache = ResourceCache()
        await cache.upsert("device", {
            "id": "d1", "label": "dev", "description": "SNX00001",
            "controls": [],
        })
        await cache.upsert("flow", {
            "id":     "flow-audio-0",
            "format": "urn:x-nmos:format:audio",
        })
        await cache.upsert("sender", {
            "id":        "sender-audio-0",
            "device_id": "d1",
            "label":     "Net Stream Audio 0",
            "flow_id":   "flow-audio-0",
            "transport": "urn:x-nmos:transport:rtp.mcast",
            "tags":      {"urn:x-nmos:tag:grouphint/v1.0": ["RTP 0:AUDIO 0"]},
            "subscription": {"active": False, "receiver_id": None},
        })
        s = cache.get_sender("sender-audio-0")
        assert s is not None
        assert s.get("format") == "urn:x-nmos:format:audio"

    @pytest.mark.asyncio
    async def test_all_senders_resolves_null_format_from_flow(self) -> None:
        """``all_senders`` (used by the single-mode compat filter via
        ``compatible_senders``) must also resolve null formats —
        otherwise the listing would drop every sender with a
        published ``format: null``.
        """
        cache = ResourceCache()
        await cache.upsert("device", {
            "id": "d1", "label": "dev", "description": "SNX00001",
            "controls": [],
        })
        await cache.upsert("flow", {
            "id":     "flow-video-0",
            "format": "urn:x-nmos:format:video",
        })
        await cache.upsert("sender", {
            "id":        "sender-video-0",
            "device_id": "d1",
            "label":     "Net Stream Video 0",
            "flow_id":   "flow-video-0",
            "transport": "urn:x-nmos:transport:rtp.mcast",
            "tags":      {"urn:x-nmos:tag:grouphint/v1.0": ["RTP 0:VIDEO 0"]},
            "subscription": {"active": False, "receiver_id": None},
        })
        senders = cache.all_senders()
        assert len(senders) == 1
        assert senders[0].get("format") == "urn:x-nmos:format:video"


class TestStatusEventStream:
    @pytest.mark.asyncio
    async def test_filter_includes_only_listed_ids(self) -> None:
        cache = ResourceCache()
        stream = StatusEventStream(cache, filter_ids={"s1"})

        await cache.upsert("sender", _sender("s1", active=False))
        await cache.upsert("sender", _sender("s2", active=False))  # filtered out
        await cache.upsert("sender", _sender("s1", active=True))

        received = []
        async def collect() -> None:
            async for ev in stream.events():
                received.append(ev)
                if len(received) == 2:
                    return

        import asyncio
        await asyncio.wait_for(collect(), timeout=1.0)

        assert [e.resource_id for e in received] == ["s1", "s1"]
        stream.close()

    @pytest.mark.asyncio
    async def test_empty_filter_receives_all(self) -> None:
        cache = ResourceCache()
        stream = StatusEventStream(cache, filter_ids=set())
        await cache.upsert("sender", _sender("s1", active=True))
        await cache.upsert("receiver",
                           {"id": "r1", "device_id": "d1",
                            "subscription": {"active": False, "sender_id": None}})

        received: list[StatusChanged] = []
        async def collect() -> None:
            async for ev in stream.events():
                received.append(ev)
                if len(received) == 2:
                    return

        import asyncio
        await asyncio.wait_for(collect(), timeout=1.0)
        ids = sorted(e.resource_id for e in received)
        assert ids == ["r1", "s1"]
        stream.close()
