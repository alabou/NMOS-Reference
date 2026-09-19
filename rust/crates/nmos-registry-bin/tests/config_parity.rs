// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Does a misconfigured registry refuse in the same words, with the same code?
//!
//! `validate_startup_certs` exists so an operator gets "a clear diagnostic
//! instead of a TLS handshake failure at the first connection". Two
//! implementations that refuse for the same reason in different words make that
//! diagnostic worse, not better -- someone who saw one message and greps the
//! other source for it finds nothing and concludes the check is missing.
//!
//! So this runs **both binaries** over the same argv and compares the
//! `CONFIG:` line and the exit code. It is the only test in the workspace that
//! executes the Python registry; everything else compares against a recording.
//! Here a recording would be worse, because half of what is being checked --
//! that the refusal happens *at all*, before anything binds -- is a property of
//! the program rather than of a message.
//!
//! # The case that matters most
//!
//! `no cert/key`. Without `validate_startup_certs` the Rust registry warns and
//! serves **plain HTTP**, because `context_for` is written to do exactly that
//! and its warning path is unreachable in the Python only because this check
//! has already exited. That divergence -- a deployment believing it runs TLS
//! and not -- is what this file is really guarding.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::path::{Path, PathBuf};
use std::process::Command;

/// `nmos-reference/`, three levels above this crate.
fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("crate is nested three deep under the repository root")
        .to_path_buf()
}

fn certs() -> PathBuf {
    repo().join("Certificates/build.0")
}

/// The Rust binary under test, as cargo built it beside this test.
fn rust_binary() -> Option<PathBuf> {
    let mut path = std::env::current_exe().ok()?;
    // .../target/<profile>/deps/config_parity-<hash>
    path.pop();
    path.pop();
    let binary = path.join("nmos-registry");
    binary.is_file().then_some(binary)
}

/// Whether both implementations and the PKI are present.
fn available() -> Option<(PathBuf, PathBuf)> {
    let python = repo().join(".venv/bin/python");
    let script = repo().join("nmos_registry.py");
    let binary = rust_binary();

    if !python.is_file() || !script.is_file() {
        eprintln!("skipping: no .venv/bin/python or nmos_registry.py to compare against");
        return None;
    }
    if !certs()
        .join("pem/ExampleDeviceServer.ABC.SNX00000.chain.pem")
        .is_file()
    {
        eprintln!("skipping: the PKI under Certificates/build.0 is not present");
        return None;
    }
    let Some(binary) = binary else {
        eprintln!("skipping: the nmos-registry binary is not built beside this test");
        return None;
    };
    Some((binary, python))
}

/// What one implementation did with an argv.
struct Refusal {
    code: Option<i32>,
    message: String,
}

/// The last `CONFIG:` line of stderr, which is the diagnostic proper.
///
/// Python may emit warnings before it; Rust prints the message alone. Taking
/// the last `CONFIG:` line compares like with like rather than comparing one
/// implementation's stderr noise with the other's.
fn config_line(stderr: &[u8]) -> String {
    String::from_utf8_lossy(stderr)
        .lines()
        .rfind(|line| line.starts_with("CONFIG:"))
        .unwrap_or_default()
        .to_owned()
}

fn run(program: &Path, leading: &[&Path], argv: &[String]) -> Refusal {
    let mut command = Command::new(program);
    for path in leading {
        command.arg(path);
    }
    command.args(argv);
    // A misconfigured registry must not reach the point of writing one.
    command.env("NMOS_LOG_LEVEL", "ERROR");
    command.stdout(std::process::Stdio::null());
    command.stderr(std::process::Stdio::piped());

    // Bounded, not `output()`. Every case here is expected to be refused, so
    // the process should exit at once -- but a case that stops being refused
    // turns `output()` into a wait with no end, and the suite hangs with a
    // *listening registry* still holding its ports. That orphan then fails
    // `a_valid_configuration_is_not_refused_by_either` with `AddrInUse`,
    // which points at the wrong test entirely. Observed, and it cost more to
    // diagnose than this loop costs to write.
    let mut child = command.spawn().expect("the process starts");
    let deadline = std::time::Instant::now()
        .checked_add(std::time::Duration::from_secs(20))
        .expect("a deadline within this century");
    let status = loop {
        match child.try_wait().expect("try_wait") {
            Some(status) => break Some(status),
            None if std::time::Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
            None => std::thread::sleep(std::time::Duration::from_millis(50)),
        }
    };

    let mut stderr = Vec::new();
    if let Some(mut pipe) = child.stderr.take() {
        use std::io::Read as _;
        let _ = pipe.read_to_end(&mut stderr);
    }

    match status {
        Some(status) => Refusal {
            code: status.code(),
            message: config_line(&stderr),
        },
        // Distinguishable from any real refusal, so the comparison reports
        // "one accepted it" rather than comparing a message that never came.
        None => Refusal {
            code: None,
            message: "<not refused: still running>".to_owned(),
        },
    }
}

#[test]
fn every_config_refusal_matches_the_python_word_for_word() {
    let Some((binary, python)) = available() else {
        return;
    };
    let script = repo().join("nmos_registry.py");
    let certs = certs();

    let chain = certs.join("pem/ExampleDeviceServer.ABC.SNX00000.chain.pem");
    let key = certs.join("key/ExampleDeviceServer.ABC.SNX00000.key");
    let ec_key = certs.join("key/ExampleDeviceServer.ABC.SNX00000.ec.key");
    let root = certs.join("ExampleRootCA.pem");

    let s = |path: &Path| path.to_string_lossy().into_owned();
    let cases: Vec<(&str, Vec<String>)> = vec![
        // The one that would otherwise be a silent downgrade to plain HTTP.
        (
            "no cert or key",
            vec!["--registrationPort".into(), "18499".into()],
        ),
        (
            "certificate missing from disk",
            vec![
                "--registryCertificate".into(),
                "/nonexistent/c.pem".into(),
                "--registryKey".into(),
                s(&key),
            ],
        ),
        (
            "key missing from disk",
            vec![
                "--registryCertificate".into(),
                s(&chain),
                "--registryKey".into(),
                "/nonexistent/k.pem".into(),
            ],
        ),
        (
            "interface anchor without the global one",
            vec![
                "--registryCertificate".into(),
                s(&chain),
                "--registryKey".into(),
                s(&key),
                "--registrationTrustedRootCA".into(),
                s(&root),
            ],
        ),
        (
            "global anchor missing from disk",
            vec![
                "--registryCertificate".into(),
                s(&chain),
                "--registryKey".into(),
                s(&key),
                "--registrationTrustedRootCA".into(),
                s(&root),
                "--trustedRootCA".into(),
                "/nonexistent/ca.pem".into(),
            ],
        ),
        (
            "serial absent from the certificate SANs",
            vec![
                "--registryCertificate".into(),
                s(&chain),
                "--registryKey".into(),
                s(&key),
                "--registrySerialNumber".into(),
                "SNX09999".into(),
                "--trustedRootCA".into(),
                s(&root),
            ],
        ),
        (
            "key belonging to another certificate",
            vec![
                "--registryCertificate".into(),
                s(&chain),
                "--registryKey".into(),
                s(&ec_key),
                "--registrySerialNumber".into(),
                "SNX00000".into(),
                "--trustedRootCA".into(),
                s(&root),
            ],
        ),
    ];

    let mut differing = Vec::new();
    for (name, argv) in &cases {
        if name.contains("another certificate") && !ec_key.is_file() {
            continue;
        }
        let ours = run(&binary, &[], argv);
        let theirs = run(&python, &[script.as_path()], argv);

        if ours.code != theirs.code || ours.message != theirs.message {
            differing.push(format!(
                "  {name}\n    rust (exit {:?}): {}\n    py   (exit {:?}): {}",
                ours.code, ours.message, theirs.code, theirs.message,
            ));
            continue;
        }
        assert_eq!(
            ours.code,
            Some(1),
            "{name}: both refused, but not with the exit code SystemExit gives",
        );
        assert!(
            ours.message.starts_with("CONFIG:"),
            "{name}: the refusal carried no CONFIG: diagnostic",
        );
    }

    assert!(
        differing.is_empty(),
        "{} of {} configurations are refused differently:\n{}",
        differing.len(),
        cases.len(),
        differing.join("\n"),
    );
}

#[test]
fn a_valid_configuration_is_not_refused_by_either() {
    // The other half: a check that refused everything would pass the test above
    // while making the registry unstartable.
    let Some((binary, python)) = available() else {
        return;
    };
    let script = repo().join("nmos_registry.py");
    let certs = certs();
    let s = |path: PathBuf| path.to_string_lossy().into_owned();

    let argv: Vec<String> = vec![
        "--registryCertificate".into(),
        s(certs.join("pem/ExampleDeviceServer.ABC.SNX00000.chain.pem")),
        "--registryKey".into(),
        s(certs.join("key/ExampleDeviceServer.ABC.SNX00000.key")),
        "--registrySerialNumber".into(),
        "SNX00000".into(),
        "--trustedRootCA".into(),
        s(certs.join("ExampleRootCA.pem")),
        // Every listening port moved off its default. Only the registration
        // port used to be, which left query and websocket on 8446/8448 -- so
        // any other registry on this machine, including one the developer is
        // running, failed this test with `AddrInUse` and a message about a
        // "refused configuration". `--help` would skip the checks, so the
        // process has to be started for real and killed below.
        "--registrationPort".into(),
        "18498".into(),
        "--queryPort".into(),
        "18496".into(),
        "--queryWebSocketPort".into(),
        "18497".into(),
    ];

    for (label, program, leading) in [
        ("rust", binary.as_path(), Vec::new()),
        ("python", python.as_path(), vec![script.as_path()]),
    ] {
        let mut command = Command::new(program);
        for path in &leading {
            command.arg(path);
        }
        command
            .args(&argv)
            .env("NMOS_LOG_LEVEL", "ERROR")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::piped());
        let mut child = command.spawn().expect("spawns");

        // Give it long enough to have refused, if it were going to.
        std::thread::sleep(std::time::Duration::from_millis(2500));
        match child.try_wait().expect("try_wait") {
            None => {
                // Still running: it accepted the configuration, which is right.
                let _ = child.kill();
                let _ = child.wait();
            }
            Some(status) => {
                let mut stderr = String::new();
                if let Some(mut pipe) = child.stderr.take() {
                    use std::io::Read as _;
                    let _ = pipe.read_to_string(&mut stderr);
                }
                panic!(
                    "{label} refused a valid configuration ({status}):\n{}",
                    config_line(stderr.as_bytes()),
                );
            }
        }
    }
}
