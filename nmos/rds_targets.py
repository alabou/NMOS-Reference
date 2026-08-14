# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Ordered registries a Node and its Controller can fall back through.

A distributed registry is several independent members serving one shared state
(``nmos/registry`` with ``--distributed``). Any member can answer, so a client
pinned to one of them is needlessly fragile: the cluster survives losing a
member, but the Node registered with it does not.

This module holds the client-side half of that: the parsed list of registries,
and the rule for moving to the next one.

Two things are deliberately NOT here
------------------------------------

**No health probing.** Nothing here polls registries to find a live one.
A target is failed when a real operation against it fails, which is the only
evidence that actually matters and costs nothing when everything is working.

**No failback.** Once moved, clients stay put until the new registry fails too.
Each switch costs a full re-registration or a re-sync of every subscription, so
returning to a preferred member the moment it reappears would turn one flapping
member into continuous churn for every client. Configuration order is the
starting preference, not a standing one.

Coordination, and its limits
----------------------------

A Controller runs six independent WebSocket subscribers against one registry.
When that member dies they all notice separately, within moments of each other.
``RegistrySelector.fail()`` therefore advances **only if the caller's target is
still the current one**, so six reports of one outage move the selection
exactly once and the other five simply re-read ``current`` and reconnect there.
Without that rule a single outage would skip the whole list.

That coordination is deliberately scoped to **one client**. The Node's
registration loop and the Controller each hold their own selector, because they
are independent clients that happen to share a process: every member of a
distributed registry serves the same shared state, so the two of them sitting
on different members is normal operation rather than a fault. Sharing a
selector would merge their failure domains, letting a Controller-side
WebSocket problem move a Node registration whose own connection was fine.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Iterator

__all__ = [
    "RegistryTarget",
    "RegistrySelector",
    "RdsSpecError",
    "MAX_REGISTRIES",
    "parse_rds_spec",
]

# 1, 3 or 5 members is the useful cluster shape (see the distributed plan's
# quorum table); the cap is 5 because a Node fanning out further is describing
# a topology this project does not support rather than a resilient one.
MAX_REGISTRIES = 5


class RdsSpecError(ValueError):
    """A ``--rds`` specification is malformed or contradictory."""


@dataclass(frozen=True)
class RegistryTarget:
    """One registry, with everything needed to reach all three of its ports.

    All three ports are held together because they describe **one member**,
    not because every client must use the same one. A Node may be registered
    with entry #2 while its Controller subscribes to entry #5; every member
    serves the same shared state, so that is normal operation.

    What must not split is a *single* client across members -- a Controller
    that created its subscription on one member and opened the WebSocket on
    another would be watching a stream nobody was feeding.
    """

    host: str
    registration_port: int
    query_port: int
    ws_port: int
    tls: bool = True
    certificate_name: str = ""
    trusted_root_ca: tuple[str, ...] = ()
    client_certificate: str = ""
    client_key: str = ""

    @property
    def label(self) -> str:
        """Short identifier for logs — what an operator needs to see."""
        return f"{self.host}:{self.registration_port}"

    def __str__(self) -> str:
        return self.label


# Field name in a --rds spec -> (attribute, converter). Spelled in the same
# camelCase as the scalar --rds* flags they default from, so an operator
# translating one to the other does not have to learn a second vocabulary.
def _port(name: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise RdsSpecError(f"{name} must be an integer, got {raw!r}") from None
    if not 1 <= value <= 65535:
        raise RdsSpecError(f"{name} must be 1-65535, got {value}")
    return value


def _bool(name: str, raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in ("", "true", "yes", "1"):
        return True
    if lowered in ("false", "no", "0"):
        return False
    raise RdsSpecError(f"{name} must be true or false, got {raw!r}")


_KNOWN = (
    "host", "registrationPort", "queryPort", "wsPort", "certName",
    "ca", "cert", "key", "disableTLS",
)


def parse_rds_spec(spec: str, default: RegistryTarget) -> RegistryTarget:
    """Parse one ``--rds`` value into a target, filling gaps from ``default``.

    The format is comma-separated ``key=value`` pairs::

        host=192.0.2.7,queryPort=8446,wsPort=8448,ca=RootCA.pem

    ``default`` carries the scalar ``--rds*`` flags, so a spec need only state
    what differs from them -- ``--rds host=192.0.2.7`` is a complete entry when
    every member shares ports and trust material, which is the common case.

    ``ca`` may be repeated to supply several trust anchors; every other key may
    appear once. ``disableTLS`` may be written bare (``disableTLS``) or with an
    explicit value.

    Raises:
        RdsSpecError: unknown key, malformed value, duplicate key, or no host.
    """
    if not spec.strip():
        raise RdsSpecError("--rds requires at least host=<address>")

    host = default.host
    ports = {
        "registrationPort": default.registration_port,
        "queryPort": default.query_port,
        "wsPort": default.ws_port,
    }
    cert_name = default.certificate_name
    cas: list[str] = list(default.trusted_root_ca)
    cas_overridden = False
    client_cert = default.client_certificate
    client_key = default.client_key
    tls = default.tls
    seen: set[str] = set()

    for item in spec.split(","):
        if not item.strip():
            continue
        name, sep, raw = item.partition("=")
        name = name.strip()
        raw = raw.strip() if sep else ""

        if name not in _KNOWN:
            raise RdsSpecError(
                f"unknown --rds field {name!r}; expected one of: "
                f"{', '.join(_KNOWN)}",
            )
        if name != "ca" and name in seen:
            raise RdsSpecError(f"--rds field {name!r} given more than once")
        seen.add(name)

        if name == "host":
            if not raw:
                raise RdsSpecError("--rds host must not be empty")
            host = raw
        elif name in ports:
            ports[name] = _port(name, raw)
        elif name == "certName":
            cert_name = raw
        elif name == "ca":
            # The first ``ca`` REPLACES the inherited defaults rather than
            # adding to them: an entry that names its own trust anchors is
            # describing a different PKI, and silently keeping the global ones
            # would make it trust more than was asked for.
            if not cas_overridden:
                cas = []
                cas_overridden = True
            cas.append(raw)
        elif name == "cert":
            client_cert = raw
        elif name == "key":
            client_key = raw
        elif name == "disableTLS":
            tls = not _bool(name, raw)

    if not host:
        raise RdsSpecError("--rds requires host=<address>")

    return RegistryTarget(
        host=host,
        registration_port=ports["registrationPort"],
        query_port=ports["queryPort"],
        ws_port=ports["wsPort"],
        tls=tls,
        certificate_name=cert_name,
        trusted_root_ca=tuple(cas),
        client_certificate=client_cert,
        client_key=client_key,
    )


def build_targets(
    specs: Iterable[str] | None,
    default: RegistryTarget,
) -> tuple[RegistryTarget, ...]:
    """Turn the ``--rds`` values into the ordered target list.

    With no ``--rds`` at all the scalar flags describe a single registry, which
    is exactly what they meant before this existed -- so an unchanged command
    line keeps working and produces a one-entry list.

    Raises:
        RdsSpecError: more than ``MAX_REGISTRIES`` entries, or two entries
            naming the same host and registration port (which would make
            failover move to a registry already known to be down).
    """
    if not specs:
        return (default,) if default.host else ()

    targets = [parse_rds_spec(spec, default) for spec in specs]
    if len(targets) > MAX_REGISTRIES:
        raise RdsSpecError(
            f"at most {MAX_REGISTRIES} --rds entries are supported, got "
            f"{len(targets)}",
        )

    seen: set[tuple[str, int]] = set()
    for target in targets:
        pair = (target.host, target.registration_port)
        if pair in seen:
            raise RdsSpecError(f"duplicate --rds entry for {target.label}")
        seen.add(pair)
    return tuple(targets)


@dataclass
class RegistrySelector:
    """Which registry clients should be using, and when to move on.

    Shared by the Node's registration loop and the Controller's subscribers so
    that one outage moves both. Single-threaded asyncio, so no locking: every
    method here completes without awaiting.
    """

    targets: tuple[RegistryTarget, ...]
    _index: int = field(default=0, repr=False)
    _failures: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if not self.targets:
            raise RdsSpecError("a RegistrySelector needs at least one registry")

    @property
    def current(self) -> RegistryTarget:
        return self.targets[self._index]

    @property
    def failover_count(self) -> int:
        """How many times the selection has moved. For tests and diagnostics."""
        return self._failures

    @property
    def has_alternatives(self) -> bool:
        return len(self.targets) > 1

    def fail(self, target: RegistryTarget) -> RegistryTarget:
        """Report ``target`` as unusable; return the registry to use now.

        A no-op unless ``target`` is still the current one. That is what keeps
        one outage -- noticed independently by the Node loop and six WebSocket
        subscribers -- from advancing seven places down the list. Later callers
        simply receive the already-updated selection.

        With a single registry configured there is nowhere to go, so the same
        target is returned and the caller retries it. That is not a failure of
        this method: a lone registry coming back is the only recovery available,
        and the client's own backoff is what waits for it.
        """
        if target != self.current:
            return self.current
        if not self.has_alternatives:
            return self.current

        self._index = (self._index + 1) % len(self.targets)
        self._failures += 1
        return self.current

    def __iter__(self) -> Iterator[RegistryTarget]:
        return iter(self.targets)

    def __len__(self) -> int:
        return len(self.targets)


def target_from_scalars(
    *,
    host: str,
    registration_port: int,
    query_port: int,
    ws_port: int,
    tls: bool,
    certificate_name: str = "",
    trusted_root_ca: Iterable[str] = (),
    client_certificate: str = "",
    client_key: str = "",
) -> RegistryTarget:
    """Build the default target from the scalar ``--rds*`` flags."""
    return RegistryTarget(
        host=host,
        registration_port=registration_port,
        query_port=query_port,
        ws_port=ws_port,
        tls=tls,
        certificate_name=certificate_name,
        trusted_root_ca=tuple(trusted_root_ca),
        client_certificate=client_certificate,
        client_key=client_key,
    )


def with_host(target: RegistryTarget, host: str) -> RegistryTarget:
    """A copy of ``target`` aimed at a different host. Used by tests."""
    return replace(target, host=host)
