// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! A seeded generator, so every choice a run makes is a function of its seed.
//!
//! Hand-written rather than a dependency: the crate links no `rand`, and the
//! soak needs nothing a forty-line generator does not give. `xoshiro256**`,
//! seeded through `SplitMix64` as its authors recommend, is fast, has no bad
//! low bits, and -- the property that matters here -- produces the same stream
//! on every platform for the same seed.
//!
//! What a seed does **not** control is the node's own election jitter, which
//! the implementation draws from OpenSSL (`node.rs::election_timeout`). A seed
//! therefore biases a run rather than replaying it, exactly as the Python soak
//! documents for itself, and a failing run has to explain itself through its
//! trace rather than be re-run.

/// A `xoshiro256**` stream.
#[derive(Debug, Clone)]
pub struct Rng {
    state: [u64; 4],
}

impl Rng {
    /// A stream fully determined by `seed`.
    #[must_use]
    pub fn new(seed: u64) -> Self {
        let mut mixer = seed;
        let mut next = || {
            mixer = mixer.wrapping_add(0x9E37_79B9_7F4A_7C15);
            let mut z = mixer;
            z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
            z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
            z ^ (z >> 31)
        };
        Self {
            state: [next(), next(), next(), next()],
        }
    }

    /// An independent stream derived from this one.
    ///
    /// Used so that, for example, the network's delays do not shift every
    /// later driver decision when one more message happens to be sent.
    #[must_use]
    pub fn fork(&mut self) -> Self {
        Self::new(self.next_u64())
    }

    /// The next 64 bits.
    pub fn next_u64(&mut self) -> u64 {
        let result = self.state[1].wrapping_mul(5).rotate_left(7).wrapping_mul(9);
        let t = self.state[1] << 17;
        self.state[2] ^= self.state[0];
        self.state[3] ^= self.state[1];
        self.state[1] ^= self.state[2];
        self.state[0] ^= self.state[3];
        self.state[2] ^= t;
        self.state[3] = self.state[3].rotate_left(45);
        result
    }

    /// Uniform in `0..n`, or 0 when `n` is 0.
    ///
    /// Lemire's multiply-shift. Its bias is below 2^-32 for every `n` the soak
    /// uses, which is irrelevant to a fault injector.
    pub fn below(&mut self, n: u64) -> u64 {
        if n == 0 {
            return 0;
        }
        ((u128::from(self.next_u64()) * u128::from(n)) >> 64) as u64
    }

    /// Uniform in `low..=high`.
    pub fn range(&mut self, low: u64, high: u64) -> u64 {
        if high <= low {
            return low;
        }
        low + self.below(high - low + 1)
    }

    /// True with probability `numerator / denominator`.
    pub fn chance(&mut self, numerator: u64, denominator: u64) -> bool {
        self.below(denominator) < numerator
    }

    /// One element, uniformly, or `None` from an empty slice.
    pub fn pick<'a, T>(&mut self, items: &'a [T]) -> Option<&'a T> {
        if items.is_empty() {
            return None;
        }
        let at = self.below(items.len() as u64) as usize;
        items.get(at)
    }

    /// One key from a weight table. Zero-weight entries are never chosen.
    ///
    /// # Panics
    ///
    /// If every weight is zero -- a table that can choose nothing is a bug in
    /// the table, not a situation to paper over with a default.
    pub fn weighted<T: Copy>(&mut self, table: &[(T, u64)]) -> T {
        let total: u64 = table.iter().map(|&(_, weight)| weight).sum();
        assert!(total > 0, "a weight table with no positive weight");
        let mut roll = self.below(total);
        for &(item, weight) in table {
            if roll < weight {
                return item;
            }
            roll -= weight;
        }
        unreachable!("the roll is below the total by construction")
    }

    /// Fisher-Yates, in place.
    pub fn shuffle<T>(&mut self, items: &mut [T]) {
        for i in (1..items.len()).rev() {
            let j = self.below(i as u64 + 1) as usize;
            items.swap(i, j);
        }
    }

    /// A version-4 UUID in canonical text form.
    ///
    /// From the stream rather than `uuid::Uuid::new_v4`, so the ids a run
    /// registers are part of what its seed determines -- a failure report that
    /// names `3f2a...` names the same resource the next time the seed runs.
    pub fn uuid(&mut self) -> String {
        let high = self.next_u64();
        let low = self.next_u64();
        // Version 4 in the high nibble of the seventh byte, RFC 4122 variant in
        // the top two bits of the ninth.
        let high = (high & 0xFFFF_FFFF_FFFF_0FFF) | 0x0000_0000_0000_4000;
        let low = (low & 0x3FFF_FFFF_FFFF_FFFF) | 0x8000_0000_0000_0000;
        format!(
            "{:08x}-{:04x}-{:04x}-{:04x}-{:012x}",
            high >> 32,
            (high >> 16) & 0xFFFF,
            high & 0xFFFF,
            low >> 48,
            low & 0xFFFF_FFFF_FFFF,
        )
    }
}
