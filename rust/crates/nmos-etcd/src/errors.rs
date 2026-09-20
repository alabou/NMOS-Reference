// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Errors raised by the etcd client, and the mapping from gRPC status codes.
//!
//! The registry has to make three different decisions from an etcd failure,
//! and they are not interchangeable:
//!
//! * **Retry, or fail the request?** A deadline or an unavailable member is
//!   worth retrying against another endpoint; a malformed request is not.
//! * **Answer 503, or answer 500?** Everything here that reaches a handler
//!   becomes a 503 with `Retry-After` -- the registry is temporarily unable to
//!   serve, not broken. An unexpected error is a bug and must not be disguised
//!   as congestion.
//! * **Resnapshot, or resume?** [`EtcdError::Compacted`] is the one failure
//!   that cannot be recovered by resuming a watch, because the history the
//!   watcher needs is gone. It is a distinct variant precisely so the watch
//!   loop cannot accidentally treat it as a transient reconnect and silently
//!   skip a range of revisions.
//!
//! That last one is why this is an enum rather than one error type. The legacy
//! dRDS collapsed these distinctions and its watch loop could not tell a
//! compaction from a dropped connection.
//!
//! # One enum where Python has a class hierarchy
//!
//! Python raises five exception classes with `EtcdError` as their base, and
//! callers catch the base to mean "any etcd failure". A Rust enum gives the
//! same two things -- one type to propagate, and a discriminated variant to
//! match on -- without the open-ended subclassing, which nothing here used.
//! The variant names and the message text match one to one, so a log line from
//! either implementation reads the same.

use std::fmt;

/// A failure from the etcd client.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EtcdError {
    /// A failure with no more specific meaning.
    ///
    /// Python's bare `EtcdError`. Not retryable on its own: it reaches a
    /// handler as a 500, because something unexpected disguised as congestion
    /// is a bug that never gets found.
    Other(String),

    /// No member could serve the request, or the deadline expired.
    ///
    /// Retryable. The caller either tries another endpoint or, once the
    /// overall mutation deadline is spent, answers 503.
    Unavailable(String),

    /// A required revision has been compacted away.
    ///
    /// Raised by a range read at a revision below the compaction point, and
    /// surfaced explicitly from the watch stream. Never retryable at the same
    /// revision: the only recovery is a fresh fixed-revision snapshot.
    Compacted {
        /// What happened, in the words an operator reads.
        message: String,
        /// The revision history was compacted to, when etcd reported it.
        ///
        /// Zero when unknown. The resnapshot path does not depend on the value
        /// -- it takes a fresh linearizable revision regardless -- but it is
        /// the first thing anyone diagnosing a compaction storm wants to see.
        compact_revision: i64,
    },

    /// A lease has expired or been revoked.
    ///
    /// Distinguished from a generic failure because it is *authoritative*: the
    /// Node that owned it is gone as far as the cluster is concerned, so a
    /// heartbeat against it answers 404 and the Node re-registers. Treating it
    /// as transient would keep a dead Node alive in the local view.
    LeaseNotFound(String),

    /// The cluster rejected this client's credentials.
    ///
    /// Not retryable and not a 503 -- the client certificate is wrong,
    /// revoked, or not permitted by `--client-cert-allowed-hostname`. Retrying
    /// cannot fix it, and reporting congestion would hide a misconfiguration
    /// that only ever gets worse.
    PermissionDenied(String),
}

impl EtcdError {
    /// Whether another member could plausibly answer this.
    ///
    /// The failover predicate, and the reason the pool does not simply retry
    /// everything: re-running a rejected request against every member turns
    /// one clear error into N confusing ones and delays the answer by the full
    /// deadline each time.
    #[must_use]
    pub const fn is_retryable(&self) -> bool {
        matches!(self, Self::Unavailable(_))
    }

    /// The message, without the variant.
    #[must_use]
    pub fn message(&self) -> &str {
        match self {
            Self::Other(message)
            | Self::Unavailable(message)
            | Self::Compacted { message, .. }
            | Self::LeaseNotFound(message)
            | Self::PermissionDenied(message) => message,
        }
    }
}

impl fmt::Display for EtcdError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.message())
    }
}

impl std::error::Error for EtcdError {}

/// Shorthand for the one error type in this crate.
pub type Result<T> = std::result::Result<T, EtcdError>;

/// Convert a gRPC status into the class the registry can act on.
///
/// Anything not explicitly listed becomes [`EtcdError::Unavailable`], which is
/// the conservative choice: it is retryable and it degrades to 503 rather than
/// to a wrong answer.
///
/// # Why the message is matched and not only the code
///
/// etcd reports both compaction and a missing lease with a status code that
/// says nothing useful on its own, so the message is the only discriminator.
/// Matched on the documented etcd error strings, exactly as the Python does,
/// with anything unmatched falling through to `Unavailable`.
#[must_use]
pub fn classify(status: &tonic::Status) -> EtcdError {
    let detail = status.message();
    // Python builds `f"{code.name.lower()}: {detail}"` from grpc's own enum
    // name. tonic's `Code` Debug spelling is `PermissionDenied` where
    // Python's is `PERMISSION_DENIED`, so the conversion is spelled out rather
    // than taken from a formatter that happens to be close.
    let text = format!("{}: {detail}", code_name(status.code()));

    if matches!(
        status.code(),
        tonic::Code::PermissionDenied | tonic::Code::Unauthenticated
    ) {
        return EtcdError::PermissionDenied(text);
    }

    if detail.contains("required revision has been compacted") {
        return EtcdError::Compacted {
            message: text,
            // etcd carries the revision on the watch response rather than on
            // a status, so a compaction surfaced this way has none. The watch
            // sets it from `compact_revision` directly.
            compact_revision: 0,
        };
    }
    if detail.contains("requested lease not found") || detail.contains("lease not found") {
        return EtcdError::LeaseNotFound(text);
    }

    EtcdError::Unavailable(text)
}

/// A gRPC code, spelled the way Python's `code.name.lower()` spells it.
///
/// Written out rather than derived from `Debug`, because the two disagree:
/// `Debug` gives `PermissionDenied`, and Python gives `permission_denied`. The
/// text reaches a log an operator compares against a Python member's.
const fn code_name(code: tonic::Code) -> &'static str {
    match code {
        tonic::Code::Ok => "ok",
        tonic::Code::Cancelled => "cancelled",
        tonic::Code::Unknown => "unknown",
        tonic::Code::InvalidArgument => "invalid_argument",
        tonic::Code::DeadlineExceeded => "deadline_exceeded",
        tonic::Code::NotFound => "not_found",
        tonic::Code::AlreadyExists => "already_exists",
        tonic::Code::PermissionDenied => "permission_denied",
        tonic::Code::ResourceExhausted => "resource_exhausted",
        tonic::Code::FailedPrecondition => "failed_precondition",
        tonic::Code::Aborted => "aborted",
        tonic::Code::OutOfRange => "out_of_range",
        tonic::Code::Unimplemented => "unimplemented",
        tonic::Code::Internal => "internal",
        tonic::Code::Unavailable => "unavailable",
        tonic::Code::DataLoss => "data_loss",
        tonic::Code::Unauthenticated => "unauthenticated",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tonic::{Code, Status};

    #[test]
    fn credentials_are_refused_rather_than_retried() {
        // Retrying a rejected certificate against every member turns one clear
        // error into N confusing ones and spends the whole deadline doing it.
        for code in [Code::PermissionDenied, Code::Unauthenticated] {
            let error = classify(&Status::new(code, "bad cert"));
            assert!(matches!(error, EtcdError::PermissionDenied(_)), "{code:?}");
            assert!(!error.is_retryable(), "{code:?}");
        }
    }

    #[test]
    fn a_compaction_is_not_a_dropped_connection() {
        // The distinction the legacy dRDS collapsed: resuming a watch after a
        // compaction skips every revision that was compacted away, and two
        // registries then disagree about state forever.
        let error = classify(&Status::new(
            Code::OutOfRange,
            "etcdserver: mvcc: required revision has been compacted",
        ));
        assert!(matches!(error, EtcdError::Compacted { .. }));
        assert!(!error.is_retryable());
    }

    #[test]
    fn a_missing_lease_is_authoritative() {
        for detail in ["etcdserver: requested lease not found", "lease not found"] {
            let error = classify(&Status::new(Code::NotFound, detail));
            assert!(matches!(error, EtcdError::LeaseNotFound(_)), "{detail}");
        }
    }

    #[test]
    fn anything_unrecognised_degrades_to_retryable() {
        // The conservative fallback: retryable, and a 503 rather than a wrong
        // answer.
        for code in [Code::Unavailable, Code::Internal, Code::Unknown] {
            let error = classify(&Status::new(code, "something"));
            assert!(matches!(error, EtcdError::Unavailable(_)), "{code:?}");
            assert!(error.is_retryable(), "{code:?}");
        }
    }

    #[test]
    fn the_code_is_spelled_the_way_python_spells_it() {
        // `code.name.lower()` in Python. tonic's `Debug` gives
        // `PermissionDenied`, which would make the two implementations
        // describe one failure with two different words in an operator's log.
        assert_eq!(
            classify(&Status::new(Code::PermissionDenied, "x")).message(),
            "permission_denied: x",
        );
        assert_eq!(
            classify(&Status::new(Code::DeadlineExceeded, "x")).message(),
            "deadline_exceeded: x",
        );
        assert_eq!(
            classify(&Status::new(Code::Unavailable, "x")).message(),
            "unavailable: x",
        );
    }

    #[test]
    fn the_message_survives_every_variant() {
        assert_eq!(EtcdError::Other("a".into()).message(), "a");
        assert_eq!(EtcdError::Unavailable("b".into()).message(), "b");
        assert_eq!(
            EtcdError::Compacted {
                message: "c".into(),
                compact_revision: 9,
            }
            .message(),
            "c",
        );
        assert_eq!(EtcdError::LeaseNotFound("d".into()).message(), "d");
        assert_eq!(EtcdError::PermissionDenied("e".into()).message(), "e");
    }
}
