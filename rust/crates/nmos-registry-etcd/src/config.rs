// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! What the etcd backend is configured with, validated.
//!
//! The port of `EtcdConfig` in `nmos/registry/distributed.py`. It lives in this
//! crate rather than in the binary because the backend is the only thing that
//! reads it, and a config type in the binary would make the backend
//! untestable without one.
//!
//! The binary's job (M10.5) is turning seventeen `--etcd*` flags into one of
//! these, with the same refusals the Python produces.

use std::path::PathBuf;
use std::time::Duration;

use nmos_cluster::ClusterLayout;

/// Everything the etcd backend needs, validated.
#[derive(Debug, Clone)]
pub struct EtcdConfig {
    /// The derived cluster: members, order, this member's index, the token.
    pub layout: ClusterLayout,
    /// Every member's client endpoint.
    pub endpoints: Vec<String>,
    /// The key prefix this registry owns.
    pub namespace: String,

    /// Whether client traffic is secured.
    pub tls: bool,
    /// The shared certificate this member presents.
    pub certificate: String,
    /// Its private key.
    pub key: String,
    /// Roots the cluster's certificates are verified against.
    pub trusted_root_ca: Vec<String>,
    /// The one shared SAN every member's certificate carries.
    ///
    /// Both the target-name override this client verifies against and etcd's
    /// own `--client-cert-allowed-hostname`.
    pub certificate_name: String,

    /// Per-RPC deadline.
    pub rpc_timeout: Duration,
    /// Overall deadline for one mutation to commit.
    pub mutation_timeout: Duration,

    /// True when no etcd process is managed by this registry -- either
    /// `--etcdExternal` was given, or this is native Windows.
    ///
    /// It changes what membership reconciliation may assert: with a managed
    /// cluster the member names were derived here and must match exactly; with
    /// an external one the operator named them and only the *size* is ours to
    /// have an opinion about.
    pub external: bool,
    /// The etcd binary, when this registry manages one.
    pub binary: String,
    /// The managed member's data directory.
    pub data_dir: PathBuf,
    /// Whether this member bootstraps a new cluster.
    pub bootstrap: bool,

    /// CRL for client certificates.
    pub client_crl_file: String,
    /// CRL for peer certificates.
    ///
    /// Two CRLs where the raft backend has one, because etcd has two distinct
    /// relationships to revoke against: clients talking to members, and
    /// members talking to each other.
    pub peer_crl_file: String,
}

impl EtcdConfig {
    /// Whether this registry spawns and supervises an etcd process.
    #[must_use]
    pub const fn manages_process(&self) -> bool {
        !self.external
    }
}
