# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The shipped walkthroughs.

Each scenario is a short script written entirely in operator-level verbs. Read
top to bottom, one is a description of what a person would do — which is the point:
if a scenario cannot be expressed this way, the driver is missing an affordance
rather than needing an escape hatch.

Ordering is read-only first. Those are demonstrable immediately and cannot leave
the rig in a changed state, so they are also the ones to run when checking that a
node is behaving.

``mutating`` scenarios issue real IS-05/IS-11 calls. Per the operator's decision
they perform **no teardown** and leave the rig in the state they reached, for
inspection. One consequence worth knowing: if ``privacy-exclusivity`` does not
reach its release step, the reservation stays held until the session expires or
someone signs out — running ``session-lost``, or signing out in a browser, releases
it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial

from .apps.nmos_controller.session import ControllerSession
from .core.affordance import Control
from .apps.nmos_controller.views import ParamWidget, ResourceRow, StatusView
from .enums import Affordance, RowAction, ToggleAction
from .errors import (
    ActionFailed,
    BlockedControl,
    ControlAbsent,
    GroupOnlyRendering,
    LiveUpdateNotObserved,
    NoSuchOption,
    SelectionGuard,
    SessionLost,
)


@dataclass(frozen=True, slots=True)
class Scenario:
    """One walkthrough."""

    name: str
    description: str
    run: Callable[[ControllerSession], None]
    mutating: bool = False


# ---------------------------------------------------------------------------
# Helpers shared by scenarios
# ---------------------------------------------------------------------------

def _first_selectable(rows: Sequence[ResourceRow]) -> ResourceRow:
    """The first row a scenario can act on."""
    if not rows:
        raise ControlAbsent(
            "this page lists no resources, so there is nothing to demonstrate. "
            "Is the node registered with a registry and serving senders?")
    return rows[0]


@dataclass(frozen=True)
class _SenderChoice:
    """One thing the compatible-senders page offers, however it renders it."""

    device_serial: str
    label: str
    ids: tuple[str, ...]
    #: True when this is a whole group rather than one sender.
    grouped: bool
    #: What to click: a member checkbox, or any member of the group's radio.
    anchor_id: str


def _compatible_senders(session: ControllerSession) -> tuple[_SenderChoice, ...]:
    """Everything selectable on the compatible-senders page, in either shape.

    The page has two shapes, decided by the mode of the receiver selection that
    produced it. In ``single`` / ``subset`` mode it lists individual senders as
    member rows. In ``group`` mode it collapses those rows
    (``device_block.html``'s ``group_only``) and offers whole groups instead --
    which happens whenever *every* member of the receiver's natural group was
    ticked, including the very common case of a group with exactly one member.

    Reading only the first shape is what let a scenario announce "no compatible
    senders" while the page displayed several: ``read_rows`` had nothing to
    match and returned empty, which is indistinguishable from a genuinely empty
    result. It now raises :class:`GroupOnlyRendering`, so the second shape is
    handled here instead of being mistaken for an absence.

    An empty tuple therefore means what it says: the page offers nothing.
    """
    try:
        rows = session.read_rows()
    except GroupOnlyRendering:
        return tuple(
            _SenderChoice(device_serial=group.device_serial,
                          label=group.label,
                          ids=tuple(group.member_ids),
                          grouped=True,
                          anchor_id=group.member_ids[0])
            for group in session.read_groups() if group.member_ids
        )
    return tuple(
        _SenderChoice(device_serial=row.device_serial,
                      label=row.label or row.resource_id,
                      ids=(row.resource_id,),
                      grouped=False,
                      anchor_id=row.resource_id)
        for row in rows
    )


def _choose_sender(session: ControllerSession, choice: _SenderChoice) -> None:
    """Select one :class:`_SenderChoice` and submit it."""
    session.clear_selection()
    if choice.grouped:
        session.select_group(member_id=choice.anchor_id)
    else:
        session.select_resource(resource_id=choice.anchor_id)
    session.submit_selection()


def _toggle_state(control: Control) -> bool | None:
    """Read an aggregate toggle as on, off, or mixed.

    Needed because the configure buttons are toggles, not commands: pressing
    "Constrain" while it is already on sends an *un*constrain. A scenario that
    ignores the current position does the opposite of what it intends roughly half
    the time, and does it silently.
    """
    if control.snapshot is None:
        return False
    state = control.snapshot.attr("aria-pressed")
    if state == "mixed":
        return None
    return state == "true"


def _ensure_toggle(session: ControllerSession, action: ToggleAction,
                   want: bool, *, why: str) -> bool:
    """Drive one toggle to a wanted position, pressing only if needed.

    Returns whether the toggle ended in the wanted position. Reads the toggles
    first — a read is free and pressing is not — so this is safe to call from any
    starting state, which matters because scenarios perform no teardown and each
    run begins wherever the last one stopped.
    """
    control = session.read_toggles()[action]
    if control.affordance is not Affordance.ENABLED:
        session.note(
            f"Cannot set {action} to {'on' if want else 'off'} ({why}): the "
            f"control is {control.affordance} — “{control.reason}”."
        )
        return False

    state = _toggle_state(control)
    if state is None:
        session.note(
            f"{action} is mixed; pressing it first normalises every selected "
            f"resource to off ({why})."
        )
        try:
            session.press_toggle(action)
        except ActionFailed as failed:
            for resource_id, message in failed.failures:
                session.note(f"{action} rejected for {resource_id}: {message}")
            return False
        except BlockedControl as blocked:
            session.note(f"{action} refused before acting: {blocked.reason!r}.")
            return False
        control = session.read_toggles()[action]
        if _toggle_state(control) is None:
            # The normalising press only partially succeeded, so the selection
            # still disagrees. Falling through would press again, and a mixed
            # button maps to "drive everything off" — so wanting *on* would
            # issue a second off-press and report a failure whose trace reads
            # as an attempted activation. Stop here and say what was observed.
            session.note(
                f"{action} is still mixed after the normalising press; some "
                f"resources did not change. Not pressing again ({why})."
            )
            return False

    if _toggle_state(control) is want:
        session.note(f"{action} is already {'on' if want else 'off'} ({why}).")
        return True

    session.note(f"Pressing {action} to turn it {'on' if want else 'off'}: {why}.")
    try:
        session.press_toggle(action)
    except ActionFailed as failed:
        for resource_id, message in failed.failures:
            session.note(f"{action} rejected for {resource_id}: {message}")
        return False
    except BlockedControl as blocked:
        session.note(f"{action} refused before acting: “{blocked.reason}”.")
        return False
    return _toggle_state(session.read_toggles()[action]) is want


def _confirm_receiver_status(
    session: ControllerSession,
    resource_id: str,
    *,
    expected_active: bool,
) -> StatusView:
    """Read, and when necessary await, a receiver marker on its list page."""
    status = session.read_status(resource_id)
    if not expected_active or status.badge_text != "idle":
        return status

    try:
        status = session.await_live_status_change(resource_id=resource_id)
        session.note(f"A live receiver status update arrived for {resource_id}.")
    except LiveUpdateNotObserved:
        session.note(
            f"No live receiver status change seen for {resource_id}. Recorded "
            "as unconfirmed because the list-page marker did not move away "
            "from its page-load value."
        )
    return status


# ---------------------------------------------------------------------------
# 1. attach-and-look
# ---------------------------------------------------------------------------

def _attach_and_look(session: ControllerSession) -> None:
    """Prove the whole chain works: attach, sign in, navigate, read."""
    session.note(
        "Attached to a node that was already running. I will only follow links "
        "and read what is rendered."
    )
    session.read_page()
    session.open_senders()

    # The padlock and circle beside each node serial answer "can the Controller
    # talk to this device at all, and how safely" -- worth establishing before
    # anything else, since an unauthorised device refuses everything.
    for dev in session.read_devices():
        session.note(
            f"Device {dev.serial or '(no serial)'} at {dev.address or '?'}: "
            f"TLS {'verified' if dev.tls_secure else 'NOT end-to-end'}, "
            f"access {dev.access} — “{dev.access_reason}”"
        )

    rows = session.read_rows()
    session.note(f"The Senders page lists {len(rows)} resource(s).")
    session.open_receivers()
    session.read_rows()


# ---------------------------------------------------------------------------
# 2. inspect-one-sender
# ---------------------------------------------------------------------------

def _inspect_one_sender(session: ControllerSession) -> None:
    """Walk one sender through capabilities to the configure page."""
    session.open_senders()
    # Mandatory: the page restores the previous visit's selection from session
    # storage, so without this a run can submit resources it never chose.
    session.clear_selection()
    rows = session.read_rows()
    target = _first_selectable(rows)
    session.note(f"Selecting sender {target.label or target.resource_id}.")

    selection = session.select_resource(resource_id=target.resource_id)
    if selection.dropped_ids:
        session.note(
            f"The page silently unticked {list(selection.dropped_ids)} — its "
            f"group confinement rule, not something I asked for."
        )
    session.submit_selection()

    sets = session.read_constraint_sets()
    session.note(f"{len(sets)} constraint set(s) offered.")
    if sets:
        first = sets[0]
        # Expanding a row also selects its radio, because the page's handler does
        # both; the journal records that second effect.
        session.set_constraint_set_expanded(
            resource_id=first.resource_id, index=first.index, expanded=True)
        # Selecting via the radio directly avoids re-toggling the panel.
        session.choose_constraint_set(
            resource_id=first.resource_id, index=first.index)
        session.continue_to_configuration()
        params = session.read_parameters()
        current = [p for p in params if p.flow_matched_options]
        session.note(
            f"{len(params)} parameter widget(s); {len(current)} show a green "
            f"value marking what the stream is currently doing."
        )

    # The grey row buttons are where most of the NMOS detail lives, and the SDP
    # sits one click below the transport view rather than beside it.
    session.open_senders()
    session.note(
        "Back on the Senders list to use the grey row buttons — transport, flow, "
        "resource and is-11 are where the NMOS detail is."
    )
    session.open_row_action(resource_id=target.resource_id,
                            action=RowAction.TRANSPORT)
    try:
        session.open_sdp()
        session.note("The SDP transport file is one click below the transport view.")
    except ControlAbsent:
        session.note(
            "No SDP link on this transport page, so this resource has no SDP — "
            "the link is only rendered when one exists."
        )

    session.open_senders()
    session.open_row_action(resource_id=target.resource_id, action=RowAction.IS11)
    # These pages hold still by design, so newer values need asking for.
    session.press_refresh()
    session.note(
        "Clicked Refresh: the NMOS detail pages do not poll, so what they show is "
        "a stable snapshot until asked for the latest published values."
    )


# ---------------------------------------------------------------------------
# 3. selection-guard
# ---------------------------------------------------------------------------

def _selection_guard(session: ControllerSession) -> None:
    """Demonstrate the page's own client-side refusal.

    Submitting an empty receiver selection is reachable on every rig and trips a
    native alert before navigation. The scenario verifies that the browser did
    not move, so a refusal cannot be mistaken for a slow page.
    """
    session.open_receivers()
    session.clear_selection()
    before = session.read_page()
    try:
        session.submit_selection()
    except SelectionGuard as guard:
        after = session.read_page()
        session.note(
            f"The page refused: “{guard.alert_text}”. It stayed on "
            f"{after.page_id}, so the refusal blocked the submission rather "
            f"than merely delaying it."
        )
        if after.url != before.url:
            raise
        return
    raise AssertionError(
        "The page accepted an empty receiver selection instead of guarding it."
    )


# ---------------------------------------------------------------------------
# 4. blocked-controls  (the gating demonstration)
# ---------------------------------------------------------------------------

def _blocked_controls(session: ControllerSession) -> None:
    """Show all three ways the Controller refuses an action, writing nothing.

    Each refusal is recorded with the server's own wording, and each blocked step
    is asserted by the step wrapper to have issued no HTTP at all — which is what
    makes "blocked" mean blocked rather than merely look like it.
    """
    session.open_receivers()
    session.clear_selection()
    rows = session.read_rows()

    # (a) A row action the server rendered as a disabled span: not applicable.
    for row in rows:
        if row.actions.get(RowAction.FLOW) is Affordance.BLOCKED:
            session.note(
                f"Receiver {row.label or row.resource_id} shows its flow action "
                f"greyed out. Attempting it should tell me why, and should cost "
                f"nothing."
            )
            try:
                session.open_row_action(resource_id=row.resource_id,
                                        action=RowAction.FLOW)
            except BlockedControl as blocked:
                session.note(
                    f"Refused as a {blocked.rendered_as}: “{blocked.reason}”. "
                    f"A span cannot carry a disabled attribute, which is why the "
                    f"server expresses this one as a CSS class."
                )
            break
    else:
        session.note(
            "Every listed receiver offers its flow action, so the "
            "not-applicable idiom is not visible on this rig right now."
        )

    # (b) A policy-disabled live control on the configure page.
    if not rows:
        return
    target = _first_selectable(rows)
    session.select_resource(resource_id=target.resource_id)
    try:
        session.submit_selection()
    except SelectionGuard as guard:
        session.note(f"Cannot reach the configure page: “{guard.alert_text}”.")
        return

    choices = _compatible_senders(session)
    if not choices:
        session.note("No compatible senders, so the configure page is unreachable.")
        return
    try:
        _choose_sender(session, choices[0])
        session.continue_to_configuration()
    except (SelectionGuard, ControlAbsent) as exc:
        session.note(f"Could not reach configuration: {exc}")
        return

    # Inspected, never pressed. Pressing a toggle that turned out to be available
    # would issue real IS-05/IS-11 calls, and this scenario must write nothing.
    toggles = session.read_toggles()
    for action, control in toggles.items():
        if control.affordance is Affordance.BLOCKED:
            session.note(
                f"{action} is refused as a {control.kind}: “{control.reason}”. "
                f"This is the policy-disabling idiom — a real disabled attribute "
                f"with the reason in the title, which is why the reason has to be "
                f"reproduced as text: a tooltip never appears in a screenshot."
            )
        elif control.affordance is Affordance.ENABLED:
            session.note(
                f"{action} is available (“{control.reason}”). Not pressing it: "
                f"this walkthrough demonstrates refusals and must change nothing."
            )
        else:
            session.note(f"{action} is {control.affordance} on this page.")

    # (c) The shape-shifting reverse-direction link: a disabled button when the
    # group cannot be resolved, an anchor when it can. Inspected, not followed.
    session.read_page()


# ---------------------------------------------------------------------------
# 5. route-one-receiver  (mutating)
# ---------------------------------------------------------------------------

def _route_one_receiver(session: ControllerSession) -> None:
    """Constrain, activate, and observe — leaving the rig as it ends up."""
    session.note(
        "This scenario makes real changes and performs no teardown, so the rig "
        "will be left in whatever state it reaches."
    )
    session.open_receivers()
    session.clear_selection()
    rows = session.read_rows()
    target = _first_selectable(rows)
    session.select_resource(resource_id=target.resource_id)
    session.submit_selection()

    choices = _compatible_senders(session)
    if not choices:
        session.note("No compatible senders for this receiver; stopping.")
        return
    _choose_sender(session, choices[0])

    sets = session.read_constraint_sets()
    if sets:
        session.choose_constraint_set(resource_id=sets[0].resource_id,
                                     index=sets[0].index)
    session.continue_to_configuration()
    session.read_parameters()

    # The ordering below is not arbitrary and is not discoverable from the UI,
    # which renders every toggle as available and lets the server refuse what is
    # out of order. See OPERATING-THE-CONTROLLER.md:
    #
    #   1. a sender must be INACTIVE before its constraints can change
    #      (nmos/api/handlers_compat.py:189 and :255 answer 423 Locked otherwise)
    #   2. a sender must be ACTIVE before its receiver can be activated
    #
    # Each step reads the toggle's position first, so this works from whatever
    # state the previous run left behind.
    session.note(
        "Sequencing this deliberately: deactivate the sender, then constrain it, "
        "then activate it, and only then the receiver. Constraints are locked "
        "while a sender is active, and a receiver has nothing to lock onto until "
        "its sender is transmitting."
    )

    _ensure_toggle(session, ToggleAction.ACTIVATE, False,
                   why="constraints cannot be changed while the sender is active")
    _ensure_toggle(session, ToggleAction.CONSTRAIN, True,
                   why="apply the chosen constraint set now that it is permitted")
    sender_up = _ensure_toggle(session, ToggleAction.ACTIVATE, True,
                               why="the sender must transmit before the receiver "
                                   "can lock onto it")
    receiver_up = False
    if sender_up:
        receiver_up = _ensure_toggle(
            session, ToggleAction.ACTIVATE_RECEIVERS, True,
            why="the sender is active, so the receiver can now join",
        )
    else:
        session.note(
            "Not activating the receiver: its sender is not active, so the "
            "activation would have nothing to lock onto."
        )

    # The badges and traffic lights only exist on the list pages, so the natural
    # verification step after activating is to go back and look at them there —
    # and that is also where clicking through to the per-facet monitor is possible.
    session.open_receivers()
    session.note(
        "Back on the Receivers list to check the traffic lights, which is where "
        "the per-facet status actually appears."
    )
    # The list page owns the receiver's live marker. Observe that receiver
    # before navigating to the static monitor detail page.
    status = _confirm_receiver_status(
        session, target.resource_id, expected_active=receiver_up,
    )
    session.note(
        f"{target.resource_id}: badge reads {status.badge_text!r}, overall "
        f"{status.overall}, facets "
        + ", ".join(f"{k}={v}" for k, v in status.facets.items())
    )
    try:
        session.open_row_action(resource_id=target.resource_id,
                                action=RowAction.MONITOR)
        session.note("Clicked the badge for the detailed status monitor.")
    except ControlAbsent:
        session.note(
            "No monitor link on this row, so this device publishes no BCP-008 "
            "monitor — which is what a grey badge means."
        )


# ---------------------------------------------------------------------------
# 6. privacy-exclusivity  (mutating)
# ---------------------------------------------------------------------------

def _privacy_exclusivity(session: ControllerSession) -> None:
    """Exercise the privacy panel and the exclusive-access reservation."""
    session.note(
        "This scenario acquires a reservation. With no teardown, an unreleased "
        "reservation stays held until the session expires or someone signs out."
    )
    session.open_receivers()
    session.clear_selection()
    rows = session.read_rows()
    target = _first_selectable(rows)
    session.select_resource(resource_id=target.resource_id)
    session.submit_selection()

    choices = _compatible_senders(session)
    if not choices:
        session.note("No compatible senders; stopping.")
        return
    _choose_sender(session, choices[0])
    session.continue_to_configuration()

    if session.read_privacy() is None:
        session.note(
            "This selection has no privacy panel, so there is no PEP to "
            "demonstrate here."
        )
        return

    # Rule C: privacy parameters and node ownership can only be changed while
    # NEITHER the sender nor the receiver is active, because both the PEP
    # parameters and the exclusive key are only mixed into the encryption key at
    # activation. One active resource locks all four controls
    # (privacy_section.html:159 -- lock_all = privacy_view.any_active), so both
    # have to come down first. Without this the panel is simply locked and the
    # scenario can only ever demonstrate the refusal.
    session.note(
        "Deactivating both sides first: privacy parameters and node ownership "
        "are only applied at activation, so the Controller locks them while "
        "anything in the selection is running."
    )
    _ensure_toggle(session, ToggleAction.ACTIVATE_RECEIVERS, False,
                   why="privacy is locked while the receiver is active")
    _ensure_toggle(session, ToggleAction.ACTIVATE, False,
                   why="privacy is locked while the sender is active")

    privacy = session.read_privacy()
    if privacy is None:
        session.note("The privacy panel disappeared after deactivating.")
        return

    # The reason is only quoted when the control is actually refused. The
    # Controller's client-side reconciler re-enables these controls without
    # rewriting their ``title``, so an enabled control can still carry the
    # page-load lock text — quoting it here would report a stale reason as current.
    refused = privacy.exclusivity_affordance is not Affordance.ENABLED
    session.note(
        f"Privacy panel: {privacy.pep_indicator or '(no indicator)'}, "
        f"exclusivity is {privacy.exclusivity_affordance}"
        + (f" — “{privacy.exclusivity_reason}”"
           if refused and privacy.exclusivity_reason else "")
    )

    if privacy.mode_options and privacy.mode not in ("", privacy.mode_options[0]):
        try:
            session.set_privacy(mode=privacy.mode_options[0])
        except BlockedControl as blocked:
            session.note(f"The mode selector is locked: “{blocked.reason}”.")

    try:
        session.acquire_exclusivity()
        session.note("Took ownership of the Nodes in the selection.")
        session.release_exclusivity()
        session.note("Released ownership.")
    except BlockedControl as blocked:
        session.note(
            f"Exclusivity is still refused: “{blocked.reason}”. If this mentions "
            f"deactivating, something in the selection is still active; if it "
            f"mentions the reservation service, no Node in the selection "
            f"advertises it."
        )


# ---------------------------------------------------------------------------
# 7. session-lost
# ---------------------------------------------------------------------------

def _session_lost(session: ControllerSession) -> None:
    """Confirm a dead session is reported rather than silently followed.

    Guards the worst silent failure available to this driver: every verb quietly
    operating on the sign-in page while reporting plausible results.
    """
    session.open_senders()
    session.sign_out()
    session.note(
        "Signed out. Any further navigation should be reported as a lost "
        "session rather than silently landing on the sign-in page."
    )
    try:
        session.open_senders()
    except SessionLost as lost:
        session.note(f"Correctly reported: {lost.msg}")
        return
    except BlockedControl as blocked:
        session.note(
            f"The navigation link is no longer offered: “{blocked.reason}”. Also "
            f"a correct outcome — the signed-out page has no Senders link."
        )
        return
    except ControlAbsent:
        session.note(
            "The Senders link is absent once signed out, which is the correct "
            "outcome: there is nothing to navigate with."
        )
        return
    raise AssertionError(
        "navigation succeeded after signing out, so a dead session went "
        "undetected — every later verb would report plausible nonsense"
    )


# ---------------------------------------------------------------------------
# 8. cross-node-reverse
# ---------------------------------------------------------------------------

def _cross_node_reverse(session: ControllerSession) -> None:
    """Route across two nodes and follow the reverse-direction companion paths.

    Needs two nodes registered with the same registry. Writes nothing: it selects,
    inspects, and follows links.

    What it demonstrates is the counter-intuitive part of a cross-node route — the
    USB and talk-back **senders** live on the node where the audio/video
    **receivers** are, and vice versa — plus both forms of the shape-shifting
    reverse-direction control in one place.
    """
    session.open_receivers()
    session.clear_selection()

    devices = session.read_devices()
    session.note(
        f"{len(devices)} device(s) visible: "
        + ", ".join(f"{d.serial} ({'/'.join(d.transports) or 'no transports'})"
                    for d in devices)
    )
    if len({d.serial for d in devices if d.serial}) < 2:
        session.note(
            "Only one node is registered, so there is no cross-node route and the "
            "reverse-direction buttons will not appear. Start a second node."
        )
        return

    rows = session.read_rows()
    receiver = next((r for r in rows if r.role.startswith("VIDEO")), None)
    if receiver is None:
        session.note("No video receiver listed; nothing to route.")
        return
    session.note(
        f"Receiver {receiver.label!r} lives on {receiver.device_serial}."
    )
    session.select_resource(resource_id=receiver.resource_id)
    session.submit_selection()

    # A sender on a *different* node is what makes the route cross-node.
    senders = _compatible_senders(session)
    remote = [x for x in senders
              if x.device_serial and x.device_serial != receiver.device_serial]
    if not remote:
        session.note(
            f"Every compatible sender is on {receiver.device_serial} too, so this "
            f"route would not cross nodes and no companion paths apply."
        )
        return

    sender = remote[0]
    session.note(
        f"Choosing sender {sender.label!r} on {sender.device_serial} — a route "
        f"from {sender.device_serial} to {receiver.device_serial}."
    )
    _choose_sender(session, sender)
    session.continue_to_configuration()

    # Both forms of the shape-shifter are usually visible together here.
    links = session.read_reverse_links()
    for group, control in links.items():
        if control.affordance is Affordance.ENABLED:
            session.note(
                f"{group.upper()} companion path is available (rendered as "
                f"{control.kind})."
            )
        else:
            session.note(
                f"{group.upper()} companion path is {control.affordance}, rendered "
                f"as {control.kind} — “{control.reason}”. Usually means the two "
                f"nodes have no matching pair for it."
            )

    for group in links:
        try:
            view = session.open_reverse_direction(group=group)
        except BlockedControl:
            continue
        session.note(
            f"Followed the {group.upper()} link to the reverse pair's picker "
            f"({view.page_id})."
        )
        # The reverse pair is named in the URL, and this is the part worth reading:
        # the reverse sender sits on the node that hosts the forward receiver.
        session.note(
            "Note which node each side of the reverse pair is on: the companion "
            "sender is on the node hosting the forward receiver, because a return "
            "path runs the other way."
        )
        session.read_constraint_sets()
        return

    session.note(
        "Neither companion path is available between these two nodes, so there is "
        "nothing further to follow."
    )


# ---------------------------------------------------------------------------
# 9. demo-group-route-tb
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class VideoChoice:
    """How the video leg of the demo should be configured.

    ``set_label`` empty means "leave the native set selected". Naming a non-native
    set is what buys the freedom to choose values at all — a native set pins one
    value per parameter and renders them readonly.

    ``settings`` are ``(parameter, wanted)`` pairs resolved against the options the
    page actually offers, so a scenario states its intent ("10-bit", "50 Hz")
    without hardcoding the exact rendering of an option value.
    """

    set_label: str = ""
    settings: tuple[tuple[str, str], ...] = ()
    description: str = "the device's native constraint set"


def _resolve_option(widget: ParamWidget, wanted: str) -> str:
    """Pick the offered option matching ``wanted``.

    Exact match first, then a unique substring match — so "10" selects the option
    ``10`` rather than ambiguously matching ``1080``, while a structured value can
    still be requested by a fragment of it.
    """
    options = list(widget.options)
    if wanted in options:
        return wanted
    partial = [o for o in options if wanted in o]
    if len(partial) == 1:
        return partial[0]
    raise NoSuchOption(
        f"{wanted!r} does not identify exactly one option for "
        f"{widget.urn.rsplit(':', 1)[-1]}; the page offers {options}",
        offered=tuple(options),
    )


def _pick_group(session: ControllerSession, *, serial: str, members: int) -> str:
    """A member id of the group on ``serial`` with ``members`` members."""
    for group in session.read_groups():
        if group.device_serial == serial and len(group.member_ids) == members:
            return group.member_ids[0]
    raise ControlAbsent(
        f"no {members}-member group on {serial} is offered on this page"
    )


def _demo_group_route_tb(session: ControllerSession,
                         video: VideoChoice | None = None) -> None:
    """The full operator demo: a grouped cross-node route, then talk-back audio.

    Audio+video **receivers as a group** on SNX00002, driven from the audio+video
    **senders as a group** on SNX00001, then the talk-back companion path
    configured and activated in the same run.

    ``video`` selects how the video leg is configured — the device's native set, or
    a named non-native set with specific values chosen from what it offers.

    Sequenced per the Controller's ordering rules: a sender's constraints are locked
    while it is active, and a receiver has nothing to lock onto until its sender is
    transmitting. Every toggle is read before it is pressed, so the demo behaves the
    same whatever state the previous run left behind.
    """
    RX_NODE, TX_NODE = "SNX00002", "SNX00001"
    video = video or VideoChoice()

    session.note(
        f"Goal: route the audio+video receiver group on {RX_NODE} from the "
        f"audio+video sender group on {TX_NODE}, with video on "
        f"{video.description}, then configure and activate the talk-back audio."
    )

    # -- 1. the receiver group, chosen with the left-hand circle ------------
    session.open_receivers()
    session.clear_selection()
    for dev in session.read_devices():
        session.note(
            f"{dev.serial} at {dev.address}: TLS "
            f"{'verified' if dev.tls_secure else 'not end-to-end'}, "
            f"access {dev.access}, transports {'/'.join(dev.transports)}."
        )

    rx_member = _pick_group(session, serial=RX_NODE, members=2)
    session.note(
        f"Selecting the whole audio+video group on {RX_NODE} with the group "
        f"circle rather than ticking members individually."
    )
    selection = session.select_group(member_id=rx_member)
    session.note(
        f"Group selected: {len(selection.group_ids)} member(s) — "
        f"{', '.join('…' + i[-4:] for i in selection.group_ids)}."
    )
    session.submit_selection()

    # -- 2. the sender group on the other node -----------------------------
    # This page renders group-only: member rows are hidden, so the group circle is
    # the only way to choose. Picking the group on the *other* node is what makes
    # the route cross-node and brings up the companion paths later.
    tx_member = _pick_group(session, serial=TX_NODE, members=2)
    session.note(
        f"Compatible senders is in group mode. Choosing the audio+video group on "
        f"{TX_NODE} — a different node, so this is a cross-node route."
    )
    session.select_group(member_id=tx_member)
    session.submit_selection()

    # -- 3. native constraint sets -----------------------------------------
    # Native is identified by a preference of 100; there is no "native" marker in
    # the markup. Native sets pin one value per parameter, so the UI renders those
    # values readonly and there is nothing to choose — which is the point here.
    sets = session.read_constraint_sets()
    natives = [cs for cs in sets if cs.native]
    session.note(
        f"{len(sets)} constraint set(s) offered; {len(natives)} native "
        f"(preference 100): "
        + ", ".join(f"#{cs.index} {cs.label!r}" for cs in natives)
    )

    video_set = None
    if video.set_label:
        video_set = next(
            (cs for cs in sets if cs.label == video.set_label), None)
        if video_set is None:
            raise ControlAbsent(
                f"no constraint set named {video.set_label!r} is offered; the "
                f"page lists "
                + ", ".join(repr(cs.label) for cs in sets if cs.meta_format == "video")
            )
        session.note(
            f"Video will use #{video_set.index} {video_set.label!r} "
            f"({video_set.media_type}, preference {video_set.preference}) — "
            f"non-native, which is what allows values to be chosen at all."
        )

    # Which set to select for which resource: the requested one for video, and
    # each other resource keeps its native (highest-preference) set.
    wanted: set[tuple[str, int]] = set()
    if video_set is not None:
        wanted.add((video_set.resource_id, video_set.index))
        wanted.update((cs.resource_id, cs.index) for cs in sets
                      if cs.native and cs.resource_id != video_set.resource_id)
    else:
        wanted.update((cs.resource_id, cs.index) for cs in sets if cs.native)

    for cs in sets:
        if (cs.resource_id, cs.index) not in wanted:
            continue
        if cs.chosen:
            session.note(
                f"#{cs.index} {cs.label!r} is already selected"
                + (" — the highest-preference set is pre-selected by default."
                   if cs.native else ".")
            )
        else:
            session.choose_constraint_set(resource_id=cs.resource_id,
                                          index=cs.index)
    session.continue_to_configuration()

    params = session.read_parameters()
    pinned = [p for p in params if not p.editable]
    session.note(
        f"{len(params)} parameter(s) on the configure page, {len(pinned)} pinned "
        f"and unchangeable. A native set pins every value; a non-native set leaves "
        f"the choices open."
    )

    # -- 3b. apply the requested video values -------------------------------
    if video.settings:
        by_name = {p.urn.rsplit(":", 1)[-1]: p for p in params}
        for name, requested in video.settings:
            widget = by_name.get(name)
            if widget is None:
                session.note(f"No {name} parameter on this page to set.")
                continue
            if not widget.editable:
                session.note(
                    f"{name} is pinned to {widget.value!r} and cannot be changed "
                    f"— a non-native set would be needed to choose it."
                )
                continue
            chosen = _resolve_option(widget, requested)
            session.set_parameter(sender_id=widget.sender_id, urn=widget.urn,
                                  value=chosen, part=widget.part)
            session.note(f"Set video {name} to {chosen}.")

    # -- 4. constrain and activate, in the order the server requires --------
    session.note(
        "Sequencing: deactivate, then constrain, then activate the sender, then "
        "the receiver. Constraints are locked while a sender is active, and a "
        "receiver cannot lock onto a sender that is not transmitting."
    )
    _ensure_toggle(session, ToggleAction.ACTIVATE, False,
                   why="a sender's constraints cannot change while it is active")
    _ensure_toggle(session, ToggleAction.CONSTRAIN, True,
                   why=f"apply {video.description}")
    sender_up = _ensure_toggle(session, ToggleAction.ACTIVATE, True,
                               why="the sender must transmit first")
    activated: list[str] = []
    if sender_up:
        _ensure_toggle(session, ToggleAction.ACTIVATE_RECEIVERS, True,
                       why="the receiver group can now join")
        # Which receivers were acted on comes from the page's own result cells,
        # not from guessing. An earlier version pattern-matched labels at the end
        # and waited on a receiver this demo never touched — legitimately idle, so
        # the wait timed out and the journal reported the run as unconfirmed while
        # the route was in fact up.
        activated += [c.resource_id
                      for c in session.read_results(receiver_side=True)
                      if c.resource_id]
    else:
        session.note("Sender did not activate, so the receiver is left alone.")

    # -- 5. talk-back audio, via the companion path ------------------------
    links = session.read_reverse_links()
    tb = links.get("tb")
    if tb is None or tb.affordance is not Affordance.ENABLED:
        session.note(
            "No talk-back companion path is available between these two nodes"
            + (f": “{tb.reason}”" if tb is not None else ".")
        )
        return

    session.note(
        "Following the talk-back button. Note which node each side lands on: the "
        "companion sender sits on the node hosting the forward receivers, because "
        "the return path runs the other way."
    )
    session.open_reverse_direction(group="tb")

    tb_sets = session.read_constraint_sets()
    session.note(
        f"Talk-back offers {len(tb_sets)} constraint set(s): "
        + ", ".join(
            f"#{cs.index} {cs.label!r} ({cs.media_type}, preference "
            f"{cs.preference})" for cs in tb_sets)
    )
    for cs in tb_sets:
        if cs.native and not cs.chosen:
            session.choose_constraint_set(resource_id=cs.resource_id,
                                          index=cs.index)
    session.continue_to_configuration()

    # -- 6. stereo PCM 48 kHz, as far as the device allows ------------------
    tb_params = session.read_parameters()
    by_name = {p.urn.rsplit(":", 1)[-1]: p for p in tb_params}
    for name in ("media_type", "sample_rate", "sample_depth", "channel_count"):
        widget = by_name.get(name)
        if widget is not None:
            session.note(
                f"talk-back {name} = {widget.value!r} "
                f"({'editable' if widget.editable else 'pinned by the native set'})"
            )

    channels = by_name.get("channel_count")
    if channels is not None and channels.editable:
        session.set_parameter(sender_id=channels.sender_id, urn=channels.urn,
                              value="2", part=channels.part)
        session.note("Set talk-back audio to 2 channels (stereo).")
    elif channels is not None:
        session.note(
            f"Stereo is not available on this talk-back path: channel_count is "
            f"pinned to {channels.value!r} by the only constraint set the sender "
            f"offers, which is native (one value per parameter). PCM at 48 kHz is "
            f"satisfied — media_type {by_name['media_type'].value!r} at "
            f"{by_name['sample_rate'].value!r} Hz — but the channel count cannot "
            f"be changed without a non-native set to choose from."
        )

    _ensure_toggle(session, ToggleAction.ACTIVATE, False,
                   why="talk-back constraints are locked while its sender is active")
    _ensure_toggle(session, ToggleAction.CONSTRAIN, True,
                   why="apply the talk-back audio configuration")
    tb_up = _ensure_toggle(session, ToggleAction.ACTIVATE, True,
                           why="the talk-back sender must transmit first")
    if tb_up:
        _ensure_toggle(session, ToggleAction.ACTIVATE_RECEIVERS, True,
                       why="the talk-back receiver can now join")
        activated += [c.resource_id
                      for c in session.read_results(receiver_side=True)
                      if c.resource_id]

    # -- 7. verify on the list page, where the traffic lights live ---------
    session.open_receivers()
    session.note(
        "Back on the Receivers list: the badges and traffic lights only exist "
        "here, and this is where the per-facet detail can be opened."
    )

    # An activation reaches the badge by way of the node, the registry, and the
    # Controller's cache, so a page loaded immediately afterwards can still render
    # the old state. Reading once here reported freshly-activated resources as
    # idle. Waiting for the status stream to move a badge away from its page-load
    # value is the honest fix -- and if nothing arrives, that is recorded as
    # unconfirmed rather than papered over.
    rows = session.read_rows()
    touched = set(activated)
    stale = [r for r in rows
             if r.resource_id in touched
             and r.status is not None and r.status.badge_text == "idle"]
    session.note(
        f"This run activated {len(touched)} receiver(s): "
        + ", ".join("…" + r[-4:] for r in sorted(touched))
    )
    if stale:
        session.note(
            f"{len(stale)} of them still read idle. Waiting for the status stream "
            f"rather than trusting a first read."
        )
        try:
            session.await_live_status_change(resource_id=stale[0].resource_id)
            session.note("A live status update arrived; re-reading the list.")
            rows = session.read_rows()
        except LiveUpdateNotObserved:
            session.note(
                "No status change arrived within the timeout, so the badges below "
                "are whatever the page rendered at load. Recorded as unconfirmed."
            )

    for row in rows:
        if row.status is None:
            continue
        facets = ", ".join(f"{k}={v}" for k, v in row.status.facets.items())
        session.note(
            f"{row.device_serial} {row.label!r}: {row.status.badge_text} "
            f"(overall {row.status.overall}; {facets})"
        )


# ---------------------------------------------------------------------------
# 11. tutorial-jpegxs
# ---------------------------------------------------------------------------

def _tutorial_jpegxs(session: ControllerSession) -> None:
    """Teach: activate a video sender and subscribe a receiver to it, over JPEG XS.

    Written for tutorial mode. Each ``teach`` call states the action and the
    observable result; the depth a reader can expand into is filled from the run's
    own data rather than composed afterwards.
    """
    session.teach(
        "Sign in and find the receivers",
        do="Open the Controller at /controller/, sign in with the administrator "
           "password, then choose **Receivers** in the navigation bar.",
        see="A table of receivers grouped under each node's serial number. Beside "
            "every serial there is a padlock showing whether the device is reached "
            "over HTTPS, and a circle showing whether the Controller is allowed to "
            "read and write to it.",
        internals="Signing in sets a session cookie scoped to /controller. The "
                  "receivers table is rendered from the Controller's cache of the "
                  "IS-04 registry rather than by querying each node — which is "
                  "why it can show resources from every node at once, and why a "
                  "change takes a moment to appear after you make it.",
        specs=(("AMWA IS-04 — Discovery & Registration", "https://specs.amwa.tv/"),) ,
        sources=(
            ("nmos/node/registry.py", "the node's IS-04 Registration API client — "
                                      "how a node publishes itself"),
            ("nmos/controller/cache.py", "the Controller's resource cache, fed "
                                         "from the registry's query WebSocket"),
        ),
    )

    session.open_receivers()
    session.clear_selection()
    devices = session.read_devices()
    rows = session.read_rows()
    receiver = next(r for r in rows
                    if r.role.startswith("VIDEO") and r.device_serial == "SNX00002")

    session.teach(
        "Check the devices are reachable",
        do="Look at the padlock and circle next to each node serial. Hover either "
           "one to read what it means.",
        see="Both nodes report the Controller is authorised to read and write. "
            "The padlock is open here because this rig runs without TLS.",
        state={f"{d.serial} — access": f"{d.access}: {d.access_reason}"
               for d in devices}
        | {f"{d.serial} — transport": ("HTTPS throughout" if d.tls_secure
                                       else "at least one control over plain HTTP")
           for d in devices},
        detail="A device whose circle is amber or red will refuse the actions "
               "later in this tutorial, and the tooltip says why. Worth checking "
               "first rather than discovering it at the activation step.",
    )

    session.select_resource(resource_id=receiver.resource_id)
    session.teach(
        "Select the video receiver you want to feed",
        do=f"Tick the square checkbox beside **{receiver.label}** on "
           f"{receiver.device_serial}. (The circle in the left-most column would "
           f"select that resource's whole group instead — useful when you want "
           f"audio and video together, but here we want one receiver.)",
        see="Only that one row is ticked. Nothing has been sent to any device yet "
            "— selecting is purely local until you submit.",
        state={"receiver": receiver.label,
               "node": receiver.device_serial,
               "id": receiver.resource_id},
    )

    session.submit_selection()
    senders = _compatible_senders(session)
    sender = next(s_ for s_ in senders if s_.device_serial == "SNX00001")

    session.teach(
        "Ask which senders it can accept",
        do="Press **Find compatible senders**.",
        see=f"A shorter list than the full senders page: only senders whose "
            f"capabilities intersect this receiver's. {len(senders)} sender(s) "
            f"qualify here.",
        detail="This list is an intersection, computed from both sides' IS-11 "
               "capabilities. An empty list means no sender can feed this "
               "receiver — not that something is broken.",
        internals="Capability matching in this project runs through the Matrox "
                  "**Capability Constraint Framework (CCF)**: each side declares "
                  "capability sets, and compatibility is their intersection. "
                  "Nothing here compares prebaked SDP templates — the matching "
                  "surface is what the devices actually advertise, which is what "
                  "lets the same code drive independent and multiplexed streams "
                  "alike.",
        specs=(
            ("AMWA BCP-004-01 — Receiver Capabilities", "https://specs.amwa.tv/"),
            ("Matrox NMOS extensions — the CCF and hierarchical capabilities", "https://github.com/alabou/NMOS-MatroxOnly"),
        ),
        sources=(
            ("caps/MatroxCCF.py", "the CCF itself — Caps, Cons, CapSet, ConSet, "
                                  "RangeValue, intersection and union"),
            ("nmos/controller/compat.py", "receiver ↔ sender capability "
                                          "intersection for this page"),
            ("nmos/node/flow_caps.py", "turning an NMOS flow into CCF "
                                       "capabilities"),
        ),
    )

    _choose_sender(session, sender)

    sets = session.read_constraint_sets()
    jxs = next(cs for cs in sets if cs.media_type == "video/jxsv"
               and "TDC" not in cs.label)

    session.teach(
        "Choose JPEG XS from the sender's capabilities",
        do=f"Select the sender **{sender.label}** on {sender.device_serial} and "
           f"press **Show capabilities**. In the table of constraint sets, click "
           f"the row labelled **{jxs.label}**.",
        see=f"{len(sets)} constraint sets, one row each, with a media type and a "
            f"preference. The set with preference 100 is the device's *native* "
            f"one — it fixes every value, so there is nothing to choose. "
            f"{jxs.label!r} has preference {jxs.preference}, which is what lets "
            f"you pick values in the next step.",
        state={f"#{cs.index} {cs.label}": f"{cs.media_type}, preference {cs.preference}"
               for cs in sets if cs.meta_format == "video"},
        detail="A green constraint-set name means it matches what the sender is "
               "transmitting right now. Preference orders the alternatives; the "
               "highest is pre-selected when the page loads.",
        internals="A constraint set is a CCF *ConSet*: one allowed combination of "
                  "parameter values. A device advertises several, and the one "
                  "with preference 100 is its native mode. For a multiplexed "
                  "stream these sets are **hierarchical** — each sub-flow "
                  "(video, audio, data) carries its own, tagged with format and "
                  "layer metadata, so one negotiation configures every layer.",
        specs=(
            ("AMWA IS-11 — Stream Compatibility", "https://specs.amwa.tv/"),
            ("AMWA BCP-006-01 — NMOS With JPEG XS", "https://specs.amwa.tv/"),
            ("Matrox NMOS extensions — One Model, format/layer metadata", "https://github.com/alabou/NMOS-MatroxOnly"),
        ),
        sources=(
            ("caps/MatroxCCF.py", "ConSet and the constraint model"),
            ("nmos/controller/flow_match.py", "what makes a set's name green — "
                                              "matching a resource's current flow "
                                              "against its declared sets"),
            ("nmos/controller/grouping.py", "natural groups and the format/layer "
                                            "metadata behind the One Model design"),
        ),
    )

    session.choose_constraint_set(resource_id=jxs.resource_id, index=jxs.index)
    session.continue_to_configuration()
    params = session.read_parameters()
    by_name = {p_.urn.rsplit(":", 1)[-1]: p_ for p_ in params}

    for name, wanted in (("frame_width", "1920"), ("frame_height", "1080"),
                         ("grain_rate", '"numerator": 50,'),
                         ("component_depth", "10")):
        widget = by_name.get(name)
        if widget is not None and widget.editable:
            session.set_parameter(sender_id=widget.sender_id, urn=widget.urn,
                                  value=_resolve_option(widget, wanted),
                                  part=widget.part)

    chosen = {p_.urn.rsplit(":", 1)[-1]: p_.value
              for p_ in session.read_parameters()
              if p_.urn.rsplit(":", 1)[-1] in
              ("media_type", "frame_width", "frame_height", "grain_rate",
               "component_depth", "profile", "level", "sublevel")}

    session.teach(
        "Configure the JPEG XS stream",
        do="Press **Configure capabilities**, then use the drop-downs to set "
           "1920 × 1080, 50 Hz and 10-bit.",
        see="Only the parameters this constraint set leaves open are editable. "
            "Greyed-out values are pinned — normal, and determined by what the "
            "device can actually do. Transport parameters are usually read-only.",
        state={k: str(v) for k, v in chosen.items()},
        detail="Nothing has been sent to the device yet. These edits are held in "
               "the browser until you press Constrain, and the Reset button "
               "discards them.",
        internals="Each drop-down offers exactly the values the chosen constraint "
                  "set permits — a CCF *RangeValue*, which may be an enumeration, "
                  "a numeric range, or a single pinned value. A pinned value "
                  "renders read-only, which is why a native set leaves nothing to "
                  "choose.",
        specs=(("AMWA BCP-004-02 — Receiver Capabilities schemas", "https://specs.amwa.tv/"),),
        sources=(
            ("caps/MatroxCCF.py", "RangeValue — how a permitted set of values is "
                                  "represented and narrowed"),
        ),
    )

    _ensure_toggle(session, ToggleAction.ACTIVATE, False,
                   why="a sender's constraints cannot be changed while it is active")
    _ensure_toggle(session, ToggleAction.CONSTRAIN, True,
                   why="apply the JPEG XS configuration to the sender")

    session.teach(
        "Apply the configuration to the sender",
        do="Make sure the sender is **not** active, then press **Constrain**.",
        see="A result cell appears beside the sender reading `OK (200)`. That is "
            "the device accepting the constraints.",
        detail="Order matters here: a sender's constraints are locked while it is "
               "transmitting, so constraining an active sender is refused with "
               "*423 Locked*. Deactivate first, constrain, then activate.",
        internals="This is **IS-11 stream compatibility**. The set you chose is "
                  "sent as the sender's *active constraints*; the device must then "
                  "produce a stream satisfying them. The 423 rule you avoided by "
                  "deactivating first is enforced node-side, not by the UI.",
        specs=(("AMWA IS-11 — Stream Compatibility", "https://specs.amwa.tv/"),),
        sources=(
            ("nmos/api/handlers_compat.py", "the IS-11 endpoints, including the "
                                            "423 Locked rule for an active sender"),
            ("nmos/node/compatibility.py", "node-side IS-11 stream compatibility "
                                           "management"),
        ),
    )

    _ensure_toggle(session, ToggleAction.ACTIVATE, True,
                   why="the sender must transmit before the receiver can join")
    activated = [c.resource_id for c in session.read_results(receiver_side=True)
                 if c.resource_id]

    session.teach(
        "Activate the sender",
        do="Press **Activate** on the sender side.",
        see="The sender's state changes to *active*. It is now transmitting.",
        internals="Activation is an **IS-05** immediate activation: the "
                  "Controller PATCHes the sender's /staged endpoint and the node "
                  "promotes staged to active. This implementation runs every "
                  "transport — RTP, SRT, USB, mux containers — through one "
                  "activation pipeline rather than a path per transport.",
        specs=(("AMWA IS-05 — Connection Management", "https://specs.amwa.tv/"),),
        sources=(
            ("nmos/api/handlers_connection.py", "the IS-05 Connection API "
                                                "endpoints"),
            ("nmos/node/activation_engine.py", "the shared 5-step activation "
                                               "pipeline"),
        ),
    )

    _ensure_toggle(session, ToggleAction.ACTIVATE_RECEIVERS, True,
                   why="the receiver can now subscribe to a transmitting sender")
    activated += [c.resource_id for c in session.read_results(receiver_side=True)
                  if c.resource_id]

    session.teach(
        "Subscribe the receiver",
        do="Press **Activate** on the receiver side.",
        see="The receiver's state changes to *active*. It is now subscribed to "
            "the sender and receiving the JPEG XS stream.",
        detail="The receiver has to come second: until the sender is transmitting "
               "there is no stream, and no transport file, for it to lock onto.",
        internals="The Controller fetches the sender's **SDP transport file** and "
                  "stages it on the receiver before activating. That is what "
                  "creates the IS-04 *subscription* linking the two resources — "
                  "the field the receivers page reads to decide whether the flow "
                  "button is offered.",
        specs=(
            ("AMWA IS-05 — Connection Management", "https://specs.amwa.tv/"),
            ("AMWA BCP-006-01 — NMOS With JPEG XS (SDP profile)", "https://specs.amwa.tv/"),
        ),
        sources=(
            ("nmos/node/sdp_transport.py", "SDP transport file generation and "
                                           "receiver-side SDP processing"),
            ("sdp/MatroxSdp.py", "the SDP model itself"),
            ("nmos/api/handlers_connection.py", "staging and activating a "
                                                "receiver"),
        ),
    )

    # -- evidence -----------------------------------------------------------
    session.open_receivers()
    rows = session.read_rows()
    touched = set(activated)
    stale = [r for r in rows if r.resource_id in touched
             and r.status is not None and r.status.badge_text == "idle"]
    if stale:
        try:
            session.await_live_status_change(resource_id=stale[0].resource_id)
        except LiveUpdateNotObserved:
            session.note("No live status change arrived within the timeout.")
    rows = session.read_rows()
    live = next((r for r in rows if r.resource_id == receiver.resource_id), None)

    session.teach(
        "Confirm it on the Receivers page",
        do="Go back to **Receivers** and look at the row for your receiver.",
        see="Its badge reads *active* and the traffic lights are green. Click the "
            "badge for the detailed per-facet monitor.",
        state=({"badge": live.status.badge_text,
                "overall": str(live.status.overall)}
               | {f"facet: {k}": str(v) for k, v in live.status.facets.items()})
        if live is not None and live.status is not None else {},
        detail="These indicators are updated live by the Controller's status "
               "stream, so they change without reloading the page. The NMOS "
               "detail pages reached from the grey buttons do not — they hold a "
               "stable snapshot until you press Refresh.",
        internals="The traffic lights are **BCP-008 status carried over IS-04**. "
                  "Status is published as monitor resources in the registry, so "
                  "any controller subscribed to the registry's query WebSocket "
                  "sees changes asynchronously — no IS-12 or MS-05-02 control "
                  "stack is required. The Controller relays them to the browser "
                  "as server-sent events.",
        specs=(
            ("AMWA BCP-008-01 — Receiver Status", "https://specs.amwa.tv/"),
            ("AMWA IS-04 — the registry query WebSocket that carries it", "https://specs.amwa.tv/"),
        ),
        sources=(
            ("nmos/node/status_monitor.py", "BCP-008 status reporting — the event "
                                            "consumer and state machine"),
            ("nmos/controller/sse.py", "the server-sent-events stream the badges "
                                       "subscribe to"),
        ),
    )

    session.open_row_action(resource_id=receiver.resource_id,
                            action=RowAction.FLOW)
    flow_text = session.read_page().text

    session.teach(
        "Check the flow really is JPEG XS",
        do="Back on Receivers, press the grey **flow** button on your receiver's "
           "row.",
        see="The Flow resource for the stream the receiver is subscribed to. Its "
            "media type is `video/jxsv` — JPEG XS — at the resolution and rate "
            "you chose.",
        detail="The **flow** button is only offered once a receiver is subscribed "
               "to a sender; before that it is greyed out, because there is no "
               "flow to show. Seeing it enabled is itself evidence the "
               "subscription exists.\n\n"
               "```json\n" + _excerpt(flow_text) + "\n```",
        internals="This is the **IS-04 Flow** resource as published by the node "
                  "and cached from the registry. Its fields are the same ones the "
                  "CCF matched against when it offered you this constraint set — "
                  "which is why the values here are exactly what you chose.",
        specs=(("AMWA IS-04 — the Flow resource", "https://specs.amwa.tv/"),),
        sources=(
            ("nmos/node/flow_caps.py", "converting a flow into CCF capabilities — "
                                       "the link between this resource and the "
                                       "matching you saw earlier"),
            ("nmos/node/publish.py", "how the node publishes its resources"),
        ),
    )


def _excerpt(text: str, keys: tuple[str, ...] = (
        "media_type", "frame_width", "frame_height", "grain_rate",
        "bit_depth", "profile", "level", "colorspace")) -> str:
    """Pull the interesting fields out of a rendered JSON blob."""
    import re
    flat = re.sub(r"\s+", " ", text)
    found: list[str] = []
    for key in keys:
        match = re.search(rf'"{key}"\s*:\s*(\{{[^}}]*\}}|"[^"]*"|[^,}}\s]+)', flat)
        if match:
            found.append(f'  "{key}": {match.group(1)}')
    return "{\n" + ",\n".join(found) + "\n}" if found else "(not found)"


# ---------------------------------------------------------------------------
# 13. tutorial-security
# ---------------------------------------------------------------------------

def _tutorial_security(session: ControllerSession) -> None:
    """Teach: how TLS and OAuth 2.0 decide what a Controller may do.

    Read-only. Nothing here activates a sender or changes a device — the whole
    subject is *permission*, and the interesting evidence is already on screen
    by the time the tutorial starts.

    Requires a Config C rig (mTLS + OAuth 2.0):

        ./start-fake-as.sh &
        ./start-registry.sh 2 &
        ./start-node1.sh XYZ-SNX00000 9443 XYZ-SNX00000 8444 --rap=2

    A second node makes the last lesson far better: tokens are scoped per
    device, so a node the token does not cover shows the refusal *and its
    reason* in the interface. With one node the tutorial still runs and says
    that the comparison was unavailable, rather than inventing it.
    """
    # The sign-in lesson is taught first, on the page signing in lands you on,
    # so its screenshot is the one a reader would be looking at. Devices are
    # only readable from a list page, so that read comes after.
    session.teach(
        "Two gates, not one",
        do="Open the Controller and sign in. You are asked for a password, and "
           "then — before any device appears — the browser leaves the "
           "Controller entirely for a second sign-in page belonging to the "
           "Authorization Server. Sign in there as the operator.",
        see="Two different forms on two different origins. The first is the "
            "Controller's own gate and only proves you may use *this* "
            "Controller. The second issues the token that decides what the "
            "Controller may do *to the devices* on your behalf.",
        detail="Look at the address bar during the second step: the host "
               "changes. That is the point — your operator password is typed "
               "into the Authorization Server, never into the Controller, so "
               "the Controller never sees it.",
        internals="The Controller does not know where the Authorization "
                  "Server's endpoints are; it asks. It fetches the metadata "
                  "document from a well-known URL and reads "
                  "``authorization_endpoint``, ``token_endpoint`` and "
                  "``jwks_uri`` out of it. IS-10 requires this and forbids the "
                  "shortcut: clients \"MUST NOT assume that every Authorization "
                  "Server instance on a network uses the same endpoint "
                  "locations\". It is what lets the same Controller drive "
                  "Keycloak, ORY Hydra, or the small test server this rig runs, "
                  "none of which agree on URL layout. The browser then comes "
                  "back with a single-use code, which the Controller exchanges "
                  "for a token over its own connection — the token never "
                  "travels through the browser.",
        specs=(
            ("AMWA IS-10 — Authorization", "https://specs.amwa.tv/is-10/"),
            ("RFC 8414 — OAuth 2.0 Authorization Server Metadata",
             "https://www.rfc-editor.org/rfc/rfc8414"),
            ("RFC 6749 §4.1 — Authorization Code Grant",
             "https://www.rfc-editor.org/rfc/rfc6749#section-4.1"),
        ),
        sources=(
            ("nmos/controller/oauth2.py", "endpoint discovery, the code "
                                          "exchange, and RFC 8414 §3.3 issuer "
                                          "validation"),
            ("nmos/controller/app.py", "the two-stage gate: the password "
                                       "session, then the redirect to the "
                                       "Authorization Server and back"),
            ("fake-as/ipmx_fake_as.py", "the Authorization Server this rig "
                                        "runs — the same one the TR-10-SEC "
                                        "certification suite validates against"),
        ),
    )

    session.open_receivers()
    devices = session.read_devices()
    covered = [d for d in devices if not d.inaccessible]
    refused = [d for d in devices if d.inaccessible]

    session.teach(
        "Devices are identified by certificate, not by address",
        do="Choose **Receivers** in the navigation bar, then look at the "
           "padlock beside each node's serial number and hover it.",
        see="Every control endpoint is HTTPS, and the tooltip names the "
            "identity the certificate presented. The Controller reached each "
            "node by that name rather than by an IP address.",
        state={f"{d.serial} — transport": (
            "HTTPS throughout" if d.tls_secure
            else "at least one control over plain HTTP") for d in devices}
        | {f"{d.serial} — address": d.address for d in devices},
        detail="This is why the rig needs hosts-file entries. A certificate "
               "carries DNS names, and TLS verification compares the name you "
               "asked for against them. Connect to the same node by "
               "``127.0.0.1`` and verification fails — not because the "
               "certificate is wrong, but because an IP address matches no DNS "
               "name in it.",
        internals="Verification here is mutual. The node presents a server "
                  "certificate the Controller checks against its trusted "
                  "roots, and the Controller presents a *client* certificate "
                  "the node checks in return — Configuration C in TR-10-SEC, "
                  "``RAAM=2``. Under OAuth 2.0 the two are linked: the token's "
                  "``client_id`` must match the identity in the client "
                  "certificate, so a stolen token is useless without the "
                  "matching private key.",
        specs=(
            ("TR-10-SEC — NMOS With Control Plane Security", ""),
            ("RFC 6125 — Service Identity in TLS",
             "https://www.rfc-editor.org/rfc/rfc6125"),
        ),
        sources=(
            ("nmos/api/tr10_tls.py", "the TLS restrictions every listener and "
                                     "client context is built with"),
            ("nmos/cert_check.py", "chain, SAN and key verification at startup"),
            ("nmos/node/security_tags.py", "NAP / RAP / RAAM / OAIM / TCT — the "
                                           "five tags a node publishes to "
                                           "declare the policy it is running"),
        ),
    )

    reasons = {}
    for device in refused:
        reasons[f"{device.serial} — refused because"] = (
            device.access_reason or "(no reason given)")

    if refused:
        see = (
            f"{len(covered)} node(s) show a green circle and "
            f"{len(refused)} show a refusal. Hover the refused one: the "
            f"Controller states plainly that its token does not cover that "
            f"device, and lists the audience entries the token does carry.")
        detail = (
            "Nothing was tried and rejected here. The Controller compared the "
            "token it holds against the device it is looking at and predicted "
            "the refusal, so the interface can grey the controls out before "
            "you click rather than failing afterwards.")
    else:
        see = (
            f"All {len(covered)} node(s) show a green circle: the token covers "
            f"every device on screen, so every control is live.")
        detail = (
            "With a single node there is nothing to contrast against. Start a "
            "second node — ``./start-node2.sh`` — and its device block will "
            "show the refusal and the reason, because the token this rig "
            "issues is scoped to SNX00001 alone.")

    session.teach(
        "What the token actually permits",
        do="Look at the circle beside each serial, and hover it.",
        see=see,
        state={f"{d.serial} — access": f"{d.access}: {d.access_reason}"
               for d in devices} | reasons,
        detail=detail,
        internals="A token is not a general permission to use NMOS. It carries "
                  "an ``aud`` claim listing who it is for, and a ``scope`` "
                  "claim listing which APIs — ``node``, ``connection``, "
                  "``streamcompatibility`` and so on. A node accepts a token "
                  "only if its own serial number appears in ``aud``, so one "
                  "operator's token can be valid at one node and rejected at "
                  "the node beside it. The token also distinguishes *who* from "
                  "*what*: ``sub`` is the operator who signed in, ``azp`` is "
                  "the Controller they signed in through. That is what makes "
                  "an audit trail able to name a person rather than a program.",
        specs=(
            ("AMWA IS-10 — Behaviour: Access Tokens",
             "https://specs.amwa.tv/is-10/"),
            ("RFC 7519 — JSON Web Token",
             "https://www.rfc-editor.org/rfc/rfc7519"),
        ),
        sources=(
            ("nmos/oauth2/__init__.py", "signature validation, JWKS lifecycle, "
                                        "and the three audience-matching modes"),
            ("nmos/controller/handlers.py", "the prediction behind the circle — "
                                            "why a control is greyed out before "
                                            "it is pressed"),
        ),
    )

    session.open_senders()
    rows = session.read_rows()

    session.teach(
        "Everything after this is ordinary NMOS",
        do="Open **Senders**.",
        see=f"{len(rows)} sender(s), listed exactly as they are on a rig with "
            f"no security at all. Security changed who may look, not what "
            f"there is to look at.",
        state={"senders visible": str(len(rows)),
               "nodes on screen": str(len(devices))},
        detail="Worth noticing what did *not* change. IS-04 discovery, IS-05 "
               "connection management and IS-11 capabilities behave "
               "identically; the security layer sits underneath them. That is "
               "deliberate in the specifications — authorization is a "
               "cross-cutting concern, not a different set of APIs.",
        internals="Each request the Controller now makes carries "
                  "``Authorization: Bearer <token>`` and is made over mutual "
                  "TLS. The node validates the signature against the "
                  "Authorization Server's public keys, which it fetched from "
                  "``jwks_uri`` — discovered the same way the Controller "
                  "discovered its endpoints — and caches. If those keys become "
                  "unavailable the node fails closed: it rejects tokens rather "
                  "than accepting unverifiable ones.",
        specs=(
            ("AMWA IS-04 — Discovery & Registration", "https://specs.amwa.tv/is-04/"),
            ("AMWA BCP-003-02 — Authorization Practice", "https://specs.amwa.tv/"),
        ),
        sources=(
            ("nmos/oauth2/jwks_cache.py", "the 23h refresh / 36h invalidate "
                                          "lifecycle, and the fail-closed rule"),
            ("nmos/api/middleware.py", "the single point every authenticated "
                                       "request passes through"),
        ),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, Scenario] = {
    scenario.name: scenario for scenario in (
        Scenario("attach-and-look",
                 "Attach, sign in, and read the sender and receiver lists.",
                 _attach_and_look),
        Scenario("inspect-one-sender",
                 "Take one sender through capabilities to the configure page.",
                 _inspect_one_sender),
        Scenario("selection-guard",
                 "Trip the page's own client-side selection guard.",
                 _selection_guard),
        Scenario("blocked-controls",
                 "Demonstrate all three ways the Controller refuses an action.",
                 _blocked_controls),
        Scenario("route-one-receiver",
                 "Constrain and activate a real route end to end.",
                 _route_one_receiver, mutating=True),
        Scenario("privacy-exclusivity",
                 "Exercise the privacy panel and the exclusive-access reservation.",
                 _privacy_exclusivity, mutating=True),
        Scenario("cross-node-reverse",
                 "Route across two nodes and follow the USB/talk-back companion "
                 "paths.",
                 _cross_node_reverse),
        Scenario("demo-group-route-tb",
                 "Grouped cross-node A/V route with native configs, then "
                 "talk-back audio.",
                 _demo_group_route_tb, mutating=True),
        Scenario("demo-group-route-hevc",
                 "As above, but video on HEVC Main10 at 10-bit / 50 Hz.",
                 partial(_demo_group_route_tb, video=VideoChoice(
                     set_label="H.265 Main10",
                     settings=(("component_depth", "10"),
                               ("grain_rate", '"numerator": 50,')),
                     description="the H.265 Main10 set at 10-bit, 50 Hz",
                 )),
                 mutating=True),
        Scenario("tutorial-jpegxs",
                 "TUTORIAL: activate a video sender and subscribe a receiver "
                 "over JPEG XS.",
                 _tutorial_jpegxs, mutating=True),
        Scenario("tutorial-security",
                 "TUTORIAL: how TLS and OAuth 2.0 decide what a Controller may "
                 "do. Needs a Config C rig; read-only.",
                 _tutorial_security),
        Scenario("session-lost",
                 "Confirm a dead session is reported, not silently followed.",
                 _session_lost),
    )
}
