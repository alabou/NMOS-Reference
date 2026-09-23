// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Refusing to start on a mis-configured certificate set.
//!
//! Port of `nmos_registry.py`'s `validate_startup_certs` and the two checks it
//! calls from `nmos/cert_check.py`. It runs after the command line is parsed
//! and before anything binds, "so the operator gets a clear diagnostic instead
//! of a TLS handshake failure at the first connection".
//!
//! # Why this is not merely a nicety
//!
//! Without it, `--registryCertificate` left out means [`crate::listen`]'s
//! `context_for` warns and serves **plain HTTP** -- a deployment that believes
//! it is running TLS and is not. `_server_context`'s warning path exists for
//! that reason and is unreachable in the real program precisely because this
//! check has already exited. Porting one without the other turns a fail-fast
//! into a silent downgrade, which is why this file exists.
//!
//! # The rule about per-interface anchors
//!
//! A per-interface trust anchor must itself chain to the global
//! `--trustedRootCA`. `validate_startup_certs` puts it plainly: "a mis-issued
//! anchor would otherwise be discovered only when a legitimate client was
//! rejected". An anchor nobody verified is an anchor that might trust the wrong
//! issuer.

use std::path::Path;

use openssl::stack::Stack;
use openssl::x509::store::X509StoreBuilder;
use openssl::x509::{X509, X509StoreContext};

use crate::cli::{self, Args};

/// A configuration that cannot be served.
///
/// Every message is prefixed `CONFIG:` and phrased as `validate_startup_certs`
/// phrases it, so an operator reading one implementation's diagnostic can search
/// the other's source for it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigError(pub String);

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ConfigError {}

/// Format a string as Python's `!r` does.
///
/// Every one of these messages interpolates a path with `!r`, which yields
/// single quotes where Rust's `{:?}` yields double. The wording is meant to be
/// searchable across both implementations, so an operator who greps one
/// source for a diagnostic they saw from the other finds it -- which stops
/// working the moment the quoting differs.
///
/// Python switches to double quotes only when the value contains a single quote
/// and no double quote; that rule is followed here rather than approximated.
fn repr(value: &str) -> String {
    if value.contains('\'') && !value.contains('"') {
        format!("\"{value}\"")
    } else {
        format!("'{}'", value.replace('\'', "\\'"))
    }
}

/// Format a list of strings as Python's `!r` does: `['a', 'b']`.
fn repr_list(values: &[String]) -> String {
    let inner: Vec<String> = values.iter().map(|value| repr(value)).collect();
    format!("[{}]", inner.join(", "))
}

/// Read every `BEGIN CERTIFICATE` block, in file order.
///
/// Order matters: a `chain.pem` is leaf first, then intermediates.
fn load_certs(path: &Path) -> Result<Vec<X509>, ConfigError> {
    let pem = std::fs::read(path).map_err(|error| {
        ConfigError(format!(
            "CONFIG: cannot read {}: {error}",
            repr(&path.to_string_lossy())
        ))
    })?;
    let certs = X509::stack_from_pem(&pem).map_err(|error| {
        ConfigError(format!(
            "CONFIG: cannot parse {}: {error}",
            repr(&path.to_string_lossy())
        ))
    })?;
    if certs.is_empty() {
        return Err(ConfigError(format!(
            "CONFIG: {} holds no certificate",
            repr(&path.to_string_lossy()),
        )));
    }
    Ok(certs)
}

/// Every certificate across a set of PEM files, as one trust store.
fn load_roots(paths: &[impl AsRef<Path>]) -> Result<Vec<X509>, ConfigError> {
    let mut roots = Vec::new();
    for path in paths {
        roots.extend(load_certs(path.as_ref())?);
    }
    Ok(roots)
}

/// Verify `leaf` chains to one of `roots`, through `intermediates`.
fn verify_chain(leaf: &X509, roots: &[X509], intermediates: &[X509]) -> Result<(), ConfigError> {
    let failed =
        |error: String| ConfigError(format!("certificate chain validation failed: {error}"));

    let mut store = X509StoreBuilder::new().map_err(|e| failed(e.to_string()))?;
    for root in roots {
        store
            .add_cert(root.clone())
            .map_err(|e| failed(e.to_string()))?;
    }
    let store = store.build();

    let mut chain = Stack::new().map_err(|e| failed(e.to_string()))?;
    for intermediate in intermediates {
        chain
            .push(intermediate.clone())
            .map_err(|e| failed(e.to_string()))?;
    }

    let mut context = X509StoreContext::new().map_err(|e| failed(e.to_string()))?;
    let verified = context
        .init(&store, leaf, &chain, |context| context.verify_cert())
        .map_err(|e| failed(e.to_string()))?;
    if verified {
        return Ok(());
    }
    // The verification ran and said no; the store context knows why.
    let reason = context
        .init(&store, leaf, &chain, |context| Ok(context.error()))
        .map(|error| error.to_string())
        .unwrap_or_else(|_| "unknown".to_owned());
    Err(failed(reason))
}

/// Verify a per-interface anchor is itself trusted by the global anchors.
///
/// Port of `check_trusted_ca`. Only the anchor file's **first** certificate is
/// checked, as Python checks `trusted_certs[0]`.
///
/// # Errors
///
/// The anchor does not chain to any of the roots, or a file is unreadable.
pub fn check_trusted_ca(
    root_ca_paths: &[impl AsRef<Path>],
    trusted_ca_path: &Path,
) -> Result<(), ConfigError> {
    let trusted = load_certs(trusted_ca_path)?;
    let roots = load_roots(root_ca_paths)?;
    let Some(leaf) = trusted.first() else {
        return Err(ConfigError(format!(
            "CONFIG: {} holds no certificate",
            repr(&trusted_ca_path.to_string_lossy()),
        )));
    };
    verify_chain(leaf, &roots, &[])
}

/// Verify the server certificate, its serial-bound SAN, and its private key.
///
/// Port of `check_certificate`. Three independent checks:
///
/// 1. the chain file's leaf, through its intermediates, chains to a root;
/// 2. the leaf's DNS SANs include `XYZ-<serial>`;
/// 3. the private key matches the leaf's public key.
///
/// # Errors
///
/// Any of the three, or a file that cannot be read or parsed.
pub fn check_certificate(
    root_ca_paths: &[impl AsRef<Path>],
    cert_path: &Path,
    key_path: &Path,
    serial_number: &str,
) -> Result<(), ConfigError> {
    let chain = load_certs(cert_path)?;
    let roots = load_roots(root_ca_paths)?;

    let Some((leaf, intermediates)) = chain.split_first() else {
        return Err(ConfigError(format!(
            "CONFIG: {} holds no certificate",
            repr(&cert_path.to_string_lossy()),
        )));
    };
    verify_chain(leaf, &roots, intermediates)?;

    // The uppercase `XYZ-` prefix is what the certificate generator actually
    // emits, verified against the shipped ExampleDeviceServer certificates --
    // not a guess at a convention.
    let expected = format!("XYZ-{serial_number}");
    let sans: Vec<String> = leaf
        .subject_alt_names()
        .map(|names| {
            names
                .iter()
                .filter_map(|name| name.dnsname().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default();
    if !sans.contains(&expected) {
        return Err(ConfigError(format!(
            "certificate {} SAN does not include {}; found DNS SANs: {}",
            repr(&cert_path.to_string_lossy()),
            repr(&expected),
            repr_list(&sans),
        )));
    }

    // Compare the DER SubjectPublicKeyInfo of each, which works the same for
    // RSA, EC and the edwards curves without narrowing to a key type.
    let key_pem = std::fs::read(key_path).map_err(|error| {
        ConfigError(format!(
            "cannot read {}: {error}",
            repr(&key_path.to_string_lossy())
        ))
    })?;
    let key = openssl::pkey::PKey::private_key_from_pem(&key_pem).map_err(|error| {
        ConfigError(format!(
            "cannot load private key {}: {error}",
            repr(&key_path.to_string_lossy()),
        ))
    })?;
    let leaf_public = leaf
        .public_key()
        .and_then(|public| public.public_key_to_der())
        .map_err(|error| ConfigError(format!("cannot read the certificate's key: {error}")))?;
    let key_public = key
        .public_key_to_der()
        .map_err(|error| ConfigError(format!("cannot read the private key: {error}")))?;
    if leaf_public != key_public {
        return Err(ConfigError(format!(
            "private key {} does not match certificate {}",
            repr(&key_path.to_string_lossy()),
            repr(&cert_path.to_string_lossy()),
        )));
    }
    Ok(())
}

/// Fail fast on a mis-configured certificate set.
///
/// The order of the checks is the order `validate_startup_certs` performs them,
/// because the first failure is the one reported and an operator comparing the
/// two implementations should get the same diagnostic first.
///
/// # Errors
///
/// The configuration cannot be served; the message says why.
pub fn validate_startup_certs(args: &Args) -> Result<(), ConfigError> {
    if args.registry_disable_tls {
        // Nothing to validate: this is the deliberate plain-HTTP configuration.
        return Ok(());
    }

    if args.registry_certificate.is_empty() || args.registry_key.is_empty() {
        return Err(ConfigError(
            "CONFIG: TLS is enabled but --registryCertificate / --registryKey \
             were not supplied. Pass both, or run with --registryDisableTLS."
                .to_owned(),
        ));
    }

    // Both options are repeatable, so they must pair up. Checked before any
    // file is opened: a count mismatch is a usage error, and reporting it as a
    // missing file would name the wrong problem. Worded exactly as
    // `nmos_registry.py` words it -- `tests/config_parity.rs` runs both
    // binaries over the same argv and compares this line to the other's.
    if args.registry_certificate.len() != args.registry_key.len() {
        return Err(ConfigError(format!(
            "CONFIG: --registryCertificate was given {} time(s) but \
             --registryKey {} time(s); pass one key per certificate, in the \
             same order",
            args.registry_certificate.len(),
            args.registry_key.len(),
        )));
    }

    // Certificate then key within each pair, so a single-identity
    // configuration produces exactly the diagnostics it did before these
    // options became repeatable.
    for (certificate, key) in cli::identities(&args.registry_certificate, &args.registry_key) {
        for (role, path) in [
            ("--registryCertificate", certificate.as_path()),
            ("--registryKey", key.as_path()),
        ] {
            if !path.is_file() {
                return Err(ConfigError(format!(
                    "CONFIG: {role} is not accessible: {}",
                    repr(&path.to_string_lossy()),
                )));
            }
        }
    }

    let interface_cas: [(&str, &Vec<std::path::PathBuf>); 2] = [
        (
            "--registrationTrustedRootCA",
            &args.registration_trusted_root_ca,
        ),
        ("--queryTrustedRootCA", &args.query_trusted_root_ca),
    ];

    if interface_cas.iter().any(|(_, cas)| !cas.is_empty()) {
        if args.trusted_root_ca.is_empty() {
            return Err(ConfigError(
                "CONFIG: --trustedRootCA is required to validate the \
                 per-interface trusted root CAs"
                    .to_owned(),
            ));
        }
        for path in &args.trusted_root_ca {
            if !path.is_file() {
                return Err(ConfigError(format!(
                    "CONFIG: --trustedRootCA is not accessible: {}",
                    repr(&path.to_string_lossy()),
                )));
            }
        }
        for (role, cas) in interface_cas {
            for path in cas {
                if !path.is_file() {
                    return Err(ConfigError(format!(
                        "CONFIG: {role} is not accessible: {}",
                        repr(&path.to_string_lossy()),
                    )));
                }
                check_trusted_ca(&args.trusted_root_ca, path).map_err(|error| {
                    ConfigError(format!(
                        "CONFIG: {role} {} is not valid based on global \
                         --trustedRootCA: {error}",
                        repr(&path.to_string_lossy()),
                    ))
                })?;
            }
        }
    }

    if !args.trusted_root_ca.is_empty() {
        // Per identity: each must chain to a configured root and each key must
        // match its own certificate. Two identities of different types chain
        // to different roots in this PKI, so --trustedRootCA has to carry
        // both -- named here rather than at a peer's handshake.
        for (certificate, key) in cli::identities(&args.registry_certificate, &args.registry_key) {
            check_certificate(
                &args.trusted_root_ca,
                certificate.as_path(),
                key.as_path(),
                &args.registry_serial_number,
            )
            .map_err(|error| {
                ConfigError(format!(
                    "CONFIG: --registryCertificate is not valid: {error}"
                ))
            })?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser as _;
    use std::path::PathBuf;

    fn parse(extra: &[&str]) -> Args {
        let mut argv = vec!["nmos-registry"];
        argv.extend_from_slice(extra);
        Args::parse_from(argv)
    }

    fn certs() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(3)
            .expect("crate is nested three deep under the repository root")
            .join("Certificates/build.0")
    }

    fn chain() -> String {
        certs()
            .join("pem/ExampleDeviceServer.ABC.SNX00000.chain.pem")
            .to_string_lossy()
            .into_owned()
    }

    fn key() -> String {
        certs()
            .join("key/ExampleDeviceServer.ABC.SNX00000.key")
            .to_string_lossy()
            .into_owned()
    }

    fn root() -> String {
        certs()
            .join("ExampleRootCA.pem")
            .to_string_lossy()
            .into_owned()
    }

    fn pki_available() -> bool {
        let present = Path::new(&chain()).is_file();
        if !present {
            eprintln!("skipping: the PKI under Certificates/build.0 is not present");
        }
        present
    }

    #[test]
    fn tls_without_a_certificate_refuses_to_start() {
        // The one that matters most: without this the registry would come up on
        // plain HTTP while its operator believed it was running TLS.
        let error = validate_startup_certs(&parse(&[])).expect_err("must refuse");
        assert!(
            error.to_string().contains("--registryCertificate"),
            "{error}"
        );
        assert!(
            error.to_string().contains("--registryDisableTLS"),
            "{error}"
        );
    }

    #[test]
    fn disabling_tls_skips_validation_entirely() {
        // The deliberate plain-HTTP configuration is not a misconfiguration.
        validate_startup_certs(&parse(&["--registryDisableTLS"])).expect("must not refuse");
    }

    #[test]
    fn a_certificate_path_that_does_not_exist_refuses_to_start() {
        if !pki_available() {
            return;
        }
        let error = validate_startup_certs(&parse(&[
            "--registryCertificate",
            "/nonexistent/cert.pem",
            "--registryKey",
            &key(),
        ]))
        .expect_err("must refuse");
        assert!(error.to_string().contains("not accessible"), "{error}");
    }

    #[test]
    fn a_missing_key_is_refused_even_when_the_certificate_is_present() {
        if !pki_available() {
            return;
        }
        let error = validate_startup_certs(&parse(&["--registryCertificate", &chain()]))
            .expect_err("must refuse");
        assert!(error.to_string().contains("--registryKey"), "{error}");
    }

    #[test]
    fn an_interface_anchor_requires_the_global_anchor() {
        if !pki_available() {
            return;
        }
        // "A mis-issued anchor would otherwise be discovered only when a
        // legitimate client was rejected."
        let error = validate_startup_certs(&parse(&[
            "--registryCertificate",
            &chain(),
            "--registryKey",
            &key(),
            "--registrationTrustedRootCA",
            &root(),
        ]))
        .expect_err("must refuse");
        assert!(error.to_string().contains("trustedRootCA"), "{error}");
    }

    #[test]
    fn a_complete_configuration_is_accepted() {
        if !pki_available() {
            return;
        }
        // Exercises all three of `check_certificate`'s checks against the real
        // PKI: the chain, the `XYZ-SNX00000` SAN, and the key match.
        validate_startup_certs(&parse(&[
            "--registryCertificate",
            &chain(),
            "--registryKey",
            &key(),
            "--registrySerialNumber",
            "SNX00000",
            "--trustedRootCA",
            &root(),
            "--registrationTrustedRootCA",
            &root(),
        ]))
        .expect("the shipped PKI should validate");
    }

    #[test]
    fn the_serial_must_appear_in_the_certificate_sans() {
        if !pki_available() {
            return;
        }
        // The serial binds the certificate to this device. A registry started
        // with someone else's serial would mint `aud` values it cannot honour.
        let error = validate_startup_certs(&parse(&[
            "--registryCertificate",
            &chain(),
            "--registryKey",
            &key(),
            "--registrySerialNumber",
            "SNX09999",
            "--trustedRootCA",
            &root(),
        ]))
        .expect_err("a mismatched serial must refuse");
        assert!(error.to_string().contains("XYZ-SNX09999"), "{error}");
        assert!(error.to_string().contains("SAN"), "{error}");
    }

    #[test]
    fn a_key_belonging_to_another_certificate_is_refused() {
        if !pki_available() {
            return;
        }
        // The EC identity's key against the RSA identity's certificate: both
        // are real and valid, and they do not go together.
        let other_key = certs().join("key/ExampleDeviceServer.ABC.SNX00000.ec.key");
        if !other_key.is_file() {
            eprintln!("skipping: no EC key in the PKI");
            return;
        }
        let error = validate_startup_certs(&parse(&[
            "--registryCertificate",
            &chain(),
            "--registryKey",
            &other_key.to_string_lossy(),
            "--registrySerialNumber",
            "SNX00000",
            "--trustedRootCA",
            &root(),
        ]))
        .expect_err("a mismatched key must refuse");
        assert!(
            error.to_string().contains("does not match certificate"),
            "{error}",
        );
    }

    #[test]
    fn an_anchor_that_does_not_chain_to_the_global_one_is_refused() {
        if !pki_available() {
            return;
        }
        // The product CA is a real intermediate, but it is not something the
        // *client* CA bundle vouches for on its own. Using the device leaf as
        // an "anchor" is the clearer case: it chains nowhere useful.
        let leaf_as_anchor = certs().join("pem/ExampleDeviceClient.ABC.SNX00000.chain.pem");
        if !leaf_as_anchor.is_file() {
            eprintln!("skipping: no client chain in the PKI");
            return;
        }
        let product = certs().join("ExampleProductCA.0.0.pem");
        if !product.is_file() {
            eprintln!("skipping: no product CA in the PKI");
            return;
        }
        // Global anchor is the *product* CA; the interface anchor is the root,
        // which the product CA does not vouch for.
        let error = validate_startup_certs(&parse(&[
            "--registryCertificate",
            &chain(),
            "--registryKey",
            &key(),
            "--registrySerialNumber",
            "SNX00000",
            "--trustedRootCA",
            &product.to_string_lossy(),
            "--registrationTrustedRootCA",
            &leaf_as_anchor.to_string_lossy(),
        ]))
        .expect_err("an unvouched anchor must refuse");
        assert!(
            error.to_string().contains("not valid based on global"),
            "{error}",
        );
    }
}
