# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Finding a running node's Controller UI and how to reach it.

The driver never starts a node. ``start-node*.sh`` documents itself as the IPMX
security validator launch contract, and those scripts drive a configuration matrix
— access policies, certificate types, audience modes — that produces materially
different user interfaces. A driver that launched its own node would be a second
implementation of that contract, and would silently decide which configuration a
demonstration exercised. Attaching leaves that choice where it belongs: with
whoever ran the script.

So everything needed is read from the node's own command line:

===============================  =====================================
``--nodeAddr`` / ``--nodeControlPort``  where the UI listens
``--nodeDisableTLS``             whether it is plaintext
``--nodeCertificate`` etc.       what to verify and pin
``--oauth2``                     whether sign-in leaves the application
``--debug-in-depth``             whether trace correlation is available
===============================  =====================================

The admin password is the deliberate exception. It is sitting right there in
``--controllerAdminPassword``, and reading it would work — but a tool that
harvests credentials out of process state is a habit not worth forming, so it
comes from the environment and is redacted out of the recorded provenance.
"""

from __future__ import annotations

import os
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ...core.adapter import Credentials, Target
from ...core.proc_scan import CommandLine, ProcessInfo, find_by_script
from ...core.tls_pin import CertificateMaterial, PinResult, resolve_tls
from ...enums import TlsPolicy
from ...errors import (
    AdminPasswordMissing,
    ControllerNotEnabled,
    NodeAmbiguous,
    NodeNotFound,
    OAuth2NotSupported,
)

#: The script whose processes are candidates to attach to.
NODE_SCRIPT = "nmos_node.py"

#: Application name recorded in artifacts and the manifest.
APP_NAME = "nmos-controller"

#: URL prefix every Controller page lives under. Matches ``URL_PREFIX`` in
#: ``nmos/controller/app.py``.
URL_PREFIX = "/controller"

#: Environment variable holding the Controller admin password.
PASSWORD_ENV = "NMOS_CONTROLLER_ADMIN_PASSWORD"

#: Options whose values must never reach an artifact file. ``/proc`` is
#: world-readable and ``ps`` shows the same text, so reading these is not a new
#: disclosure — but copying them into a shareable journal would be.
SENSITIVE_OPTIONS = frozenset({
    "--controllerAdminPassword",
    "--oauth2ClientSecret",
})

#: Bind addresses that mean "all interfaces" and must be replaced with a
#: connectable address before a browser is pointed at them.
_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", "[::]", ""})


@dataclass(frozen=True, slots=True)
class ControllerTarget:
    """A discovered Controller UI, plus how trust and tracing were resolved."""

    target: Target
    pid: int
    debug_log_path: str | None
    oauth2: bool
    pin: PinResult

    @property
    def entry_url(self) -> str:
        """The single URL the driver may navigate to directly."""
        return f"{self.target.origin}{URL_PREFIX}/"

    @property
    def debug_tracing(self) -> bool:
        """Whether the node is emitting the trace a journal can correlate to."""
        return self.debug_log_path is not None


def _resolves_to(host: str) -> bool:
    """Whether a name resolves at all, used to prefer the clean TLS path."""
    try:
        socket.getaddrinfo(host, None)
    except OSError:
        return False
    return True


def derive_debug_log_path(node_addr: str, control_port: int) -> str:
    """Reproduce the node's own debug-log path derivation.

    Mirrors ``nmos_node.py`` exactly: the bind address with colons replaced by
    hyphens, then the control port. Derived rather than obtained by calling the
    Controller's diagnostic snapshot endpoint, because that endpoint is part of the
    JSON API and reading it directly is not something the interface offers an
    operator. If this derivation ever drifts from the node's, the tail simply finds
    nothing and correlation is reported as unavailable — degraded, never wrong.
    """
    host_safe = (node_addr or "0.0.0.0").replace(":", "-")
    return os.path.join(
        tempfile.gettempdir(),
        f"nmos-controller-{host_safe}-{control_port}.log",
    )


def read_password(env_var: str = PASSWORD_ENV) -> Credentials:
    """Read the admin password from the environment.

    Not taken from the node's command line on purpose — see the module docstring.
    """
    value = os.environ.get(env_var, "")
    if not value:
        raise AdminPasswordMissing(
            f"{env_var} is not set. The Controller admin password is read from "
            f"the environment rather than harvested from the node's command "
            f"line, so export it before attaching:\n"
            f"    export {env_var}=<the --controllerAdminPassword value>"
        )
    return Credentials(password=value)


def candidates(proc_root: Path = Path("/proc")) -> tuple[ProcessInfo, ...]:
    """Every running node process that has the Controller UI enabled.

    A node started without ``--nodeControlPort`` serves no UI at all, so it is not
    a candidate; excluding it here means the ambiguity error only ever lists nodes
    that could actually be attached to.
    """
    enabled: list[ProcessInfo] = []
    for process in find_by_script(NODE_SCRIPT, proc_root=proc_root,
                                  exclude_pid=os.getpid()):
        if CommandLine(process.argv).int_value("--nodeControlPort", 0) > 0:
            enabled.append(process)
    return tuple(enabled)


def discover(
    *,
    control_port: int | None = None,
    proc_root: Path = Path("/proc"),
    prefer_policy: TlsPolicy = TlsPolicy.PIN_LEAF_SPKI,
) -> ControllerTarget:
    """Locate the node to attach to and work out how to reach its UI.

    ``control_port`` disambiguates when several nodes are running. Without it,
    multiple candidates are an error rather than a guess: ``start-node2*.sh`` and
    ``start-node3*.sh`` exist, so silently taking the first match would mean
    demonstrating the wrong rig while the journal looked entirely convincing.
    """
    found = candidates(proc_root)
    if not found:
        # Distinguish "no node at all" from "a node with no UI", because the
        # remedies are different and both are easy to hit.
        any_node = find_by_script(NODE_SCRIPT, proc_root=proc_root,
                                  exclude_pid=os.getpid())
        if any_node:
            raise ControllerNotEnabled(
                f"found {len(any_node)} running {NODE_SCRIPT} process(es), but "
                f"none was started with --nodeControlPort, so no Controller UI "
                f"is being served. Start a node with a control port, e.g. "
                f"./start-node1-bare.sh"
            )
        raise NodeNotFound(
            f"no running {NODE_SCRIPT} process found. This driver attaches to a "
            f"node you started; it never launches one. Start one first, e.g. "
            f"./start-node1-bare.sh"
        )

    if control_port is not None:
        found = tuple(
            p for p in found
            if CommandLine(p.argv).int_value("--nodeControlPort", 0) == control_port
        )
        if not found:
            raise NodeNotFound(
                f"no running {NODE_SCRIPT} has --nodeControlPort {control_port}"
            )

    if len(found) > 1:
        ports = tuple(
            (p.pid, CommandLine(p.argv).int_value("--nodeControlPort", 0))
            for p in found
        )
        raise NodeAmbiguous(
            f"{len(found)} nodes are serving a Controller UI: "
            + ", ".join(f"pid {pid} on port {port}" for pid, port in ports)
            + ". Pass --controlPort to choose one; attaching to an arbitrary "
              "node would mean demonstrating a rig you did not intend.",
            candidates=ports,
        )

    return _build(found[0], prefer_policy=prefer_policy)


def _build(
    process: ProcessInfo,
    *,
    prefer_policy: TlsPolicy,
) -> ControllerTarget:
    """Turn one node process into a target."""
    cli = CommandLine(process.argv)

    control_port = cli.int_value("--nodeControlPort", 0)
    node_addr = cli.value("--nodeAddr", "127.0.0.1") or "127.0.0.1"
    plaintext = cli.has_flag("--nodeDisableTLS")
    oauth2 = cli.has_flag("--oauth2")
    serial = cli.value("--nodeSerialNumber", "") or ""

    if oauth2:
        # Refused at discovery rather than after the browser has followed a
        # redirect to an authorization server and stalled there with no
        # explanation.
        raise OAuth2NotSupported(
            f"pid {process.pid} was started with --oauth2, so signing in leaves "
            f"the Controller for an external authorization server. Driving that "
            f"flow is not implemented yet. Use a node started without --oauth2, "
            f"e.g. ./start-node1-bare.sh"
        )

    # A wildcard bind is not a connectable address. Substituting loopback is
    # recorded in the provenance so the journal shows the address actually used.
    host = node_addr
    host_substituted = ""
    if node_addr in _WILDCARD_HOSTS:
        host = "127.0.0.1"
        host_substituted = f"{node_addr} -> 127.0.0.1"

    provenance: dict[str, str] = {
        "pid": str(process.pid),
        "command_line": cli.redacted(SENSITIVE_OPTIONS),
        "node_addr": node_addr,
        "node_config": cli.value("--nodeConfig", "") or "",
        "node_serial": serial,
        "ipmx": str(cli.has_flag("--ipmx")),
    }
    if host_substituted:
        provenance["host_substituted"] = host_substituted

    ca_paths: tuple[str, ...] = ()
    if plaintext:
        pin = PinResult(policy=TlsPolicy.PLAINTEXT,
                        detail="node started with --nodeDisableTLS")
        scheme = "http"
    else:
        # Mirrors nmos_node.py::_ca_list -- a non-empty per-service list
        # overrides the global one; the two are never merged.
        ca_paths = cli.values("--nodeTrustedRootCA") or cli.values("--trustedRootCA")
        material = CertificateMaterial(
            cert_path=cli.value("--nodeCertificate", "") or "",
            key_path=cli.value("--nodeKey", "") or "",
            serial_number=serial,
            ca_paths=ca_paths,
        )
        pin = resolve_tls(material, host=host, prefer_policy=prefer_policy,
                          resolves=_resolves_to)
        scheme = "https"
        if pin.policy is TlsPolicy.SAN_HOSTNAME and pin.san_names:
            # Connect by the certificate's own name so the chain validates with
            # no browser flag at all.
            host = pin.san_names[0]

    target = Target(
        app=APP_NAME,
        scheme=scheme,
        host=host,
        port=control_port,
        base_path=URL_PREFIX,
        tls=pin.policy,
        spki_pins=pin.pins,
        ca_paths=ca_paths,
        provenance=provenance,
    )

    debug_log_path = (
        derive_debug_log_path(node_addr, control_port)
        if cli.has_flag("--debug-in-depth") else None
    )

    return ControllerTarget(
        target=target,
        pid=process.pid,
        debug_log_path=debug_log_path,
        oauth2=oauth2,
        pin=pin,
    )
