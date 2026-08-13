# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""gRPC transport to the etcd cluster: credentials, endpoints, failover.

Two decisions here shape everything above this layer.

**Why methods are declared rather than generated.** ``protoc``'s Python service
plugin emits an unannotated ``_pb2_grpc.py`` with no type stubs, so every RPC
would return ``Any`` and the wrapper layer would either fail ``mypy --strict``
or be full of casts. Instead each RPC is a ``UnaryMethod`` / ``StreamMethod``
naming its own path, built on grpc's own typed ``channel.unary_unary`` and
``stream_stream``. That keeps the client to an explicit, audited list of the
RPCs this registry actually uses — no generic passthrough — and it is the shape
the eventual Rust/tonic port takes.

**Why a channel per endpoint rather than one multi-address channel.** gRPC's
multi-address support goes through the ``ipv4:``/``ipv6:`` schemes, which take
numeric addresses only; etcd members here are configured by *hostname*, because
the hostname is what the certificate's SAN attests. Resolving the names
ourselves to feed a single channel would move DNS out of gRPC and break
re-resolution when an address changes.

One channel per endpoint costs a few idle sockets and buys behaviour the
registry needs anyway: the local member can be *preferred* rather than merely
present, so the common case takes no network hop; failover is explicit and
observable instead of hidden in a load-balancing policy; and a watch — which
must pin to one member for the life of the stream — binds to one channel
naturally, so a reconnect after member loss is the same code path as any other
watch reconnect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Sequence, TypeVar

from nmos.etcd.errors import EtcdError, EtcdUnavailable, classify

if TYPE_CHECKING:
    import grpc
    from google.protobuf.message import Message

log = logging.getLogger(__name__)

Req = TypeVar("Req", bound="Message")
Resp = TypeVar("Resp", bound="Message")


# ---------------------------------------------------------------------------
# Method descriptors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnaryMethod(Generic[Req, Resp]):
    """One unary etcd RPC, named by its fully-qualified gRPC path.

    Build these with ``unary_method()`` rather than by writing the path out --
    see that function for why.
    """

    path: str
    request: type[Req]
    response: type[Resp]


@dataclass(frozen=True)
class StreamMethod(Generic[Req, Resp]):
    """One bidirectional-streaming etcd RPC (only ``Watch`` and ``LeaseKeepAlive``)."""

    path: str
    request: type[Req]
    response: type[Resp]


def _resolve_path(service: str, method: str) -> str:
    """Build a gRPC path, checking it against the compiled proto descriptor.

    A hand-written path string is checked by nothing: ``etcdserverpb.KV/Compaction``
    looks entirely plausible next to ``CompactionRequest``, but the method is
    actually named ``Compact``, and the only symptom is an ``UNIMPLEMENTED`` at
    the first call — which, for an RPC used only on the compaction-recovery
    path, could easily be the first call in production.

    Resolving through ``DESCRIPTOR`` turns that into an ``EtcdError`` at import
    time, listing the methods that do exist. The descriptor is generated from
    the same vendored proto the server was built from, so it is the authority.
    """
    from nmos.etcd.generated import rpc_pb2

    # ``services_by_name`` is keyed by the SHORT name ("KV"), while the wire
    # path needs the full one ("etcdserverpb.KV"). Both spellings are accepted
    # so a caller can write whichever reads better at the declaration site.
    services = rpc_pb2.DESCRIPTOR.services_by_name
    descriptor = services.get(service)
    if descriptor is None:
        descriptor = next(
            (s for s in services.values() if s.full_name == service), None,
        )
    if descriptor is None:
        available = ", ".join(
            sorted(s.full_name for s in services.values())
        )
        raise EtcdError(
            f"unknown etcd service {service!r}; available: {available}",
        )
    if method not in descriptor.methods_by_name:
        available = ", ".join(m.name for m in descriptor.methods)
        raise EtcdError(
            f"unknown method {method!r} on {descriptor.full_name}; "
            f"available: {available}",
        )
    return f"/{descriptor.full_name}/{method}"


def unary_method(
    service: str, method: str, request: type[Req], response: type[Resp],
) -> UnaryMethod[Req, Resp]:
    """Declare a unary RPC, validating its name against the proto descriptor."""
    return UnaryMethod(_resolve_path(service, method), request, response)


def stream_method(
    service: str, method: str, request: type[Req], response: type[Resp],
) -> StreamMethod[Req, Resp]:
    """Declare a streaming RPC, validating its name against the proto descriptor."""
    return StreamMethod(_resolve_path(service, method), request, response)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def build_credentials(
    *,
    trusted_root_ca: Sequence[str],
    certificate: str,
    key: str,
) -> grpc.ChannelCredentials:
    """Build mTLS credentials from the member's shared etcd certificate.

    The same certificate the local etcd member presents on its client and peer
    listeners is presented here as a *client* certificate — that is what its
    dual ``serverAuth, clientAuth`` EKU is for, and it is what
    ``--client-cert-allowed-hostname`` on the server side checks. Presenting an
    ordinary device certificate instead would be rejected by that restriction,
    which is the control that stops any device sharing the Product CA from
    writing to the registry database.
    """
    import grpc

    roots = b"".join(_read_bytes(path) for path in trusted_root_ca)
    return grpc.ssl_channel_credentials(
        root_certificates=roots,
        private_key=_read_bytes(key),
        certificate_chain=_read_bytes(certificate),
    )


def _read_bytes(path: str) -> bytes:
    """Read a PEM file, failing with the path rather than a bare OSError."""
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise EtcdError(f"cannot read {path!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Endpoint:
    """One etcd member's client endpoint.

    ``target`` is what gRPC dials (``host:port``); ``local`` marks the member
    co-located with this registry, which is tried first.
    """

    target: str
    local: bool = False

    @property
    def label(self) -> str:
        return f"{self.target}{' (local)' if self.local else ''}"


def parse_endpoints(
    endpoints: Sequence[str], *, local_target: str | None = None,
) -> tuple[Endpoint, ...]:
    """Normalise configured endpoint strings into ordered ``Endpoint``s.

    Accepts the ``https://host:port`` form the CLI and etcd itself use, as well
    as a bare ``host:port``. The scheme is dropped because gRPC targets do not
    carry one — TLS is selected by the credentials, not by the URL — but it is
    accepted on input so operators can paste the same strings they give etcd.

    The local endpoint sorts first so it is always tried first; the rest keep
    their configured order, which keeps failover deterministic and therefore
    reproducible in a test.
    """
    parsed: list[Endpoint] = []
    seen: set[str] = set()

    for raw in endpoints:
        target = raw.strip()
        if not target:
            continue
        for scheme in ("https://", "http://"):
            if target.startswith(scheme):
                target = target[len(scheme):]
                break
        target = target.rstrip("/")
        if not target:
            raise EtcdError(f"empty etcd endpoint in {raw!r}")
        if ":" not in target:
            raise EtcdError(
                f"etcd endpoint {raw!r} has no port; expected host:port",
            )
        if target in seen:
            continue
        seen.add(target)
        parsed.append(Endpoint(target=target, local=target == local_target))

    if not parsed:
        raise EtcdError("no etcd endpoints configured")

    return tuple(
        sorted(parsed, key=lambda e: (not e.local, parsed.index(e))),
    )


# ---------------------------------------------------------------------------
# Channel pool
# ---------------------------------------------------------------------------

class EtcdChannelPool:
    """One gRPC channel per member, with local-first failover.

    Args:
        endpoints: Every member's client endpoint, local first.
        credentials: mTLS credentials, or None for the ``--etcdDisableTLS``
            testing mode.
        target_name: The shared certificate name every member is verified
            against (``--etcdCertificateName``). Set as gRPC's
            ``ssl_target_name_override`` so one certificate validates against
            every member regardless of the host in the endpoint — which is
            exactly why the generated etcd certificates carry a shared SAN
            alongside their per-member one.
        rpc_timeout: Default per-RPC deadline in seconds.
    """

    __slots__ = (
        "_endpoints", "_credentials", "_target_name", "_rpc_timeout", "_channels",
    )

    def __init__(
        self,
        endpoints: Sequence[Endpoint],
        *,
        credentials: grpc.ChannelCredentials | None,
        target_name: str | None,
        rpc_timeout: float,
    ) -> None:
        if not endpoints:
            raise EtcdError("EtcdChannelPool requires at least one endpoint")
        self._endpoints = tuple(endpoints)
        self._credentials = credentials
        self._target_name = target_name
        self._rpc_timeout = rpc_timeout
        self._channels: dict[str, grpc.aio.Channel] = {}

    @property
    def endpoints(self) -> tuple[Endpoint, ...]:
        return self._endpoints

    def _options(self) -> list[tuple[str, str | int]]:
        """Channel options shared by every endpoint.

        Keepalives are on because the watch stream is idle for long stretches by
        design — a registry with no registrations happening still needs to
        notice that its member died, and without keepalives a silently dropped
        connection looks identical to "nothing has changed".
        """
        options: list[tuple[str, str | int]] = [
            ("grpc.keepalive_time_ms", 30_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.max_pings_without_data", 0),
            # etcd's default maximum request size is 1.5 MiB; accept responses
            # comfortably above it so a large preload page is never truncated
            # into an opaque RESOURCE_EXHAUSTED.
            ("grpc.max_receive_message_length", 32 * 1024 * 1024),
        ]
        if self._target_name:
            options.append(("grpc.ssl_target_name_override", self._target_name))
        return options

    def channel(self, endpoint: Endpoint) -> grpc.aio.Channel:
        """The channel for one endpoint, created on first use.

        Channels are lazy so that a member which is down at startup costs
        nothing until something actually tries to reach it, and cached so that
        the TLS handshake is paid once rather than per RPC.
        """
        import grpc

        existing = self._channels.get(endpoint.target)
        if existing is not None:
            return existing

        options = self._options()
        if self._credentials is None:
            created = grpc.aio.insecure_channel(endpoint.target, options=options)
        else:
            created = grpc.aio.secure_channel(
                endpoint.target, self._credentials, options=options,
            )
        self._channels[endpoint.target] = created
        return created

    async def call(
        self,
        method: UnaryMethod[Req, Resp],
        request: Req,
        *,
        timeout: float | None = None,
    ) -> Resp:
        """Invoke a unary RPC, trying each endpoint until one answers.

        Failover is only attempted for errors that another member could plausibly
        answer. A rejection — bad credentials, a malformed request — is raised
        immediately: retrying it against every member turns one clear error into
        N confusing ones and delays the answer by the full deadline each time.
        """
        deadline = self._rpc_timeout if timeout is None else timeout
        last: EtcdError | None = None

        for endpoint in self._endpoints:
            try:
                return await self._call_one(method, request, endpoint, deadline)
            except EtcdUnavailable as exc:
                last = exc
                log.debug(
                    "etcd: %s failed on %s: %s", method.path, endpoint.label, exc,
                )
                continue

        raise EtcdUnavailable(
            f"{method.path}: no etcd member answered "
            f"({', '.join(e.label for e in self._endpoints)}): {last}",
        )

    async def _call_one(
        self,
        method: UnaryMethod[Req, Resp],
        request: Req,
        endpoint: Endpoint,
        deadline: float,
    ) -> Resp:
        import grpc

        callable_ = self.channel(endpoint).unary_unary(
            method.path,
            request_serializer=method.request.SerializeToString,
            response_deserializer=method.response.FromString,
        )
        try:
            response = await callable_(request, timeout=deadline)
        except grpc.aio.AioRpcError as exc:
            raise classify(exc) from exc
        # The deserializer is typed as returning the response class, but grpc's
        # stubs describe the multicallable generically; the cast-free way to
        # keep --strict satisfied is an explicit check that also catches a
        # mismatched path returning something unexpected.
        if not isinstance(response, method.response):
            raise EtcdError(
                f"{method.path}: expected {method.response.__name__}, "
                f"got {type(response).__name__}",
            )
        return response

    def open_stream(
        self, method: StreamMethod[Req, Resp], endpoint: Endpoint,
    ) -> grpc.aio.StreamStreamCall:
        """Open a bidirectional stream on one specific endpoint.

        Deliberately takes an explicit endpoint and does **not** fail over: a
        stream carries state the caller owns. The watch in particular is
        positioned at a revision, so silently reconnecting it to another member
        underneath the caller would resume from the wrong place. The caller
        chooses the member, notices the failure, and re-opens at
        ``last_applied_revision + 1`` — which is the same code path a normal
        watch reconnect takes anyway.
        """
        # stream_stream() returns a *multicallable* -- a factory. Invoking it
        # with no request iterator starts the call and gives back a
        # StreamStreamCall that can be written to and read from independently,
        # which is what both the watch (long-lived, with concurrent progress
        # requests) and lease renewal (send one, read one) need.
        multicallable = self.channel(endpoint).stream_stream(
            method.path,
            request_serializer=method.request.SerializeToString,
            response_deserializer=method.response.FromString,
        )
        call: grpc.aio.StreamStreamCall = multicallable()
        return call

    async def call_stream_once(
        self,
        method: StreamMethod[Req, Resp],
        request: Req,
        *,
        timeout: float | None = None,
    ) -> Resp:
        """Send one message on a bidi stream and await one reply, with failover.

        etcd exposes lease renewal only as a bidirectional stream, but a single
        renewal is logically a unary call. Rather than pretend otherwise, this
        opens a stream, sends once, reads once and closes -- which is exactly
        what etcd's own client library does for ``KeepAliveOnce``, and on an established
        HTTP/2 channel costs one round trip and no new connection.
        """
        import grpc

        deadline = self._rpc_timeout if timeout is None else timeout
        last: EtcdError | None = None

        for endpoint in self._endpoints:
            call = self.open_stream(method, endpoint)
            try:
                await call.write(request)
                await call.done_writing()
                response = await call.read()
                if response is grpc.aio.EOF or not isinstance(
                    response, method.response,
                ):
                    raise EtcdUnavailable(
                        f"{method.path}: stream closed without a reply",
                    )
                return response
            except grpc.aio.AioRpcError as exc:
                error = classify(exc)
                if not isinstance(error, EtcdUnavailable):
                    raise error from exc
                last = error
                log.debug(
                    "etcd: %s failed on %s: %s", method.path, endpoint.label,
                    error,
                )
            except EtcdUnavailable as exc:
                last = exc
            finally:
                call.cancel()

        raise EtcdUnavailable(
            f"{method.path}: no etcd member answered "
            f"({', '.join(e.label for e in self._endpoints)}): {last}",
        )

    async def close(self) -> None:
        """Close every open channel. Safe to call more than once."""
        channels = list(self._channels.values())
        self._channels.clear()
        for channel in channels:
            await channel.close()
