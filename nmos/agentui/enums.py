# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Enumerations shared by the agent-UI core and its application adapters.

Every constant an agent-UI caller passes in or reads back is an enum member
rather than a bare string. That is not decoration: these values end up in the
run journal, so a typo in a scenario would otherwise produce a plausible-looking
journal entry describing something that never happened.

``StrEnum`` is used throughout so members serialise to JSON as their own value
without a custom encoder, while still comparing equal to the literal string a
selector or template happens to use.
"""

from __future__ import annotations

from enum import StrEnum


class PageId(StrEnum):
    """Identity of a rendered Controller page.

    A page's identity is derived from its URL path (see the adapter's
    ``identify_page``), never from its heading, because headings are
    localisable prose while paths are part of the route table.

    ``UNKNOWN`` exists so an unexpected navigation is *reported* rather than
    crashing the classifier — a scenario that lands somewhere unmapped should
    fail with a journal entry naming the URL, not with a KeyError.
    """

    LOGIN = "login"
    INDEX = "index"
    SENDERS = "senders"
    SENDERS_CAPS = "senders_caps"
    SENDERS_CONFIGURE = "senders_configure"
    RECEIVERS = "receivers"
    RECEIVERS_COMPATIBLE_SENDERS = "receivers_compatible_senders"
    RECEIVERS_VIEW_CAPS = "receivers_view_caps"
    RECEIVERS_CAPS = "receivers_caps"
    RECEIVERS_CONFIGURE = "receivers_configure"
    TRANSPORT_DETAIL = "transport_detail"
    SDP_VIEW = "sdp_view"
    IS11_STATUS = "is11_status"
    MONITOR_DETAIL = "monitor_detail"
    FLOW_DETAIL = "flow_detail"
    SOURCE_DETAIL = "source_detail"
    RESOURCE_DETAIL = "resource_detail"
    DEVICE_DETAIL = "device_detail"
    NODE_DETAIL = "node_detail"
    OAUTH2_SIGNIN = "oauth2_signin"
    """The Authorization Server's own sign-in form.

    The only page in this enum served by a *different* origin than the
    Controller, which is exactly how it is recognised — its path is whatever
    the Authorization Server publishes in its RFC 8414 metadata
    (``/realms/<realm>/authorize`` here, ``/oauth2/auth`` on ORY Hydra,
    ``/protocol/openid-connect/auth`` on Keycloak) and matching on any of
    those would bind the driver to one vendor.
    """
    UNKNOWN = "unknown"


class RowAction(StrEnum):
    """The per-row navigation actions offered on an inspectable list page.

    The member *values* are the href suffixes the server renders, because that
    is the only stable way to locate these controls: the "resource" action's
    visible text is the resource kind (``sender``/``receiver``) and the IS-11
    action's text is ``is-11`` while its path segment is ``is11``. Matching on
    visible text would therefore be both kind-dependent and wrong.

    ``MONITOR`` is deliberately part of this enum even though it lives in the
    status column rather than the ``.row-actions`` button group — from the
    operator's point of view it is one more thing you can click on that row.
    """

    TRANSPORT = "transport"
    FLOW = "flow"
    RESOURCE = "resource"
    DEVICE = "device"
    IS11 = "is11"
    MONITOR = "monitor"


class ToggleAction(StrEnum):
    """The three master toggles on a configure page.

    Values match the server's ``data-action`` attribute verbatim, which is what
    ``controller.js`` itself dispatches on. Sender-side and receiver-side
    activation are separate members because both appear on the receivers
    configure page and act on different resources.
    """

    CONSTRAIN = "constrain"
    ACTIVATE = "activate"
    ACTIVATE_RECEIVERS = "activate_receivers"


class Affordance(StrEnum):
    """What a located control offers the operator right now.

    The distinction between ``ABSENT`` and ``BLOCKED`` is the entire point of
    this enum, and collapsing them would make a gating demo dishonest:

    - ``ABSENT``  — the server did not render the control. Not applicable.
    - ``HIDDEN``  — rendered but not visible; the operator would first have to
                    reveal it (open a dropdown, unhide a note).
    - ``BLOCKED`` — rendered, visible, and deliberately refused by policy. The
                    reason is in the control's ``title``.
    - ``ENABLED`` — the operator can act on it.
    """

    ABSENT = "absent"
    HIDDEN = "hidden"
    BLOCKED = "blocked"
    ENABLED = "enabled"


class ControlKind(StrEnum):
    """The HTML element a control resolved to.

    This is recorded because *how* the server expressed a block is meaningful:
    a ``SPAN`` block is the row-action idiom (a span cannot carry ``disabled``),
    a ``BUTTON`` block is a policy-disabled live control, and the reverse-
    direction links shape-shift between ``BUTTON`` (blocked) and ``ANCHOR``
    (available) depending on whether the group resolves.
    """

    ANCHOR = "anchor"
    BUTTON = "button"
    INPUT = "input"
    SELECT = "select"
    TEXTAREA = "textarea"
    SPAN = "span"
    OTHER = "other"


class Health(StrEnum):
    """BCP-008 overall//per-facet health, as encoded in the ``is-*`` CSS class.

    Values match the class suffix the server renders (``is-healthy`` ->
    ``healthy``) so a snapshot's class set maps to a member without a lookup
    table. ``NOT_USED`` is the grey state for a device that publishes no
    monitor at all — distinct from ``INACTIVE``, which means monitored and idle.
    """

    INACTIVE = "inactive"
    HEALTHY = "healthy"
    PARTIALLY_HEALTHY = "partially-healthy"
    UNHEALTHY = "unhealthy"
    NOT_USED = "not-used"
    UNKNOWN = "unknown"


class DeviceAccess(StrEnum):
    """Whether the Controller is authorised to reach a device.

    Shown on the senders/receivers pages as a small icon beside the node serial —
    a green check-circle when all is well, an amber or red triangle when not, each
    carrying its reason in a ``title``. Worth reading before blaming a failed
    action on the driver: a device the Controller cannot write to will refuse
    everything, and it says so up front.
    """

    AUTHORIZED = "authorized"
    WRITES_BLOCKED = "writes_blocked"
    READS_BLOCKED = "reads_blocked"
    UNKNOWN = "unknown"


class TlsPolicy(StrEnum):
    """How the browser is made to trust the Controller UI's certificate.

    There is deliberately **no** ``INSECURE`` member. Disabling certificate
    verification is not expressible through this API, because a demo that
    silently stopped validating TLS would still look exactly like a passing
    demo. The two real options both keep verification on:

    - ``SAN_HOSTNAME``  — connect by the certificate's DNS SAN so the chain
                          validates cleanly and no browser flag is needed.
    - ``PIN_LEAF_SPKI`` — connect by IP and pin the leaf's SPKI hash, which
                          suppresses errors *only* for that exact key.
    - ``PIN_CHAIN_SPKI``— as above but pinning every cert in the chain, which
                          survives leaf reissue under the same intermediate.
    - ``PLAINTEXT``     — the node was started with ``--nodeDisableTLS``.
    """

    PLAINTEXT = "plaintext"
    SAN_HOSTNAME = "san_hostname"
    PIN_LEAF_SPKI = "pin_leaf_spki"
    PIN_CHAIN_SPKI = "pin_chain_spki"


class WaitSignal(StrEnum):
    """The named condition a verb waited on.

    Recording *which* signal was awaited (rather than just "it worked") is what
    makes a journal auditable: a reader can tell whether an activation was
    confirmed by its result cells or merely by a button changing colour, and
    those are not the same evidence.
    """

    PAGE_LOADED = "page_loaded"
    SELECTION_SUBMITTED = "selection_submitted"
    TOGGLE_STARTED = "toggle_started"
    TOGGLE_FINISHED = "toggle_finished"
    RESULTS_TERMINAL = "results_terminal"
    CAPS_DETAIL_TOGGLED = "caps_detail_toggled"
    RADIO_SELECTED = "radio_selected"
    PARAM_APPLIED = "param_applied"
    PRIVACY_PENDING = "privacy_pending"
    PRIVACY_SETTLED = "privacy_settled"
    LIVE_STATUS_CHANGED = "live_status_changed"
    NONE = "none"


class StepOutcome(StrEnum):
    """How a journal step ended.

    ``BLOCKED`` is a first-class outcome rather than a failure: a scenario that
    demonstrates gating *expects* it, and a run whose blocked steps were
    recorded as errors would misrepresent a successful demo as a broken one.
    """

    OK = "ok"
    BLOCKED = "blocked"
    GUARDED = "guarded"
    FAILED = "failed"


class CorrelationKind(StrEnum):
    """How completely a step could be joined to the node's own debug trace.

    ``SERVER_ONLY`` is expected for the sign-in step: ``login.html`` extends no
    base template and loads no JavaScript, so no client-side UI event is ever
    posted for that click and only the server's own request records exist.
    """

    FULL = "full"
    SERVER_ONLY = "server_only"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class SseVerdict(StrEnum):
    """Whether a live status update was actually observed during the run.

    ``UNCONFIRMED`` exists so a run can say "I waited and saw nothing" without
    that being mistaken for either a pass or a crash. Reporting a live update
    that was never observed is the single easiest way to produce a misleading
    demo, because both server-rendered markers this UI uses are present at page
    load.
    """

    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    NOT_EXERCISED = "not_exercised"
