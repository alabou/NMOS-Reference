# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Agent-driven control of the embedded NMOS Controller UI.

This package lets an agent operate the Controller through a real browser with
exactly the information and exactly the control a signed-in operator has, and
writes a screenshot-and-text journal of every step it takes.

Why a browser rather than an API
--------------------------------
A dedicated agent API would be easier to build and would also be worthless for
the purpose. It would hand the caller the server's own data structures — so the
agent would "know" things the UI deliberately hides — and let it act directly on
resources, bypassing the per-control gating and inaccessibility logic that is the
most interesting thing about this UI. It would also be a second implementation of
the request paths ``handlers.py`` already owns, drifting apart from the first one
the moment either changed.

Driving the rendered page instead means the thing being demonstrated is the thing
that ships. No server-side code changes, and nothing to keep in sync.

Why the discipline is structural
--------------------------------
A browser automation library will happily let a caller navigate straight to a
URL, execute arbitrary JavaScript, or call the JSON API behind the UI's back —
which would quietly reintroduce the very agent API this design rejects. So
user-fidelity is not left to convention:

* :class:`~nmos.agentui.core.surface.Surface` is a capability whitelist with no
  ``goto``, ``evaluate``, ``request``, or ``route``, and session code is typed
  against it — so cheating is a type error at author time.
* Attribute reads are restricted to an allowlist, enforced both when a snapshot
  is built and when it is read.
* Runtime invariants fail the run on a second browser page, a navigation no step
  claimed, or any HTTP the driver itself issued.

Optional by construction
------------------------
The node runtime never imports this package, and playwright is confined to
``nmos.agentui.driver``. A checkout without the optional extra can import
everything here; only launching a browser requires it.
"""

from __future__ import annotations

from .enums import (
    Affordance,
    ControlKind,
    CorrelationKind,
    Health,
    PageId,
    RowAction,
    SseVerdict,
    StepOutcome,
    TlsPolicy,
    ToggleAction,
    WaitSignal,
)
from .errors import (
    ActionFailed,
    AdminPasswordMissing,
    AgentUiError,
    AmbiguousTarget,
    BlockedControl,
    ControlAbsent,
    ControlHidden,
    ControlNotAvailable,
    ControllerJsNotLoaded,
    ControllerNotEnabled,
    DependencyMissing,
    DisallowedAttribute,
    FidelityViolation,
    LiveUpdateNotObserved,
    LoginRejected,
    NodeAmbiguous,
    NodeNotFound,
    NoSuchOption,
    OAuth2NotSupported,
    PageModelMismatch,
    SelectionGuard,
    SessionLost,
    TargetUnreachable,
    TlsPinError,
    WaitTimeout,
)

__all__ = [
    # Enums
    "Affordance",
    "ControlKind",
    "CorrelationKind",
    "Health",
    "PageId",
    "RowAction",
    "SseVerdict",
    "StepOutcome",
    "TlsPolicy",
    "ToggleAction",
    "WaitSignal",
    # Errors
    "ActionFailed",
    "AdminPasswordMissing",
    "AgentUiError",
    "AmbiguousTarget",
    "BlockedControl",
    "ControlAbsent",
    "ControlHidden",
    "ControlNotAvailable",
    "ControllerJsNotLoaded",
    "ControllerNotEnabled",
    "DependencyMissing",
    "DisallowedAttribute",
    "FidelityViolation",
    "LiveUpdateNotObserved",
    "LoginRejected",
    "NodeAmbiguous",
    "NodeNotFound",
    "NoSuchOption",
    "OAuth2NotSupported",
    "PageModelMismatch",
    "SelectionGuard",
    "SessionLost",
    "TargetUnreachable",
    "TlsPinError",
    "WaitTimeout",
]
