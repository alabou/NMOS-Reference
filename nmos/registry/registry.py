# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The Registry facade and the per-interface security snapshots.

``Registry`` is the single object the handlers reach for. It owns the store
and the subscription manager and provides the one operation that must never be
split across them: applying a change and publishing the resulting grains in
the same synchronous step.

Interface security
------------------
The Registration and Query interfaces are separately configured and
deliberately asymmetric — see the package docstring for the TR-10-SEC citation
that makes OAuth 2.0 forbidden on Registration. ``InterfaceSecurity`` captures
one interface's configuration.

It exists in this shape because the registry reuses the Node's middleware
verbatim rather than forking it. ``client_auth_middleware`` and
``check_oauth2`` (``nmos/api/middleware.py``) read their configuration off
``request.app["node"]`` using ``getattr`` with defaults — they are duck-typed
against whatever that key holds, not typed against ``Node``. Publishing an
``InterfaceSecurity`` under that key therefore gets the registry the Node's
exact, already-tested enforcement semantics: mTLS required on state-changing
verbs only, read-only verbs passing through, OAuth 2.0 token validation with
audience and client-certificate binding.

The alternative — a second copy of that logic specialised for the registry —
would be two implementations of the same security rule that could drift apart.
That is the failure mode worth avoiding here.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nmos.node.security_tags import RAP
from nmos.registry.metrics import Event, RegistryMetrics
from nmos.registry.store import RegistryStore
from nmos.registry.types import (
    Body,
    RegistrationResult,
    ResourceEvent,
    ResourceType,
    TaiCursor,
)

if TYPE_CHECKING:
    from nmos.registry.subscriptions import SubscriptionManager

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-interface security
# ---------------------------------------------------------------------------

@dataclass
class InterfaceSecurity:
    """Configuration of one listener, in the shape the shared middleware reads.

    Every attribute here exists because ``nmos/api/middleware.py`` looks it up
    by name on ``app["node"]``. Renaming one silently disables the check that
    reads it, so treat the names as an interface contract rather than as
    internal detail:

    ``client_auth_required``  read by ``client_auth_middleware`` -- when true,
        state-changing verbs require a verified TLS client certificate.
    ``oauth2``                read by ``check_oauth2`` -- when false the whole
        decorator is a pass-through. Always false on Registration.
    ``oauth2_keys``           the JWKS used to validate bearer tokens. Mutated
        in place by the JWKSCache callback, which is why this dataclass is not
        frozen.
    ``serial_number``         the BCP-002-02 instance identifier, matched
        against the token's ``aud``.
    ``tls_server_cert_names`` CN/SAN identities of our own server
        certificate, the other half of the ``aud`` check.
    ``use_serial_number_in_aud`` selects the TR-10-SEC OAIM mode.
    ``client_credentials_only``  restricts accepted grant types.
    ``exclusive_session``     the Node Reservation session. A registry has no
        such concept; None makes the reservation check a no-op.
    """

    client_auth_required: bool = False
    oauth2: bool = False
    serial_number: str = ""
    tls_server_cert_names: list[str] = field(default_factory=list)
    use_serial_number_in_aud: bool = True
    client_credentials_only: bool = False
    exclusive_session: None = None
    oauth2_keys: Any = None

    def rap_for(self, *, tls: bool) -> RAP:
        """Classify this interface against the TR-10-SEC RAP enumeration.

        Only meaningful for the Registration interface, whose three permitted
        modes are exactly RAP 0/1/2 (§"Registry Access Policy"): plain HTTP,
        server-authenticated TLS, and mutual TLS.

        A method rather than a property because whether TLS is active belongs
        to the *listener*, not to this snapshot. Inferring it from
        ``client_auth_required`` alone would misreport a server-TLS
        deployment as plain HTTP, which is the difference between RAP 1 and
        RAP 0 — a compliance claim, not a cosmetic label.
        """
        if not tls:
            return RAP.UNRESTRICTED_HTTP
        if self.client_auth_required:
            return RAP.RESTRICTED_MTLS
        return RAP.UNRESTRICTED_HTTPS


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class Registry:
    """Everything the registry knows, and the operations that change it.

    Args:
        store: The resource store.
        query_id: UUID identifying this Query API instance. It becomes the
            ``source_id`` of every grain -- ``Behaviour - Querying.md:37``:
            "the ``source_id`` in each message identifies the Query API
            instance".
    """

    __slots__ = ("_store", "query_id", "_subscriptions", "_metrics")

    def __init__(
        self,
        store: RegistryStore,
        *,
        query_id: str,
        metrics: RegistryMetrics | None = None,
    ) -> None:
        self._store = store
        self.query_id = query_id
        self._subscriptions: SubscriptionManager | None = None
        # Always on, standalone included. The Query and subscription paths are
        # the two the distributed backend never sees, so if their samples only
        # existed in distributed mode the buffer could never answer "is this
        # cost ours or etcd's?" -- which is the question it exists for.
        self._metrics = metrics if metrics is not None else RegistryMetrics()

    @property
    def metrics(self) -> RegistryMetrics:
        """The trace buffer shared by the Query, subscription and etcd paths."""
        return self._metrics

    # -----------------------------------------------------------------------
    # The store
    # -----------------------------------------------------------------------

    @property
    def store(self) -> RegistryStore:
        """The resource store backing every read.

        A property rather than a plain attribute so the whole store can be
        replaced in one assignment -- see ``swap_store``. Read-only from the
        outside: the three call sites that reach for it
        (``handlers_query``, ``subscriptions``) only ever read.
        """
        return self._store

    def swap_store(self, store: RegistryStore) -> RegistryStore:
        """Replace the store atomically, returning the old one.

        Needed for the distributed compaction path: when etcd discards the
        history a watch was following, the local view has to be rebuilt from a
        fresh snapshot. That snapshot is built *off to the side* and installed
        here in a single assignment, so no reader ever observes an empty or
        half-loaded registry -- Query keeps serving the previous view right up
        to the swap.

        Deliberately not used in standalone mode, where the store is created
        once and lives for the life of the process.
        """
        previous = self._store
        self._store = store
        return previous

    # -----------------------------------------------------------------------
    # Subscription manager wiring
    # -----------------------------------------------------------------------

    @property
    def subscriptions(self) -> SubscriptionManager:
        """The subscription manager.

        Set once at startup by ``attach_subscriptions``. Accessing it before
        then is a wiring bug, not a runtime condition, so it raises rather
        than returning a null object that would silently swallow every grain.
        """
        if self._subscriptions is None:
            raise RuntimeError(
                "subscription manager not attached; "
                "call Registry.attach_subscriptions() during startup",
            )
        return self._subscriptions

    def attach_subscriptions(self, manager: SubscriptionManager) -> None:
        self._subscriptions = manager

    # -----------------------------------------------------------------------
    # Mutations -- these are the only paths that may change resource state
    # -----------------------------------------------------------------------

    def register(
        self, resource_type: ResourceType, body: Body,
    ) -> RegistrationResult:
        """Apply a registration and publish the resulting events.

        ``body`` has already been schema-validated by the Registration API
        handler; the decoded object is not carried here because nothing below
        this point reads it, and ``body.text`` is what gets served back.
        """
        result = self.store.insert_or_update(resource_type, body)
        if result.ok:
            self._publish(result.events)
        return result

    def unregister(
        self, resource_type: ResourceType, resource_id: str,
    ) -> bool:
        """Delete a resource and its descendants. False if it was not found."""
        events = self.store.delete(resource_type, resource_id)
        if events is None:
            return False
        self._publish(events)
        return True

    def collect_garbage(self) -> int:
        """Run one garbage-collection pass. Returns resources collected."""
        events = self.store.collect_garbage()
        self._publish(events)
        return len(events)

    def publish(self, events: list[ResourceEvent]) -> None:
        """Queue grains for changes applied by something other than this class.

        The distributed backend mutates the store directly, because in that
        mode the authoritative decision was made by etcd and the local store is
        a read model catching up. It still has to publish, and it must do so
        through the same path as everything else so that a subscriber cannot
        tell where a change originated.

        Same contract as every mutation here: synchronous, non-awaiting, and
        called in the same uninterrupted step as the store mutation it
        describes.
        """
        self._publish(events)

    def _publish(self, events: list[ResourceEvent]) -> None:
        """Hand change events to the subscription manager.

        Deliberately synchronous and deliberately non-awaiting. The store
        mutation and the publication happen in one uninterrupted step, so no
        other coroutine can observe the registry in a state where a resource
        has changed but its grain has not been queued. Actual delivery to
        WebSocket clients is asynchronous and rate-limited downstream; what is
        atomic here is the *queueing*.
        """
        if self._subscriptions is None or not events:
            return
        started = time.monotonic()
        self._subscriptions.publish(events)
        # The queueing is what this class controls, so that is what is timed.
        # ``subscriptions`` is the input that drives the cost: one change
        # fanning out to fifty subscribers is a different event from one
        # fanning out to none, and only the count distinguishes them.
        self._metrics.record(
            Event.SUBSCRIPTION_FANOUT,
            time.monotonic() - started,
            events=len(events),
            subscriptions=self._subscriptions.count(),
        )

    # -----------------------------------------------------------------------
    # Statistics
    # -----------------------------------------------------------------------

    def statistics(self) -> Any:
        """Snapshot for the status line, including subscription/grain counts."""
        subscriptions = 0
        grains = 0
        if self._subscriptions is not None:
            subscriptions = self._subscriptions.count()
            grains = self._subscriptions.grain_count()
        return self.store.statistics(
            subscriptions=subscriptions, grains=grains,
        )

    def status_line(self) -> str:
        """The periodic status line, in nmos-cpp's exact format.

        ``"At <now>, the registry contains <statistics>"`` -- nmos-cpp emits
        this from both its expiry thread and its ``POST /resource`` handler,
        and so does this registry.
        """
        return (
            f"At {TaiCursor.now()}, the registry contains "
            f"{self.statistics().render()}"
        )
