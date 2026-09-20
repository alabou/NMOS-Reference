// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! gRPC transport to the etcd cluster: credentials, endpoints, failover.
//!
//! Three decisions here shape everything above this layer.
//!
//! # Why methods are declared rather than generated
//!
//! Each RPC is a [`UnaryMethod`] or [`StreamMethod`] naming its own path, and
//! the service stubs are deliberately not generated. That keeps the client to
//! an explicit, audited list of the RPCs this registry actually uses -- no
//! generic passthrough, and no API surface covering the etcd methods it must
//! never call.
//!
//! `nmos/etcd/channel.py` records the same decision for the Python, and its
//! stated reason does not carry over: `protoc`'s Python service plugin emits
//! an unannotated module, whereas tonic's generated clients are fully typed.
//! The reason that does carry over is the audit surface, and it is the one
//! that mattered.
//!
//! # Why a channel per endpoint rather than one multi-address channel
//!
//! One channel per endpoint costs a few idle sockets and buys behaviour the
//! registry needs anyway: the local member can be *preferred* rather than
//! merely present, so the common case takes no network hop; failover is
//! explicit and observable instead of hidden in a load-balancing policy; and a
//! watch -- which must pin to one member for the life of the stream -- binds to
//! one channel naturally, so a reconnect after member loss is the same code
//! path as any other watch reconnect.
//!
//! # Why TLS is OpenSSL and not tonic's own
//!
//! tonic's `tls-*` features are rustls. This workspace is OpenSSL by an
//! explicit decision (`plans/20260918T175734Z-rust-tls-openssl-decision.md`):
//! both implementations sit on one `libssl.so.3` so a FIPS-validated provider
//! can later serve both from a single certification boundary. Enabling a
//! `tls-*` feature would put a second TLS stack in the binary and give up
//! exactly that.
//!
//! So the crate takes tonic with `default-features = false` and dials through
//! [`Endpoint::connect_with_connector`] over a `tokio-openssl` stream. That is
//! not a workaround; it is a better fit for what this client needs. The Python
//! sets `grpc.ssl_target_name_override` so one shared certificate validates
//! against every member whatever host the endpoint names -- which is why the
//! generated etcd certificates carry a shared SAN alongside their per-member
//! one. Here that override is simply the name handed to `into_ssl`, which is
//! both the SNI and the name verified.

use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use hyper_util::rt::TokioIo;
use openssl::ssl::{SslConnector, SslFiletype, SslMethod, SslVerifyMode};
use parking_lot::Mutex;
use prost::Message;
use std::collections::HashMap;
use tokio::net::TcpStream;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tonic::transport::{Channel, Endpoint as TonicEndpoint, Uri};
use tower::service_fn;

use crate::errors::{EtcdError, Result, classify};

/// etcd's default maximum request size is 1.5 MiB; accept responses
/// comfortably above it so a large preload page is never truncated into an
/// opaque `RESOURCE_EXHAUSTED`.
const MAX_DECODING_MESSAGE_SIZE: usize = 32 * 1024 * 1024;

/// Keepalives are on because the watch stream is idle for long stretches by
/// design -- a registry with no registrations happening still needs to notice
/// that its member died, and without keepalives a silently dropped connection
/// looks identical to "nothing has changed".
const KEEPALIVE_INTERVAL: Duration = Duration::from_secs(30);
/// How long a keepalive ping may go unanswered before the connection is
/// considered dead.
const KEEPALIVE_TIMEOUT: Duration = Duration::from_secs(10);

/// How many requests may sit unsent on a stream's request half.
///
/// Small on purpose. The only writers are a watch's one create request and
/// its occasional progress requests, so anything larger would be buffering
/// for a producer that does not exist.
const STREAM_REQUEST_BUFFER: usize = 8;

// ---------------------------------------------------------------------------
// Method descriptors
// ---------------------------------------------------------------------------

/// One unary etcd RPC, named by its fully-qualified gRPC path.
///
/// The path is a `const` string rather than something resolved at runtime. The
/// Python validates each path against the compiled proto descriptor at import,
/// because a hand-written `etcdserverpb.KV/Compaction` looks entirely
/// plausible next to `CompactionRequest` while the method is actually named
/// `Compact` -- and the only symptom is `UNIMPLEMENTED` at the first call.
///
/// That check is unnecessary here and the reason is worth stating: every path
/// below is exercised by the client's own tests against a real etcd, and a
/// wrong one fails there rather than in production. What the Python cannot do
/// and this can is keep them `const`, so the whole table is one readable list.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct UnaryMethod {
    /// The gRPC path, `/service/Method`.
    pub path: &'static str,
}

/// One bidirectional-streaming etcd RPC -- only `Watch` and `LeaseKeepAlive`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StreamMethod {
    /// The gRPC path, `/service/Method`.
    pub path: &'static str,
}

impl UnaryMethod {
    /// Declare a unary RPC.
    #[must_use]
    pub const fn new(path: &'static str) -> Self {
        Self { path }
    }
}

impl StreamMethod {
    /// Declare a streaming RPC.
    #[must_use]
    pub const fn new(path: &'static str) -> Self {
        Self { path }
    }
}

// ---------------------------------------------------------------------------
// Credentials
// ---------------------------------------------------------------------------

/// The PEM files this client presents and trusts.
///
/// The same certificate the local etcd member presents on its client and peer
/// listeners is presented here as a *client* certificate -- that is what its
/// dual `serverAuth, clientAuth` EKU is for, and it is what
/// `--client-cert-allowed-hostname` on the server side checks. Presenting an
/// ordinary device certificate instead would be rejected by that restriction,
/// which is the control that stops any device sharing the Product CA from
/// writing to the registry database.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Credentials {
    /// Every root the cluster's certificates chain to.
    pub trusted_root_ca: Vec<String>,
    /// This member's certificate chain.
    pub certificate: String,
    /// Its private key.
    pub key: String,
}

impl Credentials {
    /// Build the OpenSSL connector these files describe.
    ///
    /// # Errors
    ///
    /// `EtcdError::Other` naming the file, rather than a bare OpenSSL error
    /// with no indication of which path was unreadable.
    pub fn connector(&self) -> Result<SslConnector> {
        let mut builder = SslConnector::builder(SslMethod::tls_client())
            .map_err(|exc| EtcdError::Other(format!("cannot build a TLS context: {exc}")))?;
        for path in &self.trusted_root_ca {
            builder
                .set_ca_file(path)
                .map_err(|exc| EtcdError::Other(format!("cannot read {path:?}: {exc}")))?;
        }
        builder
            .set_certificate_chain_file(&self.certificate)
            .map_err(|exc| {
                EtcdError::Other(format!("cannot read {:?}: {exc}", self.certificate))
            })?;
        builder
            .set_private_key_file(&self.key, SslFiletype::PEM)
            .map_err(|exc| EtcdError::Other(format!("cannot read {:?}: {exc}", self.key)))?;
        // Mutual: the client verifies the member, and the member verifies this
        // client against `--client-cert-allowed-hostname`.
        builder.set_verify(SslVerifyMode::PEER);

        // **ALPN `h2`, and it is not optional.** gRPC is HTTP/2, and over TLS
        // HTTP/2 is selected by ALPN -- there is no upgrade path. Without this
        // the handshake succeeds, the server falls back to HTTP/1.1, and tonic
        // reports `h2 protocol error: http2 error` from a connection that
        // looks perfectly healthy at the TLS layer.
        //
        // Cleartext hides it completely: tonic speaks h2c with prior
        // knowledge and negotiates nothing, so every plaintext test passes.
        // Found by the first run against the real etcd certificate set.
        builder
            .set_alpn_protos(b"\x02h2")
            .map_err(|exc| EtcdError::Other(format!("cannot request ALPN h2: {exc}")))?;

        Ok(builder.build())
    }
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

/// One etcd member's client endpoint.
///
/// `target` is what is dialled (`host:port`); `local` marks the member
/// co-located with this registry, which is tried first.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Endpoint {
    /// `host:port`, with no scheme.
    pub target: String,
    /// Whether this member is co-located with this registry.
    pub local: bool,
}

impl Endpoint {
    /// How this endpoint is named in a log.
    #[must_use]
    pub fn label(&self) -> String {
        if self.local {
            format!("{} (local)", self.target)
        } else {
            self.target.clone()
        }
    }
}

/// Normalise configured endpoint strings into ordered [`Endpoint`]s.
///
/// Accepts the `https://host:port` form the CLI and etcd itself use, as well
/// as a bare `host:port`. The scheme is dropped because gRPC targets do not
/// carry one -- TLS is selected by the credentials, not by the URL -- but it
/// is accepted on input so operators can paste the same strings they give
/// etcd.
///
/// The local endpoint sorts first so it is always tried first; the rest keep
/// their configured order, which keeps failover deterministic and therefore
/// reproducible in a test.
///
/// # Errors
///
/// `EtcdError::Other` when an endpoint has no port, is empty after the scheme
/// is stripped, or when nothing is configured at all.
pub fn parse_endpoints(endpoints: &[String], local_target: Option<&str>) -> Result<Vec<Endpoint>> {
    let mut parsed: Vec<Endpoint> = Vec::new();
    let mut seen: Vec<String> = Vec::new();

    for raw in endpoints {
        let mut target = raw.trim();
        if target.is_empty() {
            continue;
        }
        for scheme in ["https://", "http://"] {
            if let Some(rest) = target.strip_prefix(scheme) {
                target = rest;
                break;
            }
        }
        let target = target.trim_end_matches('/');
        if target.is_empty() {
            return Err(EtcdError::Other(format!("empty etcd endpoint in {raw:?}")));
        }
        if !target.contains(':') {
            return Err(EtcdError::Other(format!(
                "etcd endpoint {raw:?} has no port; expected host:port",
            )));
        }
        if seen.iter().any(|s| s == target) {
            continue;
        }
        seen.push(target.to_owned());
        parsed.push(Endpoint {
            target: target.to_owned(),
            local: local_target == Some(target),
        });
    }

    if parsed.is_empty() {
        return Err(EtcdError::Other("no etcd endpoints configured".to_owned()));
    }

    // A *stable* partition, so the non-local members keep their configured
    // order. `sort_by_key` is stable in Rust, which is what makes this match
    // Python's `sorted(key=lambda e: (not e.local, index))` without carrying
    // the index around.
    parsed.sort_by_key(|endpoint| !endpoint.local);
    Ok(parsed)
}

// ---------------------------------------------------------------------------
// Channel pool
// ---------------------------------------------------------------------------

/// One gRPC channel per member, with local-first failover.
pub struct EtcdChannelPool {
    endpoints: Vec<Endpoint>,
    connector: Option<SslConnector>,
    /// The shared certificate name every member is verified against
    /// (`--etcdCertificateName`). Both the SNI and the verified name, which is
    /// what lets one certificate validate against every member regardless of
    /// the host in the endpoint.
    target_name: Option<String>,
    rpc_timeout: Duration,
    /// Created on first use and cached, so the TLS handshake is paid once
    /// rather than per RPC, and a member that is down at startup costs nothing
    /// until something tries to reach it.
    ///
    /// `Channel` is itself cheap to clone -- it is a handle onto a shared
    /// connection pool -- so the lock is held only to look one up.
    channels: Mutex<HashMap<String, Channel>>,
}

impl std::fmt::Debug for EtcdChannelPool {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("EtcdChannelPool")
            .field("endpoints", &self.endpoints)
            .field("secured", &self.connector.is_some())
            .field("target_name", &self.target_name)
            .field("rpc_timeout", &self.rpc_timeout)
            .finish_non_exhaustive()
    }
}

impl EtcdChannelPool {
    /// Build a pool over the configured members.
    ///
    /// `connector` is `None` for the `--etcdDisableTLS` testing mode.
    ///
    /// # Errors
    ///
    /// `EtcdError::Other` when no endpoints were given.
    pub fn new(
        endpoints: Vec<Endpoint>,
        connector: Option<SslConnector>,
        target_name: Option<String>,
        rpc_timeout: Duration,
    ) -> Result<Self> {
        if endpoints.is_empty() {
            return Err(EtcdError::Other(
                "EtcdChannelPool requires at least one endpoint".to_owned(),
            ));
        }
        Ok(Self {
            endpoints,
            connector,
            target_name,
            rpc_timeout,
            channels: Mutex::new(HashMap::new()),
        })
    }

    /// The members, local first.
    #[must_use]
    pub fn endpoints(&self) -> &[Endpoint] {
        &self.endpoints
    }

    /// The channel for one endpoint, created on first use.
    ///
    /// # Errors
    ///
    /// `EtcdError::Unavailable` when the member cannot be reached or the TLS
    /// handshake fails -- retryable, because another member may answer.
    pub async fn channel(&self, endpoint: &Endpoint) -> Result<Channel> {
        if let Some(existing) = self.channels.lock().get(&endpoint.target) {
            return Ok(existing.clone());
        }

        let created = self.connect(endpoint).await?;
        // Another task may have connected the same endpoint while this one was
        // awaiting. Keeping the first insertion rather than overwriting means
        // the loser's channel is simply dropped, which closes it -- cheaper
        // than holding the lock across the handshake, which would serialise
        // every first connection in the cluster behind one member's timeout.
        let mut channels = self.channels.lock();
        Ok(channels
            .entry(endpoint.target.clone())
            .or_insert(created)
            .clone())
    }

    async fn connect(&self, endpoint: &Endpoint) -> Result<Channel> {
        // tonic needs a URI for the `:authority` header even though the
        // dialling below is ours. The scheme decides nothing here.
        let uri: Uri = format!("http://{}", endpoint.target)
            .parse()
            .map_err(|exc| {
                EtcdError::Other(format!("bad etcd target {:?}: {exc}", endpoint.target))
            })?;

        let builder = TonicEndpoint::from(uri)
            .http2_keep_alive_interval(KEEPALIVE_INTERVAL)
            .keep_alive_timeout(KEEPALIVE_TIMEOUT)
            // Keepalives while no call is in flight: the watch stream is idle
            // by design, and that is exactly when a dead member must still be
            // noticed.
            .keep_alive_while_idle(true);

        let Some(connector) = self.connector.clone() else {
            return builder
                .connect()
                .await
                .map_err(|exc| EtcdError::Unavailable(format!("{}: {exc}", endpoint.label())));
        };

        // The shared-certificate name, or the endpoint's own host when none is
        // configured. This is the `ssl_target_name_override` equivalent.
        let verify_name = self.target_name.clone().unwrap_or_else(|| {
            endpoint
                .target
                .rsplit_once(':')
                .map_or(endpoint.target.clone(), |(host, _)| host.to_owned())
        });
        let target = endpoint.target.clone();

        builder
            .connect_with_connector(service_fn(move |_: Uri| {
                let connector = connector.clone();
                let verify_name = verify_name.clone();
                let target = target.clone();
                async move {
                    let tcp = TcpStream::connect(&target).await?;
                    let config = connector.configure()?;
                    // `into_ssl` sets both the SNI and the name the peer
                    // certificate is verified against, which is the whole of
                    // what `grpc.ssl_target_name_override` does.
                    let ssl = config.into_ssl(&verify_name)?;
                    let mut stream = tokio_openssl::SslStream::new(ssl, tcp)?;
                    Pin::new(&mut stream).connect().await?;
                    Ok::<_, Box<dyn std::error::Error + Send + Sync>>(TokioIo::new(stream))
                }
            }))
            .await
            .map_err(|exc| EtcdError::Unavailable(format!("{}: {exc}", endpoint.label())))
    }

    /// Invoke a unary RPC, trying each endpoint until one answers.
    ///
    /// Failover is only attempted for errors another member could plausibly
    /// answer. A rejection -- bad credentials, a malformed request -- is
    /// returned immediately: retrying it against every member turns one clear
    /// error into N confusing ones and delays the answer by the full deadline
    /// each time.
    ///
    /// # Errors
    ///
    /// The first non-retryable failure, or `EtcdError::Unavailable` naming
    /// every member when none answered.
    pub async fn call<Req, Resp>(
        &self,
        method: UnaryMethod,
        request: Req,
        timeout: Option<Duration>,
    ) -> Result<Resp>
    where
        Req: Message + Clone + 'static,
        Resp: Message + Default + 'static,
    {
        let deadline = timeout.unwrap_or(self.rpc_timeout);
        let mut last: Option<EtcdError> = None;

        for endpoint in &self.endpoints {
            match self.call_one(method, &request, endpoint, deadline).await {
                Ok(response) => return Ok(response),
                Err(error) if error.is_retryable() => {
                    tracing::debug!(
                        method = method.path,
                        endpoint = %endpoint.label(),
                        %error,
                        "etcd: call failed, trying the next member",
                    );
                    last = Some(error);
                }
                Err(error) => return Err(error),
            }
        }

        Err(EtcdError::Unavailable(format!(
            "{}: no etcd member answered ({}): {}",
            method.path,
            self.member_list(),
            last.map_or_else(
                || "no attempt was made".to_owned(),
                |e| e.message().to_owned()
            ),
        )))
    }

    async fn call_one<Req, Resp>(
        &self,
        method: UnaryMethod,
        request: &Req,
        endpoint: &Endpoint,
        deadline: Duration,
    ) -> Result<Resp>
    where
        Req: Message + Clone + 'static,
        Resp: Message + Default + 'static,
    {
        let channel = self.channel(endpoint).await?;
        let mut grpc =
            tonic::client::Grpc::new(channel).max_decoding_message_size(MAX_DECODING_MESSAGE_SIZE);
        grpc.ready()
            .await
            .map_err(|exc| EtcdError::Unavailable(format!("{}: {exc}", endpoint.label())))?;

        let path = http::uri::PathAndQuery::from_static(method.path);
        // Cloned because `tonic::Request` takes the message by value and the
        // failover loop may send the same one to several members. Every prost
        // message derives `Clone`, and for the requests this client sends --
        // a key, a value and a few integers -- the copy is negligible beside
        // the round trip it precedes.
        let mut tonic_request = tonic::Request::new(request.clone());
        tonic_request.set_timeout(deadline);

        grpc.unary(tonic_request, path, tonic_prost::ProstCodec::default())
            .await
            .map(tonic::Response::into_inner)
            .map_err(|status| classify(&status))
    }

    /// Open a bidirectional stream on one specific endpoint.
    ///
    /// Deliberately takes an explicit endpoint and does **not** fail over: a
    /// stream carries state the caller owns. The watch in particular is
    /// positioned at a revision, so silently reconnecting it to another member
    /// underneath the caller would resume from the wrong place. The caller
    /// chooses the member, notices the failure, and re-opens at
    /// `last_applied_revision + 1` -- which is the same code path a normal
    /// watch reconnect takes anyway.
    ///
    /// Returns a sender for further requests and the response stream. The
    /// sender's buffer is small because the only later writers are a watch's
    /// occasional progress requests; dropping it ends the stream.
    ///
    /// # `first` is not a convenience, it is the whole call
    ///
    /// The opening request is seeded into the channel **before** the call is
    /// made, and it has to be. `Grpc::streaming` does not return until the
    /// response *headers* arrive, and etcd's Go server sends headers lazily --
    /// only when its handler first writes. Both bidi handlers here (`Watch`
    /// and `LeaseKeepAlive`) write nothing until they have received a request.
    ///
    /// So opening the stream first and sending afterwards deadlocks: the
    /// client waits for headers that the server will not send until it
    /// receives a message the client is not yet in a position to send.
    ///
    /// Measured, not reasoned about: with the request sent after the open, all
    /// six unary tests in `tests/live_etcd.rs` passed and all four streaming
    /// tests hung indefinitely against a real etcd.
    ///
    /// # Errors
    ///
    /// `EtcdError::Unavailable` when the member cannot be reached.
    pub async fn open_stream<Req, Resp>(
        &self,
        method: StreamMethod,
        endpoint: &Endpoint,
        first: Req,
    ) -> Result<(mpsc::Sender<Req>, tonic::Streaming<Resp>)>
    where
        Req: Message + Clone + Send + 'static,
        Resp: Message + Default + 'static,
    {
        let channel = self.channel(endpoint).await?;
        let mut grpc =
            tonic::client::Grpc::new(channel).max_decoding_message_size(MAX_DECODING_MESSAGE_SIZE);
        grpc.ready()
            .await
            .map_err(|exc| EtcdError::Unavailable(format!("{}: {exc}", endpoint.label())))?;

        let (sender, receiver) = mpsc::channel::<Req>(STREAM_REQUEST_BUFFER);
        // Cannot fail: the buffer is larger than one and nothing else holds a
        // sender yet. Spelled as a `Result` anyway rather than unwrapped,
        // because this crate's write path is held to being panic-free.
        sender
            .try_send(first)
            .map_err(|_| EtcdError::Other(format!("{}: could not seed the stream", method.path)))?;

        let path = http::uri::PathAndQuery::from_static(method.path);
        let responses = grpc
            .streaming(
                tonic::Request::new(ReceiverStream::new(receiver)),
                path,
                tonic_prost::ProstCodec::default(),
            )
            .await
            .map_err(|status| classify(&status))?;
        Ok((sender, responses.into_inner()))
    }

    /// Send one message on a bidi stream and await one reply, with failover.
    ///
    /// etcd exposes lease renewal only as a bidirectional stream, but a single
    /// renewal is logically a unary call. Rather than pretend otherwise, this
    /// opens a stream, sends once, reads once and closes -- which is exactly
    /// what etcd's own client library does for `KeepAliveOnce`, and on an
    /// established HTTP/2 channel costs one round trip and no new connection.
    ///
    /// # Errors
    ///
    /// The first non-retryable failure, or `EtcdError::Unavailable` naming
    /// every member when none answered.
    pub async fn call_stream_once<Req, Resp>(
        &self,
        method: StreamMethod,
        request: Req,
        timeout: Option<Duration>,
    ) -> Result<Resp>
    where
        Req: Message + Clone + Send + 'static,
        Resp: Message + Default + 'static,
    {
        let deadline = timeout.unwrap_or(self.rpc_timeout);
        let mut last: Option<EtcdError> = None;

        for endpoint in &self.endpoints {
            match self
                .stream_once_on::<Req, Resp>(method, request.clone(), endpoint, deadline)
                .await
            {
                Ok(response) => return Ok(response),
                Err(error) if error.is_retryable() => {
                    tracing::debug!(
                        method = method.path,
                        endpoint = %endpoint.label(),
                        %error,
                        "etcd: stream call failed, trying the next member",
                    );
                    last = Some(error);
                }
                Err(error) => return Err(error),
            }
        }

        Err(EtcdError::Unavailable(format!(
            "{}: no etcd member answered ({}): {}",
            method.path,
            self.member_list(),
            last.map_or_else(
                || "no attempt was made".to_owned(),
                |e| e.message().to_owned()
            ),
        )))
    }

    async fn stream_once_on<Req, Resp>(
        &self,
        method: StreamMethod,
        request: Req,
        endpoint: &Endpoint,
        deadline: Duration,
    ) -> Result<Resp>
    where
        Req: Message + Clone + Send + 'static,
        Resp: Message + Default + 'static,
    {
        let exchange = async {
            let (sender, mut responses) = self
                .open_stream::<Req, Resp>(method, endpoint, request)
                .await?;
            // Dropping the sender is `done_writing`: it ends the request half
            // so etcd knows no more messages are coming. The one request was
            // seeded by `open_stream`, which is what lets the call complete at
            // all -- see its documentation.
            drop(sender);
            responses
                .message()
                .await
                .map_err(|status| classify(&status))?
                .ok_or_else(|| {
                    EtcdError::Unavailable(format!(
                        "{}: stream closed without a reply",
                        method.path,
                    ))
                })
        };

        // The deadline is applied here rather than through `set_timeout`,
        // because a streaming call's timeout covers the whole stream and this
        // one is logically a single request/response pair.
        match tokio::time::timeout(deadline, exchange).await {
            Ok(result) => result,
            Err(_) => Err(EtcdError::Unavailable(format!(
                "{}: {} did not answer within {:?}",
                method.path,
                endpoint.label(),
                deadline,
            ))),
        }
    }

    fn member_list(&self) -> String {
        self.endpoints
            .iter()
            .map(Endpoint::label)
            .collect::<Vec<_>>()
            .join(", ")
    }
}

/// Shared by the whole client, so the pool is built once.
pub type SharedPool = Arc<EtcdChannelPool>;

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| (*s).to_owned()).collect()
    }

    #[test]
    fn the_scheme_is_accepted_and_dropped() {
        // Operators paste the same strings they give etcd, which carry a
        // scheme; gRPC targets do not.
        let parsed =
            parse_endpoints(&strings(&["https://a:1", "http://b:2", "c:3"]), None).unwrap();
        assert_eq!(
            parsed.iter().map(|e| e.target.as_str()).collect::<Vec<_>>(),
            ["a:1", "b:2", "c:3"],
        );
    }

    #[test]
    fn the_local_member_sorts_first_and_the_rest_keep_their_order() {
        // Local-first is what makes the common case take no network hop; the
        // rest keeping configured order is what makes failover reproducible.
        let parsed = parse_endpoints(&strings(&["a:1", "b:2", "c:3"]), Some("b:2")).unwrap();
        assert_eq!(
            parsed.iter().map(|e| e.target.as_str()).collect::<Vec<_>>(),
            ["b:2", "a:1", "c:3"],
        );
        assert!(parsed[0].local);
        assert!(!parsed[1].local && !parsed[2].local);
    }

    #[test]
    fn a_local_target_that_matches_nothing_leaves_the_order_alone() {
        let parsed = parse_endpoints(&strings(&["a:1", "b:2"]), Some("z:9")).unwrap();
        assert_eq!(
            parsed.iter().map(|e| e.target.as_str()).collect::<Vec<_>>(),
            ["a:1", "b:2"],
        );
        assert!(parsed.iter().all(|e| !e.local));
    }

    #[test]
    fn duplicates_collapse_and_blanks_are_skipped() {
        let parsed =
            parse_endpoints(&strings(&["a:1", "https://a:1/", "  ", "a:1", "b:2"]), None).unwrap();
        assert_eq!(
            parsed.iter().map(|e| e.target.as_str()).collect::<Vec<_>>(),
            ["a:1", "b:2"],
        );
    }

    #[test]
    fn an_endpoint_without_a_port_is_refused() {
        // gRPC would otherwise dial a default port nobody configured.
        let error = parse_endpoints(&strings(&["host"]), None).unwrap_err();
        assert_eq!(
            error.message(),
            "etcd endpoint \"host\" has no port; expected host:port",
        );
    }

    #[test]
    fn a_scheme_with_nothing_after_it_is_refused() {
        let error = parse_endpoints(&strings(&["https://"]), None).unwrap_err();
        assert_eq!(error.message(), "empty etcd endpoint in \"https://\"");
    }

    #[test]
    fn no_endpoints_at_all_is_refused() {
        assert_eq!(
            parse_endpoints(&[], None).unwrap_err().message(),
            "no etcd endpoints configured",
        );
        assert_eq!(
            parse_endpoints(&strings(&["", "   "]), None)
                .unwrap_err()
                .message(),
            "no etcd endpoints configured",
        );
    }

    #[test]
    fn a_label_says_which_member_is_local() {
        // It reaches a log, and "which member answered" is the first question
        // anyone asks of a failover.
        assert_eq!(
            Endpoint {
                target: "a:1".to_owned(),
                local: true,
            }
            .label(),
            "a:1 (local)",
        );
        assert_eq!(
            Endpoint {
                target: "a:1".to_owned(),
                local: false,
            }
            .label(),
            "a:1",
        );
    }

    #[test]
    fn a_pool_needs_a_member() {
        let error =
            EtcdChannelPool::new(Vec::new(), None, None, Duration::from_secs(1)).unwrap_err();
        assert_eq!(
            error.message(),
            "EtcdChannelPool requires at least one endpoint",
        );
    }

    #[test]
    fn credentials_name_the_file_they_could_not_read() {
        // A bare OpenSSL error says "system lib" and nothing about which path
        // was wrong, which is the whole of what an operator needs.
        let credentials = Credentials {
            trusted_root_ca: vec!["/nonexistent/ca.pem".to_owned()],
            certificate: "/nonexistent/cert.pem".to_owned(),
            key: "/nonexistent/key.pem".to_owned(),
        };
        let error = credentials.connector().unwrap_err();
        assert!(
            error.message().contains("/nonexistent/ca.pem"),
            "{}",
            error.message(),
        );
    }
}
