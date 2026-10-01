// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! A panic in a task ends the process, with the panic on the record.
//!
//! The hazard this guards (`panic_policy`'s module doc): tokio turns a panic
//! inside a spawned task into a `JoinError`, and the consensus member's tasks
//! are never joined, so a member whose tick or apply panicked served on as a
//! zombie that no service manager would restart. That state is reproduced here
//! on purpose, by the same child run without the policy, so the test says
//! both what the policy does and what happens without it.
//!
//! The child is this test binary itself, re-run on its ignored half: no
//! production code knows about the environment variables below.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::io::Read as _;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::time::{Duration, Instant};

/// What the planted panic says, and what every record of it must carry.
const MARKER: &str = "planted: a panic inside a spawned task";
/// Set for the child only: where it logs, and that it is the child.
const LOG_ENV: &str = "NMOS_PANIC_POLICY_LOG";
/// Set for the child only: `0` runs it without the policy, to show the hazard.
const POLICY_ENV: &str = "NMOS_PANIC_POLICY_INSTALL";
/// libc's `SIGABRT` on Linux, which `std::process::abort` raises; libc is not
/// a dependency of this crate.
#[cfg(unix)]
const SIGABRT: i32 = 6;

/// A directory of its own for one run, removed afterwards.
struct Scratch(PathBuf);

impl Scratch {
    fn new(label: &str) -> Self {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "nmos-panic-policy-{}-{label}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed),
        ));
        std::fs::create_dir_all(&path).expect("a scratch directory");
        Self(path)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        drop(std::fs::remove_dir_all(&self.0));
    }
}

/// The child half: install the policy (or not), spawn a task that panics,
/// await it, and return normally -- which is reached only without the policy.
#[test]
#[ignore = "the child half of the tests below; it runs only when NMOS_PANIC_POLICY_LOG is set"]
fn child_panics_in_a_task() {
    let Ok(log) = std::env::var(LOG_ENV) else {
        return;
    };
    if std::env::var(POLICY_ENV).as_deref() != Ok("0") {
        nmos_registry_bin::panic_policy::install();
    }
    nmos_registry_bin::logging::init(Path::new(&log));
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("a runtime");
    runtime.block_on(async {
        let task = tokio::spawn(async {
            panic!("{MARKER}");
        });
        // Without the policy this is a `JoinError`, and the process goes on.
        drop(task.await);
    });
}

struct Outcome {
    status: Option<ExitStatus>,
    stderr: String,
    recorded: String,
}

/// Run the child half with the policy on or off, bounded.
fn run_child(policy: bool) -> Outcome {
    let scratch = Scratch::new(if policy { "with" } else { "without" });
    let log = scratch.0.join("registry.log");
    let mut child: Child = Command::new(std::env::current_exe().expect("this test binary"))
        .args([
            "child_panics_in_a_task",
            "--exact",
            "--ignored",
            "--nocapture",
        ])
        .env(LOG_ENV, &log)
        .env(POLICY_ENV, if policy { "1" } else { "0" })
        .env("NMOS_LOG_LEVEL", "ERROR")
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the child starts");

    // Bounded: a child that neither aborts nor exits is a hang, not a pass.
    let deadline = Instant::now()
        .checked_add(Duration::from_secs(20))
        .expect("a deadline within this century");
    let status = loop {
        match child.try_wait().expect("try_wait") {
            Some(status) => break Some(status),
            None if Instant::now() >= deadline => {
                drop(child.kill());
                drop(child.wait());
                break None;
            }
            None => std::thread::sleep(Duration::from_millis(50)),
        }
    };
    let mut stderr = String::new();
    if let Some(mut pipe) = child.stderr.take() {
        drop(pipe.read_to_string(&mut stderr));
    }
    let recorded = std::fs::read_to_string(&log).unwrap_or_default();
    Outcome {
        status,
        stderr,
        recorded,
    }
}

#[test]
fn without_the_policy_a_panic_in_a_task_is_swallowed_and_the_process_goes_on() {
    let outcome = run_child(false);
    let status = outcome.status.expect("the child ended within the deadline");
    assert!(
        status.success(),
        "the hazard is gone without the policy? status {status}, stderr: {}",
        outcome.stderr,
    );
    // The default hook still printed it: a panic is never silent on stderr.
    // What nobody did was act on it.
    assert!(outcome.stderr.contains(MARKER), "{}", outcome.stderr);
    assert!(
        !outcome.recorded.contains("this process stops"),
        "{}",
        outcome.recorded,
    );
}

#[test]
fn a_panic_in_a_task_ends_the_process_and_is_logged() {
    let outcome = run_child(true);
    let status = outcome.status.expect("the child ended within the deadline");
    assert!(
        !status.success(),
        "the child exited successfully: the panic was swallowed as a JoinError and the \
         process served on; stderr: {}",
        outcome.stderr,
    );
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt as _;
        assert_eq!(
            status.signal(),
            Some(SIGABRT),
            "not an abort: {status}; stderr: {}",
            outcome.stderr,
        );
    }
    // stderr: the default hook's text, chained first.
    assert!(outcome.stderr.contains("panicked"), "{}", outcome.stderr);
    assert!(outcome.stderr.contains(MARKER), "{}", outcome.stderr);
    // The log file: the record, with the location and the policy's own line.
    assert!(outcome.recorded.contains(MARKER), "{}", outcome.recorded);
    assert!(
        outcome.recorded.contains("tests/panic_policy.rs"),
        "{}",
        outcome.recorded,
    );
    assert!(
        outcome.recorded.contains("this process stops"),
        "{}",
        outcome.recorded,
    );
}
