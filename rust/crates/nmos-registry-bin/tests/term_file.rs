// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! A member's term file is the member's, whichever implementation runs it.
//!
//! Both implementations write the file alike, byte for byte (`persist.rs`), so
//! a member switched from one to the other can keep its term, its vote and its
//! start count -- if both look for it under the same name. This build named it
//! after the member while the Python called it `raft-state.json`; a member
//! switched in place (`start-registry-raft.sh --rust`) found no file and came
//! back as brand new, and one this machine's launcher had run both ways held
//! both files, term 1 in one and term 12 in the other.
//!
//! So this runs **both registries** over a term file, as `config_parity.rs`
//! runs them over an argv: each must load that very file and carry on from it.
//! A name that drifts in either implementation fails here.
//!
//! Process-level fault injection -- SIGKILL, SIGSTOP, rolling restarts -- is
//! not duplicated here: the Python rig `nmos/registry/tests/_processes.py`
//! drives both binaries, each test as `[python]` and `[rust]`, since the two
//! take the same command line.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

/// The name both implementations give the term file.
const TERM_FILE: &str = "raft-state.json";

/// What an earlier build named the file for a lone member on 127.0.0.1: the
/// member's derived name.
const EARLIER_TERM_FILE: &str = "nmos-registry-127.0.0.1.json";

/// A member that has started nine times, voted for member 1 in term 12, and
/// reserved no paging cursor -- as either implementation writes it.
const STORED: &str = "{\n  \"version\": 1,\n  \"term\": 12,\n  \"voted_for\": 1,\n  \
                      \"incarnation\": 9,\n  \"cursor_reservation\": null\n}";

/// `nmos-reference/`, three levels above this crate.
fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("crate is nested three deep under the repository root")
        .to_path_buf()
}

/// The Rust binary under test, as cargo built it beside this test.
fn rust_binary() -> Option<PathBuf> {
    let mut path = std::env::current_exe().ok()?;
    // .../target/<profile>/deps/term_file-<hash>
    path.pop();
    path.pop();
    let binary = path.join("nmos-registry");
    binary.is_file().then_some(binary)
}

/// A directory of its own for one run, removed afterwards.
struct Scratch(PathBuf);

impl Scratch {
    fn new(label: &str) -> Self {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "nmos-term-file-{}-{label}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed),
        ));
        std::fs::create_dir_all(path.join("state")).expect("a scratch directory");
        Self(path)
    }

    fn state(&self) -> PathBuf {
        self.0.join("state")
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        drop(std::fs::remove_dir_all(&self.0));
    }
}

/// A port nothing is listening on, and the one above it: the peer port is the
/// client port plus one.
fn free_pair() -> u16 {
    for _ in 0..200 {
        let probe = std::net::TcpListener::bind("127.0.0.1:0").expect("a port");
        let port = probe.local_addr().expect("an address").port();
        if let Some(above) = port.checked_add(1)
            && std::net::TcpListener::bind(("127.0.0.1", above)).is_ok()
        {
            return port;
        }
    }
    panic!("no two consecutive free ports");
}

fn free_port() -> u16 {
    let probe = std::net::TcpListener::bind("127.0.0.1:0").expect("a port");
    probe.local_addr().expect("an address").port()
}

/// A lone raft member over `scratch`'s state directory, with nothing secured.
fn argv(scratch: &Scratch) -> Vec<String> {
    let client = free_pair();
    [
        "--registryDisableTLS",
        "--registryAddr",
        "127.0.0.1",
        "--registrationPort",
        &free_port().to_string(),
        "--queryPort",
        &free_port().to_string(),
        "--queryWebSocketPort",
        &free_port().to_string(),
        "--logFile",
        &scratch.0.join("registry.log").to_string_lossy(),
        "--statusInterval",
        "0",
        "--distributed",
        "--distributedBackend",
        "raft",
        "--raftDisableTLS",
        "--raftStateDir",
        &scratch.state().to_string_lossy(),
        "--raftNamespace",
        "/term-file",
        "--registryAdvertisedHost",
        &format!("127.0.0.1:{client}"),
        "--raftPeerPort",
        &client
            .checked_add(1)
            .expect("free_pair leaves a port above")
            .to_string(),
    ]
    .into_iter()
    .map(str::to_owned)
    .collect()
}

fn spawn(program: &Path, leading: &[&Path], argv: &[String]) -> Child {
    let mut command = Command::new(program);
    for path in leading {
        command.arg(path);
    }
    command
        .args(argv)
        .env("NMOS_LOG_LEVEL", "ERROR")
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the process starts")
}

/// Poll `ready` for up to `seconds`.
fn within(seconds: u64, mut ready: impl FnMut() -> bool) -> bool {
    let deadline = Instant::now()
        .checked_add(Duration::from_secs(seconds))
        .expect("a deadline within this century");
    while Instant::now() < deadline {
        if ready() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    ready()
}

/// The stored term and start count, if the file holds them.
fn stored(path: &Path) -> Option<(u64, u64)> {
    let text = std::fs::read_to_string(path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    Some((value["term"].as_u64()?, value["incarnation"].as_u64()?))
}

/// Every `.json` file in `directory` -- temporary files, which begin with a
/// dot, aside.
fn term_files(directory: &Path) -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(directory)
        .expect("the state directory")
        .filter_map(|entry| entry.ok()?.file_name().into_string().ok())
        .filter(|name| name.ends_with(".json") && !name.starts_with('.'))
        .collect();
    names.sort_unstable();
    names
}

fn stop(mut child: Child) {
    drop(child.kill());
    drop(child.wait());
}

#[test]
fn both_registries_carry_on_from_the_term_file_the_other_wrote() {
    let Some(binary) = rust_binary() else {
        eprintln!("skipping: the nmos-registry binary is not built beside this test");
        return;
    };
    let python = repo().join(".venv/bin/python");
    let script = repo().join("nmos_registry.py");
    let mut registries = vec![("rust", binary, Vec::new())];
    if python.is_file() && script.is_file() {
        registries.push(("python", python, vec![script]));
    } else {
        eprintln!("the Python half is skipped: no .venv/bin/python or nmos_registry.py");
    }

    for (label, program, leading) in registries {
        let scratch = Scratch::new(label);
        let path = scratch.state().join(TERM_FILE);
        std::fs::write(&path, STORED).expect("written");

        let leading: Vec<&Path> = leading.iter().map(PathBuf::as_path).collect();
        let child = spawn(&program, &leading, &argv(&scratch));
        // Loading is what bumps the start count, and it is saved at once: 10
        // is this file, read. Anything else is another file, or none.
        let loaded = within(30, || {
            stored(&path).is_some_and(|(_, started)| started == 10)
        });
        let now = stored(&path);
        let files = term_files(&scratch.state());
        stop(child);

        assert!(
            loaded,
            "{label} never carried on from {TERM_FILE}: it holds {now:?} (term, starts), \
             and the state directory holds {files:?}",
        );
        let (term, _) = now.expect("read above");
        assert!(term >= 12, "{label} went back from term 12 to term {term}");
        assert_eq!(
            files,
            vec![TERM_FILE.to_owned()],
            "{label} wrote a second term file"
        );
    }
}

#[test]
fn a_term_file_an_earlier_build_named_is_refused_not_ignored() {
    let Some(binary) = rust_binary() else {
        eprintln!("skipping: the nmos-registry binary is not built beside this test");
        return;
    };
    // The earlier build's file alone -- a member that only ever ran it -- and
    // beside the current one, as this machine's launcher left two members.
    for (case, current) in [("alone", false), ("beside the current file", true)] {
        let scratch = Scratch::new("earlier");
        let earlier = scratch.state().join(EARLIER_TERM_FILE);
        std::fs::write(&earlier, STORED).expect("written");
        if current {
            std::fs::write(scratch.state().join(TERM_FILE), STORED).expect("written");
        }

        let mut child = spawn(&binary, &[], &argv(&scratch));
        let exited = within(20, || child.try_wait().expect("try_wait").is_some());
        let status = child.try_wait().expect("try_wait");
        let mut stderr = String::new();
        if exited && let Some(mut pipe) = child.stderr.take() {
            use std::io::Read as _;
            drop(pipe.read_to_string(&mut stderr));
        }
        stop(child);

        assert!(
            exited,
            "{case}: the member started beside {EARLIER_TERM_FILE} instead of refusing",
        );
        assert_eq!(status.and_then(|s| s.code()), Some(1), "{case}: {stderr}");
        let refusal = stderr
            .lines()
            .rfind(|line| line.starts_with("CONFIG:"))
            .unwrap_or_default();
        assert!(
            refusal.contains(EARLIER_TERM_FILE) && refusal.contains("Refusing to start"),
            "{case}: refused, but not for the earlier file: {stderr}",
        );
        assert_eq!(
            std::fs::read_to_string(&earlier).expect("still there"),
            STORED,
            "{case}: the earlier file was changed",
        );
    }
}
