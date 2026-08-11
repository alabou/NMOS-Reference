# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NMOS Node event system.

Event types and constants for the NMOS Node event definitions.
Used by the streaming engine, and in future by IS-12 NMOS Events API
and Node monitoring.

Events are emitted to an asyncio.Queue on the Node object. The queue
is non-blocking: if full, events are dropped.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from enum import IntEnum


# ---------------------------------------------------------------------------
# Alert Domains
# ---------------------------------------------------------------------------

class AlertDomain(IntEnum):
    """Alert domain — categorizes the subsystem reporting the event."""
    LINK = 1
    TRANSPORT = 2
    ESSENCE = 3
    APPLICATION = 4
    CLOCK = 5
    VENDOR = 10
    VENDOR_LINK = 11
    VENDOR_TRANSPORT = 12
    VENDOR_ESSENCE = 13
    VENDOR_APPLICATION = 14
    VENDOR_CLOCK = 15


# ---------------------------------------------------------------------------
# Alert Scopes
# ---------------------------------------------------------------------------

class AlertScope(IntEnum):
    """Alert scope — identifies the resource type."""
    DEVICE = 0
    SENDER = 1
    SENDER_VIDEO = 2
    SENDER_AUDIO = 3
    SENDER_DATA = 4
    SENDER_MUX = 5
    RECEIVER = 6
    RECEIVER_VIDEO = 7
    RECEIVER_AUDIO = 8
    RECEIVER_DATA = 9
    RECEIVER_MUX = 10
    INPUT = 11
    OUTPUT = 12


# ---------------------------------------------------------------------------
# Event States
# ---------------------------------------------------------------------------

class EventState(IntEnum):
    """Event state — severity of the event."""
    UNKNOWN = 0
    INACTIVE = 1
    WAITING = 2
    NORMAL = 3
    WARNING = 4
    ERROR = 5
    MALFUNCTION = 6


# ---------------------------------------------------------------------------
# Event IDs
# Base values: domain × 1000
# ---------------------------------------------------------------------------

class EventId(IntEnum):
    """Specific event identifiers."""
    # Base domain events (value = domain × 1000)
    LINK = 1000
    TRANSPORT = 2000
    ESSENCE = 3000
    APPLICATION = 4000
    CLOCK = 5000
    VENDOR = 10000
    VENDOR_LINK = 11000
    VENDOR_TRANSPORT_BASE = 12000   # Base (not activate/deactivate)
    VENDOR_ESSENCE_BASE = 13000     # Base (not start/stop)
    VENDOR_CLOCK = 15000

    # Link events (1000-series)
    LINK_DOWN = 1001
    LINK_OK = 1999

    # Transport events (2000-series)
    TRANSPORT_PACKET_LOST = 2001
    TRANSPORT_PACKET_LATE = 2002
    TRANSPORT_PACKET_RECOVERED = 2003
    TRANSPORT_STREAM_ERROR = 2004
    TRANSPORT_OK = 2999

    # Essence events (3000-series)
    ESSENCE_STREAM_ERROR = 3001
    ESSENCE_OK = 3999

    # Application events (4000-series)
    APPLICATION_OK = 4999

    # Clock events (5000-series)
    CLOCK_SOURCE_CHANGE = 5001
    CLOCK_UNLOCK = 5002
    CLOCK_OK = 5999

    # Vendor events (10000+ series)
    VENDOR_TEMPERATURE = 10001

    # Vendor transport events (12000-series)
    VENDOR_TRANSPORT_ACTIVATE = 12001
    VENDOR_TRANSPORT_DEACTIVATE = 12002

    # Vendor essence events (13000-series)
    VENDOR_ESSENCE_START = 13001
    VENDOR_ESSENCE_STOP = 13002
    # IS-11 compatibility-state → monitor essence-status bridge.
    # ``VENDOR_ESSENCE_CONSTRAINT_VIOLATED`` flips ``essence_status``
    # (sender) / ``stream_status`` (receiver) to ``NC_UNHEALTHY`` when
    # the Node computes ``active_constraints_violation`` (sender) or
    # ``non_compliant_stream`` (receiver). ``_OK`` is the recovery
    # edge. Kept distinct from the plain ``ESSENCE`` / ``ESSENCE_OK``
    # events so the IS-11 state machine cannot stomp an in-flight
    # streaming-engine error and vice-versa — see the comment on
    # ``Node.set_sender_compatibility_state`` for the transition-
    # only emit logic.
    VENDOR_ESSENCE_CONSTRAINT_VIOLATED = 13003
    VENDOR_ESSENCE_CONSTRAINT_OK = 13004
    # Partial/amber edge — the IS-11 sender states ``no_essence`` /
    # ``awaiting_essence`` map to ``NC_PARTIALLY_HEALTHY`` on the essence
    # facet (there is no essence yet, but it isn't a constraint violation).
    VENDOR_ESSENCE_CONSTRAINT_PARTIAL = 13005


# ---------------------------------------------------------------------------
# Engine Event
# ---------------------------------------------------------------------------

@dataclass
class EngineEvent:
    """A single event from the streaming engine or Node subsystem."""
    domain: int    # AlertDomain value
    scope: int     # AlertScope value
    event: int     # EventId value
    state: int     # EventState value
    count: int     # Occurrence count (e.g., number of packets lost)
    id: str        # Resource UUID (sender or receiver)
    name: str      # Interface name or "*"
    info: str      # Human-readable description


# ---------------------------------------------------------------------------
# Event Queue Helper
# ---------------------------------------------------------------------------

def emit_event(queue: asyncio.Queue[EngineEvent] | None, event: EngineEvent) -> None:
    """Emit an event to the queue (non-blocking, drop if full)."""
    if queue is None:
        return
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        pass  # Drop the event when the queue is full


# ---------------------------------------------------------------------------
# Convenience emitters for the streaming engine lifecycle
# ---------------------------------------------------------------------------

def emit_activate(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
) -> None:
    """Emit activation event (1 event)."""
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    role = "sender" if is_sender else "receiver"
    emit_event(queue, EngineEvent(
        domain=AlertDomain.VENDOR_TRANSPORT, scope=scope,
        event=EventId.VENDOR_TRANSPORT_ACTIVATE, state=EventState.NORMAL,
        count=1, id=resource_id, name=interface_name,
        info=f"{role} activate",
    ))


def emit_deactivate(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
) -> None:
    """Emit deactivation event (1 event)."""
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    role = "sender" if is_sender else "receiver"
    emit_event(queue, EngineEvent(
        domain=AlertDomain.VENDOR_TRANSPORT, scope=scope,
        event=EventId.VENDOR_TRANSPORT_DEACTIVATE, state=EventState.INACTIVE,
        count=1, id=resource_id, name=interface_name,
        info=f"{role} deactivate",
    ))


def emit_starting(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
) -> None:
    """Emit starting events (4 events).

    NOTE: the CLOCK domain is intentionally NOT touched here. Stream
    activation does not mean the resource is clock-locked — that would
    falsely report ``synchronization_status = Healthy`` (green) for a
    stream on an internal clock (no PTP). The synchronization facet
    reflects the EFFECTIVE clock and is driven separately, only when the
    clock is actually a locked PTP reference: senders from their source's
    ``clock_name``, receivers from the negotiated SDP ``ts-refclk`` — both
    at the activation handlers. Absent such an event the sync domain stays
    ``NC_INACTIVE`` (NotUsed / grey), which is correct for an internal clock.
    """
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    role = "sender" if is_sender else "receiver"
    msg = f"{role} starting"

    for domain, event_id in [
        (AlertDomain.VENDOR_ESSENCE, EventId.VENDOR_ESSENCE_START),
        (AlertDomain.TRANSPORT, EventId.TRANSPORT_OK),
        (AlertDomain.ESSENCE, EventId.ESSENCE_OK),
        (AlertDomain.LINK, EventId.LINK_OK),
    ]:
        emit_event(queue, EngineEvent(
            domain=domain, scope=scope,
            event=event_id, state=EventState.NORMAL,
            count=1, id=resource_id, name=interface_name,
            info=msg,
        ))


def emit_clock_locked(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
) -> None:
    """Emit a CLOCK_OK event → synchronization_status Healthy (green).

    Emitted by the activation handlers ONLY when the resource's effective
    clock is a locked PTP reference (sender: its source's clock_name;
    receiver: the negotiated SDP ts-refclk). For an internal clock no event
    is emitted, leaving the sync domain at NC_INACTIVE (NotUsed / grey).
    """
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    role = "sender" if is_sender else "receiver"
    emit_event(queue, EngineEvent(
        domain=AlertDomain.CLOCK, scope=scope,
        event=EventId.CLOCK_OK, state=EventState.NORMAL,
        count=1, id=resource_id, name=interface_name,
        info=f"{role} clock locked (ptp)",
    ))


def emit_stopping(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
) -> None:
    """Emit stopping event (1 event)."""
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    role = "sender" if is_sender else "receiver"
    emit_event(queue, EngineEvent(
        domain=AlertDomain.VENDOR_ESSENCE, scope=scope,
        event=EventId.VENDOR_ESSENCE_STOP, state=EventState.INACTIVE,
        count=1, id=resource_id, name=interface_name,
        info=f"{role} stopping",
    ))


def emit_is11_compatibility_event(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, is_sender: bool, tier: str, info: str,
) -> None:
    """Emit the IS-11 compatibility transition as a vendor-essence event.

    Used by ``Node.set_sender_compatibility_state`` (and the receiver
    equivalent) ONLY on state-transition edges — not on every call.
    See the design notes in those methods for why transition-only is
    required (hysteresis delay reset + overall_message churn).

    Args:
        queue: the Node's event queue. No-op when ``None`` (e.g. in
            unit tests that construct a Node without wiring the
            status-monitor task).
        resource_id: the sender or receiver UUID.
        is_sender: picks ``AlertScope.SENDER`` / ``AlertScope.RECEIVER``.
        tier: the target essence tier — ``"violation"`` (UNHEALTHY),
            ``"partial"`` (PARTIALLY_HEALTHY, e.g. sender
            ``no_essence``/``awaiting_essence``), or ``"healthy"``
            (recovery edge).
        info: human-readable ``overall_message`` text.
    """
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    if tier == "violation":
        event_id = EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED
        state = EventState.ERROR
    elif tier == "partial":
        event_id = EventId.VENDOR_ESSENCE_CONSTRAINT_PARTIAL
        state = EventState.WARNING
    else:
        event_id = EventId.VENDOR_ESSENCE_CONSTRAINT_OK
        state = EventState.NORMAL
    emit_event(queue, EngineEvent(
        domain=AlertDomain.VENDOR_ESSENCE, scope=scope,
        event=event_id, state=state,
        count=1, id=resource_id, name="*",
        info=info,
    ))


#: ``IFF_UP`` from ``<net/if.h>`` — the interface is administratively up.
_IFF_UP = 0x1

#: ``operstate`` values that mean the interface is *not* carrying traffic.
#: "unknown" is NOT among them: loopback reports it, and it means the driver
#: does not track operational state rather than that the link is down.
_OPERSTATE_DOWN = frozenset({"down", "lowerlayerdown", "notpresent"})

_SYSFS_NET = "/sys/class/net"

#: ``IF_OPER_STATUS`` from ``<ifdef.h>`` — the Windows half of the same
#: question sysfs answers with ``carrier`` / ``operstate``. Values are MIB-II
#: ``ifOperStatus`` (RFC 2863), which is what ``operstate`` is modelled on, so
#: the two platforms are being asked the same thing rather than two similar
#: things.
_IF_OPER_STATUS_UP = 1
_IF_OPER_STATUS_DOWN = 2
_IF_OPER_STATUS_TESTING = 3
_IF_OPER_STATUS_UNKNOWN = 4
_IF_OPER_STATUS_DORMANT = 5
_IF_OPER_STATUS_NOT_PRESENT = 6
_IF_OPER_STATUS_LOWER_LAYER_DOWN = 7

#: The statuses that mean *down*, chosen to mirror :data:`_OPERSTATE_DOWN`
#: exactly: the same three conditions, spelled in the Windows enum. Everything
#: else — including ``Unknown`` and ``Dormant`` — reads as up, for the reason
#: given there: a driver that does not track operational state is not a driver
#: reporting a dead link.
_WINDOWS_OPER_STATUS_DOWN = frozenset({
    _IF_OPER_STATUS_DOWN,
    _IF_OPER_STATUS_NOT_PRESENT,
    _IF_OPER_STATUS_LOWER_LAYER_DOWN,
})

#: ``IF_TYPE_SOFTWARE_LOOPBACK`` from ``<ipifcons.h>``.
_IF_TYPE_SOFTWARE_LOOPBACK = 24

#: ``GetAdaptersAddresses`` returned more data than the buffer held.
_ERROR_BUFFER_OVERFLOW = 111

#: Skip the per-address lists: only ``OperStatus`` and the names are wanted,
#: and asking for the rest allocates for nothing.
_GAA_FLAG_SKIP_EVERYTHING = 0x0010 | 0x0020 | 0x0040 | 0x0080


def _windows_adapters() -> list[tuple[str, str, str, int, int]]:
    """Every adapter as ``(guid, friendly_name, description, if_type, status)``.

    Windows has no sysfs, so the operational state comes from
    ``GetAdaptersAddresses`` in ``iphlpapi``. Its ``OperStatus`` field is the
    MIB-II ``ifOperStatus`` of the interface, which already folds in both
    halves sysfs keeps apart: Windows reports ``Down`` for an administratively
    disabled adapter *and* for a connected one whose cable is out.

    Returns an empty list on any failure — the caller turns that into ``None``
    ("could not determine"), never into "down".

    The imports and the structure are built here rather than at module scope
    because ``ctypes.wintypes`` is unimportable on non-Windows platforms.
    """
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        class _IpAdapterAddresses(ctypes.Structure):
            """``IP_ADAPTER_ADDRESSES_LH``, truncated after ``OperStatus``.

            Only the leading fields are declared: the list is walked through
            ``Next`` and read field-by-field, so the tail is never touched.
            Field order and widths up to ``OperStatus`` must match the SDK
            exactly, because that is what fixes the offsets.
            """

        _IpAdapterAddresses._fields_ = [
            ("Length", wintypes.ULONG),
            ("IfIndex", wintypes.DWORD),
            ("Next", ctypes.POINTER(_IpAdapterAddresses)),
            ("AdapterName", ctypes.c_char_p),
            ("FirstUnicastAddress", ctypes.c_void_p),
            ("FirstAnycastAddress", ctypes.c_void_p),
            ("FirstMulticastAddress", ctypes.c_void_p),
            ("FirstDnsServerAddress", ctypes.c_void_p),
            ("DnsSuffix", ctypes.c_wchar_p),
            ("Description", ctypes.c_wchar_p),
            ("FriendlyName", ctypes.c_wchar_p),
            ("PhysicalAddress", ctypes.c_ubyte * 8),
            ("PhysicalAddressLength", wintypes.ULONG),
            ("Flags", wintypes.ULONG),
            ("Mtu", wintypes.ULONG),
            ("IfType", wintypes.ULONG),
            ("OperStatus", ctypes.c_int),
        ]

        iphlpapi = ctypes.WinDLL("iphlpapi")
        size = wintypes.ULONG(0)
        # First call sizes the buffer; the adapter list can change between the
        # two calls, so a grown buffer is retried rather than treated as fatal.
        for _ in range(3):
            buffer = ctypes.create_string_buffer(size.value)
            table = ctypes.cast(buffer, ctypes.POINTER(_IpAdapterAddresses))
            result = iphlpapi.GetAdaptersAddresses(
                0, _GAA_FLAG_SKIP_EVERYTHING, None,
                table if size.value else None, ctypes.byref(size))
            if result == 0:
                break
            if result != _ERROR_BUFFER_OVERFLOW:
                return []
        else:
            return []

        adapters: list[tuple[str, str, str, int, int]] = []
        node = table
        while node:
            entry = node.contents
            guid = entry.AdapterName.decode() if entry.AdapterName else ""
            adapters.append((
                guid,
                entry.FriendlyName or "",
                entry.Description or "",
                int(entry.IfType),
                int(entry.OperStatus),
            ))
            node = entry.Next
        return adapters
    except (OSError, AttributeError, ValueError):
        # No iphlpapi, or a layout the platform did not recognise. Both mean
        # "cannot look", which must not be reported as a link failure.
        return []


def _windows_oper_status(interface_name: str) -> int | None:
    """``OperStatus`` for ``interface_name``, or ``None`` if it is unknown here.

    Matched against all three names an adapter answers to. The GUID is the one
    that matters in practice — it is what ``netifaces`` calls a Windows
    interface, so it is what reaches this module through
    ``find_interface_name_for_address`` — but an operator naming an interface
    by hand will write what the Network Connections panel shows, so the
    friendly name and description are accepted too.
    """
    for guid, friendly, description, _if_type, status in _windows_adapters():
        if interface_name in (guid, friendly, description):
            return status
    return None


def interface_names() -> tuple[str, ...]:
    """Every network interface the OS reports, named in its own vocabulary.

    ``lo``/``eth0`` style names from sysfs; adapter GUIDs on Windows, which is
    what ``netifaces`` yields there and therefore what :func:`is_link_down`
    expects. Empty when the interfaces cannot be listed at all.
    """
    if sys.platform == "win32":
        return tuple(guid for guid, *_rest in _windows_adapters() if guid)
    try:
        return tuple(sorted(os.listdir(_SYSFS_NET)))
    except OSError:
        return ()


def loopback_interface_name() -> str | None:
    """The operating system's own name for the loopback interface.

    ``lo`` everywhere sysfs exists; on Windows the loopback is a pseudo-adapter
    identified by its type rather than a fixed name, and it answers to a GUID.
    Callers that want to reason about loopback without hard-coding a Linux name
    need this.
    """
    if sys.platform != "win32":
        return "lo"
    for guid, _friendly, _description, if_type, _status in _windows_adapters():
        if if_type == _IF_TYPE_SOFTWARE_LOOPBACK:
            return guid
    return None


def is_link_down(interface_name: str) -> bool | None:
    """Whether the named network interface is down.

    ``True`` down, ``False`` up, ``None`` when it cannot be determined here.

    This asks the operating system about the interface. It deliberately does
    *not* infer link state from a socket error, because a socket error says
    nothing about the local interface: BCP-008-01 §"Link Status" scopes
    linkStatus to "the health of all the physical links associated with the
    receiver", with AllUp / SomeDown / AllDown defined over *interfaces*. A
    peer that refuses a connection, closes one, or stops sending leaves our
    Ethernet exactly as it was.

    An interface counts as up only when it is **both** administratively up and
    carrying — the two conditions ``IFF_UP`` and ``IFF_RUNNING`` name. Sysfs
    splits them across two files, and only the first is in ``flags``:

    * ``flags`` carries the administrative flags. ``IFF_RUNNING`` is *never*
      set there — reading it out of ``flags`` reports every interface on the
      machine, loopback included, as down.
    * ``carrier`` is the operational half: 1 carrying, 0 not. Where a driver
      does not publish it, ``operstate`` is consulted instead.

    ``None`` is returned where the interface cannot be inspected — no sysfs,
    or a name that does not exist. Callers must not treat that as "down":
    claiming AllDown because we could not look is the same false alarm as
    claiming it because a peer hung up.

    **Windows** has no sysfs, so the same question goes to
    ``GetAdaptersAddresses``. Its ``OperStatus`` is MIB-II ``ifOperStatus``,
    which is the model ``operstate`` follows, and it already combines the two
    conditions sysfs splits: an administratively disabled adapter and a
    connected one with the cable out both report ``Down``. The three statuses
    treated as down are the same three named in :data:`_OPERSTATE_DOWN`.
    Without this branch every interface on Windows answered ``None``, so a
    genuinely dead link was indistinguishable from one that could not be
    inspected and BCP-008-01 ``linkStatus`` could never leave ``AllUp``.
    """
    if sys.platform == "win32":
        status = _windows_oper_status(interface_name)
        if status is None:
            return None
        return status in _WINDOWS_OPER_STATUS_DOWN

    try:
        with open(f"{_SYSFS_NET}/{interface_name}/flags") as handle:
            flags = int(handle.read().strip(), 16)
    except (OSError, ValueError):
        return None
    if not flags & _IFF_UP:
        return True

    try:
        with open(f"{_SYSFS_NET}/{interface_name}/carrier") as handle:
            return handle.read().strip() != "1"
    except (OSError, ValueError):
        # Reading ``carrier`` fails with ENOENT/EINVAL on some drivers, and
        # notably while the interface is down, so fall through rather than
        # concluding anything from the failure itself.
        pass

    try:
        with open(f"{_SYSFS_NET}/{interface_name}/operstate") as handle:
            return handle.read().strip().lower() in _OPERSTATE_DOWN
    except OSError:
        return None


def emit_transport_error(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
    info: str = "",
) -> None:
    """Emit a transport error, plus a link-down event if the link really is down.

    The transport error always goes out: something went wrong with the stream,
    and that is what connection/transmission status is for. Whether the *link*
    is also down is then a separate question, answered by asking the operating
    system about the interface rather than by interpreting the socket error.

    Every receiver in the streaming emulation used to decide this with a
    ``link_down=True`` argument at the call site, passed for any socket
    problem at all — so a sender closing its TCP connection, an ordinary end
    to a stream, published linkStatus AllDown on a node whose interface was
    working perfectly, sending an operator to inspect cabling. The parameter
    is gone rather than defaulted, so no call site can make that claim again.
    """
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    role = "sender" if is_sender else "receiver"
    emit_event(queue, EngineEvent(
        domain=AlertDomain.TRANSPORT, scope=scope,
        event=EventId.TRANSPORT_STREAM_ERROR, state=EventState.ERROR,
        count=1, id=resource_id, name=interface_name,
        info=info or f"{role} packet lost",
    ))
    if is_link_down(interface_name) is True:
        emit_event(queue, EngineEvent(
            domain=AlertDomain.LINK, scope=scope,
            event=EventId.LINK_DOWN, state=EventState.INACTIVE,
            count=1, id=resource_id, name=interface_name,
            info=f"link down on {interface_name}",
        ))


def emit_recovery(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
    pending_events: set[int],
) -> None:
    """Emit recovery Ok events for each pending domain.

    Iterates the pending events; domain = event/1000.
    """
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    for event_id in pending_events:
        emit_event(queue, EngineEvent(
            domain=event_id // 1000, scope=scope,
            event=event_id, state=EventState.NORMAL,
            count=1, id=resource_id, name=interface_name,
            info="recovery",
        ))
