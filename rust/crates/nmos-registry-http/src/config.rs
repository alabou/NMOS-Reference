// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Process-wide switches, of which there is currently one.
//!
//! Port of `nmos/config.py`'s `ALLOW_NON_TLS_FOR_TESTING`.
//!
//! # Why a process global rather than per-listener configuration
//!
//! Because that is what it is in Python, and the two implementations have to
//! behave identically. It is deliberately **not** an `InterfaceSecurity` field:
//! a per-interface flag could be set in a deployment and would then look like
//! an ordinary configuration option, whereas a process global that defaults to
//! off and is only ever flipped by a test reads as what it is.
//!
//! # What it relaxes, and what it does not
//!
//! Only the "is this connection TLS at all" question. A TLS connection whose
//! peer presented no certificate is refused whether or not this is set --
//! `middleware.py:288-293` returns the flag for a missing transport and for a
//! missing `ssl_object`, and then falls through to `peercert is not None`.
//!
//! So this cannot turn a real mTLS deployment permissive. It exists for
//! in-process tests that cannot run a TLS handshake, which is the same reason
//! Python has it.

use std::sync::atomic::{AtomicBool, Ordering};

static ALLOW_NON_TLS_FOR_TESTING: AtomicBool = AtomicBool::new(false);

/// Whether a non-TLS connection may stand in for an authenticated one.
///
/// **False in production, and that is the whole point.** TLS is mandatory for
/// the security modes that consult this; the flag is the only bypass and it
/// exists for tests.
#[must_use]
pub fn allow_non_tls_for_testing() -> bool {
    ALLOW_NON_TLS_FOR_TESTING.load(Ordering::Relaxed)
}

/// Set the flag for as long as the guard lives, restoring the prior value.
///
/// The counterpart to Python's `allow_non_tls_for_testing_context`, and for the
/// same reason: a test that flipped the flag and left it set would relax every
/// test that ran afterwards, and the failure would appear somewhere else
/// entirely.
#[derive(Debug)]
pub struct AllowNonTlsForTesting {
    prior: bool,
}

impl AllowNonTlsForTesting {
    /// Turn the flag on until this value is dropped.
    #[must_use]
    pub fn enable() -> Self {
        Self {
            prior: ALLOW_NON_TLS_FOR_TESTING.swap(true, Ordering::Relaxed),
        }
    }
}

impl Drop for AllowNonTlsForTesting {
    fn drop(&mut self) {
        ALLOW_NON_TLS_FOR_TESTING.store(self.prior, Ordering::Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn it_is_off_unless_a_test_turns_it_on() {
        // The production default. A registry that shipped with this on would
        // accept unauthenticated state changes over plain HTTP.
        assert!(!allow_non_tls_for_testing());
    }

    #[test]
    fn the_guard_restores_the_prior_value() {
        let outer = allow_non_tls_for_testing();
        {
            let _guard = AllowNonTlsForTesting::enable();
            assert!(allow_non_tls_for_testing());
        }
        assert_eq!(
            allow_non_tls_for_testing(),
            outer,
            "the flag leaked out of its scope",
        );
    }

    #[test]
    fn nesting_restores_correctly() {
        let _outer = AllowNonTlsForTesting::enable();
        {
            let _inner = AllowNonTlsForTesting::enable();
            assert!(allow_non_tls_for_testing());
        }
        assert!(
            allow_non_tls_for_testing(),
            "an inner guard turned the flag off while an outer one held it",
        );
    }
}
