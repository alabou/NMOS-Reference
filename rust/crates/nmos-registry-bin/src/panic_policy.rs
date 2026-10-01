// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! What a panic does to this process: it ends it, on the record.
//!
//! A panic inside a tokio task is caught at the task boundary and becomes a
//! `JoinError` that nothing here awaits -- the consensus member's tick, apply
//! and drain tasks are spawned and kept only to be aborted at close
//! (`nmos_registry_raft::node`), and the transport's tasks likewise. Left to
//! that, a panic in the tick leaves a member that never campaigns or heartbeats
//! again, and one in apply leaves a member serving a store that no longer
//! moves, while its HTTP listeners keep answering and its readiness keeps
//! reading READY: a zombie no service manager restarts, because nothing exited.
//!
//! The Python member stops itself on an exception nothing in it expected
//! (`RaftUnexpectedError`, `RaftNode._fail`) and its process exits 1. A panic
//! is the Rust twin of that exception, and the policy is the same: the process
//! ends, with the panic on the record first, and the service manager brings the
//! member back empty, to be caught up as a non-voting learner. `go.etcd.io/raft`
//! takes the same position: `Panicf`, and no `recover()`.
//!
//! The hook aborts on its own rather than leaving it to `panic = "abort"` in
//! the release profile, so a debug build and the child-process test behave
//! exactly as the release binary does; the profile setting stays as belt and
//! braces, and because an unwinding panic across the OpenSSL callbacks would
//! be undefined behaviour anyway. `abort` skips destructors, and nothing here
//! needs them: the term file is written atomically (`persist.rs`), and the log
//! sinks write through, one syscall per record (`logging.rs`), so whatever
//! reached the record before the abort is on disk.

use std::any::Any;
use std::io::Write as _;
use std::panic::Location;

/// What the record needs of a panic.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PanicReport {
    /// The thread that panicked, or `<unnamed>`.
    pub thread: String,
    /// `file:line:column`, or `<unknown>`.
    pub location: String,
    /// The payload's text -- what `panic!` was given -- or a note that it was
    /// not text.
    pub message: String,
}

impl PanicReport {
    /// Describe a panic from its parts. Pure, so a test can hand it any.
    #[must_use]
    pub fn describe(
        thread: Option<&str>,
        location: Option<&Location<'_>>,
        payload: &(dyn Any + Send),
    ) -> Self {
        let message = if let Some(text) = payload.downcast_ref::<&str>() {
            (*text).to_owned()
        } else if let Some(text) = payload.downcast_ref::<String>() {
            text.clone()
        } else {
            "(non-text panic payload)".to_owned()
        };
        Self {
            thread: thread.unwrap_or("<unnamed>").to_owned(),
            location: location.map_or_else(|| "<unknown>".to_owned(), ToString::to_string),
            message,
        }
    }

    fn of(info: &std::panic::PanicHookInfo<'_>) -> Self {
        let current = std::thread::current();
        Self::describe(current.name(), info.location(), info.payload())
    }
}

/// Install the policy: every panic ends the process, after it is logged.
///
/// Called first thing in `main`, before the logging sinks exist. Until
/// `logging::init` has run, the chained default hook still prints the panic to
/// stderr (with `RUST_BACKTRACE` honoured) and the abort still happens; after
/// it, the record also reaches the log file and the console.
pub fn install() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        // The default text to stderr first: it needs no subscriber and takes
        // no lock, so it is written whatever state the rest is in.
        previous(info);
        let report = PanicReport::of(info);
        tracing::error!(
            thread = %report.thread,
            location = %report.location,
            message = %report.message,
            "registry: panic; this process stops",
        );
        // The console sink is line-buffered and every record ends a line; this
        // is belt and braces for whatever else stdout holds.
        drop(std::io::stdout().flush());
        std::process::abort();
    }));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_text_payload_is_the_message() {
        let report = PanicReport::describe(Some("worker"), None, &"boom");
        assert_eq!(report.message, "boom");
        assert_eq!(report.thread, "worker");
        assert_eq!(report.location, "<unknown>");
    }

    #[test]
    fn a_string_payload_is_the_message() {
        let payload: Box<dyn Any + Send> = Box::new(String::from("formatted boom"));
        let report = PanicReport::describe(None, None, payload.as_ref());
        assert_eq!(report.message, "formatted boom");
        assert_eq!(report.thread, "<unnamed>");
    }

    #[test]
    fn a_payload_that_is_not_text_is_said_to_be_one() {
        let payload: Box<dyn Any + Send> = Box::new(42u8);
        let report = PanicReport::describe(None, None, payload.as_ref());
        assert_eq!(report.message, "(non-text panic payload)");
    }

    #[test]
    fn the_location_is_file_line_and_column() {
        let here = Location::caller();
        let report = PanicReport::describe(None, Some(here), &"boom");
        assert!(
            report
                .location
                .ends_with(&format!(":{}:{}", here.line(), here.column()))
        );
        assert!(
            report.location.contains("panic_policy.rs"),
            "{}",
            report.location
        );
    }
}
