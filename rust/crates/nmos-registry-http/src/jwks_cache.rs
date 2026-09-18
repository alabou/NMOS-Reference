// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The TR-10-SEC §14.3.2 lifecycle for the Authorization Server's signing keys.
//!
//! Port of `nmos/oauth2/jwks_cache.py`. The specification asks for four things,
//! and each is a rule about *time* rather than about HTTP:
//!
//! - an initial fetch at boot, with **fail-closed** behaviour until it succeeds;
//! - refresh every **23 hours plus 0-3600 s of jitter**, so a fleet does not
//!   stampede the Authorization Server on the same second;
//! - **36-hour hard invalidation** -- keys older than that are discarded and
//!   bearer access is refused until a fresh set arrives;
//! - **exponential backoff** between failures, 1 s doubling to a 64 s ceiling.
//!
//! # Why the timing is a separate, synchronous type
//!
//! [`JwksPolicy`] holds every one of those rules and touches no clock, no
//! network and no runtime: it is told what time it is and returns how long to
//! wait. That is what makes the 36-hour invalidation testable at all -- the
//! alternative is a test that sleeps for a day and a half, which is to say a
//! rule that is never tested.
//!
//! [`run`] is the thin part: fetch, hand the outcome to the policy, sleep for
//! whatever it says.
//!
//! Python reaches the same end by injecting `sleep`, `monotonic` and
//! `random_jitter` as constructor arguments. Splitting the type instead means
//! the rules cannot accidentally consult the real clock, because they have no
//! way to reach it.

use std::sync::Arc;
use std::time::Duration;

use crate::oauth2::{Jwks, SharedJwks};

/// Minimum delay between successful fetches, before jitter.
pub const REFRESH_INTERVAL_SECONDS: f64 = 23.0 * 3600.0;

/// Upper bound of the additive jitter on the refresh interval.
pub const REFRESH_JITTER_MAX_SECONDS: f64 = 3600.0;

/// Maximum age of a cached keyset before it is discarded.
///
/// "shall invalidate the Public Keys from a previous fetch / update operation
/// 36 hours after obtaining them."
pub const INVALIDATION_AGE_SECONDS: f64 = 36.0 * 3600.0;

/// Backoff after the first failure.
pub const BACKOFF_INITIAL_SECONDS: f64 = 1.0;

/// Backoff ceiling -- doubling stops here.
pub const BACKOFF_MAX_SECONDS: f64 = 64.0;

/// What a failed fetch means for the keys already held.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OnFailure {
    /// Keep serving the cached keys; they are still inside the window.
    Retain,
    /// Discard them: they are older than [`INVALIDATION_AGE_SECONDS`] and
    /// bearer access must now be refused.
    Invalidate,
}

/// The timing rules, with no clock of their own.
#[derive(Debug, Clone, Default)]
pub struct JwksPolicy {
    last_fetch: Option<f64>,
    consecutive_failures: u32,
}

impl JwksPolicy {
    /// A policy that has never fetched -- the fail-closed starting state.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Failures since the last success.
    #[must_use]
    pub const fn consecutive_failures(&self) -> u32 {
        self.consecutive_failures
    }

    /// When the last successful fetch happened, on the caller's clock.
    #[must_use]
    pub const fn last_fetch(&self) -> Option<f64> {
        self.last_fetch
    }

    /// Record a success and return how long to wait before refreshing.
    ///
    /// `jitter` is supplied rather than drawn here, for the same reason the
    /// clock is: a rule that reaches for randomness cannot be asserted on.
    pub fn on_success(&mut self, now: f64, jitter: f64) -> Duration {
        self.last_fetch = Some(now);
        self.consecutive_failures = 0;
        Duration::from_secs_f64(REFRESH_INTERVAL_SECONDS + jitter)
    }

    /// Record a failure, and say whether the cached keys survive it.
    ///
    /// Returns the verdict and how long to wait before retrying.
    pub fn on_failure(&mut self, now: f64) -> (OnFailure, Duration) {
        self.consecutive_failures = self.consecutive_failures.saturating_add(1);

        let verdict = match self.last_fetch {
            Some(last) if now - last > INVALIDATION_AGE_SECONDS => {
                // Past the window. Drop the keys *and* the timestamp: the cache
                // is back to its initial state, so a later failure must not
                // re-invalidate something already gone.
                self.last_fetch = None;
                OnFailure::Invalidate
            }
            // Either still inside the window, or nothing was ever fetched --
            // and there is nothing to invalidate in the second case, which is
            // already the fail-closed state.
            _ => OnFailure::Retain,
        };
        (verdict, self.backoff())
    }

    /// The current backoff: 1, 2, 4, 8, 16, 32, 64, 64, ...
    ///
    /// Called after the failure has been counted, so one failure means an
    /// exponent of zero and a one-second wait.
    #[must_use]
    pub fn backoff(&self) -> Duration {
        let exponent = self.consecutive_failures.saturating_sub(1);
        // `exp2` rather than repeated doubling so a long outage cannot overflow
        // -- it saturates at infinity, and the `min` below takes the ceiling.
        let candidate = BACKOFF_INITIAL_SECONDS * f64::from(exponent).exp2();
        Duration::from_secs_f64(candidate.min(BACKOFF_MAX_SECONDS))
    }
}

/// Everything [`run`] needs from the outside world.
///
/// A trait rather than a closure because the fetch is `async` and has to be
/// callable repeatedly; an `async` closure that borrows its environment across
/// a loop is considerably more awkward to express than this.
pub trait FetchJwks: Send + Sync {
    /// Fetch the keyset, or explain why not.
    fn fetch(&self) -> impl std::future::Future<Output = Result<Jwks, String>> + Send;
}

/// Keep `keys` current, for ever.
///
/// Returns only if the task is cancelled, which for a registry is at shutdown.
///
/// `jitter` is called once per successful fetch and should return a value in
/// `[0, REFRESH_JITTER_MAX_SECONDS]`.
pub async fn run<F, J>(fetcher: F, keys: SharedJwks, mut jitter: J)
where
    F: FetchJwks,
    J: FnMut() -> f64 + Send,
{
    let start = std::time::Instant::now();
    let elapsed = || start.elapsed().as_secs_f64();
    let mut policy = JwksPolicy::new();

    loop {
        let delay = match fetcher.fetch().await {
            Ok(fetched) => {
                let count = fetched.keys.len();
                keys.set(Some(Arc::new(fetched)));
                let delay = policy.on_success(elapsed(), jitter());
                tracing::info!("JWKS: fetched {count} public key(s)");
                delay
            }
            Err(error) => {
                let (verdict, delay) = policy.on_failure(elapsed());
                tracing::warn!(
                    "JWKS: fetch failed (attempt {}): {error}",
                    policy.consecutive_failures(),
                );
                if verdict == OnFailure::Invalidate {
                    tracing::warn!(
                        "JWKS: invalidating keys after more than {INVALIDATION_AGE_SECONDS:.0}s \
                         without a refresh \u{2014} bearer access is now refused",
                    );
                    keys.set(None);
                }
                delay
            }
        };
        tokio::time::sleep(delay).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nothing_is_trusted_before_the_first_fetch() {
        // The fail-closed start. A cache that began by serving an empty keyset
        // would be indistinguishable to the gate from a fetched one.
        let keys = SharedJwks::empty();
        assert!(keys.get().is_none());
        let policy = JwksPolicy::new();
        assert_eq!(policy.last_fetch(), None);
        assert_eq!(policy.consecutive_failures(), 0);
    }

    #[test]
    fn the_refresh_delay_is_twenty_three_hours_plus_the_jitter() {
        let mut policy = JwksPolicy::new();
        assert_eq!(policy.on_success(0.0, 0.0).as_secs_f64(), 23.0 * 3600.0,);
        assert_eq!(policy.on_success(0.0, 3600.0).as_secs_f64(), 24.0 * 3600.0,);
    }

    #[test]
    fn the_backoff_doubles_to_a_ceiling_of_sixty_four_seconds() {
        // IS-10: "SHOULD use an exponential backoff, from 1 to 64 seconds."
        let mut policy = JwksPolicy::new();
        let mut seen = Vec::new();
        for _ in 0..10 {
            let (_, delay) = policy.on_failure(0.0);
            seen.push(delay.as_secs_f64());
        }
        assert_eq!(
            seen,
            [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 64.0, 64.0, 64.0],
        );
    }

    #[test]
    fn a_long_outage_cannot_overflow_the_backoff() {
        // Doubling by multiplication would overflow long before this; the
        // ceiling has to hold however many failures accumulate.
        let mut policy = JwksPolicy::new();
        for _ in 0..5_000 {
            let _ = policy.on_failure(0.0);
        }
        assert_eq!(policy.backoff().as_secs_f64(), 64.0);
    }

    #[test]
    fn a_success_resets_the_backoff() {
        let mut policy = JwksPolicy::new();
        for _ in 0..5 {
            let _ = policy.on_failure(0.0);
        }
        assert_eq!(policy.consecutive_failures(), 5);
        policy.on_success(0.0, 0.0);
        assert_eq!(policy.consecutive_failures(), 0);
        let (_, delay) = policy.on_failure(0.0);
        assert_eq!(delay.as_secs_f64(), 1.0, "backoff did not restart at 1s");
    }

    #[test]
    fn keys_survive_failures_inside_the_thirty_six_hour_window() {
        // The point of the window: a flaky Authorization Server must not take
        // the registry's bearer validation down with it.
        let mut policy = JwksPolicy::new();
        policy.on_success(0.0, 0.0);
        for hours in [1.0, 12.0, 23.0, 35.9] {
            let (verdict, _) = policy.on_failure(hours * 3600.0);
            assert_eq!(
                verdict,
                OnFailure::Retain,
                "keys were dropped after only {hours}h",
            );
        }
    }

    #[test]
    fn keys_are_invalidated_once_past_thirty_six_hours() {
        let mut policy = JwksPolicy::new();
        policy.on_success(0.0, 0.0);
        let (verdict, _) = policy.on_failure(36.0 * 3600.0 + 1.0);
        assert_eq!(verdict, OnFailure::Invalidate);
    }

    #[test]
    fn exactly_thirty_six_hours_is_not_yet_stale() {
        // The comparison is strictly greater, as Python's is. Worth pinning:
        // flipping it to `>=` would be invisible except exactly on the boundary.
        let mut policy = JwksPolicy::new();
        policy.on_success(0.0, 0.0);
        let (verdict, _) = policy.on_failure(36.0 * 3600.0);
        assert_eq!(verdict, OnFailure::Retain);
    }

    #[test]
    fn invalidation_happens_once_and_does_not_repeat() {
        // After invalidating, the cache is back to its initial state. A second
        // failure must report `Retain` -- not because anything is retained, but
        // because there is nothing left to invalidate, and re-reporting it
        // would log a fresh alarm every backoff interval for ever.
        let mut policy = JwksPolicy::new();
        policy.on_success(0.0, 0.0);
        let (first, _) = policy.on_failure(40.0 * 3600.0);
        assert_eq!(first, OnFailure::Invalidate);
        let (second, _) = policy.on_failure(41.0 * 3600.0);
        assert_eq!(second, OnFailure::Retain);
        assert_eq!(policy.last_fetch(), None);
    }

    #[test]
    fn a_failure_before_any_success_invalidates_nothing() {
        // There is nothing to drop, and the state is already fail-closed.
        let mut policy = JwksPolicy::new();
        let (verdict, _) = policy.on_failure(1_000_000.0);
        assert_eq!(verdict, OnFailure::Retain);
    }

    #[test]
    fn the_shared_handle_publishes_to_readers_that_already_cloned_it() {
        // The property the whole `SharedJwks` design exists for: a reader that
        // took its copy before the first fetch must see the keys afterwards.
        // With a plain value it would hold `None` for ever.
        let keys = SharedJwks::empty();
        let reader = keys.clone();
        assert!(reader.get().is_none());

        keys.set(Some(Arc::new(Jwks {
            keys: vec![Default::default()],
        })));
        assert_eq!(
            reader.get().map(|k| k.keys.len()),
            Some(1),
            "an existing clone did not observe the fetched keys",
        );

        keys.set(None);
        assert!(
            reader.get().is_none(),
            "an existing clone did not observe the invalidation",
        );
    }
}
