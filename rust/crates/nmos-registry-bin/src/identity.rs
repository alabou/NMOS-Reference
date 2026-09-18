// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Who the peer is, as the TLS session reports it.
//!
//! The producing half of [`PeerIdentity`]; the consuming half -- the verb gate
//! that lets a read through without a certificate and refuses a write without
//! one -- lives in `nmos-registry-http`'s `security` module.
//!
//! Port of `middleware.py`'s `_client_authenticated` and
//! `_get_client_cert_names`.
//!
//! # Why this is a request extension rather than a header
//!
//! Python reads the connection directly: `request.transport`, then
//! `get_extra_info("ssl_object")`, then `getpeercert()`. An axum handler has no
//! such reach -- by the time it runs, the TLS session is owned by the accept
//! loop. So the accept loop reads the session once, at the only point it is
//! visible, and attaches the answer to the request.
//!
//! An extension rather than a header because a header can be *sent by the
//! client*. A trusted-header scheme would mean a plain `X-Client-Cert: yes`
//! from anyone on the network authenticated them, which is the whole control
//! inverted. Extensions are in-process values that no peer can set.
//!
//! Neither mechanism exists in Python, which needs neither; this is not a
//! change in what the registry decides, only in how the answer reaches the
//! code that decides it.

use std::path::Path;

use nmos_registry_http::security::PeerIdentity;
use openssl::nid::Nid;
use openssl::ssl::SslRef;
use openssl::x509::X509;

/// The DNS identities a certificate may be known by: subject CN first, then
/// every DNS SAN, in declaration order, without duplicates.
///
/// One function for what Python spells twice -- `_get_client_cert_names` for the
/// peer and `cert_dns_identities` for our own server certificate. The two build
/// the same list by the same rule; only the caller differs.
///
/// # The one difference, and why it cannot matter
///
/// `cert_dns_identities` de-duplicates CNs against each other and skips empty
/// ones; `_get_client_cert_names` does neither. This does both, so a
/// certificate carrying the *same* CN twice yields it once here and twice from
/// `_get_client_cert_names`. Nothing downstream can observe that: the client
/// path feeds `check_client_cert_name`, which asks whether *any* name matches,
/// and `is_authenticated`, which asks whether the list is non-empty. Neither
/// counts.
///
/// Other SAN kinds -- email, IP, URI -- are ignored by both, because these
/// names are matched against hostnames.
#[must_use]
pub fn names_of(certificate: &X509) -> Vec<String> {
    let mut names: Vec<String> = Vec::new();
    let mut push = |name: String| {
        if !name.is_empty() && !names.contains(&name) {
            names.push(name);
        }
    };

    // `to_string` and not `as_utf8`: the latter stops at the first interior NUL
    // byte, so a CN of `evil.example.com\0registry.example.com` would be read
    // as the part before the NUL. That is the classic null-byte name-injection
    // shape, and these names are matched against hostnames. CPython builds the
    // subject tuple from the ASN.1 string with its explicit length and keeps
    // the NUL, so not truncating is also what matches Python.
    for entry in certificate.subject_name().entries_by_nid(Nid::COMMONNAME) {
        if let Ok(name) = entry.data().to_string() {
            push(name);
        }
    }
    if let Some(alternatives) = certificate.subject_alt_names() {
        for alternative in &alternatives {
            if let Some(dns) = alternative.dnsname() {
                push(dns.to_owned());
            }
        }
    }
    names
}

/// The identities of our own server certificate, for the OAuth 2.0 `aud` check.
///
/// Port of `cert_dns_identities`. A token's `aud` entry must correspond to one
/// of these, so an empty result makes every audience check fail -- which is the
/// correct reading of an unconfigured or unreadable certificate, and why every
/// error path here returns the empty list rather than propagating.
///
/// The chain file's **first** block is the leaf; the intermediates below it are
/// not identities of this server.
#[must_use]
pub fn server_cert_names(chain: &Path) -> Vec<String> {
    std::fs::read(chain)
        .ok()
        .and_then(|pem| X509::from_pem(&pem).ok())
        .as_ref()
        .map(names_of)
        .unwrap_or_default()
}

/// Read the peer's identity off a completed TLS session.
///
/// The three states correspond exactly to `_client_authenticated`'s three
/// outcomes. Its first two -- a missing transport and a missing `ssl_object` --
/// both mean "not TLS" and both defer to `ALLOW_NON_TLS_FOR_TESTING`, so they
/// collapse into one state here; the deferral happens in
/// [`PeerIdentity::is_authenticated`], not here.
#[must_use]
pub fn peer_identity(ssl: &SslRef) -> PeerIdentity {
    let Some(certificate) = ssl.peer_certificate() else {
        // `getpeercert()` returning None. The handshake completed, so this is
        // a CERT_OPTIONAL listener and the client chose not to present one.
        //
        // Under CERT_OPTIONAL a certificate that *was* presented and failed to
        // verify aborts the handshake, so there is no fourth state in which a
        // request arrives carrying an untrusted certificate: reaching a
        // handler at all means either no certificate or a verified one.
        return PeerIdentity::TlsAnonymous;
    };

    let names = names_of(&certificate);

    // `_get_client_cert_names` returns None for an empty list, and
    // `PeerIdentity::is_authenticated` treats `Verified` with no names as
    // unauthenticated, so the empty vector carries the same meaning without a
    // second nullable layer.
    PeerIdentity::Verified { names }
}

#[cfg(test)]
mod tests {
    use super::*;
    use openssl::x509::X509;

    /// A leaf from the PKI the TLS suites use.
    ///
    /// A chain file holds the leaf first, then intermediates, and
    /// `X509::from_pem` reads exactly the first block -- which is the identity
    /// wanted here.
    fn leaf(role: &str) -> Option<X509> {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(3)?
            .join("Certificates/build.0/pem")
            .join(format!("ExampleDevice{role}.ABC.SNX00000.chain.pem"));
        X509::from_pem(&std::fs::read(path).ok()?).ok()
    }

    /// What `_tls_helpers.server_cert_names("SNX00000")` returns.
    ///
    /// Transcribed rather than derived, so that this asserts against the same
    /// fixed list the Python suites seed `node.tls_server_cert_names` from. If
    /// the PKI is regenerated with different names, both must move together.
    const SERVER_NAMES: &[&str] = &[
        "Example.Company.Device.Server.ABC.SNX00000.example.com", // CN + SAN
        "Example.Company.Device.example.com",
        "Example.Company.Device.Server.example.com",
        "Example.Company.Device.Server.ABC.example.com",
        "XYZ-SNX00000.local",
        "XYZ-SNX00000",
    ];

    #[test]
    fn the_server_identity_yields_cn_then_every_dns_san_in_order() {
        // The strong assertion: the exact list, in order, that
        // `_get_client_cert_names` produces for this identity and that the
        // Python suites seed `node.tls_server_cert_names` with. Order is not
        // cosmetic -- CN comes first and callers match in order.
        let Some(certificate) = leaf("Server") else {
            eprintln!("skipping: the PKI under Certificates/build.0 is not present");
            return;
        };
        assert_eq!(
            names_of(&certificate),
            SERVER_NAMES,
            "the extracted names differ from what _tls_helpers.server_cert_names \
             promises, so OAuth2 audience and reservation client_id binding \
             would match differently here than in Python",
        );
    }

    #[test]
    fn a_name_that_is_both_cn_and_san_appears_once() {
        // The client identity carries one name, as *both* CN and DNS SAN --
        // `_tls_helpers.client_cert_name` documents it as "CN + SAN". Without
        // the de-duplication pass it would come out twice, so this is what
        // keeps that branch honest rather than a defensive nicety.
        let Some(certificate) = leaf("Client") else {
            eprintln!("skipping: the PKI under Certificates/build.0 is not present");
            return;
        };
        assert_eq!(
            names_of(&certificate),
            ["Example.Company.Device.Client.ABC.SNX00000.example.com"],
            "the CN/SAN de-duplication is not working",
        );
    }

    #[test]
    fn an_identity_with_no_certificate_is_anonymous_not_verified() {
        // The distinction the verb gate rests on: `TlsAnonymous` is a peer that
        // completed a CERT_OPTIONAL handshake without presenting anything, and
        // it must never be mistaken for a verified one with an empty name list.
        assert!(!PeerIdentity::TlsAnonymous.is_authenticated());
        assert!(
            !PeerIdentity::Verified { names: Vec::new() }.is_authenticated(),
            "a certificate carrying no usable name authenticated nobody",
        );
    }
}
