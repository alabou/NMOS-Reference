# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The application seam.

Everything above this line is generic: the capability whitelist, the wait
semantics, the affordance rules, the journal. Everything an application
contributes goes through :class:`AppAdapter`.

Adding a second application means implementing this protocol plus a selector
module and a session facade. Nothing in ``core/`` or ``driver/`` mentions the
Controller, NMOS, or any product concept, which is what makes that true rather
than aspirational.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..enums import PageId, TlsPolicy
from .surface import Surface


@dataclass(frozen=True, slots=True)
class Credentials:
    """A secret needed to sign in.

    Held separately from :class:`Target` rather than as a field on it, and with a
    redacting ``__repr__``, because :class:`Target` is written verbatim into the
    run manifest. A credential that lives in the same object as the provenance
    data is one refactor away from being serialised into an artifact file.
    """

    password: str

    operator_username: str = ""
    """End-user account for the Authorization Server's sign-in form.

    Only used when the rig runs with OAuth 2.0: the Controller's own gate takes
    a password with no username, but the AS authenticates a *person*. Empty on
    rigs with no Authorization Server."""

    operator_password: str = ""
    """Password for :attr:`operator_username`.

    Defaults to :attr:`password` when unset — the reference rig gives the
    Controller admin gate and the AS operator the same secret, so a tutorial
    operator types one password rather than two."""

    @property
    def oauth2_password(self) -> str:
        """The secret to type into the Authorization Server's form."""
        return self.operator_password or self.password

    def __repr__(self) -> str:
        # Defeats the usual ways a secret escapes: f-strings, logging calls,
        # exception rendering, and pytest assertion output. The username is a
        # non-secret identifier and is shown, because a failed AS sign-in is
        # otherwise impossible to diagnose from the journal.
        return f"Credentials(operator_username={self.operator_username!r}, password=***)"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class Target:
    """A discovered application endpoint, safe to serialise.

    ``provenance`` carries how the target was found — process id, the command
    line it was read from — so a journal reader can confirm which rig produced
    the run. Discovery is responsible for excluding secrets from it.
    """

    app: str
    scheme: str
    host: str
    port: int
    base_path: str = ""
    tls: TlsPolicy = TlsPolicy.PLAINTEXT
    spki_pins: tuple[str, ...] = ()
    ca_paths: tuple[str, ...] = ()
    provenance: Mapping[str, str] = field(default_factory=dict)

    @property
    def origin(self) -> str:
        """Scheme, host and port, with no path."""
        return f"{self.scheme}://{self.host}:{self.port}"

    def to_json(self) -> dict[str, object]:
        """Render for the manifest. Contains no secrets by construction."""
        return {
            "app": self.app,
            "origin": self.origin,
            "base_path": self.base_path,
            "tls": self.tls,
            "spki_pins": list(self.spki_pins),
            "ca_paths": list(self.ca_paths),
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class Precondition:
    """A run-level health check performed once, after the entry navigation.

    The canonical example is confirming the application's own JavaScript
    announced itself. If it did not run — a stale cached asset, a blocked
    script — then every DOM-mutation wait signal the driver relies on is
    fiction, and the run should fail saying *that* rather than time out later
    with something misleading.

    ``check`` returns ``None`` on success or a human-readable reason on failure,
    rather than raising, so the caller chooses which error type to raise and the
    reason lands in the journal either way.
    """

    name: str
    check: Callable[[Surface], str | None]


@runtime_checkable
class AppAdapter(Protocol):
    """What an application must provide to be driven.

    Note what is *not* here. There is no hook for issuing a request, no hook for
    reading server state, and no hook for navigating to a constructed URL beyond
    the single entry point. An adapter cannot widen the driver's powers; it can
    only describe its own application in terms the core already permits.
    """

    #: Short identifier used in artifact paths and the manifest.
    name: str

    #: Selector for the region whose visible text is captured as step state.
    main_selector: str

    def discover(self) -> Target:
        """Locate a running instance to attach to.

        Attaching rather than launching is deliberate: the application's own
        start-up scripts remain the single launch contract, and the
        configuration a run exercises is whichever one the operator chose to
        start.
        """
        ...

    def entry_url(self, target: Target) -> str:
        """The one URL the driver is allowed to navigate to directly.

        Equivalent to a person typing an address. Everything after this must be
        reached by clicking.
        """
        ...

    def identify_page(self, url: str) -> PageId:
        """Classify a page from its URL.

        URL-based on purpose: paths are part of the route table, whereas
        headings are prose that can be reworded without any behavioural change.
        """
        ...

    def authenticate(self, surface: Surface, credentials: Credentials) -> None:
        """Sign in through the application's own login UI.

        Implemented with the same verbs a scenario uses — fill the field, click
        the button — so the sign-in step is itself an audited, journaled
        interaction rather than a privileged short cut.
        """
        ...

    def preconditions(self) -> tuple[Precondition, ...]:
        """Health checks to run once the first authenticated page is loaded."""
        ...
