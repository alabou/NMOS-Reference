# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Managing the local etcd member process.

Scope, stated first because it is the thing most easily got wrong: a registry
supervises **exactly one** etcd process, its own local member. It never starts,
stops, reconfigures or removes any other member's etcd -- peers are reached only
as gRPC clients -- and it never changes cluster *membership*. Bootstrap and
resizing are explicit operator actions; this class refuses to automate them.

The ownership rule
------------------
**Stop what you started, never stop what you adopted.**

===========================================  ==========  ==================
Situation                                    Starts it?  Stops it on exit?
===========================================  ==========  ==================
Nothing on the configured port               yes         yes
etcd running, identity matches               no, adopts  **no**
etcd running, identity differs               refuses     n/a
===========================================  ==========  ==================

Terminating a self-launched child is what stops a Ctrl-C'd development run from
orphaning a process that still holds the client port and the data-directory
lock. Never terminating an adopted one is what stops the registry from killing a
service-managed etcd out from under systemd. In production the recommended shape
is exactly that: etcd under systemd, registry adopting it, so a registry restart
costs one reconnect instead of a member leave/rejoin with the leader election
and catch-up that implies.

What is never done
------------------
The legacy dRDS start scripts did three things on every single start: ``rm -f -r``
the data directory, ``member remove`` followed by ``member add``, and pinned an
end-of-life binary. Each is a way to lose data on what is meant to be a routine
restart. None happens here: the data directory is reused and never deleted,
membership is never touched, and the binary's version is checked rather than
assumed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from nmos.etcd.channel import Endpoint, EtcdChannelPool, unary_method
from nmos.etcd.cluster import ClusterLayout
from nmos.etcd.errors import EtcdError, EtcdUnavailable
from nmos.etcd.generated import rpc_pb2

log = logging.getLogger(__name__)

_STATUS = unary_method(
    "Maintenance", "Status", rpc_pb2.StatusRequest, rpc_pb2.StatusResponse,
)
_MEMBER_LIST = unary_method(
    "Cluster", "MemberList",
    rpc_pb2.MemberListRequest, rpc_pb2.MemberListResponse,
)

# The design depends on `--watch-progress-notify-interval` being a stable flag
# and on watch progress semantics settled in 3.4+; 3.5 still spells the flag
# `--experimental-...`. Rather than support both spellings, require 3.6.
MINIMUM_ETCD_VERSION = (3, 6)

# Restart backoff. Capped low enough that a member which crashed for a
# transient reason rejoins inside one garbage-collection interval, and high
# enough that a member crashing on every start does not spin.
_RESTART_BACKOFF_INITIAL = 0.5
_RESTART_BACKOFF_MAX = 30.0


class SupervisorError(EtcdError):
    """The local etcd member cannot be brought up safely."""


class ProcessOwnership(Enum):
    """Whether this supervisor may stop the member it is talking to."""

    LAUNCHED = "launched"
    """We started it, so we stop it on shutdown."""

    ADOPTED = "adopted"
    """It was already running and matched; someone else owns its lifetime."""

    EXTERNAL = "external"
    """``--etcdExternal``: no process management at all."""


@dataclass
class MemberIdentity:
    """Who a running etcd says it is."""

    cluster_id: int
    member_id: int
    name: str
    peer_urls: tuple[str, ...]
    version: str


def _read_root(path: str) -> bytes:
    """Read one trusted root, failing with the path rather than a bare OSError.

    Mirrors ``nmos.etcd.channel._read_bytes``: the two sides of mutual TLS read
    the same files, and a typo in one of them should read the same either way.
    """
    try:
        with open(path, "rb") as handle:
            content = handle.read()
    except OSError as exc:
        raise SupervisorError(f"cannot read --etcdTrustedRootCA {path!r}: {exc}") from exc

    # PEM files do not always end in a newline, and two roots concatenated
    # without one between them produce a single unparseable block: etcd would
    # then trust the first root only, which is the failure this whole method
    # exists to prevent.
    return content if content.endswith(b"\n") else content + b"\n"


@dataclass
class EtcdSupervisor:
    """Owns the local etcd member process.

    POSIX only. It is never constructed on native Windows: etcd classifies
    windows/amd64 as Tier 3 -- "considered unstable", unmaintained, and not
    covered by the functional and robustness suites that verify Raft/WAL/fsync
    durability -- so this project never runs a member there. A Windows registry
    is a client of a cluster managed elsewhere (``--etcdExternal``), which also
    means the absence of SIGTERM on Windows never enters this shutdown path.
    """

    layout: ClusterLayout
    binary: str
    data_dir: Path
    bootstrap: bool = False
    tls: bool = True
    certificate: str | None = None
    key: str | None = None
    trusted_root_ca: tuple[str, ...] = ()
    certificate_name: str | None = None
    client_crl_file: str | None = None
    peer_crl_file: str | None = None
    startup_timeout: float = 60.0

    _process: asyncio.subprocess.Process | None = field(default=None, init=False)
    _generated_ca: Path | None = field(default=None, init=False)
    """A trust store this supervisor combined from several roots, if it did."""

    _ownership: ProcessOwnership | None = field(default=None, init=False)
    _monitor: asyncio.Task[None] | None = field(default=None, init=False)
    _stopping: bool = field(default=False, init=False)

    # -----------------------------------------------------------------------
    # Introspection
    # -----------------------------------------------------------------------

    @property
    def ownership(self) -> ProcessOwnership | None:
        return self._ownership

    @property
    def owns_process(self) -> bool:
        """True only when this supervisor launched the member itself."""
        return self._ownership is ProcessOwnership.LAUNCHED

    # -----------------------------------------------------------------------
    # Startup
    # -----------------------------------------------------------------------

    async def start(self) -> ProcessOwnership:
        """Bring the local member up, or adopt one that is already running."""
        if sys.platform == "win32":
            raise SupervisorError(
                "the etcd supervisor is POSIX-only; on Windows run the "
                "registry with --etcdExternal against a cluster managed "
                "elsewhere",
            )

        self._validate_data_dir()

        existing = await self._probe()
        if existing is not None:
            self._verify_identity(existing)
            self._ownership = ProcessOwnership.ADOPTED
            log.info(
                "etcd: adopted running member %s (cluster %x, version %s); "
                "this registry did not start it and will not stop it",
                existing.name, existing.cluster_id, existing.version,
            )
            return self._ownership

        await self._launch()
        self._ownership = ProcessOwnership.LAUNCHED
        return self._ownership

    def _validate_data_dir(self) -> None:
        """Check the data directory, and decide bootstrap against it.

        The two rules here are the ones that keep a routine restart from
        becoming data loss:

        * ``--etcdBootstrap`` on a non-empty directory is refused. Bootstrapping
          an existing member creates a *new* cluster whose data is the old
          member's, which is how a cluster silently forks.
        * A missing or empty directory is **never** taken as "bootstrap me". It
          means ``initial-cluster-state=existing``, i.e. this member was added
          to the cluster by an explicit membership operation and is now starting
          for the first time. Inferring a new cluster from an absent directory
          is how an operator recovering one dead member ends up with two
          clusters.
        """
        parent = self.data_dir.parent
        if not parent.is_dir():
            raise SupervisorError(
                f"data directory parent does not exist: {parent}",
            )

        populated = self.data_dir.is_dir() and any(self.data_dir.iterdir())

        if self.bootstrap and populated:
            raise SupervisorError(
                f"--etcdBootstrap was given but {self.data_dir} is not empty. "
                f"Bootstrapping an existing member forks the cluster. Remove "
                f"the flag to start normally, or move the directory aside "
                f"deliberately if you really are creating a new cluster.",
            )

        if not self.data_dir.exists():
            self.data_dir.mkdir(parents=True, mode=0o700)
        else:
            # Someone else's readable data directory is a security problem as
            # well as an ownership one: it holds every registered resource.
            os.chmod(self.data_dir, 0o700)

    async def _probe(self) -> MemberIdentity | None:
        """Look for an etcd already listening on the local client port.

        Returns None when the port is closed, meaning we should launch. Raises
        when the port is open but does not answer as an etcd we recognise --
        never returning None in that case, because launching on top of an
        unrelated process is exactly the interference this must not cause.
        """
        member = self.layout.local
        if not _port_is_open(member.bind_address, member.client_port):
            return None

        log.info(
            "etcd: something is already listening on %s:%d; checking whether "
            "it is our member",
            member.bind_address, member.client_port,
        )

        pool = self._probe_pool()
        try:
            status = await pool.call(_STATUS, rpc_pb2.StatusRequest())
            members = await pool.call(_MEMBER_LIST, rpc_pb2.MemberListRequest())
        except EtcdUnavailable as exc:
            raise SupervisorError(
                f"{member.bind_address}:{member.client_port} is in use but "
                f"does not answer as an etcd this registry can talk to "
                f"({exc}). Refusing to start a second member on that port; "
                f"stop whatever is there, or point --etcdClientPort elsewhere.",
            ) from exc
        finally:
            await pool.close()

        answering = next(
            (m for m in members.members if m.ID == status.header.member_id),
            None,
        )
        if answering is None:
            raise SupervisorError(
                "the running etcd did not list itself in its own member list; "
                "refusing to adopt a member in that state",
            )

        return MemberIdentity(
            cluster_id=status.header.cluster_id,
            member_id=status.header.member_id,
            name=answering.name,
            peer_urls=tuple(answering.peerURLs),
            version=status.version,
        )

    def _verify_identity(self, identity: MemberIdentity) -> None:
        """Adopt only a member that is unmistakably the one we are configured as.

        Name and peer URL both have to match. Name alone is not enough: two
        deployments on one machine can easily share a member name while
        advertising different peer ports, and adopting the wrong one would make
        this registry serve another cluster's data.
        """
        expected = self.layout.local

        if identity.name != expected.name:
            raise SupervisorError(
                f"an etcd is running on {expected.client_target} but calls "
                f"itself {identity.name!r}, not {expected.name!r}. Refusing to "
                f"adopt a member belonging to a different configuration.",
            )

        wanted_peer = expected.advertise_peer_url(tls=self.tls)
        if wanted_peer not in identity.peer_urls:
            raise SupervisorError(
                f"the running member {identity.name!r} advertises peer URLs "
                f"{list(identity.peer_urls)}, which do not include "
                f"{wanted_peer}. Refusing to adopt it.",
            )

        _require_supported_version(identity.version)

    async def _launch(self) -> None:
        """Start etcd as a child process and wait for it to answer."""
        argv = self._build_argv()
        log.info(
            "etcd: launching member %s (data-dir %s, cluster-state %s)",
            self.layout.local.name, self.data_dir,
            "new" if self.bootstrap else "existing",
        )
        log.debug("etcd: %s", " ".join(argv))

        try:
            # No shell: the argv carries certificate paths and hostnames from
            # configuration, and a shell would give any of them the chance to
            # be interpreted rather than passed.
            self._process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            raise SupervisorError(
                f"cannot execute {self.binary!r}: {exc}. Install it with "
                f"./install-etcd.sh, or point --etcdBinary at one.",
            ) from exc

        await self._await_ready()
        self._monitor = asyncio.create_task(
            self._supervise(), name=f"etcd-supervisor-{self.layout.local.name}",
        )

    def _build_argv(self) -> list[str]:
        """The exact etcd command line, derived entirely from the layout."""
        member = self.layout.local
        argv = [
            self.binary,
            "--name", member.name,
            "--data-dir", str(self.data_dir),
            "--listen-client-urls", member.listen_client_url(tls=self.tls),
            "--advertise-client-urls", member.advertise_client_url(tls=self.tls),
            "--listen-peer-urls", member.listen_peer_url(tls=self.tls),
            "--initial-advertise-peer-urls",
            member.advertise_peer_url(tls=self.tls),
            "--initial-cluster", self.layout.initial_cluster(),
            "--initial-cluster-token", self.layout.token,
            "--initial-cluster-state", "new" if self.bootstrap else "existing",
        ]

        if not self.tls:
            return argv

        if not (self.certificate and self.key):
            raise SupervisorError(
                "TLS is enabled but --etcdCertificate/--etcdKey were not "
                "supplied",
            )
        if not self.trusted_root_ca:
            raise SupervisorError(
                "TLS is enabled but --etcdTrustedRootCA was not supplied",
            )

        # One certificate serves all four roles -- client listener, peer
        # listener, outbound peer connection, and the registry's own client
        # connection -- which is why it carries both serverAuth and clientAuth.
        ca = self._trusted_ca_file()
        argv += [
            "--cert-file", self.certificate,
            "--key-file", self.key,
            "--trusted-ca-file", ca,
            "--client-cert-auth",
            "--peer-cert-file", self.certificate,
            "--peer-key-file", self.key,
            "--peer-trusted-ca-file", ca,
            "--peer-client-cert-auth",
            "--tls-min-version", "TLS1.2",
        ]

        if self.certificate_name:
            # The control that stops any device sharing the Product CA from
            # writing to the registry database: the CA alone is not enough,
            # the certificate must also carry the etcd SAN.
            argv += [
                "--client-cert-allowed-hostname", self.certificate_name,
                "--peer-cert-allowed-hostname", self.certificate_name,
            ]
        if self.client_crl_file:
            argv += ["--client-crl-file", self.client_crl_file]
        if self.peer_crl_file:
            argv += ["--peer-crl-file", self.peer_crl_file]

        return argv

    def _discard_generated_ca(self) -> None:
        """Remove a trust store this supervisor wrote, if it wrote one.

        Best effort: the file holds public certificates, so leaving one behind
        after an abrupt exit leaks nothing, and failing a shutdown over it would
        be worse than the litter.
        """
        bundle = self._generated_ca
        self._generated_ca = None
        if bundle is None:
            return
        try:
            bundle.unlink(missing_ok=True)
        except OSError as exc:                          # pragma: no cover
            log.debug("etcd: could not remove %s: %s", bundle, exc)

    def _trusted_ca_file(self) -> str:
        """One file for etcd, however many roots the registry was given.

        ``--trusted-ca-file`` and ``--peer-trusted-ca-file`` each take a
        *single* path, while ``--etcdTrustedRootCA`` is repeatable and
        ``build_credentials`` trusts every root it is handed. Passing only the
        first would split the trust store in half: this member would reject
        peers and clients that the registry's own client channel, on the very
        same process, accepts -- and reject them with a certificate error
        naming a certificate that is perfectly valid.

        So several roots are concatenated into one file. That is what a PEM
        trust store is; ``Certificates/build.0/ExampleRootCA-bundle.pem``, which
        holds the RSA and ECDSA generations of the same CA, is exactly this file
        prepared by hand.

        The bundle is written beside the data directory rather than inside it.
        Inside, it would make an empty data directory non-empty and so trip the
        bootstrap refusal -- turning a first start into "the data directory is
        already initialised".
        """
        roots = self.trusted_root_ca
        if len(roots) == 1:
            return roots[0]

        bundle = self.data_dir.with_name(f"{self.data_dir.name}.trusted-roots.pem")
        try:
            bundle.parent.mkdir(parents=True, exist_ok=True)
            bundle.write_bytes(b"".join(_read_root(path) for path in roots))
        except OSError as exc:
            raise SupervisorError(
                f"cannot write the combined trust store {bundle}: {exc}",
            ) from exc

        self._generated_ca = bundle
        return str(bundle)

    def _probe_pool(self) -> EtcdChannelPool:
        """A short-lived pool aimed only at the local member."""
        from nmos.etcd.channel import build_credentials

        member = self.layout.local
        credentials = None
        if self.tls and self.certificate and self.key:
            credentials = build_credentials(
                trusted_root_ca=list(self.trusted_root_ca),
                certificate=self.certificate,
                key=self.key,
            )
        return EtcdChannelPool(
            [Endpoint(target=member.client_target, local=True)],
            credentials=credentials,
            target_name=self.certificate_name,
            rpc_timeout=2.0,
        )

    async def _await_ready(self) -> None:
        """Wait until the launched member answers, or fail with a reason."""
        member = self.layout.local
        deadline = asyncio.get_running_loop().time() + self.startup_timeout
        last: Exception | None = None

        while asyncio.get_running_loop().time() < deadline:
            process = self._process
            if process is not None and process.returncode is not None:
                raise SupervisorError(
                    f"etcd exited with status {process.returncode} during "
                    f"startup. With --initial-cluster-state="
                    f"{'new' if self.bootstrap else 'existing'} this usually "
                    f"means the member set or the data directory disagrees "
                    f"with the cluster.",
                )

            if _port_is_open(member.bind_address, member.client_port):
                pool = self._probe_pool()
                try:
                    status = await pool.call(_STATUS, rpc_pb2.StatusRequest())
                    _require_supported_version(status.version)
                    log.info(
                        "etcd: member %s is serving (version %s)",
                        member.name, status.version,
                    )
                    return
                except EtcdUnavailable as exc:
                    last = exc
                finally:
                    await pool.close()

            await asyncio.sleep(0.2)

        await self.stop()
        raise SupervisorError(
            f"etcd did not become ready within {self.startup_timeout:.0f}s"
            + (f": {last}" if last else ""),
        )

    # -----------------------------------------------------------------------
    # Supervision
    # -----------------------------------------------------------------------

    async def _supervise(self) -> None:
        """Restart the child if it exits, with bounded exponential backoff.

        The data directory is reused every time. Nothing is deleted and
        membership is never touched: a member that crashed still *is* a member,
        and re-adding it would be a membership change nobody asked for.
        """
        backoff = _RESTART_BACKOFF_INITIAL

        while not self._stopping:
            process = self._process
            if process is None:
                return
            code = await process.wait()
            if self._stopping:
                return

            log.error(
                "etcd: member %s exited with status %s; Registration is "
                "DEGRADED until it returns. Restarting in %.1fs.",
                self.layout.local.name, code, backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RESTART_BACKOFF_MAX)

            try:
                # Always "existing" on a restart: the cluster already knows
                # this member, and re-bootstrapping would fork it.
                self.bootstrap = False
                await self._launch_for_restart()
                backoff = _RESTART_BACKOFF_INITIAL
                log.info("etcd: member %s restarted", self.layout.local.name)
            except SupervisorError as exc:
                log.error("etcd: restart failed: %s", exc)

    async def _launch_for_restart(self) -> None:
        argv = self._build_argv()
        self._process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await self._await_ready()

    # -----------------------------------------------------------------------
    # Shutdown
    # -----------------------------------------------------------------------

    async def stop(self) -> None:
        """Stop the member -- but only if we started it.

        An adopted member is left running: this supervisor did not start it, so
        something else owns its lifetime, and killing a service-managed etcd on
        registry shutdown would take the cluster down with the registry.
        """
        self._stopping = True

        monitor = self._monitor
        self._monitor = None
        if monitor is not None:
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass

        if self._ownership is not ProcessOwnership.LAUNCHED:
            if self._ownership is ProcessOwnership.ADOPTED:
                log.info(
                    "etcd: leaving adopted member %s running",
                    self.layout.local.name,
                )
            return

        self._discard_generated_ca()

        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return

        log.info("etcd: stopping member %s", self.layout.local.name)
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            log.warning(
                "etcd: member %s did not exit on SIGTERM; killing it",
                self.layout.local.name,
            )
            process.kill()
            await process.wait()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _port_is_open(host: str, port: int, *, timeout: float = 0.5) -> bool:
    """Whether something accepts TCP on host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def parse_etcd_version(version: str) -> tuple[int, ...]:
    """Parse ``"3.6.14"`` into ``(3, 6, 14)``, stopping at any suffix.

    Pre-release builds label themselves ``3.7.0-rc.1``. Parsing must stop at
    the first component that is not purely numeric and return ``(3, 7, 0)`` --
    continuing past it would splice the release-candidate number in as a fourth
    version component.
    """
    parts: list[int] = []
    for chunk in version.split("."):
        digits = ""
        for character in chunk:
            if not character.isdigit():
                break
            digits += character
        if not digits:
            break
        parts.append(int(digits))
        if digits != chunk:
            # A suffix such as "0-rc" ends the version proper; whatever follows
            # belongs to the pre-release label, not to the version.
            break
    if not parts:
        raise SupervisorError(f"cannot parse etcd version {version!r}")
    return tuple(parts)


def _require_supported_version(version: str) -> None:
    """Refuse an etcd older than 3.6.

    Checked through ``Maintenance.Status`` rather than by parsing
    ``etcd --version`` from a launched child, so the same gate applies in every
    mode -- managed, adopted, and ``--etcdExternal`` -- instead of only where
    this process happens to spawn the binary.
    """
    parsed = parse_etcd_version(version)
    if parsed[: len(MINIMUM_ETCD_VERSION)] < MINIMUM_ETCD_VERSION:
        wanted = ".".join(str(part) for part in MINIMUM_ETCD_VERSION)
        raise SupervisorError(
            f"etcd {version} is too old; {wanted} or later is required. "
            f"This design uses watch progress requests and the stable "
            f"--watch-progress-notify-interval flag, which 3.5 spells "
            f"--experimental-watch-progress-notify-interval.",
        )
