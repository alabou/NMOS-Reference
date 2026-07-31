# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Every CSS selector the Controller adapter uses, and nothing else.

One module, so that a change to the markup has exactly one place to be reflected.
Session code below never contains a selector string; it asks here.

Each selector is annotated with the template and line it was read from, because
this file is the driver's model of somebody else's markup and the only way to
review it is against the original. Nothing here was guessed.

Three things about this UI shaped the selectors materially:

* **Row actions are located by href suffix, not by text.** The "resource" action's
  visible text is the resource kind (``sender``/``receiver``), and the IS-11
  action reads ``is-11`` while its path segment is ``is11``. Matching on text
  would be kind-dependent and, for IS-11, simply wrong.

* **List-page submit buttons have no id, name, or data attribute.** They are
  reachable only as the primary button inside their form, so they are scoped that
  way rather than by their visible label, which is prose.

* **Selection state lives in hidden inputs whose ``name`` and ``id`` differ.**
  ``receivers.html`` renders ``name="mode" id="selection_mode"``, so the id is
  what to select on.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ...enums import PageId, RowAction, ToggleAction

#: URL prefix for every Controller page.
PREFIX = "/controller"


# ---------------------------------------------------------------------------
# Page identity
# ---------------------------------------------------------------------------
#
# Ordered longest-path-first, because ``/controller/receivers`` is a prefix of
# ``/controller/receivers/caps`` and a shortest-match scan would classify every
# receivers sub-page as the list page.

_PATH_TO_PAGE: tuple[tuple[str, PageId], ...] = (
    (f"{PREFIX}/login", PageId.LOGIN),
    (f"{PREFIX}/senders/caps", PageId.SENDERS_CAPS),
    (f"{PREFIX}/senders/configure", PageId.SENDERS_CONFIGURE),
    (f"{PREFIX}/receivers/compatible-senders", PageId.RECEIVERS_COMPATIBLE_SENDERS),
    (f"{PREFIX}/receivers/view-caps", PageId.RECEIVERS_VIEW_CAPS),
    (f"{PREFIX}/receivers/caps", PageId.RECEIVERS_CAPS),
    (f"{PREFIX}/receivers/configure", PageId.RECEIVERS_CONFIGURE),
    (f"{PREFIX}/senders", PageId.SENDERS),
    (f"{PREFIX}/receivers", PageId.RECEIVERS),
    (f"{PREFIX}/flows/", PageId.FLOW_DETAIL),
    (f"{PREFIX}/sources/", PageId.SOURCE_DETAIL),
    (f"{PREFIX}/devices/", PageId.DEVICE_DETAIL),
    (f"{PREFIX}/nodes/", PageId.NODE_DETAIL),
)

#: Trailing path segments on the ``/{kind}s/{id}/...`` detail routes.
_SUFFIX_TO_PAGE: tuple[tuple[str, PageId], ...] = (
    ("/transport", PageId.TRANSPORT_DETAIL),
    ("/sdp", PageId.SDP_VIEW),
    ("/is11", PageId.IS11_STATUS),
    ("/monitor", PageId.MONITOR_DETAIL),
    ("/flow", PageId.FLOW_DETAIL),
    ("/resource", PageId.RESOURCE_DETAIL),
)


def identify(url: str) -> PageId:
    """Classify a page from its URL path.

    The scheme and host are stripped via :func:`urlsplit` — matching against the
    raw URL would compare ``"http://host/controller/..."`` to ``"/controller"``
    and never match anything. Query strings are ignored: the same page is the same
    page whether or not it carries selection parameters.

    Matching order is deliberate. The per-resource detail suffixes are tested
    first because they are unambiguous, then the path table longest-first so that
    ``/controller/senders`` cannot swallow ``/controller/senders/caps``. The index
    is matched exactly and never by prefix, or it would claim every page.
    """
    path = urlsplit(url).path
    # Collapse a trailing slash so "/controller/" and "/controller" agree, while
    # keeping a bare root intact.
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    if path == PREFIX:
        return PageId.INDEX

    for suffix, page in _SUFFIX_TO_PAGE:
        if path.endswith(suffix):
            return page

    for prefix, page in _PATH_TO_PAGE:
        entry = prefix.rstrip("/")
        if path == entry or path.startswith(entry + "/"):
            return page

    return PageId.UNKNOWN


# ---------------------------------------------------------------------------
# Global chrome -- base.html
# ---------------------------------------------------------------------------

#: The content region on every page that extends base.html. base.html:22.
MAIN = "main"

#: What each step captures as "what was on screen".
#:
#: ``body`` rather than ``main`` because ``login.html`` extends no base template
#: and has no ``<main>`` at all — so a main-only capture would record nothing for
#: the sign-in step, which is exactly the step a reader checks first. The
#: navigation chrome comes along with it, which is honest: it is on screen.
BODY = "body"

#: First heading, used as a page's human name. Spans several levels because the
#: sign-in card uses ``h4`` while content pages use ``h1``.
HEADING = "h1, h2, h3, h4"

#: Row text cells. partials/device_block.html:127-134.
ROW_ROLE = "td.role-name"
ROW_LABEL = "td.sender-label"

#: Navigation links. base.html:26-34 -- class and href only, no ids. Note the
#: sign-out label renders as ``Sign&nbsp;out``, which is why every text
#: comparison goes through the normaliser.
NAV_SENDERS = f'a.nav-link[href="{PREFIX}/senders"]'
NAV_RECEIVERS = f'a.nav-link[href="{PREFIX}/receivers"]'
NAV_SIGN_OUT = f'a.nav-link[href="{PREFIX}/logout"]'

#: Server-rendered notices. Present on most pages with role="alert".
ALERTS = "main .alert"
ALERT_DANGER = "main .alert-danger"

#: Set to "1" by the server when --debug-in-depth is active, which is also what
#: enables the client-side event posting the journal correlates against.
HTML_ROOT = "html"


# ---------------------------------------------------------------------------
# Sign-in -- login.html
# ---------------------------------------------------------------------------
#
# login.html extends no base template and loads no JavaScript at all, so there is
# no console beacon and no client-side trace record for this page. There is also
# no username field: the form is password-only.

LOGIN_PASSWORD = "#password"                       # login.html:43
LOGIN_SUBMIT = 'form[action$="/login"] button[type="submit"]'   # login.html:47


# ---------------------------------------------------------------------------
# Selection pages -- senders.html, receivers.html, receivers_compatible_senders.html
# ---------------------------------------------------------------------------

SENDERS_FORM = "#senders-form"                     # senders.html:15
RECEIVERS_FORM = "#receivers-form"                 # receivers.html:14
COMPATIBLE_SENDERS_FORM = "#compatible-senders-form"   # ...compatible_senders.html:43
CAPS_FORM = "#caps-form"                           # senders_caps.html:178
CONFIGURE_FORM = "#configure-form"

#: Hidden inputs recording what the page will submit. Selected by id because the
#: name differs: receivers.html:17 renders ``name="mode" id="selection_mode"``.
SENDER_IDS = "#sender_ids"                         # senders.html:16
RECEIVER_IDS = "#receiver_ids"                     # receivers.html:16
SELECTION_MODE = "#selection_mode"                 # receivers.html:17

# ---------------------------------------------------------------------------
# Device header -- partials/device_block.html:46-82
# ---------------------------------------------------------------------------
#
# Each device on a list page has a title row carrying its serial number and two
# small status icons. They are the fastest way to see *why* a device might refuse
# everything you ask of it, and both carry their explanation in a ``title``.

DEVICE_BLOCKS = "tbody.device-title-tbody"
DEVICE_SERIAL = ".device-serial"
DEVICE_ADDRESS = ".device-address"
DEVICE_TRANSPORTS = ".device-transports"

#: Applied to the whole device block when reads are blocked.
DEVICE_INACCESSIBLE_CLASS = "device-inaccessible"

#: The padlock: closed when every control on the device is https, open when at
#: least one is plain HTTP.
DEVICE_TLS_SECURE = "i.lock-tls-secure"
DEVICE_TLS_INSECURE = "i.lock-tls-insecure"

#: The authorisation indicator. Green check-circle when the Controller can read and
#: write; a triangle otherwise — red for reads blocked, amber for writes only.
DEVICE_AUTH_OK = "i.device-cap-lock.bi-check-circle-fill"
DEVICE_AUTH_READS_BLOCKED = "i.device-cap-lock.text-danger"
DEVICE_AUTH_WRITES_BLOCKED = "i.device-cap-lock.text-warning"

#: Refresh link on the NMOS detail pages. These pages deliberately do **not**
#: poll, so the view stays a stable snapshot until this is clicked. Located by
#: exact text because several pages carry more than one ``.btn-link``.
REFRESH_LINK = 'main a.btn-link:text-is("Refresh")'


#: Per-resource rows and their controls. partials/device_block.html:120-124.
MEMBER_ROWS = "tr.member-row[data-resource-id]"
MEMBER_CHECKS = "tr.member-row input.member-check"

#: The group's name cell. device_block.html:103.
GROUP_NAME = "td.group-name"

#: Whole-group radio. device_block.html:99-100 -- data-ids is a CSV of members.
GROUP_RADIOS = 'input[type="radio"][name="_group"][data-ids]'


#: Every ``tbody`` of the cross-device table, in document order.
#:
#: The list pages use ONE table for all devices so the column widths line up, and
#: each device contributes a ``device-title-tbody`` followed by one or more sibling
#: ``group-tbody`` elements (``partials/device_block.html:1-10``). The member rows
#: are therefore **not** inside the device header — they follow it. Walking these in
#: order and remembering the last serial seen is what associates a row with its node.
TABLE_BODIES = "table.devices-table > tbody"

#: Class marking a device's header body, and a member-carrying body.
DEVICE_TITLE_CLASS = "device-title-tbody"
GROUP_BODY_CLASS = "group-tbody"


def table_body_nth(index: int) -> str:
    """The n-th ``tbody`` of the devices table, scoped positionally."""
    return f"{TABLE_BODIES} >> nth={index}"


def device_block_nth(index: int) -> str:
    """The n-th device block, scoped positionally.

    Positional rather than by serial because a serial is what we are trying to
    *read* — and because two devices can legitimately share one. Playwright's
    ``>> nth=`` chaining is what makes a descendant read stay inside one block;
    a plain CSS descendant selector resolves against the whole page and quietly
    returns the first match, which made every device report the first one's serial.
    """
    return f"{DEVICE_BLOCKS} >> nth={index}"


def within(scope: str, child: str) -> str:
    """A descendant read scoped to a positionally-selected ancestor."""
    return f"{scope} >> {child}"


def member_row(resource_id: str) -> str:
    """The row for one resource."""
    return f'tr.member-row[data-resource-id="{resource_id}"]'


def member_check(resource_id: str) -> str:
    """The selection checkbox for one resource. device_block.html:123-124."""
    return f'input.member-check[data-ids="{resource_id}"]'


def row_action(resource_id: str, action: RowAction) -> str:
    """A row's navigation action, scoped to the row and matched by href suffix.

    The enabled form is an ``<a href>``; when the server considers the action
    inapplicable it renders a ``<span class="btn ... disabled">`` instead, which
    :func:`row_action_blocked` locates. device_block.html:139-158.
    """
    row = member_row(resource_id)
    if action is RowAction.DEVICE:
        # Points at the owning device, not at this resource. device_block.html:154.
        return f'{row} a[href^="{PREFIX}/devices/"]'
    if action is RowAction.MONITOR:
        # Lives in the status column wrapping the badge, not in .row-actions.
        # device_block.html:167-169.
        return f'{row} a.status-badge-link[href$="/monitor"]'
    return f'{row} .row-actions a[href$="/{action.value}"]'


def row_action_blocked(resource_id: str, action: RowAction) -> str:
    """The refused form of a row action.

    A ``<span>`` cannot carry ``disabled``, so the class is the only signal the
    server can use here. device_block.html:147-148.
    """
    return f'{member_row(resource_id)} .row-actions span.btn.disabled'


#: The primary submit of a selection form. These buttons carry no id, name, or
#: data attribute (receivers.html:54, senders.html:53, ...caps:262), so they are
#: reachable only as the primary button within their form.
def form_submit(form: str) -> str:
    """The primary submit button inside a form."""
    return f'{form} button[type="submit"].btn-primary'


def form_secondary_submit(form: str) -> str:
    """A secondary submit that overrides the action via ``formaction``."""
    return f"{form} button[formaction]"


#: Status indicators. device_block.html:170-185. Health is carried in ``is-*``
#: classes; both the badge text and those classes are rewritten by the status
#: stream, which is why a live-update check compares against a page-load baseline.
def status_badge(resource_id: str) -> str:
    return f'.status-badge[data-resource-id="{resource_id}"][data-kind="overall"]'


def status_dot(resource_id: str, kind: str) -> str:
    return f'.status-dot[data-resource-id="{resource_id}"][data-kind="{kind}"]'


#: The four per-facet dots. device_block.html:183-185.
STATUS_DOT_KINDS = ("link", "sync", "conn", "media")

#: Prefix identifying a health class, for baseline comparison.
HEALTH_CLASS_PREFIX = "is-"


# ---------------------------------------------------------------------------
# Capabilities pages -- senders_caps.html, receivers_caps.html
# ---------------------------------------------------------------------------

CAPS_ROWS = "tr.caps-row[data-caps-row]"           # senders_caps.html:22


def caps_row(resource_id: str, index: int) -> str:
    """One constraint-set row. The key is ``"<resource-id>-<cs-index>"``."""
    return f'tr.caps-row[data-caps-row="{resource_id}-{index}"]'


def caps_row_radio(resource_id: str, index: int) -> str:
    """A constraint-set row's radio.

    Clicked **directly** rather than via the row, because the row-level click
    handler returns early for ``INPUT`` targets — so targeting the input selects
    the set without also toggling its detail panel. senders_caps.html:25.
    """
    return f'{caps_row(resource_id, index)} input[type="radio"]'


def caps_row_cell(resource_id: str, index: int) -> str:
    """A non-input cell of a constraint-set row.

    Clicking here expands the detail panel — and also selects the row's radio,
    because the handler does both. That double effect is journaled rather than
    hidden. senders_caps.html:29.
    """
    return f'{caps_row(resource_id, index)} td.caps-set-cell'


#: Cells of a constraint-set row, in the order the template emits them
#: (``senders_caps.html:22-34`` and ``receivers_caps.html`` are identical):
#: radio, disclosure, label, media type, format, layer, preference.
#:
#: ``cs-label`` is the *name* — and is also where the green flow-match class lands.
#: An earlier version read the disclosure cell instead and saw only "▸ #0", losing
#: the name entirely.
CAPS_CELL_LABEL = "td.cs-label"
CAPS_CELL_MEDIA_TYPE = "td.caps-summary"
CAPS_CELL_FORMAT = "td:nth-child(5)"
CAPS_CELL_LAYER = "td:nth-child(6)"

#: Preference. The highest-preference set is pre-selected by default
#: (``receivers_caps.html:154``), and a preference of 100 is what marks a set as
#: **native** — there is no explicit "native" marker anywhere in the markup.
CAPS_CELL_PREFERENCE = "td:nth-child(7)"

#: Preference value that identifies a native constraint set.
NATIVE_PREFERENCE = 100


def caps_details(resource_id: str, index: int) -> str:
    """The sibling detail row, revealed by removing its ``hidden`` attribute.

    That attribute is the deterministic expand signal. senders_caps.html:40.
    """
    return f'tr[data-caps-details-for="{resource_id}-{index}"]'


#: The SDP link on a transport-detail page.
#:
#: The SDP is one click deeper than the transport view, and rendered only when the
#: resource actually has one (``transport_detail.html:16-19``, guarded by
#: ``has_sdp``). Absence therefore means "no SDP for this resource" rather than a
#: missing feature.
SDP_LINK = 'main a[href$="/sdp"]'

#: Applied to a constraint-set label when it matches the sender's current flow.
FLOW_MATCH_CLASS = "flow-match"


# ---------------------------------------------------------------------------
# Configure pages -- senders_configure.html, receivers_configure.html
# ---------------------------------------------------------------------------

def toggle(action: ToggleAction) -> str:
    """A master toggle.

    Blocked by policy with a real ``disabled`` attribute and the reason in
    ``title`` (the server's ``_block_reason``). Sender-side and receiver-side
    activation are distinct actions and both appear on the receivers page.
    receivers_configure.html:71-96.
    """
    return f'button.btn-toggle[data-action="{action.value}"]'


#: Discards local edits by reloading the page. receivers_configure.html:100.
RESET_BUTTON = "button.btn-reset"

#: Added to a toggle for the whole duration of its sequential per-resource
#: request loop, and removed when the loop ends. controller.js:852-893. This is
#: the correct completion signal for an action.
WORKING_CLASS = "is-working"

#: Added to nav-form submit buttons on submit. Self-heals after four seconds
#: (controller.js:164), so it is a tie-breaker for diagnostics only and never a
#: success criterion.
SUBMITTING_CLASS = "is-submitting"

#: Result cells. receivers_configure.html:306-321.
RESULT_CELLS_SENDER = ".result-cell[data-result-for]"
RESULT_CELLS_RECEIVER = ".result-cell[data-result-for-receiver]"

#: Terminal result classes. An error is a legitimate ending — the action finished
#: and the operator can see what happened; only ``pending`` means keep waiting.
RESULT_PENDING = "pending"
RESULT_TERMINAL = ("ok", "error")


def result_cell(resource_id: str, *, receiver_side: bool = False) -> str:
    attribute = "data-result-for-receiver" if receiver_side else "data-result-for"
    return f'.result-cell[{attribute}="{resource_id}"]'


#: Parameter widgets, addressed by the (sender, parameter, part) triple that
#: uniquely identifies one editable value. receivers_configure.html:344-399.
PARAM_INPUTS = ".param-input[data-sender-id][data-param-urn]"


def param_widget(sender_id: str, urn: str, part: str) -> str:
    return (f'.param-input[data-sender-id="{sender_id}"]'
            f'[data-param-urn="{urn}"][data-cs-part="{part}"]')


#: The numeric mirror beside a range slider. Its update is the ``input`` handler
#: landing, which is the deterministic signal that a slider move took effect.
PARAM_RANGE_MIRROR = ".param-range-value"

#: A single pinned value rather than a chooser.
PARAM_SINGLE_CLASS = "param-single"


def reverse_link(group: str) -> str:
    """A reverse-direction link, which changes tag with its state.

    ``<button disabled>`` when the group cannot be resolved, ``<a href>`` when it
    can — both carrying ``data-reverse-group``, so one selector finds either and
    the classifier follows the tag. receivers_configure.html:452-469.
    """
    return f'[data-reverse-group="{group}"]'


REVERSE_LINKS = "[data-reverse-group]"


# ---------------------------------------------------------------------------
# Privacy panel -- partials/privacy_section.html
# ---------------------------------------------------------------------------

PRIVACY_PANEL = ".privacy-panel"
PRIVACY_FORM = "#privacy-form"
PRIVACY_PROTOCOL = "#privacy-protocol"
PRIVACY_MODE = "#privacy-mode"
PRIVACY_CURVE = "#privacy-curve"
PRIVACY_EXCLUSIVITY = "#privacy-exclusivity"

#: The wrapping label of the exclusivity switch.
#:
#: A Bootstrap ``custom-switch`` splits one logical control across two
#: elements: the ``<input>`` holds ``checked``/``disabled`` but is made
#: invisible by the styling, while this label is what the operator sees and
#: clicks -- and is where the server puts the ``title`` explaining a lock.
#: privacy_section.html:219-226.
PRIVACY_EXCLUSIVITY_LABEL = ".privacy-exclusivity label.custom-switch"

#: Read-only indicators, the one part of this UI with ``data-role`` hooks.
PRIVACY_PEP_INDICATOR = '[data-role="privacy-pep-indicator"]'
PRIVACY_LOCK_INDICATOR = '[data-role="privacy-lock-indicator"]'
PRIVACY_RESERVATION_STATUS = '[data-role="privacy-reservation-status"]'
PRIVACY_LOCKED_NOTE = '[data-role="privacy-locked-note"]'

#: Panel state classes driven by the acquire/release flow. controller.js:1604-1641.
PRIVACY_PENDING_CLASS = "is-pending"
PRIVACY_RESERVED_CLASS = "is-reserved"


# ---------------------------------------------------------------------------
# Client-side instrumentation the journal correlates against
# ---------------------------------------------------------------------------

#: Emitted once per document by controller.js:182. Its absence means the script
#: never ran, which would make every DOM-based wait signal in this driver
#: fiction — so it is checked as a run precondition rather than assumed.
JS_BEACON_PREFIX = "[nmos-controller.js v"

#: Path the client posts UI events to when tracing is enabled. Observed passively
#: for its trace id; never called by the driver.
CLIENT_EVENT_PATH = f"{PREFIX}/api/debug/client-event"
