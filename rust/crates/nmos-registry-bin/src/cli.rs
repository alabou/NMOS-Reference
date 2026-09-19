// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The command line, which must be **identical** to `nmos_registry.py`'s.
//!
//! Not merely similar. An operator, a launch script and every
//! `start-registry*.sh` in this repository move between the two implementations
//! without changing a word, and the M6 gate runs those scripts against this
//! binary with only the executable path swapped. A flag that differs in
//! spelling, default, type or arity breaks that **silently**: the script still
//! runs, it just configures something else.
//!
//! So every `long` is given explicitly. clap would otherwise derive
//! kebab-case from the field name and produce `--registration-port` where
//! Python has `--registrationPort`, which is a divergence on every flag at
//! once.
//!
//! `tests/cli_parity.rs` checks this struct against a recording of what
//! `argparse` actually holds, rather than against a transcription of it.
//!
//! # Flag names mirror `nmos_node.py` deliberately
//!
//! One shared server certificate for the process, with a separate trust anchor
//! per interface -- exactly as the Node has one `--nodeCertificate` with
//! `--nodeTrustedRootCA` and `--controlTrustedRootCA`. An operator who knows
//! the Node's flags already knows these.

use std::path::PathBuf;

use clap::{Parser, ValueEnum};

/// OAuth 2.0 Audience Identification Mode -- TR-10-SEC §12.4.
#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum AudienceMode {
    /// Match the BCP-002-02 instance identifier.
    #[value(name = "serial")]
    Serial,
    /// Match the TLS server certificate identity.
    #[value(name = "cert")]
    Cert,
    /// Either is sufficient.
    #[value(name = "either")]
    Either,
}

/// NMOS IS-04 Registry (Registration + Query APIs).
#[derive(Debug, Parser)]
#[command(
    name = "nmos-registry",
    about = "NMOS IS-04 Registry (Registration + Query APIs)"
)]
pub struct Args {
    // --- Registry server (shared across both interfaces) ---
    /// Bind address for all registry listeners.
    #[arg(long = "registryAddr", default_value = "127.0.0.1")]
    pub registry_addr: String,

    /// Server certificate (PEM) used by both listeners.
    #[arg(long = "registryCertificate", default_value = "")]
    pub registry_certificate: String,

    /// Private key (PEM) for the server certificate.
    #[arg(long = "registryKey", default_value = "")]
    pub registry_key: String,

    /// Serve plain HTTP.
    ///
    /// TR-10-SEC RAP 0. A development configuration -- the specification
    /// requires TLS for a compliant deployment.
    #[arg(long = "registryDisableTLS")]
    pub registry_disable_tls: bool,

    /// The BCP-002-02 instance identifier, matched against a token's `aud`.
    #[arg(long = "registrySerialNumber", default_value = "SNR12345")]
    pub registry_serial_number: String,

    // --- Registration interface ---
    /// Registration API port (the Node's `--rdsRegPort`).
    #[arg(long = "registrationPort", default_value_t = 8447)]
    pub registration_port: u16,

    /// Trusted root CA for Registration client-certificate auth.
    ///
    /// May be repeated. Empty leaves the listener at RAP 1 (server-
    /// authenticated TLS); non-empty makes it RAP 2 (mutual TLS).
    #[arg(long = "registrationTrustedRootCA")]
    pub registration_trusted_root_ca: Vec<PathBuf>,

    /// Accept unauthenticated clients at the TLS layer and enforce client
    /// certificates in the application instead.
    #[arg(long = "registrationOptionalClientAuth")]
    pub registration_optional_client_auth: bool,

    // --- Query interface ---
    /// Query API port (the Node's `--rdsQueryPort`).
    #[arg(long = "queryPort", default_value_t = 8446)]
    pub query_port: u16,

    /// Query API WebSocket port, advertised in `ws_href`.
    #[arg(long = "queryWebSocketPort", default_value_t = 8448)]
    pub query_websocket_port: u16,

    /// Trusted root CA for Query client-certificate auth. May be repeated.
    #[arg(long = "queryTrustedRootCA")]
    pub query_trusted_root_ca: Vec<PathBuf>,

    /// Allow unauthenticated clients read-only access to the Query API.
    ///
    /// The TLS layer stops requiring a certificate; state-changing verbs still
    /// require one, enforced in the application. Without this a client with no
    /// certificate cannot complete the handshake at all, so read-only access
    /// would be impossible -- which is the whole reason the flag exists.
    #[arg(long = "queryOptionalClientAuth")]
    pub query_optional_client_auth: bool,

    // --- Behaviour ---
    /// Seconds of heartbeat silence after which a Node and its sub-resources
    /// are collected.
    #[arg(long = "garbageCollectionInterval", default_value_t = 12.0)]
    pub garbage_collection_interval: f64,

    /// Seconds after which a tombstoned resource is forgotten.
    #[arg(long = "forgetInterval", default_value_t = 60.0)]
    pub forget_interval: f64,

    /// Default page size.
    #[arg(long = "pagingLimit", default_value_t = 10)]
    pub paging_limit: usize,

    /// Largest page size the server will honour.
    #[arg(long = "pagingLimitMax", default_value_t = 100)]
    pub paging_limit_max: usize,

    /// Seconds between status-line log entries. Zero disables reporting.
    #[arg(long = "statusInterval", default_value_t = 5.0)]
    pub status_interval: f64,

    // --- OAuth 2.0 (Query API only) ---
    /// Enable OAuth 2.0 authorization on the Query API.
    ///
    /// Has no effect on the Registration API, which TR-10-SEC:105 forbids from
    /// requiring OAuth 2.0.
    #[arg(long = "oauth2")]
    pub oauth2: bool,

    /// OAuth2 authorization server host.
    #[arg(long = "oauth2Host", default_value = "")]
    pub oauth2_host: String,

    /// OAuth2 authorization server port.
    #[arg(long = "oauth2Port", default_value_t = 4444)]
    pub oauth2_port: u16,

    /// OAuth2 trusted root CA (PEM path; may be repeated).
    #[arg(long = "oauth2TrustedRootCA")]
    pub oauth2_trusted_root_ca: Vec<PathBuf>,

    /// Disable TLS towards the OAuth2 server.
    #[arg(long = "oauth2DisableTLS")]
    pub oauth2_disable_tls: bool,

    /// IS-10 / RFC 8414 §3.1 `api_selector` -- the path component of the
    /// issuer identifier.
    ///
    /// Empty for ORY Hydra; `realms/<realm>` for Keycloak.
    #[arg(long = "oauth2ApiSelector", default_value = "realms/TR-10-SEC")]
    pub oauth2_api_selector: String,

    /// OAuth 2.0 Audience Identification Mode (TR-10-SEC §12.4).
    #[arg(long = "oauth2AudienceMode", value_enum, default_value = "serial")]
    pub oauth2_audience_mode: AudienceMode,

    // --- Trust and revocation ---
    /// Trusted root CA for outbound connections (PEM path; may be repeated).
    #[arg(long = "trustedRootCA")]
    pub trusted_root_ca: Vec<PathBuf>,

    /// Global certificate revocation list (PEM).
    #[arg(long = "gcrl")]
    pub gcrl: Option<PathBuf>,

    // --- Logging ---
    /// Where to write the log.
    ///
    /// A `String` rather than a `PathBuf`, deliberately. `--logFile ""` is a
    /// documented setting -- it silences the *file* handler and leaves the
    /// console one alone -- and `bench_registry/compare.py` passes exactly that
    /// for its matched-quiet runs. clap's `PathBuf` parser rejects an empty
    /// value outright ("a value is required ... but none was supplied"), so
    /// typing this as a path makes a configuration the Python accepts
    /// impossible to express.
    #[arg(long = "logFile", default_value = "/tmp/nmos-registry.log")]
    pub log_file: String,

    // -- Distributed Registry -------------------------------------------
    //
    // Without `--distributed` nothing below is read and the registry behaves
    // exactly as it always has.
    /// Which storage layer backs `--distributed`.
    ///
    /// Both values the Python accepts are accepted here, deliberately, even
    /// though only `raft` is built. Rejecting `etcd` at the parser would give
    /// clap's generic "invalid value for --distributedBackend"; accepting it
    /// and refusing in the resolver gives the operator the sentence that
    /// actually helps -- that the backend is deferred rather than gone, and
    /// that the Python registry still offers it.
    #[arg(long = "distributedBackend", value_parser = ["raft", "etcd"], default_value = "raft")]
    pub distributed_backend: String,

    /// Share state with 1, 3 or 5 peer registries.
    #[arg(long = "distributed", default_value_t = false)]
    pub distributed: bool,

    /// This member's advertised host, as `host` or `host:client_port`.
    ///
    /// MUST be a SAN of this member's peer certificate.
    #[arg(long = "registryAdvertisedHost", default_value = "")]
    pub registry_advertised_host: String,

    /// A peer registry's advertised host. Repeat once per peer.
    #[arg(long = "registryNeighbour")]
    pub registry_neighbour: Vec<String>,

    /// Key namespace. Part of the cluster token.
    #[arg(long = "raftNamespace", default_value = "/nmos-reference/registry/v1")]
    pub raft_namespace: String,

    /// Member-status port.
    #[arg(long = "raftClientPort", default_value_t = 2481)]
    pub raft_client_port: u16,

    /// Peer transport port.
    #[arg(long = "raftPeerPort", default_value_t = 2482)]
    pub raft_peer_port: u16,

    /// Where this member's term/vote file lives.
    #[arg(long = "raftStateDir", default_value = "/var/lib/nmos-registry/raft")]
    pub raft_state_dir: String,

    /// Shared peer certificate chain.
    #[arg(long = "raftCertificate", default_value = "")]
    pub raft_certificate: String,

    /// Private key for the peer certificate.
    #[arg(long = "raftKey", default_value = "")]
    pub raft_key: String,

    /// Trusted root CA for peer verification. May be repeated.
    #[arg(long = "raftTrustedRootCA")]
    pub raft_trusted_root_ca: Vec<String>,

    /// The shared SAN every peer is verified against.
    #[arg(
        long = "raftCertificateName",
        default_value = "Example.Company.Device.Etcd.ABC.example.com"
    )]
    pub raft_certificate_name: String,

    /// CRL for peer certificates.
    #[arg(long = "raftCrlFile", default_value = "")]
    pub raft_crl_file: String,

    /// Disable TLS between members. TESTING ONLY -- refused off the loopback.
    #[arg(long = "raftDisableTLS", default_value_t = false)]
    pub raft_disable_tls: bool,

    /// Per-message deadline, in seconds.
    #[arg(long = "raftRpcTimeout", default_value_t = 2.0)]
    pub raft_rpc_timeout: f64,

    /// Overall deadline for one registration to commit, in seconds.
    #[arg(long = "raftMutationTimeout", default_value_t = 7.0)]
    pub raft_mutation_timeout: f64,
}

impl Args {
    /// Whether the Registration listener runs mutual TLS.
    ///
    /// Derived from the trust anchor rather than from a flag of its own,
    /// matching `build_registration_ssl_context`: an anchor is what makes
    /// client certificates verifiable, so its presence *is* the decision.
    #[must_use]
    pub fn registration_client_auth(&self) -> bool {
        !self.registration_trusted_root_ca.is_empty()
    }

    /// Whether the Query listener runs mutual TLS.
    #[must_use]
    pub fn query_client_auth(&self) -> bool {
        !self.query_trusted_root_ca.is_empty()
    }

    /// Whether TLS is active at all.
    #[must_use]
    pub const fn tls(&self) -> bool {
        !self.registry_disable_tls
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(argv: &[&str]) -> Args {
        let mut full = vec!["nmos-registry"];
        full.extend_from_slice(argv);
        Args::try_parse_from(full).expect("the flags parse")
    }

    #[test]
    fn the_defaults_match_the_python_parser() {
        // Every default is part of the contract: a launch script that omits a
        // flag must get the same value from either implementation.
        let args = parse(&[]);
        assert_eq!(args.registry_addr, "127.0.0.1");
        assert_eq!(args.registration_port, 8447);
        assert_eq!(args.query_port, 8446);
        assert_eq!(args.query_websocket_port, 8448);
        assert_eq!(args.registry_serial_number, "SNR12345");
        assert!((args.garbage_collection_interval - 12.0).abs() < f64::EPSILON);
        assert!((args.forget_interval - 60.0).abs() < f64::EPSILON);
        assert_eq!(args.paging_limit, 10);
        assert_eq!(args.paging_limit_max, 100);
        assert!((args.status_interval - 5.0).abs() < f64::EPSILON);
        assert_eq!(args.oauth2_port, 4444);
        assert_eq!(args.oauth2_api_selector, "realms/TR-10-SEC");
        assert_eq!(args.oauth2_audience_mode, AudienceMode::Serial);
        assert_eq!(args.log_file, "/tmp/nmos-registry.log");
    }

    #[test]
    fn every_switch_defaults_off() {
        let args = parse(&[]);
        assert!(!args.registry_disable_tls);
        assert!(!args.registration_optional_client_auth);
        assert!(!args.query_optional_client_auth);
        assert!(!args.oauth2);
        assert!(!args.oauth2_disable_tls);
    }

    #[test]
    fn the_repeatable_flags_accumulate() {
        // `action="append"` in Python. Taking only the last would silently drop
        // trust anchors, which fails closed in a way nobody notices until a
        // Node cannot connect.
        let args = parse(&[
            "--registrationTrustedRootCA",
            "/a.pem",
            "--registrationTrustedRootCA",
            "/b.pem",
        ]);
        assert_eq!(
            args.registration_trusted_root_ca,
            [PathBuf::from("/a.pem"), PathBuf::from("/b.pem")],
        );
    }

    #[test]
    fn the_camel_case_spellings_are_the_ones_python_uses() {
        // The failure this guards: clap deriving `--registration-port` from the
        // field name, which would diverge on every flag at once.
        let args = parse(&["--registrationPort", "9000", "--queryPort", "9001"]);
        assert_eq!(args.registration_port, 9000);
        assert_eq!(args.query_port, 9001);

        assert!(
            Args::try_parse_from(["nmos-registry", "--registration-port", "9000"]).is_err(),
            "a kebab-case spelling was accepted, so both forms exist here and \
             only one exists in Python",
        );
    }

    #[test]
    fn the_audience_mode_choices_are_the_three_python_offers() {
        for (text, expected) in [
            ("serial", AudienceMode::Serial),
            ("cert", AudienceMode::Cert),
            ("either", AudienceMode::Either),
        ] {
            let args = parse(&["--oauth2AudienceMode", text]);
            assert_eq!(args.oauth2_audience_mode, expected);
        }
        assert!(
            Args::try_parse_from(["nmos-registry", "--oauth2AudienceMode", "nonsense"]).is_err(),
            "an unlisted audience mode was accepted",
        );
    }

    #[test]
    fn client_auth_follows_the_trust_anchor() {
        // `build_registration_ssl_context`: the anchor is what makes a client
        // certificate verifiable, so its presence is the decision.
        assert!(!parse(&[]).registration_client_auth());
        assert!(parse(&["--registrationTrustedRootCA", "/ca.pem"]).registration_client_auth(),);
        assert!(!parse(&[]).query_client_auth());
        assert!(parse(&["--queryTrustedRootCA", "/ca.pem"]).query_client_auth());
    }

    #[test]
    fn disabling_tls_is_the_only_way_to_plain_http() {
        assert!(parse(&[]).tls());
        assert!(!parse(&["--registryDisableTLS"]).tls());
    }

    #[test]
    fn an_empty_log_file_is_accepted_and_means_no_file() {
        // `--logFile ""` silences the file handler and leaves the console one
        // alone. `bench_registry/compare.py` passes it for every matched-quiet
        // run, and clap's `PathBuf` parser refuses an empty value -- so typing
        // this as a path made a configuration the Python accepts impossible to
        // express, and the benchmark could not start this registry at all.
        //
        // The flag-shape parity test cannot catch this: it compares names,
        // defaults and arities, not which *values* each parser will take.
        let args = parse(&["--logFile", ""]);
        assert_eq!(args.log_file, "");
    }

    #[test]
    fn an_unknown_flag_is_refused() {
        // A launch script carrying a flag this build does not know must fail
        // loudly rather than start a differently-configured registry.
        assert!(Args::try_parse_from(["nmos-registry", "--notAFlag"]).is_err());
    }
}
