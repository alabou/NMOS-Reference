// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Turning `--distributed` and its flags into a validated cluster.
//!
//! Port of the **raft arm** of `nmos/registry/distributed.py`.
//!
//! # What is here, and what is not
//!
//! The Python module serves two backends and spends most of its length on
//! keeping them apart: flags named for one are *refused* when the other is
//! selected, never reinterpreted, because a `--etcd…` flag silently ignored
//! under raft is an operator reading back their own command line and believing
//! it took effect.
//!
//! etcd is deferred in this implementation (decision #10 of the port plan), so
//! that separation collapses into one refusal: **any etcd flag, and
//! `--distributedBackend etcd` itself, is refused with a message saying so.**
//! That is not the same as reinterpreting it, and it is not a silent success --
//! which is the property the Python's rules exist to guarantee.
//!
//! # The refusals that do carry over
//!
//! Every one of them is a way a cluster would otherwise come up wrong:
//!
//! * a member list that does not name this member -- the registry would start,
//!   join a cluster it is not a member of, and serve from it while no other
//!   member expected it to exist;
//! * a duplicate member -- two entries for one endpoint look like a working
//!   smaller cluster while one member's traffic lands on another's listener;
//! * **plaintext peer traffic off the loopback** -- that traffic carries every
//!   registered resource and every write that changes them, so there is no
//!   configuration in which it should be in the clear, and "we were only
//!   testing" is exactly how it ends up deployed;
//! * **plaintext peer traffic under a TLS registry** -- strictly worse than a
//!   plain-HTTP registry, because it encrypts the interface an operator can see
//!   while leaving the whole database readable and writable by anyone who can
//!   reach the port. It also fails *silently*: TLS is decided by
//!   `--raftDisableTLS` alone, so a command line carrying both that flag and a
//!   full certificate set is accepted with the certificates ignored;
//! * an incomplete certificate set -- caught before anything tries to use it.

use std::net::IpAddr;
use std::path::PathBuf;
use std::time::Duration;

use nmos_cluster::{ClusterLayout, Derivation, MemberSpec, derive_cluster};
use nmos_registry_etcd::EtcdConfig;
use nmos_registry_raft::cluster::RAFT_FLAVOUR;

/// A configuration that cannot be turned into a cluster.
///
/// Fatal at startup, always. A member that started anyway would join something
/// it had not been told to join.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DistributedConfigError(pub String);

impl std::fmt::Display for DistributedConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for DistributedConfigError {}

/// Everything the consensus backend needs, validated.
#[derive(Debug, Clone)]
pub struct RaftConfig {
    /// The derived cluster: members, order, this member's index, the token.
    pub layout: ClusterLayout,
    /// The key namespace, part of the token.
    pub namespace: String,
    /// Whether peer traffic is secured.
    pub tls: bool,
    /// The shared peer certificate chain.
    pub certificate: String,
    /// Its private key.
    pub key: String,
    /// Roots peer certificates are verified against.
    pub trusted_root_ca: Vec<String>,
    /// The one shared SAN every peer must carry.
    pub certificate_name: String,
    /// Per-message deadline.
    pub rpc_timeout: Duration,
    /// Overall deadline for one registration to commit.
    pub mutation_timeout: Duration,
    /// Where the term/vote file lives.
    ///
    /// **Not a database directory.** The log is in memory; what reaches the
    /// disk is about 24 bytes written when the election term changes. Deleting
    /// it is not like deleting an etcd data directory -- it is telling this
    /// member it has never voted, which is exactly the state election safety
    /// depends on it not being in.
    pub state_dir: PathBuf,
    /// CRL for peer certificates. One, not two: members talk only to each
    /// other, so there is a single relationship to revoke against.
    pub crl_file: String,
    /// This member's peer port.
    pub peer_port: u16,
}

/// One backend's storage-layer flag family, by name and by value.
///
/// The security refusals below -- plaintext off the loopback, plaintext under
/// a secured registry, and the certificate-set check -- are identical
/// arguments whichever storage layer is running, and they are the most
/// dangerous code in this module to duplicate. A second copy written for a new
/// backend that happened to omit one would ship an unencrypted registry
/// database on a LAN, while the refusal that should have caught it sat ten
/// lines away, working perfectly, for the other backend.
///
/// So there is one implementation, and this record is what tells it which
/// flags to name in its message and which values to inspect. `flag` builds the
/// name from the prefix, so `--etcdDisableTLS` and `--raftDisableTLS` come out
/// of the same format string. Ported from `_StorageFlags` in
/// `nmos/registry/distributed.py`, which makes the same argument.
#[derive(Debug, Clone)]
pub struct StorageFlags {
    /// Flag-name stem: `etcd` gives `--etcdCertificate`.
    pub prefix: &'static str,
    /// How the storage layer is referred to in prose, mid-sentence.
    pub noun: &'static str,
    /// What the one shared certificate covers.
    pub roles_hint: &'static str,
    /// Why the shared SAN matters, for the empty-name refusal.
    pub name_hint: &'static str,

    /// Whether TLS was disabled for this storage layer.
    pub disable_tls: bool,
    /// The shared certificate chain.
    pub certificate: String,
    /// Its private key.
    pub key: String,
    /// Roots the storage layer's certificates are verified against.
    pub trusted_root_ca: Vec<String>,
    /// The one shared SAN every member must carry.
    pub certificate_name: String,
}

impl StorageFlags {
    /// `--<prefix><Suffix>`, so one format string serves both backends.
    fn flag(&self, suffix: &str) -> String {
        format!("--{}{suffix}", self.prefix)
    }
}

/// The flags this module reads.
///
/// A struct rather than the whole CLI, so the rules can be tested without
/// building a command line -- and so it is obvious which flags participate.
#[derive(Debug, Clone)]
pub struct DistributedFlags {
    /// Whether `--distributed` was given.
    pub distributed: bool,
    /// Which storage layer was asked for.
    pub backend: String,
    /// This member's advertised host, as `host` or `host:client_port`.
    pub advertised_host: String,
    /// Each peer's advertised host, same form.
    pub neighbours: Vec<String>,
    /// The key namespace.
    pub namespace: String,
    /// Default client port when a member names none.
    pub client_port: u16,
    /// Default peer port when a member names none.
    pub peer_port: u16,
    /// Where the term/vote file lives.
    pub state_dir: String,
    /// The shared peer certificate chain.
    pub certificate: String,
    /// Its private key.
    pub key: String,
    /// Roots peer certificates are verified against.
    pub trusted_root_ca: Vec<String>,
    /// The one shared SAN every peer must carry.
    pub certificate_name: String,
    /// CRL for peer certificates.
    pub crl_file: String,
    /// Whether peer TLS was disabled.
    pub disable_tls: bool,
    /// Per-message deadline, in seconds.
    pub rpc_timeout: f64,
    /// Overall commit deadline, in seconds.
    pub mutation_timeout: f64,
    /// Whether the Registration and Query listeners run over TLS.
    ///
    /// Computed from the same three inputs that decide the registry's access
    /// policy -- TLS is on when it was not disabled *and* a certificate and key
    /// were actually supplied -- so the two can never disagree about whether a
    /// given command line describes a secured registry.
    pub registry_listeners_are_tls: bool,
    /// The `--etcd*` family, read only when the backend is `etcd`.
    ///
    /// Nested rather than flattened beside the raft fields: the two backends
    /// have their own namespace, ports, certificate set and timeouts, and
    /// sharing one set of fields between them is how `--raftNamespace` ends up
    /// quietly configuring an etcd cluster.
    pub etcd: EtcdFlags,
}

/// Resolve `--distributed` into a cluster, or `None` when it was not asked for.
///
/// # Errors
///
/// [`DistributedConfigError`] for any of the refusals in the module docs.
pub fn resolve(flags: &DistributedFlags) -> Result<Option<Cluster>, DistributedConfigError> {
    if !flags.distributed {
        // Nothing below is read, and the registry behaves exactly as it always
        // has. A flag that only matters under `--distributed` must not change
        // anything without it.
        return Ok(None);
    }

    match flags.backend.as_str() {
        "raft" => resolve_raft(flags).map(|config| Some(Cluster::Raft(Box::new(config)))),
        "etcd" => {
            resolve_etcd(flags, &flags.etcd).map(|config| Some(Cluster::Etcd(Box::new(config))))
        }
        other => Err(DistributedConfigError(format!(
            "--distributedBackend {other} is not a storage layer this registry \
             knows. Use 'raft' or 'etcd'.",
        ))),
    }
}

/// The raft arm: in-process consensus, nothing installed, no child process.
fn resolve_raft(flags: &DistributedFlags) -> Result<RaftConfig, DistributedConfigError> {
    let tls = !flags.disable_tls;
    let storage = raft_storage(flags);
    let members = canonical_members(flags)?;
    reject_plaintext_under_a_secure_registry(&storage, flags.registry_listeners_are_tls, tls)?;
    reject_plaintext_off_the_loopback(&storage, tls, &members)?;
    validate_tls_inputs(&storage, tls)?;

    let specs: Vec<MemberSpec> = members
        .iter()
        .map(|&(ref host, client, peer)| MemberSpec {
            host: host.clone(),
            client_port: client,
            peer_port: peer,
            name: None,
            // The advertised name is what peers verify; the address it
            // resolves to is what this member binds. They differ whenever a
            // routable name points at a loopback interface, which is what the
            // single-machine rig does.
            bind_address: resolve_host(host).map(|address| address.to_string()),
        })
        .collect();

    let Some(&(ref local_host, _, local_peer)) = members.first() else {
        return Err(DistributedConfigError("no members configured".to_owned()));
    };

    let layout = derive_cluster(
        &specs,
        &Derivation {
            local_host,
            local_peer_port: Some(local_peer),
            namespace: &flags.namespace,
            tls,
            flavour: RAFT_FLAVOUR,
        },
    )
    .map_err(|error| DistributedConfigError(error.0))?;

    let peer_port = layout.local.peer_port;
    Ok(RaftConfig {
        layout,
        namespace: flags.namespace.clone(),
        tls,
        certificate: flags.certificate.clone(),
        key: flags.key.clone(),
        trusted_root_ca: flags.trusted_root_ca.clone(),
        certificate_name: flags.certificate_name.clone(),
        rpc_timeout: Duration::from_secs_f64(flags.rpc_timeout.max(0.0)),
        mutation_timeout: Duration::from_secs_f64(flags.mutation_timeout.max(0.0)),
        state_dir: PathBuf::from(&flags.state_dir),
        crl_file: flags.crl_file.clone(),
        peer_port,
    })
}

/// The raft flag family, as the shared validators see it.
fn raft_storage(flags: &DistributedFlags) -> StorageFlags {
    StorageFlags {
        prefix: "raft",
        noun: "peer",
        roles_hint: "Peer",
        name_hint: "It is the one shared SAN that separates cluster members \
                    from every other device the same CA has signed.",
        disable_tls: flags.disable_tls,
        certificate: flags.certificate.clone(),
        key: flags.key.clone(),
        trusted_root_ca: flags.trusted_root_ca.clone(),
        certificate_name: flags.certificate_name.clone(),
    }
}

/// `host` or `host:client_port` into `(host, client_port, peer_port)`.
///
/// Members carry their own ports because they do not always have an address to
/// themselves. When several share one machine they must share its address as
/// well, so the port is what separates them. The peer port is the client port
/// plus one, which is the relationship between the two defaults.
fn split_member(
    value: &str,
    client_port: u16,
    peer_port: u16,
) -> Result<(String, u16, u16), DistributedConfigError> {
    let Some((host, port)) = value.rsplit_once(':') else {
        return Ok((value.to_owned(), client_port, peer_port));
    };
    let parsed = port.parse::<u16>().ok().filter(|_| !host.is_empty());
    let Some(client) = parsed.filter(|_| port.bytes().all(|b| b.is_ascii_digit())) else {
        return Err(DistributedConfigError(format!(
            "member '{value}' is not host or host:client_port",
        )));
    };
    Ok((host.to_owned(), client, client.saturating_add(1)))
}

/// The canonical member list: this member first, then its neighbours.
fn canonical_members(
    flags: &DistributedFlags,
) -> Result<Vec<(String, u16, u16)>, DistributedConfigError> {
    canonical_members_with(flags, flags.client_port, flags.peer_port)
}

/// The same member list, with the default ports named explicitly.
///
/// The two backends have their own port flags -- `--raftClientPort` and
/// `--etcdClientPort` -- and everything else about deriving the list is
/// identical, so the defaults are a parameter rather than a second copy.
fn canonical_members_with(
    flags: &DistributedFlags,
    client_port: u16,
    peer_port: u16,
) -> Result<Vec<(String, u16, u16)>, DistributedConfigError> {
    if flags.advertised_host.is_empty() {
        return Err(DistributedConfigError(
            "--distributed requires --registryAdvertisedHost naming this \
             member. It must be a SAN of this member's certificate."
                .to_owned(),
        ));
    }

    let mut members = vec![split_member(
        &flags.advertised_host,
        client_port,
        peer_port,
    )?];
    for neighbour in &flags.neighbours {
        let trimmed = neighbour.trim();
        if !trimmed.is_empty() {
            members.push(split_member(trimmed, client_port, peer_port)?);
        }
    }

    // Keyed on host **and** port: co-located members legitimately share a host
    // and are distinguished by port, so refusing a repeated host outright would
    // refuse the single-machine cluster this exists to support.
    let mut seen = std::collections::BTreeSet::new();
    let mut duplicates = std::collections::BTreeSet::new();
    for &(ref host, client, _) in &members {
        if !seen.insert((host.clone(), client)) {
            duplicates.insert(format!("{host}:{client}"));
        }
    }
    if !duplicates.is_empty() {
        return Err(DistributedConfigError(format!(
            "duplicate member(s) in the list: {}. Each member needs its own \
             host, or its own port on a shared host.",
            duplicates.into_iter().collect::<Vec<_>>().join(", "),
        )));
    }
    Ok(members)
}

/// An unsecured cluster may exist on one machine and nowhere else.
///
/// Loopback is the one case where plaintext is defensible: the packets cannot
/// leave the host. Anything else -- a private LAN address included, since
/// reachable is reachable -- is refused.
///
/// Names that do not resolve are left alone. That is a different failure with
/// its own diagnosis, and guessing about it here would turn a DNS problem into
/// a confusing security message.
fn reject_plaintext_off_the_loopback(
    storage: &StorageFlags,
    tls: bool,
    members: &[(String, u16, u16)],
) -> Result<(), DistributedConfigError> {
    if tls {
        return Ok(());
    }
    let exposed: Vec<String> = members
        .iter()
        .filter_map(|(host, _, _)| {
            let address = resolve_host(host)?;
            (!address.is_loopback()).then(|| format!("{host} ({address})"))
        })
        .collect();

    if exposed.is_empty() {
        return Ok(());
    }
    Err(DistributedConfigError(format!(
        "{} is refused off the loopback: {} would carry every registered \
         resource, and every write that changes them, in the clear. It exists \
         for a single-machine development rig and nothing else.",
        storage.flag("DisableTLS"),
        exposed.join(", "),
    )))
}

/// A secured registry may not keep its database on a plaintext peer link.
fn reject_plaintext_under_a_secure_registry(
    storage: &StorageFlags,
    registry_listeners_are_tls: bool,
    tls: bool,
) -> Result<(), DistributedConfigError> {
    if tls || !registry_listeners_are_tls {
        return Ok(());
    }

    let supplied: Vec<String> = [
        (storage.flag("Certificate"), !storage.certificate.is_empty()),
        (storage.flag("Key"), !storage.key.is_empty()),
        (
            storage.flag("TrustedRootCA"),
            !storage.trusted_root_ca.is_empty(),
        ),
    ]
    .into_iter()
    .filter_map(|(flag, given)| given.then_some(flag))
    .collect();

    let ignored = if supplied.is_empty() {
        String::new()
    } else {
        format!(
            "\n  {} would be IGNORED: {} is the only input that decides this, \
             so the certificates you passed would never reach the {} \
             transport.",
            supplied.join(", "),
            storage.flag("DisableTLS"),
            storage.noun,
        )
    };

    Err(DistributedConfigError(format!(
        "{} is refused under a registry that serves TLS. The {} link holds \
         every registered resource, so this combination is strictly worse than \
         a plain-HTTP registry: it encrypts the interface an operator can see \
         and leaves the whole database readable, and writable, by anyone who \
         can reach the port.{ignored}",
        storage.flag("DisableTLS"),
        storage.noun,
    )))
}

/// Check the certificate set before anything tries to use it.
fn validate_tls_inputs(storage: &StorageFlags, tls: bool) -> Result<(), DistributedConfigError> {
    if !tls {
        return Ok(());
    }
    if storage.certificate.is_empty() || storage.key.is_empty() {
        return Err(DistributedConfigError(format!(
            "--distributed needs {} and {}, or {} on a loopback rig. {} traffic \
             carries the whole registry.",
            storage.flag("Certificate"),
            storage.flag("Key"),
            storage.flag("DisableTLS"),
            storage.roles_hint,
        )));
    }
    if storage.trusted_root_ca.is_empty() {
        return Err(DistributedConfigError(format!(
            "--distributed needs {}: without a root to verify against, a \
             peer's certificate proves nothing.",
            storage.flag("TrustedRootCA"),
        )));
    }
    if storage.certificate_name.is_empty() {
        return Err(DistributedConfigError(format!(
            "{} may not be empty. {}",
            storage.flag("CertificateName"),
            storage.name_hint,
        )));
    }
    Ok(())
}

/// The address `host` resolves to, or `None` if it does not resolve.
fn resolve_host(host: &str) -> Option<IpAddr> {
    use std::net::ToSocketAddrs as _;
    // Port 0 because only the address is wanted; `to_socket_addrs` needs one.
    (host, 0u16)
        .to_socket_addrs()
        .ok()?
        .find(|address| address.is_ipv4())
        .map(|address| address.ip())
}

/// The `--etcd*` flags, lifted out of the parsed CLI.
#[derive(Debug, Clone, Default)]
pub struct EtcdFlags {
    /// Comma-separated client endpoints. Required with `external`.
    pub endpoints: String,
    /// Connect to an etcd someone else manages.
    pub external: bool,
    /// The etcd executable, when this registry manages one.
    pub binary: String,
    /// The managed member's data directory.
    pub data_dir: String,
    /// One-time cluster initialisation.
    pub bootstrap: bool,
    /// The key namespace, part of the cluster token.
    pub namespace: String,
    /// Default client port when a member names none.
    pub client_port: u16,
    /// Default peer port when a member names none.
    pub peer_port: u16,
    /// The shared certificate chain.
    pub certificate: String,
    /// Its private key.
    pub key: String,
    /// Roots the cluster's certificates are verified against.
    pub trusted_root_ca: Vec<String>,
    /// The one shared SAN every member must carry.
    pub certificate_name: String,
    /// CRL for client certificates.
    pub client_crl_file: String,
    /// CRL for peer certificates.
    pub peer_crl_file: String,
    /// Whether TLS was disabled.
    pub disable_tls: bool,
    /// Per-RPC deadline, in seconds.
    pub rpc_timeout: f64,
    /// Overall deadline for one mutation, in seconds.
    pub mutation_timeout: f64,
}

/// Which storage layer `--distributed` resolved to.
#[derive(Debug, Clone)]
pub enum Cluster {
    /// In-process consensus.
    Raft(Box<RaftConfig>),
    /// An etcd cluster, managed here or elsewhere.
    Etcd(Box<EtcdConfig>),
}

/// The etcd flag family, as the shared validators see it.
fn etcd_storage(flags: &EtcdFlags) -> StorageFlags {
    StorageFlags {
        prefix: "etcd",
        noun: "etcd",
        roles_hint: "etcd",
        name_hint: "It is both the gRPC target-name override and etcd's \
                    --client/peer-cert-allowed-hostname, which is what stops \
                    any other device the same Product CA has signed from \
                    writing to the registry database.",
        disable_tls: flags.disable_tls,
        certificate: flags.certificate.clone(),
        key: flags.key.clone(),
        trusted_root_ca: flags.trusted_root_ca.clone(),
        certificate_name: flags.certificate_name.clone(),
    }
}

/// `host:port` pairs out of a comma-separated endpoint list.
fn split_endpoints(value: &str) -> Vec<(String, u16)> {
    value
        .split(',')
        .map(str::trim)
        .filter(|item| !item.is_empty())
        .filter_map(|item| {
            let bare = item
                .strip_prefix("https://")
                .or_else(|| item.strip_prefix("http://"))
                .unwrap_or(item)
                .trim_end_matches('/');
            let (host, port) = bare.rsplit_once(':')?;
            Some((host.to_owned(), port.parse().ok()?))
        })
        .collect()
}

/// The etcd arm: a child process, a platform gate, and a cluster someone may
/// already own.
fn resolve_etcd(
    flags: &DistributedFlags,
    etcd: &EtcdFlags,
) -> Result<EtcdConfig, DistributedConfigError> {
    // Native Windows has no managed etcd: the binary is Tier 3 there, so this
    // registry is client-only and the cluster must be someone else's.
    let windows = cfg!(target_os = "windows");
    let external = etcd.external || windows;
    if windows && !etcd.external {
        tracing::warn!(
            "registry: --etcdExternal is implied on native Windows, where etcd \
             is not supported as a managed process",
        );
    }

    let tls = !etcd.disable_tls;
    let storage = etcd_storage(etcd);

    let explicit = split_endpoints(&etcd.endpoints);
    if external && explicit.is_empty() {
        return Err(DistributedConfigError(
            "--etcdExternal needs --etcdEndpoints: the cluster is someone \
             else's, so there is nothing to derive its addresses from."
                .to_owned(),
        ));
    }

    let (specs, local_host, local_peer, members_for_checks) =
        if external && !explicit.is_empty() && flags.neighbours.is_empty() {
            // In external mode the cluster is someone else's, and the
            // endpoints are the only truthful description of it we have.
            // Deriving the layout from an empty neighbour list instead would
            // make this a "1 member" cluster that reports "tolerates 0
            // failures" while actually talking to three -- an operator reading
            // that would believe they had no resilience.
            let specs: Vec<MemberSpec> = explicit
                .iter()
                .enumerate()
                .map(|(index, (host, port))| MemberSpec {
                    host: host.clone(),
                    client_port: *port,
                    peer_port: port.saturating_add(1),
                    name: Some(format!("external-{index}")),
                    bind_address: Some(host.clone()),
                })
                .collect();
            let members: Vec<(String, u16, u16)> = explicit
                .iter()
                .map(|(host, port)| (host.clone(), *port, port.saturating_add(1)))
                .collect();
            let Some(first) = specs.first() else {
                return Err(DistributedConfigError(
                    "--etcdEndpoints named no usable host:port".to_owned(),
                ));
            };
            let (host, peer) = (first.host.clone(), first.peer_port);
            (specs, host, peer, members)
        } else {
            let members = canonical_members_with(flags, etcd.client_port, etcd.peer_port)?;
            let specs: Vec<MemberSpec> = members
                .iter()
                .map(|(host, client, peer)| MemberSpec {
                    host: host.clone(),
                    client_port: *client,
                    peer_port: *peer,
                    name: None,
                    // A member is NAMED for its certificate but must LISTEN on
                    // an address: etcd refuses a hostname in `--listen-*-urls`
                    // outright ("expected IP in URL for binding"), so a managed
                    // member whose bind address defaulted to its own name could
                    // never start.
                    bind_address: resolve_host(host).map(|address| address.to_string()),
                })
                .collect();
            let Some((host, _, peer)) = members.first().cloned() else {
                return Err(DistributedConfigError("no members configured".to_owned()));
            };
            (specs, host, peer, members)
        };

    reject_plaintext_under_a_secure_registry(&storage, flags.registry_listeners_are_tls, tls)?;
    reject_plaintext_off_the_loopback(&storage, tls, &members_for_checks)?;
    validate_tls_inputs(&storage, tls)?;

    let layout = derive_cluster(
        &specs,
        &Derivation {
            local_host: &local_host,
            local_peer_port: Some(local_peer),
            namespace: &etcd.namespace,
            tls,
            // Empty, deliberately. etcd's token predates the flavour;
            // `raft` added one to distinguish itself, and changing etcd's now
            // would make this implementation derive a different token from
            // the Python for the same member list -- which is precisely the
            // split-cluster failure the token exists to prevent.
            flavour: "",
        },
    )
    .map_err(|error| DistributedConfigError(error.0))?;

    let endpoints: Vec<String> = if explicit.is_empty() {
        layout
            .members
            .iter()
            .map(|member| format!("{}:{}", member.host, member.client_port))
            .collect()
    } else {
        explicit
            .iter()
            .map(|(host, port)| format!("{host}:{port}"))
            .collect()
    };

    if !external && etcd.bootstrap {
        // Not an error -- forming a cluster genuinely requires every member to
        // bootstrap once -- but worth saying out loud, because leaving the
        // flag in place is the mistake that forks the cluster on a later
        // restart.
        tracing::warn!(
            "registry: --etcdBootstrap is set. This is a ONE-TIME cluster \
             initialization; remove the flag once all {} members have formed \
             the cluster, or a later restart on an emptied data directory will \
             create a second cluster.",
            layout.size(),
        );
    }

    Ok(EtcdConfig {
        layout,
        endpoints,
        namespace: etcd.namespace.clone(),
        tls,
        certificate: etcd.certificate.clone(),
        key: etcd.key.clone(),
        trusted_root_ca: etcd.trusted_root_ca.clone(),
        certificate_name: etcd.certificate_name.clone(),
        rpc_timeout: Duration::from_secs_f64(etcd.rpc_timeout.max(0.0)),
        mutation_timeout: Duration::from_secs_f64(etcd.mutation_timeout.max(0.0)),
        external,
        binary: if external {
            String::new()
        } else {
            resolve_etcd_binary(&etcd.binary)
        },
        data_dir: if external {
            PathBuf::new()
        } else {
            PathBuf::from(&etcd.data_dir)
        },
        bootstrap: etcd.bootstrap && !external,
        client_crl_file: etcd.client_crl_file.clone(),
        peer_crl_file: etcd.peer_crl_file.clone(),
    })
}

/// The etcd executable to spawn.
///
/// An explicit `--etcdBinary` wins; otherwise the repo-local copy
/// `./install-etcd.sh` fetches, and failing that whatever `etcd` is on PATH.
/// Resolved here rather than in the supervisor so a missing binary is a
/// configuration refusal at startup and not a spawn failure later.
fn resolve_etcd_binary(explicit: &str) -> String {
    if !explicit.is_empty() {
        return explicit.to_owned();
    }
    // `nmos-registry-bin/` -> `crates/` -> `rust/` -> repository root.
    let local = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .map(|root| root.join(".etcd/etcd"));
    match local {
        Some(path) if path.is_file() => path.to_string_lossy().into_owned(),
        _ => "etcd".to_owned(),
    }
}
