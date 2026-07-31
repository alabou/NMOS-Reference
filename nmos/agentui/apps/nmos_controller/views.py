# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""What the verbs hand back: immutable observations of the rendered page.

Every assertion-bearing field here comes from text that was on screen or from the
narrow set of attributes carrying identity, gating, and wait markers. None of it
comes from the server's JSON, which is what makes a claim about the operator's
experience honest rather than merely plausible.

Two fields exist specifically to stop this driver overstating itself:

``ResultCell.live_active`` **paired with** ``baseline_live_active``
    The attribute alone proves nothing — the server renders it at page load — so
    it is only ever meaningful next to what it was before.

``ActionOutcome.confirmed_by``
    Names the evidence an action's success rests on. "The button turned green" and
    "every result cell reported OK" are very different claims, and the button only
    changes when *all* resources succeed, so a partial failure and an untouched
    button look identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...enums import (
    Affordance,
    DeviceAccess,
    Health,
    PageId,
    RowAction,
    ToggleAction,
)


@dataclass(frozen=True, slots=True)
class PageView:
    """A page as the operator sees it."""

    page_id: PageId
    url: str
    heading: str = ""
    alerts: tuple[str, ...] = ()
    text: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "page_id": self.page_id,
            "url": self.url,
            "heading": self.heading,
            "alerts": list(self.alerts),
        }


@dataclass(frozen=True, slots=True)
class StatusView:
    """A resource's health, as the badge and dots render it."""

    resource_id: str
    badge_text: str = ""
    overall: Health = Health.UNKNOWN
    facets: dict[str, Health] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "badge_text": self.badge_text,
            "overall": self.overall,
            "facets": {k: str(v) for k, v in self.facets.items()},
        }


@dataclass(frozen=True, slots=True)
class DeviceView:
    """A device's header on a list page: identity, transport security, access.

    Worth reading before blaming a refused action on the driver. A device the
    Controller cannot write to refuses everything, and it says so here — with its
    reason — rather than only at the point of failure.
    """

    serial: str = ""
    address: str = ""
    transports: tuple[str, ...] = ()
    tls_secure: bool = False
    tls_reason: str = ""
    access: DeviceAccess = DeviceAccess.UNKNOWN
    access_reason: str = ""
    inaccessible: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "serial": self.serial,
            "address": self.address,
            "transports": list(self.transports),
            "tls_secure": self.tls_secure,
            "tls_reason": self.tls_reason,
            "access": self.access,
            "access_reason": self.access_reason,
            "inaccessible": self.inaccessible,
        }


@dataclass(frozen=True, slots=True)
class ResourceRow:
    """One row of a selection page."""

    resource_id: str
    label: str = ""
    role: str = ""
    #: Serial of the node this resource belongs to. The page groups rows under a
    #: device header, so this is on screen — and it is what makes a cross-node
    #: route identifiable without guessing from id patterns.
    device_serial: str = ""
    checked: bool = False
    status: StatusView | None = None
    #: Which row actions are offered, and in what state. Records the refused ones
    #: too: "this action does not apply here" is information the operator has.
    actions: dict[RowAction, Affordance] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "label": self.label,
            "role": self.role,
            "device_serial": self.device_serial,
            "checked": self.checked,
            "status": self.status.to_json() if self.status else None,
            "actions": {str(k): str(v) for k, v in self.actions.items()},
        }


@dataclass(frozen=True, slots=True)
class GroupView:
    """One selectable natural group — the "circle" in the left-most column.

    Needed as its own read because the compatible-senders page renders in
    group-only mode: it hides the individual member rows entirely, so there are no
    resource rows to choose from and the group radio is the only way in.
    """

    member_ids: tuple[str, ...] = ()
    label: str = ""
    device_serial: str = ""
    checked: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "member_ids": list(self.member_ids),
            "label": self.label,
            "device_serial": self.device_serial,
            "checked": self.checked,
        }


@dataclass(frozen=True, slots=True)
class SelectionView:
    """What the page will submit, read from its own hidden fields.

    ``dropped_ids`` is the important one. Choosing a group silently unchecks
    members outside it, with no change event, so a scenario can end up submitting
    resources it never selected — or losing ones it did — while the hidden field
    reports the result perfectly happily. Surfacing the difference makes that
    visible instead of surprising.
    """

    checked_ids: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()
    submitted_sender_ids: tuple[str, ...] = ()
    submitted_receiver_ids: tuple[str, ...] = ()
    mode: str = ""
    dropped_ids: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.checked_ids and not self.group_ids

    def to_json(self) -> dict[str, object]:
        return {
            "checked_ids": list(self.checked_ids),
            "group_ids": list(self.group_ids),
            "submitted_sender_ids": list(self.submitted_sender_ids),
            "submitted_receiver_ids": list(self.submitted_receiver_ids),
            "mode": self.mode,
            "dropped_ids": list(self.dropped_ids),
        }


@dataclass(frozen=True, slots=True)
class ConstraintSetRow:
    """One constraint set offered for a resource."""

    resource_id: str
    index: int
    label: str = ""
    media_type: str = ""
    meta_format: str = ""
    meta_layer: str = ""
    part: str = ""
    #: Preference as rendered. ``None`` when the column could not be read.
    preference: int | None = None
    chosen: bool = False
    expanded: bool = False
    flow_match: bool = False
    selectable: bool = True

    @property
    def native(self) -> bool:
        """Whether this is the device's native constraint set.

        Identified by a preference of 100 — there is no "native" marker in the
        markup. A native set pins one value per parameter, so selecting it leaves
        nothing to choose; a non-native set is what offers alternatives.
        """
        return self.preference == 100

    def to_json(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "index": self.index,
            "label": self.label,
            "media_type": self.media_type,
            "meta_format": self.meta_format,
            "meta_layer": self.meta_layer,
            "part": self.part,
            "preference": self.preference,
            "native": self.native,
            "chosen": self.chosen,
            "expanded": self.expanded,
            "flow_match": self.flow_match,
            "selectable": self.selectable,
        }


@dataclass(frozen=True, slots=True)
class ParamWidget:
    """One editable parameter on a configure page."""

    sender_id: str
    urn: str
    part: str
    kind: str = "select"
    value: str = ""
    options: tuple[str, ...] = ()
    flow_matched_options: tuple[str, ...] = ()
    affordance: Affordance = Affordance.ENABLED
    reason: str = ""

    @property
    def editable(self) -> bool:
        return self.affordance is Affordance.ENABLED

    def to_json(self) -> dict[str, object]:
        return {
            "sender_id": self.sender_id,
            "urn": self.urn,
            "part": self.part,
            "kind": self.kind,
            "value": self.value,
            "options": list(self.options),
            "flow_matched_options": list(self.flow_matched_options),
            "affordance": self.affordance,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ResultCell:
    """The outcome the page showed for one resource.

    Captured the instant the action's working marker clears. It has to be: the
    next status frame overwrites the cell's class, replaces its text with
    ``active``/``idle``, and deletes the tooltip carrying the full error body. A
    capture even slightly late shows a plausible screen from which the outcome has
    silently vanished.
    """

    resource_id: str
    side: str = "sender"
    kind: str = "state"
    text: str = ""
    detail: str = ""
    live_active: str | None = None
    baseline_live_active: str | None = None

    @property
    def failed(self) -> bool:
        return self.kind == "error"

    @property
    def live_changed(self) -> bool:
        """Whether the liveness marker moved since the page loaded.

        The only honest evidence of a server-sent update here, because the marker
        itself is rendered by the server at page load.
        """
        if self.live_active is None or self.baseline_live_active is None:
            return False
        return self.live_active != self.baseline_live_active

    def to_json(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "side": self.side,
            "kind": self.kind,
            "text": self.text,
            "detail": self.detail,
            "live_active": self.live_active,
            "baseline_live_active": self.baseline_live_active,
        }


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    """The result of pressing a master toggle."""

    action: ToggleAction
    cells: tuple[ResultCell, ...] = ()
    aria_pressed_before: str | None = None
    aria_pressed_after: str | None = None
    confirmed_by: str = "result cells"

    @property
    def failures(self) -> tuple[ResultCell, ...]:
        return tuple(cell for cell in self.cells if cell.failed)

    @property
    def succeeded(self) -> bool:
        return bool(self.cells) and not self.failures

    def to_json(self) -> dict[str, object]:
        return {
            "action": self.action,
            "cells": [c.to_json() for c in self.cells],
            "aria_pressed": {
                "before": self.aria_pressed_before,
                "after": self.aria_pressed_after,
            },
            "confirmed_by": self.confirmed_by,
            "succeeded": self.succeeded,
        }


@dataclass(frozen=True, slots=True)
class PrivacyView:
    """The privacy panel's state, or absent when the selection has no PEP."""

    pep_indicator: str = ""
    reservation_status: str = ""
    locked: bool = False
    pending: bool = False
    reserved: bool = False
    protocol: str = ""
    mode: str = ""
    curve: str = ""
    protocol_options: tuple[str, ...] = ()
    mode_options: tuple[str, ...] = ()
    curve_options: tuple[str, ...] = ()
    exclusivity_checked: bool = False
    exclusivity_affordance: Affordance = Affordance.ABSENT
    exclusivity_reason: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "pep_indicator": self.pep_indicator,
            "reservation_status": self.reservation_status,
            "locked": self.locked,
            "pending": self.pending,
            "reserved": self.reserved,
            "protocol": self.protocol,
            "mode": self.mode,
            "curve": self.curve,
            "protocol_options": list(self.protocol_options),
            "mode_options": list(self.mode_options),
            "curve_options": list(self.curve_options),
            "exclusivity": {
                "checked": self.exclusivity_checked,
                "affordance": self.exclusivity_affordance,
                "reason": self.exclusivity_reason,
            },
        }
