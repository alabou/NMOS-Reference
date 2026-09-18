// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! TR-10-SEC section 8 TLS policy.
//!
//! A port of `nmos/api/tr10_tls.py` onto the same library it runs on. CPython's
//! `ssl` is a binding over the system OpenSSL -- `_ssl.…so` links
//! `libssl.so.3` and `libcrypto.so.3` -- and so is this, through the `openssl`
//! crate. The two implementations are therefore not two implementations of
//! TLS; they are two callers of one.
//!
//! # Why OpenSSL and not rustls
//!
//! So that a FIPS-validated crypto module can later serve both from a single
//! certification boundary. rustls has a FIPS story of its own through
//! `aws-lc-rs`, but it is a *different* validated module from the OpenSSL FIPS
//! provider Python would use, which would leave one registry carrying two.
//! The cost is accepted knowingly: `libssl` is C, and avoiding that was the
//! original reason to reach for rustls.
//!
//! When that work starts, note that this crate's `fips` module is the **wrong
//! one** -- it wraps the legacy 140-2 `FIPS_mode_set`, which OpenSSL 3.x
//! removed. The 3.x path is `Provider::load(ctx, "fips")`.
//!
//! # What this reaches that Python cannot
//!
//! Three `SSL_CTX_*` entry points decide the whole difference between the two,
//! and all three are in the library Python has already loaded. CPython simply
//! does not wrap them:
//!
//! | here | C | CPython |
//! |---|---|---|
//! | [`SslContextBuilder::set_cipher_list`] | `SSL_CTX_set_cipher_list` | `set_ciphers` |
//! | [`SslContextBuilder::set_ciphersuites`] | `SSL_CTX_set_ciphersuites` | **absent** |
//! | [`SslContextBuilder::set_groups_list`] | `SSL_CTX_set1_groups_list` | **absent** |
//!
//! `tr10_tls.py` reaches for the second with
//! `getattr(ctx, "set_ciphersuites", None)` and never finds it, so Python's
//! TLS 1.3 list stays at the OpenSSL default and `TLS_AES_128_CCM_SHA256`
//! (SEC-8-8, should) goes unoffered. It reaches for the third not at all,
//! because there is nothing to reach for, so unlisted groups stay enabled and
//! SEC-8-9 -- "only the cipher suites and key exchange groups listed … shall
//! be used" -- is not met.
//!
//! Both are closed here, in-process. Measured against live listeners with
//! `openssl s_client` (`tests/tls_handshake.rs`):
//!
//! | TR-10-SEC section 8 | Python | here |
//! |---|---|---|
//! | SEC-8-5 shall -- `25519`, `secp256r1` | yes | yes |
//! | SEC-8-5 should -- `secp521r1`, `448` | yes | yes |
//! | SEC-8-6 shall -- `ECDHE_RSA_AES_128_GCM` | yes | yes |
//! | SEC-8-6 should -- `DHE_RSA` GCM x2 | **no** | **no** |
//! | SEC-8-8 should -- `TLS_AES_128_CCM_SHA256` | **no** | yes |
//! | SEC-8-9 shall -- closed list | **no** | yes |
//!
//! Python's two shortfalls are binding limitations rather than anything about
//! the deployment, and SEC-8-9 is closable there too without touching CPython:
//! an `OPENSSL_CONF` carrying `Groups = X25519:P-256:P-521:X448` satisfies it.
//! `tr10_tls.py:36-39` describes that gap as waiting on a future
//! `SSLContext.set_groups`, which is not the only route, and understates what
//! is currently accepted -- `ffdhe2048` and `ffdhe3072` get in alongside the
//! `secp384r1` it names.
//!
//! The `DHE_RSA` row is the one both miss, and it is worth being precise about
//! because the cipher list is misleading here. Both implementations *advertise*
//! those two suites and neither can complete one. Classic TLS 1.2 DHE sends
//! explicit parameters in ServerKeyExchange, so it needs DH parameters set on
//! the context -- it is unrelated to `set_groups_list`, and pinning the groups
//! to SEC-8-5's all-EC list does not cause this. Neither side sets them:
//! `apply_tr10_tls_restrictions` never calls `load_dh_params`, and
//! [`server_context`] never calls `set_tmp_dh`. Left that way on purpose, so
//! the two behave alike; closing it is one line on each side and belongs to a
//! decision taken for both at once.

use std::path::Path;

use openssl::ssl::{
    SslContext, SslContextBuilder, SslFiletype, SslMethod, SslOptions, SslVerifyMode, SslVersion,
};
use openssl::x509::verify::X509VerifyFlags;

/// TLS 1.2 suites TR-10-SEC section 8 permits, SHALL first.
///
/// Ordered so the server prefers the strongest mutually supported suite, which
/// is why SHALL leads rather than because the order is normative.
///
/// CBC-mode suites are absent although SEC-8-6's `may` list permits them:
/// SEC-8-7 says a CBC suite should not be used unless `encrypt_then_mac` is
/// negotiated, and `may` makes supporting them optional. Python omits them for
/// the same reason.
pub const TR10_TLS12_CIPHERS: &[&str] = &[
    // SHALL (SEC-8-6)
    "TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256",
    // SHOULD (SEC-8-6)
    "TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256",
    "TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384",
    "TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384",
    "TLS_DHE_RSA_WITH_AES_128_GCM_SHA256",
    "TLS_DHE_RSA_WITH_AES_256_GCM_SHA384",
    // MAY (SEC-8-6)
    "TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256",
    "TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256",
];

/// IANA name to the OpenSSL cipher-string name `set_cipher_list` wants.
///
/// The same table `tr10_tls.py` carries, for the same reason: TR-10-SEC names
/// suites the IANA way and OpenSSL's cipher strings do not.
const IANA_TO_OPENSSL: &[(&str, &str)] = &[
    (
        "TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256",
        "ECDHE-RSA-AES128-GCM-SHA256",
    ),
    (
        "TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256",
        "ECDHE-ECDSA-AES128-GCM-SHA256",
    ),
    (
        "TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384",
        "ECDHE-ECDSA-AES256-GCM-SHA384",
    ),
    (
        "TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384",
        "ECDHE-RSA-AES256-GCM-SHA384",
    ),
    (
        "TLS_DHE_RSA_WITH_AES_128_GCM_SHA256",
        "DHE-RSA-AES128-GCM-SHA256",
    ),
    (
        "TLS_DHE_RSA_WITH_AES_256_GCM_SHA384",
        "DHE-RSA-AES256-GCM-SHA384",
    ),
    (
        "TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256",
        "ECDHE-RSA-CHACHA20-POLY1305",
    ),
    (
        "TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256",
        "ECDHE-ECDSA-CHACHA20-POLY1305",
    ),
];

/// TLS 1.3 suites TR-10-SEC section 8 permits, SHALL first.
///
/// IANA and OpenSSL spell these the same, so no translation table is needed.
/// `TLS_AES_128_CCM_SHA256` is the one Python asks for and does not get.
pub const TR10_TLS13_CIPHERS: &[&str] = &[
    // SHALL (SEC-8-8)
    "TLS_AES_128_GCM_SHA256",
    // SHOULD (SEC-8-8)
    "TLS_AES_256_GCM_SHA384",
    "TLS_AES_128_CCM_SHA256",
    // MAY (SEC-8-8)
    "TLS_CHACHA20_POLY1305_SHA256",
];

/// Key-exchange groups TR-10-SEC section 8 permits, SHALL first.
///
/// SEC-8-5 names them `25519`, `secp256r1`, `secp521r1` and `448`; the spec's
/// own note says those follow VSF TR-10-13's nomenclature and correspond to
/// `X25519` and `X448` in TLS terms. These are the OpenSSL spellings
/// `set_groups_list` accepts.
///
/// SEC-8-9 makes the list **closed** -- "only the cipher suites and key
/// exchange groups listed … shall be used" -- which is why pinning it matters
/// rather than merely supporting its members.
pub const TR10_GROUPS: &[&str] = &[
    // SHALL (SEC-8-5)
    "X25519", "P-256", // SHOULD (SEC-8-5)
    "P-521", "X448",
];

/// What the library accepted of the policy.
///
/// Kept from the rustls implementation, where it existed because rustls could
/// not offer parts of the whitelist and a silently narrowed policy would have
/// been the worst outcome. OpenSSL takes all of it, so the lists should come
/// back empty -- which is itself worth asserting rather than assuming, since a
/// build against a cut-down or FIPS-restricted provider may not.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct PolicyReport {
    /// TLS 1.2 suites the library accepted, in OpenSSL spelling.
    pub tls12: Vec<String>,
    /// TLS 1.3 suites requested.
    pub tls13: Vec<String>,
    /// Groups requested.
    pub groups: Vec<String>,
}

impl PolicyReport {
    /// One line per restriction applied, for the startup log.
    #[must_use]
    pub fn describe(&self) -> Vec<String> {
        vec![
            format!("TR-10-SEC TLS 1.2 suites: {}", self.tls12.join(":")),
            format!("TR-10-SEC TLS 1.3 suites: {}", self.tls13.join(":")),
            format!("TR-10-SEC key exchange groups: {}", self.groups.join(":")),
        ]
    }
}

/// Why a GCRL could not be honoured.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GcrlError(String);

impl std::fmt::Display for GcrlError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for GcrlError {}

/// Check the declared CRL bundle before anything starts listening.
///
/// SEC-14.3.3.5-3: "If a Certificate Revocation List (CRL) is required but
/// cannot be retrieved, has an invalid signature, or is expired, the Node shall
/// treat all certificates that would have been validated against that CRL as
/// invalid and shall deny access."
///
/// Refusing to start is the cleanest way to honour that when the operator named
/// a path that cannot be read: a registry that came up and quietly verified
/// nothing against revocation would be the worst available reading of it.
/// `apply_tr10_tls_restrictions` raises for the same reason.
///
/// Note the spec calls the combined bundle **GTCRL** (Global Trusted
/// Certificate Revocation List); the option is spelled `--gcrl` because that is
/// what the Python command line already calls it.
///
/// # Errors
///
/// The path was given and does not exist.
pub fn check_gcrl(path: Option<&Path>) -> Result<(), GcrlError> {
    let Some(path) = path else {
        // Not configured. Per 14.3.3.5-3 a device deployed without CRL
        // configuration does not fail closed on revocation; deployments that
        // require enforcement pass `--gcrl`.
        return Ok(());
    };
    if path.exists() {
        return Ok(());
    }
    Err(GcrlError(format!(
        "--gcrl points at a path that does not exist ({}) -- refusing to apply \
         section 8 / GTCRL policy without the declared CRL \
         (SEC-14.3.3.5-3 fail-closed)",
        path.display(),
    )))
}

/// How the TLS layer treats a client that offers no certificate.
///
/// The split that makes "a certificate only for state-changing verbs" possible
/// at all. Under [`Self::Required`] such a client cannot complete the
/// handshake, so read-only access is impossible; [`Self::Optional`] lets the
/// connection up and leaves the decision to the application, which is what
/// `--queryOptionalClientAuth` selects.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClientAuth {
    /// No trust anchor configured: server-authenticated TLS only.
    None,
    /// `CERT_OPTIONAL` -- verify a certificate if one is offered.
    Optional,
    /// `CERT_REQUIRED` -- refuse the handshake without one.
    Required,
}

impl ClientAuth {
    /// Choose the mode the way `_server_context` does.
    ///
    /// The trust anchor decides whether client certificates are verifiable at
    /// all; the flag then decides whether one is mandatory.
    #[must_use]
    pub const fn select(has_trust_anchor: bool, optional: bool) -> Self {
        if !has_trust_anchor {
            return Self::None;
        }
        if optional {
            Self::Optional
        } else {
            Self::Required
        }
    }

    /// The `SSL_VERIFY_*` bits this mode corresponds to.
    #[must_use]
    fn verify_mode(self) -> SslVerifyMode {
        match self {
            Self::None => SslVerifyMode::NONE,
            // PEER alone requests a certificate and verifies one if sent --
            // CERT_OPTIONAL. Adding FAIL_IF_NO_PEER_CERT makes it mandatory,
            // which is CERT_REQUIRED.
            Self::Optional => SslVerifyMode::PEER,
            Self::Required => SslVerifyMode::PEER | SslVerifyMode::FAIL_IF_NO_PEER_CERT,
        }
    }
}

/// Why a TLS listener could not be configured.
#[derive(Debug)]
pub enum TlsError {
    /// OpenSSL refused something.
    Ssl(openssl::error::ErrorStack),
    /// The declared GCRL was unusable.
    Gcrl(GcrlError),
}

impl std::fmt::Display for TlsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Ssl(error) => write!(f, "OpenSSL refused the configuration: {error}"),
            Self::Gcrl(error) => std::fmt::Display::fmt(error, f),
        }
    }
}

impl std::error::Error for TlsError {}

impl From<openssl::error::ErrorStack> for TlsError {
    fn from(error: openssl::error::ErrorStack) -> Self {
        Self::Ssl(error)
    }
}

impl From<GcrlError> for TlsError {
    fn from(error: GcrlError) -> Self {
        Self::Gcrl(error)
    }
}

/// Apply TR-10-SEC section 8 to a context under construction.
///
/// The direct counterpart of `apply_tr10_tls_restrictions`, and deliberately
/// in the same order so the two read side by side. Every call here has a line
/// in that function except `set_groups_list`, which has no CPython equivalent
/// to correspond to.
///
/// # Errors
///
/// OpenSSL rejected a list -- which it does when *none* of the requested
/// members is supported, so a failure here means a misconfiguration or a
/// cut-down provider, and is worth being loud about at startup.
pub fn apply_tr10_restrictions(builder: &mut SslContextBuilder) -> Result<PolicyReport, TlsError> {
    // TLS 1.2 minimum (SEC-8-2: shall support 1.2, should support 1.3).
    builder.set_min_proto_version(Some(SslVersion::TLS1_2))?;

    // TLS 1.2 suites. `set_cipher_list` raises only if none of the requested
    // suites is supported; we want that loud rather than swallowed.
    let tls12: Vec<String> = TR10_TLS12_CIPHERS
        .iter()
        .filter_map(|iana| {
            IANA_TO_OPENSSL
                .iter()
                .find(|(name, _)| name == iana)
                .map(|(_, openssl_name)| (*openssl_name).to_owned())
        })
        .collect();
    builder.set_cipher_list(&tls12.join(":"))?;

    // TLS 1.3 suites -- the call `tr10_tls.py` cannot make. This is what
    // closes SEC-8-8's `TLS_AES_128_CCM_SHA256`.
    let tls13: Vec<String> = TR10_TLS13_CIPHERS.iter().map(|s| (*s).to_owned()).collect();
    builder.set_ciphersuites(&tls13.join(":"))?;

    // Key-exchange groups -- the other call CPython does not expose, and what
    // makes SEC-8-9's closed list enforceable in-process rather than through
    // an OPENSSL_CONF the operator has to remember.
    let groups: Vec<String> = TR10_GROUPS.iter().map(|s| (*s).to_owned()).collect();
    builder.set_groups_list(&groups.join(":"))?;

    // Defense in depth, matching `ctx.options |=`: no compression (CRIME,
    // BREACH) and no renegotiation. Not section 8 requirements.
    builder.set_options(SslOptions::NO_COMPRESSION | SslOptions::NO_RENEGOTIATION);

    Ok(PolicyReport {
        tls12,
        tls13,
        groups,
    })
}

/// Build the TLS context for an NMOS API port.
///
/// The counterpart of `build_server_ssl_context` followed by
/// `apply_tr10_tls_restrictions`.
///
/// # Errors
///
/// The certificate, key, trust anchor or CRL could not be loaded, or OpenSSL
/// refused the policy.
pub fn server_context(
    chain: &Path,
    key: &Path,
    trust_anchors: &[impl AsRef<Path>],
    optional_client_auth: bool,
    gcrl: Option<&Path>,
) -> Result<(SslContext, PolicyReport, ClientAuth), TlsError> {
    check_gcrl(gcrl)?;

    // A bare server context, as `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)` is.
    // Not `SslAcceptor::mozilla_intermediate_v5`, which would install its own
    // cipher list and options and then have to be argued out of them.
    let mut builder = SslContextBuilder::new(SslMethod::tls_server())?;
    let report = apply_tr10_restrictions(&mut builder)?;

    // The server's own identity: leaf + intermediates, then the key.
    builder.set_certificate_chain_file(chain)?;
    builder.set_private_key_file(key, SslFiletype::PEM)?;
    builder.check_private_key()?;

    // `--registrationTrustedRootCA` and `--queryTrustedRootCA` are both
    // repeatable, and `_server_context` loads every one of them before setting
    // the verify mode. More than one matters during re-provisioning, when the
    // old and new anchors must both be accepted for a while.
    let mode = ClientAuth::select(!trust_anchors.is_empty(), optional_client_auth);
    for anchor in trust_anchors {
        builder.set_ca_file(anchor.as_ref())?;
    }
    builder.set_verify(mode.verify_mode());

    if let Some(gcrl) = gcrl {
        // The GTCRL bundle may hold several `X509 CRL` blocks, one per
        // configured CA; OpenSSL matches each to its issuer from the trust
        // store at verify time.
        builder.set_ca_file(gcrl)?;
        // `CRL_CHECK` is `X509_V_FLAG_CRL_CHECK`, which is exactly what
        // Python's `VERIFY_CRL_CHECK_LEAF` sets -- the leaf only, not
        // `CRL_CHECK_ALL`'s whole chain.
        builder
            .verify_param_mut()
            .set_flags(X509VerifyFlags::CRL_CHECK)?;
    }

    Ok((builder.build(), report, mode))
}

/// Build the TLS context this registry uses **as a client**.
///
/// It reaches out to exactly one peer -- the OAuth 2.0 Authorization Server,
/// for metadata discovery and the JWKS fetch -- and TR-10-SEC section 8 governs
/// that connection as much as an inbound one. `tr10_tls.py` says so directly:
/// "Both server contexts and client contexts are restricted -- TR-10-SEC §3
/// doesn't carve out a direction-specific exemption."
///
/// With no trust anchors given, the system's default store is used, matching
/// `load_default_certs()`. Server certificates are verified either way: a
/// registry that fetched signing keys over an unauthenticated connection would
/// accept tokens minted by whoever answered.
///
/// # Errors
///
/// A trust anchor or CRL that cannot be read, or a policy OpenSSL refuses.
pub fn client_context(
    trust_anchors: &[impl AsRef<Path>],
    gcrl: Option<&Path>,
) -> Result<SslContext, TlsError> {
    check_gcrl(gcrl)?;

    let mut builder = SslContextBuilder::new(SslMethod::tls_client())?;
    apply_tr10_restrictions(&mut builder)?;

    if trust_anchors.is_empty() {
        builder.set_default_verify_paths()?;
    } else {
        for anchor in trust_anchors {
            builder.set_ca_file(anchor.as_ref())?;
        }
    }
    // Verify the peer. `SslMethod::tls_client` does not imply it, and a client
    // that skipped verification here would be the whole point of the exercise
    // thrown away.
    builder.set_verify(SslVerifyMode::PEER);

    if let Some(gcrl) = gcrl {
        builder.set_ca_file(gcrl)?;
        builder
            .verify_param_mut()
            .set_flags(X509VerifyFlags::CRL_CHECK)?;
    }

    Ok(builder.build())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn context() -> (SslContextBuilder, PolicyReport) {
        let mut builder =
            SslContextBuilder::new(SslMethod::tls_server()).expect("a server context");
        let report = apply_tr10_restrictions(&mut builder).expect("the policy applies");
        (builder, report)
    }

    #[test]
    fn the_whole_whitelist_is_accepted_by_this_library() {
        // The point of being on OpenSSL: nothing in the policy is refused for
        // want of support. A cut-down or FIPS-restricted provider could change
        // that, which is why this asserts rather than assumes.
        let (_, report) = context();
        assert_eq!(report.tls12.len(), TR10_TLS12_CIPHERS.len());
        assert_eq!(report.tls13.len(), TR10_TLS13_CIPHERS.len());
        assert_eq!(report.groups.len(), TR10_GROUPS.len());
    }

    #[test]
    fn every_tls12_suite_has_an_openssl_spelling() {
        // A missing row in the translation table would silently shorten the
        // cipher string instead of failing, so the suite would simply never be
        // offered.
        for iana in TR10_TLS12_CIPHERS {
            assert!(
                IANA_TO_OPENSSL.iter().any(|(name, _)| name == iana),
                "{iana} has no OpenSSL spelling, so it would be dropped silently",
            );
        }
        assert_eq!(IANA_TO_OPENSSL.len(), TR10_TLS12_CIPHERS.len());
    }

    #[test]
    fn the_ccm_suite_python_cannot_reach_is_in_the_tls13_list() {
        // SEC-8-8 should. `tr10_tls.py` asks for it through `set_ciphersuites`
        // and CPython has no such method, so Python does not offer it. Here
        // the call exists.
        assert!(
            TR10_TLS13_CIPHERS.contains(&"TLS_AES_128_CCM_SHA256"),
            "the whole SEC-8-8 gain of this port is missing from the list",
        );
        let (_, report) = context();
        assert!(report.tls13.iter().any(|s| s == "TLS_AES_128_CCM_SHA256"));
    }

    #[test]
    fn the_group_list_is_exactly_sec_8_5() {
        // SEC-8-9 makes it closed, so an extra entry is as much a defect as a
        // missing one.
        assert_eq!(TR10_GROUPS, ["X25519", "P-256", "P-521", "X448"]);
    }

    #[test]
    fn a_bogus_group_name_is_refused_rather_than_ignored() {
        // Proves `set_groups_list` is actually validating the string. If it
        // silently accepted anything, the pinning tests would pass against a
        // context that had never been restricted -- which is the failure mode
        // that makes a security test worthless.
        let mut builder =
            SslContextBuilder::new(SslMethod::tls_server()).expect("a server context");
        assert!(
            builder.set_groups_list("X25519:not-a-real-group").is_err(),
            "OpenSSL accepted a nonexistent group name",
        );
    }

    #[test]
    fn a_bogus_tls13_suite_is_refused_rather_than_ignored() {
        // The same argument for `set_ciphersuites`.
        let mut builder =
            SslContextBuilder::new(SslMethod::tls_server()).expect("a server context");
        assert!(
            builder.set_ciphersuites("TLS_NOT_A_REAL_SUITE").is_err(),
            "OpenSSL accepted a nonexistent TLS 1.3 suite",
        );
    }

    // -- the GCRL ---------------------------------------------------------

    #[test]
    fn an_absent_gcrl_option_is_not_an_error() {
        // 14.3.3.5-3: a device deployed without CRL configuration does not
        // fail closed on revocation.
        assert!(check_gcrl(None).is_ok());
    }

    #[test]
    fn a_declared_but_missing_gcrl_fails_closed_at_startup() {
        // SEC-14.3.3.5-3.
        let error = check_gcrl(Some(Path::new("/nonexistent/gcrl.pem")))
            .expect_err("a missing CRL must refuse to start");
        assert!(error.to_string().contains("fail-closed"), "{error}");
        assert!(
            error.to_string().contains("/nonexistent/gcrl.pem"),
            "{error}",
        );
    }

    #[test]
    fn a_present_gcrl_is_accepted() {
        let path = std::env::temp_dir().join("nmos-openssl-test-gcrl.pem");
        std::fs::write(&path, b"-----BEGIN X509 CRL-----\n").expect("write");
        assert!(check_gcrl(Some(&path)).is_ok());
        let _ = std::fs::remove_file(&path);
    }

    // -- client auth ------------------------------------------------------

    #[test]
    fn client_auth_follows_the_anchor_then_the_flag() {
        // `_server_context`: the anchor decides whether client certificates
        // are verifiable at all, and the flag then decides whether one is
        // required.
        assert_eq!(ClientAuth::select(false, false), ClientAuth::None);
        assert_eq!(
            ClientAuth::select(false, true),
            ClientAuth::None,
            "the optional flag invented an anchor that was not configured",
        );
        assert_eq!(ClientAuth::select(true, false), ClientAuth::Required);
        assert_eq!(ClientAuth::select(true, true), ClientAuth::Optional);
    }

    #[test]
    fn the_verify_bits_match_cert_optional_and_cert_required() {
        // The distinction the verb gate rests on. PEER alone asks for a
        // certificate; FAIL_IF_NO_PEER_CERT is what makes one mandatory.
        assert_eq!(ClientAuth::None.verify_mode(), SslVerifyMode::NONE);
        assert_eq!(ClientAuth::Optional.verify_mode(), SslVerifyMode::PEER);
        assert!(
            !ClientAuth::Optional
                .verify_mode()
                .contains(SslVerifyMode::FAIL_IF_NO_PEER_CERT),
            "CERT_OPTIONAL would reject an anonymous client, so no read-only \
             access would be possible",
        );
        assert!(
            ClientAuth::Required
                .verify_mode()
                .contains(SslVerifyMode::FAIL_IF_NO_PEER_CERT),
            "CERT_REQUIRED would admit a client presenting no certificate",
        );
    }
}
