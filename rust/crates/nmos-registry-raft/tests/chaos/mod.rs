// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Seeded, randomised churn against the Rust consensus backend.
//!
//! The Rust counterpart of `nmos/raft/tests/test_chaos_soak.py` and its
//! helpers. Layout, one concern per file:
//!
//! * [`rng`] -- the seeded stream every choice is drawn from;
//! * [`net`] -- an in-process network with the production transport's
//!   connection semantics, delays, stalls and correlated requests;
//! * [`cluster`] -- members built as production builds them, and real
//!   restarts;
//! * [`workload`] -- what a client does, at consensus and at registry level;
//! * [`monitor`] -- the properties, checked after every step, and the
//!   client-facing ledger checked at the end;
//! * [`audit`] -- every leader commit decision, checked against Figure 2's
//!   rule at the instant it is made;
//! * [`capture`] -- the node's own log lines and panics, turned into oracles;
//! * [`forensics`] -- the bounded record a failure is explained from;
//! * [`driver`] -- one run: the event mix, the fault budget, convergence.

pub mod audit;
pub mod capture;
pub mod cluster;
pub mod driver;
pub mod forensics;
pub mod monitor;
pub mod net;
pub mod rng;
pub mod workload;
