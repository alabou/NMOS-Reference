// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! A minimal plaintext registry, for the M5 conformance run.
//!
//! Deliberately not the real CLI. `nmos_registry.py` carries roughly a hundred
//! flags -- TLS, the TR-10-SEC restrictions, OAuth 2.0, the distributed
//! backend's twenty interlocking validation rules -- and all of that is M6 and
//! M9. What this binary is for is running AMWA IS-04-02 against the Rust
//! registry over plain HTTP, which is the M5 gate, and it accepts exactly the
//! three port numbers that takes.
//!
//! ```text
//! nmos-registry [registration_port [query_port [websocket_port]]]
//! ```
//!
//! Defaults are the Python launcher's: 8447, 8446, 8448.

#![allow(clippy::print_stderr, clippy::print_stdout)]

use nmos_registry::registry::Registry;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_http::serve::{Assembly, Ports, run};

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let defaults = Ports::default();
    let port = |index: usize, fallback: u16| -> u16 {
        args.get(index)
            .and_then(|text| text.parse().ok())
            .unwrap_or(fallback)
    };
    let ports = Ports {
        registration: port(0, defaults.registration),
        query: port(1, defaults.query),
        websocket: port(2, defaults.websocket),
    };

    // Without a subscriber, every `tracing::info!` in the workspace is
    // evaluated and discarded. That matters beyond losing the log:
    // `bench_registry/compare.py --verify-log-volume` rejects a comparison
    // when two targets' log bytes differ by more than an order of magnitude,
    // so a silent registry cannot be benchmarked against the Python one at all.
    //
    // `RUST_LOG` overrides the default, matching the Python launcher's
    // `--logLevel`.
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let assembly = Assembly::new(
        Registry::new(RegistryStore::new()),
        uuid::Uuid::new_v4().to_string(),
    );

    println!(
        "nmos-registry: registration :{} query :{} websocket :{}",
        ports.registration, ports.query, ports.websocket,
    );
    run(&assembly, ports).await
}
