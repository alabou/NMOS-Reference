// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The member set, its canonical order, and the token every member derives.
//!
//! Port of `nmos/cluster/layout.py`.
//!
//! Every value here is a pure function of the configured member list, and that
//! is the whole point: two members handed the same list must derive the same
//! names, the same order and the same token, or they form two clusters that
//! each believe they are the whole thing.
//!
//! # Why this is its own crate
//!
//! It is shared. The consensus layer needs the member index and the token; the
//! binary needs the same derivation to validate its flags before anything
//! starts; a future etcd backend needs the endpoint formatting. The Python
//! keeps it in `nmos/cluster/` for the same reason, one level above both
//! storage layers.
//!
//! # The token must match the Python's byte for byte
//!
//! It travels in the transport handshake, and a member whose token differs is
//! refused. So a mixed Python/Rust cluster does not merely need an equivalent
//! hash -- it needs the same digest over the same material string, and
//! `token_parity` asserts exactly that against vectors recorded from the
//! Python.

#![forbid(unsafe_code)]
#![cfg_attr(
    test,
    allow(
        clippy::unwrap_used,
        clippy::expect_used,
        clippy::indexing_slicing,
        clippy::panic
    )
)]

use openssl::hash::{MessageDigest, hash};

/// Cluster sizes this implementation accepts.
///
/// An even-sized cluster tolerates no more failures than the odd size below
/// it, so it costs a member and buys nothing.
pub const PERMITTED_SIZES: [usize; 3] = [1, 3, 5];

/// Default client port, as the etcd backend advertises it.
pub const DEFAULT_CLIENT_PORT: u16 = 2381;

/// Default peer port, as the etcd backend advertises it.
pub const DEFAULT_PEER_PORT: u16 = 2382;

/// Every derived member name begins with this, and so does the token.
pub const MEMBER_NAME_PREFIX: &str = "nmos-registry";

/// A configuration that cannot be turned into a cluster.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClusterConfigError(pub String);

impl std::fmt::Display for ClusterConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ClusterConfigError {}

/// Replace every run of characters a name may not contain with one `-`, and
/// trim leading and trailing `-`.
///
/// Hand-rolled rather than a regex: the Python's `[^A-Za-z0-9._-]+` is a
/// character class and a repetition, and pulling the regex crate into this
/// crate to express four lines of `char::is_ascii_*` would be the larger
/// dependency for the smaller reason.
#[must_use]
pub fn sanitise(host: &str) -> String {
    let mut out = String::with_capacity(host.len());
    let mut in_run = false;
    for ch in host.chars() {
        if ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | '-') {
            out.push(ch);
            in_run = false;
        } else if !in_run {
            out.push('-');
            in_run = true;
        }
    }
    out.trim_matches('-').to_owned()
}

/// One configured member, before derivation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemberSpec {
    /// Advertised hostname. Must be a SAN of that member's certificate -- it
    /// is the name every peer and every registry client verifies against.
    pub host: String,
    /// Port its client listener advertises.
    pub client_port: u16,
    /// Port its peer listener advertises.
    pub peer_port: u16,
    /// Explicit member name.
    ///
    /// Normally `None` and derived from the host. It exists for the
    /// same-machine test rigs, where several members share one address and
    /// differ only by port, so a host-derived name would collide.
    pub name: Option<String>,
    /// Address to listen on, when it differs from the advertised host.
    pub bind_address: Option<String>,
}

impl MemberSpec {
    /// A spec with the default ports and a host-derived name.
    #[must_use]
    pub fn new(host: impl Into<String>) -> Self {
        Self {
            host: host.into(),
            client_port: DEFAULT_CLIENT_PORT,
            peer_port: DEFAULT_PEER_PORT,
            name: None,
            bind_address: None,
        }
    }

    /// The name this spec would take if its host appeared only once.
    #[must_use]
    pub fn derived_name(&self) -> String {
        self.name
            .clone()
            .unwrap_or_else(|| format!("{MEMBER_NAME_PREFIX}-{}", sanitise(&self.host)))
    }
}

/// One member of a derived cluster.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Member {
    /// Its derived name. A pure function of the member list.
    pub name: String,
    /// Its advertised host.
    pub host: String,
    /// Its client port.
    pub client_port: u16,
    /// Its peer port.
    pub peer_port: u16,
    /// The address it binds, which may differ from `host`.
    pub bind_address: String,
}

impl Member {
    /// `host:port` as a client dials it, without a scheme.
    #[must_use]
    pub fn client_target(&self) -> String {
        format!("{}:{}", self.host, self.client_port)
    }

    /// The URL this member advertises for peers.
    #[must_use]
    pub fn advertise_peer_url(&self, tls: bool) -> String {
        let scheme = if tls { "https" } else { "http" };
        format!("{scheme}://{}:{}", self.host, self.peer_port)
    }

    /// The URL this member advertises for clients.
    #[must_use]
    pub fn advertise_client_url(&self, tls: bool) -> String {
        let scheme = if tls { "https" } else { "http" };
        format!("{scheme}://{}:{}", self.host, self.client_port)
    }

    /// The peer URL this member listens on.
    #[must_use]
    pub fn listen_peer_url(&self, tls: bool) -> String {
        let scheme = if tls { "https" } else { "http" };
        format!("{scheme}://{}:{}", self.bind_address, self.peer_port)
    }

    /// The client URL this member listens on.
    #[must_use]
    pub fn listen_client_url(&self, tls: bool) -> String {
        let scheme = if tls { "https" } else { "http" };
        format!("{scheme}://{}:{}", self.bind_address, self.client_port)
    }
}

/// A validated, fully derived cluster.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClusterLayout {
    /// Every member, in canonical order.
    pub members: Vec<Member>,
    /// Which one this process is.
    pub local: Member,
    /// The identity every member derives identically.
    pub token: String,
    /// The key namespace, which is part of the token.
    pub namespace: String,
    /// Whether the storage layer speaks TLS.
    pub tls: bool,
}

impl ClusterLayout {
    /// How many members there are.
    #[must_use]
    pub fn size(&self) -> usize {
        self.members.len()
    }

    /// How many must agree.
    #[must_use]
    pub fn quorum(&self) -> usize {
        // Saturating, though a member count can never approach the limit:
        // the lint that forbids bare arithmetic is the one keeping the write
        // path panic-free, and an exception granted because the author
        // checked is the kind that outlives the reasoning behind it.
        self.size().div_euclid(2).saturating_add(1)
    }

    /// How many may fail.
    #[must_use]
    pub fn failures_tolerated(&self) -> usize {
        self.size().saturating_sub(self.quorum())
    }

    /// etcd's `--initial-cluster` string, identical on every member.
    ///
    /// The one storage-specific formatter here. The consensus backend never
    /// calls it: its member set travels in the transport handshake and is
    /// checked against the token, rather than being formatted into a child
    /// process's command line.
    #[must_use]
    pub fn initial_cluster(&self, tls: Option<bool>) -> String {
        let secure = tls.unwrap_or(self.tls);
        self.members
            .iter()
            .map(|m| format!("{}={}", m.name, m.advertise_peer_url(secure)))
            .collect::<Vec<_>>()
            .join(",")
    }

    /// Every member's client endpoint, local first.
    ///
    /// Local first because a channel pool tries them in order and the
    /// co-located member costs no network hop. The rest keep canonical order so
    /// failover is deterministic and reproducible in a test.
    #[must_use]
    pub fn client_endpoints(&self) -> Vec<String> {
        let mut endpoints = vec![self.local.client_target()];
        endpoints.extend(
            self.members
                .iter()
                .filter(|m| m.name != self.local.name)
                .map(Member::client_target),
        );
        endpoints
    }

    /// Find a member by its derived name.
    #[must_use]
    pub fn member_by_name(&self, name: &str) -> Option<&Member> {
        self.members.iter().find(|m| m.name == name)
    }
}

/// How a cluster's identity is derived.
#[derive(Debug, Clone)]
pub struct Derivation<'a> {
    /// This registry's advertised host, used to find which member is us.
    pub local_host: &'a str,
    /// Disambiguates when several members share `local_host`.
    pub local_peer_port: Option<u16>,
    /// The key namespace, part of the token.
    pub namespace: &'a str,
    /// Whether the storage layer speaks TLS.
    pub tls: bool,
    /// Which storage layer this layout is for, part of the token.
    ///
    /// Empty -- what the etcd path passes -- reproduces the original token
    /// exactly, so existing deployments keep their data directories.
    pub flavour: &'a str,
}

/// Validate a member set and derive everything else from it.
///
/// # Errors
///
/// [`ClusterConfigError`] if the set is not 1/3/5 members, contains a
/// duplicate name or endpoint, or does not contain `local_host`.
pub fn derive_cluster(
    specs: &[MemberSpec],
    derivation: &Derivation<'_>,
) -> Result<ClusterLayout, ClusterConfigError> {
    if specs.is_empty() {
        return Err(ClusterConfigError(
            "no members configured; --distributed needs \
             --registryAdvertisedHost plus --registryNeighbour"
                .to_owned(),
        ));
    }

    if !PERMITTED_SIZES.contains(&specs.len()) {
        let permitted = PERMITTED_SIZES
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>()
            .join(", ");
        return Err(ClusterConfigError(format!(
            "a cluster must have {permitted} members, got {}. An even-sized \
             cluster tolerates no more failures than the odd size below it.",
            specs.len(),
        )));
    }

    for spec in specs {
        validate_spec(spec)?;
    }

    // Total order over (host, peer_port, client_port): every member sorts the
    // same list the same way, which is what makes the derivation agree across
    // hosts.
    let mut ordered: Vec<&MemberSpec> = specs.iter().collect();
    ordered.sort_by(|a, b| {
        (&a.host, a.peer_port, a.client_port).cmp(&(&b.host, b.peer_port, b.client_port))
    });

    // Members that share a host are told apart by their peer port, because the
    // host alone no longer identifies them. Co-location is not exotic: members
    // on one machine must share its address, so the port is the only thing left
    // that differs.
    //
    // A host appearing once keeps the plain `nmos-registry-<host>`: the name
    // becomes a data-directory path, and changing it for existing deployments
    // would orphan their databases.
    let members: Vec<Member> = ordered
        .iter()
        .map(|spec| {
            let shared = ordered
                .iter()
                .filter(|other| other.host == spec.host)
                .count()
                > 1;
            Member {
                name: spec.name.clone().unwrap_or_else(|| {
                    if shared {
                        format!("{}-{}", spec.derived_name(), spec.peer_port)
                    } else {
                        spec.derived_name()
                    }
                }),
                host: spec.host.clone(),
                client_port: spec.client_port,
                peer_port: spec.peer_port,
                bind_address: spec
                    .bind_address
                    .clone()
                    .unwrap_or_else(|| spec.host.clone()),
            }
        })
        .collect();

    reject_duplicates(&members)?;
    let local = find_local(&members, derivation.local_host, derivation.local_peer_port)?;
    let token = cluster_token(&members, derivation.namespace, derivation.flavour)?;

    Ok(ClusterLayout {
        members,
        local,
        token,
        namespace: derivation.namespace.to_owned(),
        tls: derivation.tls,
    })
}

fn validate_spec(spec: &MemberSpec) -> Result<(), ClusterConfigError> {
    if spec.host.is_empty() || spec.host != spec.host.trim() {
        return Err(ClusterConfigError(format!(
            "member host '{}' is empty or has surrounding whitespace",
            spec.host,
        )));
    }
    for (label, port) in [("client", spec.client_port), ("peer", spec.peer_port)] {
        // `u16` already excludes everything above 65535, so only zero is left
        // -- which the Python's `1 <= port` also rejects.
        if port == 0 {
            return Err(ClusterConfigError(format!(
                "{}: {label} port {port} is out of range",
                spec.host,
            )));
        }
    }
    if spec.client_port == spec.peer_port {
        return Err(ClusterConfigError(format!(
            "{}: client and peer ports are both {}; they are separate \
             listeners and cannot share a port",
            spec.host, spec.client_port,
        )));
    }
    if spec.name.as_ref().is_some_and(String::is_empty) {
        return Err(ClusterConfigError(format!(
            "{}: explicit member name is empty",
            spec.host,
        )));
    }
    Ok(())
}

/// Refuse a set with a repeated name or endpoint.
///
/// A duplicate name makes the join fail; a duplicate endpoint is worse, because
/// it can look like a working smaller cluster while one member's traffic
/// silently lands on another's listener.
fn reject_duplicates(members: &[Member]) -> Result<(), ClusterConfigError> {
    let mut names = std::collections::HashSet::new();
    let mut peers = std::collections::HashSet::new();
    let mut clients = std::collections::HashSet::new();

    for member in members {
        if !names.insert(member.name.clone()) {
            return Err(ClusterConfigError(format!(
                "duplicate member name '{}'. Members sharing a host must be \
                 given explicit distinct names.",
                member.name,
            )));
        }
        if !peers.insert((member.host.clone(), member.peer_port)) {
            return Err(ClusterConfigError(format!(
                "duplicate peer endpoint {}:{}",
                member.host, member.peer_port,
            )));
        }
        if !clients.insert((member.host.clone(), member.client_port)) {
            return Err(ClusterConfigError(format!(
                "duplicate client endpoint {}",
                member.client_target(),
            )));
        }
    }
    Ok(())
}

/// Locate this registry's own member entry.
///
/// A configuration whose member list does not contain the local host is always
/// a mistake, and a dangerous one: the registry would start, join a cluster it
/// is not a member of, and serve from it while no other member expected it to
/// exist.
fn find_local(
    members: &[Member],
    local_host: &str,
    local_peer_port: Option<u16>,
) -> Result<Member, ClusterConfigError> {
    let candidates: Vec<&Member> = members.iter().filter(|m| m.host == local_host).collect();
    if candidates.is_empty() {
        let known = members
            .iter()
            .map(|m| m.host.clone())
            .collect::<Vec<_>>()
            .join(", ");
        return Err(ClusterConfigError(format!(
            "local host '{local_host}' is not in the member list ({known}). \
             --registryAdvertisedHost must name this member, and the same \
             canonical set must be configured on every member.",
        )));
    }

    if let Some(port) = local_peer_port {
        let exact: Vec<&Member> = candidates
            .iter()
            .copied()
            .filter(|m| m.peer_port == port)
            .collect();
        return match exact.first() {
            Some(member) => Ok((*member).clone()),
            None => {
                let ports = candidates
                    .iter()
                    .map(|m| m.peer_port.to_string())
                    .collect::<Vec<_>>()
                    .join(", ");
                Err(ClusterConfigError(format!(
                    "no member at '{local_host}' has peer port {port} \
                     (have: {ports})",
                )))
            }
        };
    }

    if candidates.len() > 1 {
        let ports = candidates
            .iter()
            .map(|m| m.peer_port.to_string())
            .collect::<Vec<_>>()
            .join(", ");
        return Err(ClusterConfigError(format!(
            "'{local_host}' matches {} members (peer ports: {ports}); specify \
             which one this is",
            candidates.len(),
        )));
    }
    candidates
        .first()
        .map(|m| (*m).clone())
        .ok_or_else(|| ClusterConfigError("no local member".to_owned()))
}

/// A cluster token every member derives identically.
///
/// The token keeps unrelated clusters from joining each other, so it must be
/// *stable* across restarts and *identical* across members. A random or
/// timestamped token would make every restart a new cluster; a constant one
/// would let two separate deployments on the same network merge.
///
/// Hashing the canonical member list plus the key namespace gives both
/// properties, and makes a mismatched member list fail at the storage layer --
/// with a cluster-ID mismatch -- rather than silently forming a split cluster.
///
/// `flavour` distinguishes the storage layers. Without it, an etcd cluster and
/// a consensus cluster deployed on the same hosts under the same namespace
/// would derive the same token, and a member of one could present credentials
/// that looked, to the other, like a peer it was expecting.
///
/// # Errors
///
/// [`ClusterConfigError`] if the digest cannot be computed, which means the
/// linked OpenSSL has no SHA-256 -- fatal, and worth saying rather than
/// substituting another hash.
pub fn cluster_token(
    members: &[Member],
    namespace: &str,
    flavour: &str,
) -> Result<String, ClusterConfigError> {
    let material = members
        .iter()
        .map(|m| format!("{}={}:{}", m.name, m.host, m.peer_port))
        .collect::<Vec<_>>()
        .join("|");

    let digest = hash(
        MessageDigest::sha256(),
        format!("{flavour}{namespace}\n{material}").as_bytes(),
    )
    .map_err(|e| ClusterConfigError(format!("cannot compute the cluster token: {e}")))?;

    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    // The token only has to be a string; 16 hex characters is ample separation
    // and keeps log lines readable.
    Ok(format!("{MEMBER_NAME_PREFIX}-{}", &hex[..16]))
}
