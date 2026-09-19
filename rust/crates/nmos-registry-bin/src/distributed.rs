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
    /// Any etcd flag the command line carried, by name.
    ///
    /// Collected rather than ignored: see the module docs.
    pub etcd_flags_given: Vec<String>,
}

/// Resolve `--distributed` into a cluster, or `None` when it was not asked for.
///
/// # Errors
///
/// [`DistributedConfigError`] for any of the refusals in the module docs.
pub fn resolve(flags: &DistributedFlags) -> Result<Option<RaftConfig>, DistributedConfigError> {
    if !flags.distributed {
        // Nothing below is read, and the registry behaves exactly as it always
        // has. A flag that only matters under `--distributed` must not change
        // anything without it.
        return Ok(None);
    }

    if flags.backend != "raft" {
        return Err(DistributedConfigError(format!(
            "--distributedBackend {} is not available in this implementation. \
             Only 'raft' is built here; the etcd backend is deferred, not \
             removed, and the Python registry still offers it.",
            flags.backend,
        )));
    }
    if !flags.etcd_flags_given.is_empty() {
        // Refused, never reinterpreted. An etcd flag silently ignored is an
        // operator reading back their own command line and believing it took
        // effect.
        return Err(DistributedConfigError(format!(
            "{} named for the etcd backend, which is not built in this \
             implementation. They are refused rather than ignored, because a \
             flag that is silently dropped reads back as one that was honoured.",
            flags.etcd_flags_given.join(", "),
        )));
    }

    let tls = !flags.disable_tls;
    let members = canonical_members(flags)?;
    reject_plaintext_under_a_secure_registry(flags, tls, &members)?;
    reject_plaintext_off_the_loopback(tls, &members)?;
    validate_tls_inputs(flags, tls)?;

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
    Ok(Some(RaftConfig {
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
    }))
}

/// `host` or `host:client_port` into `(host, client_port, peer_port)`.
///
/// Members carry their own ports because they do not always have an address to
/// themselves. When several share one machine they must share its address as
/// well, so the port is what separates them. The peer port is the client port
/// plus one, which is the relationship between the two defaults.
fn split_member(
    value: &str,
    flags: &DistributedFlags,
) -> Result<(String, u16, u16), DistributedConfigError> {
    let Some((host, port)) = value.rsplit_once(':') else {
        return Ok((value.to_owned(), flags.client_port, flags.peer_port));
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
    if flags.advertised_host.is_empty() {
        return Err(DistributedConfigError(
            "--distributed requires --registryAdvertisedHost naming this \
             member. It must be a SAN of this member's certificate."
                .to_owned(),
        ));
    }

    let mut members = vec![split_member(&flags.advertised_host, flags)?];
    for neighbour in &flags.neighbours {
        let trimmed = neighbour.trim();
        if !trimmed.is_empty() {
            members.push(split_member(trimmed, flags)?);
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
        "--raftDisableTLS is refused off the loopback: {} would carry every \
         registered resource, and every write that changes them, in the clear. \
         It exists for a single-machine development rig and nothing else.",
        exposed.join(", "),
    )))
}

/// A secured registry may not keep its database on a plaintext peer link.
fn reject_plaintext_under_a_secure_registry(
    flags: &DistributedFlags,
    tls: bool,
    _members: &[(String, u16, u16)],
) -> Result<(), DistributedConfigError> {
    if tls || !flags.registry_listeners_are_tls {
        return Ok(());
    }

    let supplied: Vec<&str> = [
        ("--raftCertificate", !flags.certificate.is_empty()),
        ("--raftKey", !flags.key.is_empty()),
        ("--raftTrustedRootCA", !flags.trusted_root_ca.is_empty()),
    ]
    .into_iter()
    .filter_map(|(flag, given)| given.then_some(flag))
    .collect();

    let ignored = if supplied.is_empty() {
        String::new()
    } else {
        format!(
            "\n  {} would be IGNORED: --raftDisableTLS is the only input that \
             decides this, so the certificates you passed would never reach \
             the peer transport.",
            supplied.join(", "),
        )
    };

    Err(DistributedConfigError(format!(
        "--raftDisableTLS is refused under a registry that serves TLS. The \
         peer link holds every registered resource, so this combination is \
         strictly worse than a plain-HTTP registry: it encrypts the interface \
         an operator can see and leaves the whole database readable, and \
         writable, by anyone who can reach the port.{ignored}",
    )))
}

/// Check the certificate set before anything tries to use it.
fn validate_tls_inputs(flags: &DistributedFlags, tls: bool) -> Result<(), DistributedConfigError> {
    if !tls {
        return Ok(());
    }
    if flags.certificate.is_empty() || flags.key.is_empty() {
        return Err(DistributedConfigError(
            "--distributed needs --raftCertificate and --raftKey, or \
             --raftDisableTLS on a loopback rig. Peer traffic carries the \
             whole registry."
                .to_owned(),
        ));
    }
    if flags.trusted_root_ca.is_empty() {
        return Err(DistributedConfigError(
            "--distributed needs --raftTrustedRootCA: without a root to verify \
             against, a peer's certificate proves nothing."
                .to_owned(),
        ));
    }
    if flags.certificate_name.is_empty() {
        return Err(DistributedConfigError(
            "--raftCertificateName may not be empty. It is the one shared SAN \
             that separates cluster members from every other device the same \
             CA has signed."
                .to_owned(),
        ));
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
