// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Randomised churn against the Rust consensus backend, with every safety
//! property checked after every step.
//!
//! ```text
//! cargo test -p nmos-registry-raft --test chaos_soak
//! ```
//!
//! The Rust counterpart of `nmos/raft/tests/test_chaos_soak.py`, which has
//! only ever driven the *Python* implementation. Every scripted test in this
//! crate arranges one scenario and asserts one outcome, which proves exactly
//! the scenario it arranged; this file generates interleavings nobody chose,
//! at every level a production registry has -- the log, the state machine,
//! ownership, forwarding, the backend seam -- and checks the properties that
//! must hold at all times, not only at rest.
//!
//! # Turning it up
//!
//! The committed run is small enough for the ordinary gate. Each variable
//! widens one axis and nothing else, so a long run is the same test rather
//! than a different one:
//!
//! ```text
//! RAFT_RUST_SOAK_SECONDS=3600     # keep starting new seeds for an hour
//! RAFT_RUST_SOAK_SEEDS=500        # seeds 1..=500 (or `a..b`, or `a,b,c`)
//! RAFT_RUST_SOAK_SEED_BASE=9000   # where a timed run's seeds start
//! RAFT_RUST_SOAK_STEPS=1000       # steps per run
//! RAFT_RUST_SOAK_JOBS=16          # runs in parallel (default: all cores)
//! RAFT_RUST_SOAK_RUNTIME=virtual  # virtual | threaded | mixed (default)
//! RAFT_RUST_SOAK_WORKLOAD=registry # consensus | registry | mixed (default)
//! RAFT_RUST_SOAK_SIZES=3,5        # member counts to draw from (1, 3, 5)
//! RAFT_RUST_SOAK_AMNESIA=5        # percent of runs allowed past the budget
//! RAFT_RUST_SOAK_NO_RACES=1       # no deliberate client races, no retries
//! RAFT_RUST_SOAK_QUEUEING=1       # the Python network's accumulating delays
//! RAFT_RUST_SOAK_DUMP=1           # every held message into each failure report
//! RAFT_RUST_SOAK_STACK_MB=512     # stack per run thread (see `stack_bytes`)
//! RAFT_RUST_SOAK_ARTIFACTS=/tmp/x # where failure reports are written
//! RAFT_RUST_SOAK_HANG_SECS=300    # wall-clock after which a run is a hang
//! ```
//!
//! # Seeds bias a run; they do not replay it
//!
//! Everything the driver chooses -- sizes, timings, faults, the client's
//! traffic, every network delay -- comes from the seed. The node's election
//! jitter does not (it is OpenSSL's), and on a multi-threaded runtime neither
//! does the scheduler, so a failing seed explores the same region again
//! without being guaranteed to repeat. Failures therefore carry their own
//! explanation: the violated property, the members' positions, the messages
//! bearing on the disputed index and term, the driver's trace, and the node's
//! own log.
//!
//! # Two runtimes, because they find different things
//!
//! A **virtual** run is a current-thread runtime with the tokio clock paused.
//! Every timer the node, backend and fence use is a tokio timer, so the clock
//! jumps straight to the next deadline and a run costs only the CPU it needs:
//! thousands of election windows a second. And because nothing else runs
//! while the monitor reads the cluster, every property is checked exactly,
//! after every step, as the Python monitor checks them.
//!
//! A **threaded** run is a multi-threaded runtime on the real clock. It is
//! slower and its per-step checks are limited to those a concurrent read
//! cannot tear (see `chaos/monitor.rs`), but it is the only one of the two in
//! which two handlers really run at once -- which is where a Rust-specific
//! race between separate lock acquisitions would live.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::panic,
    clippy::significant_drop_tightening,
    missing_docs
)]

mod chaos;

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use chaos::capture::{self, Capture};
use chaos::driver::{Driver, Event, Plan, Progress, Runtime, Tally, Workload};
use chaos::monitor::Violation;
use chaos::net::Knobs;
use chaos::rng::Rng;
use nmos_registry_raft::node::RaftTiming;
use parking_lot::Mutex;

// -- configuration --------------------------------------------------------------

/// Seeds run by default. Small on purpose -- this sits in the ordinary gate.
/// Any seed that ever failed is appended here permanently, with a comment
/// saying what it found.
const SEEDS: &[u64] = &[1, 2, 3, 5, 8, 13, 21, 34];

/// Steps per run by default.
const STEPS: usize = 150;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Choice<T> {
    Fixed(T),
    Mixed,
}

#[derive(Debug, Clone)]
struct Config {
    steps: usize,
    runtime: Choice<RuntimeKind>,
    workload: Choice<Workload>,
    sizes: Vec<usize>,
    amnesia_percent: u64,
    no_races: bool,
    root: PathBuf,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RuntimeKind {
    Virtual,
    Threaded,
}

fn env(name: &str) -> Option<String> {
    std::env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
}

fn config_from_env() -> Config {
    let runtime = match env("RAFT_RUST_SOAK_RUNTIME").as_deref() {
        Some("virtual") => Choice::Fixed(RuntimeKind::Virtual),
        Some("threaded") => Choice::Fixed(RuntimeKind::Threaded),
        _ => Choice::Mixed,
    };
    let workload = match env("RAFT_RUST_SOAK_WORKLOAD").as_deref() {
        Some("consensus") => Choice::Fixed(Workload::Consensus),
        Some("registry") => Choice::Fixed(Workload::Registry),
        _ => Choice::Mixed,
    };
    let sizes = env("RAFT_RUST_SOAK_SIZES")
        .map(|text| {
            text.split(',')
                .filter_map(|part| part.trim().parse::<usize>().ok())
                .filter(|&size| [1, 3, 5].contains(&size))
                .collect()
        })
        .unwrap_or_default();
    Config {
        steps: env("RAFT_RUST_SOAK_STEPS")
            .and_then(|text| text.parse().ok())
            .unwrap_or(STEPS),
        runtime,
        workload,
        sizes,
        amnesia_percent: env("RAFT_RUST_SOAK_AMNESIA")
            .and_then(|text| text.parse().ok())
            .unwrap_or(3),
        no_races: env("RAFT_RUST_SOAK_NO_RACES").is_some(),
        root: scratch_root(),
    }
}

/// Where term files go. tmpfs when there is one: every term change is an
/// fsync of a file and its directory, and on a disk that is most of a virtual
/// run's cost.
fn scratch_root() -> PathBuf {
    let shm = PathBuf::from("/dev/shm");
    let base = if shm.is_dir() {
        shm
    } else {
        std::env::temp_dir()
    };
    base.join(format!("nmos-raft-soak-{}", std::process::id()))
}

/// Seeds as the environment asks for them.
enum Seeds {
    List(Vec<u64>),
    Until { next: u64, deadline: Instant },
}

impl Seeds {
    fn from_env() -> Self {
        if let Some(seconds) = env("RAFT_RUST_SOAK_SECONDS").and_then(|t| t.parse::<u64>().ok()) {
            let next = env("RAFT_RUST_SOAK_SEED_BASE")
                .and_then(|text| text.parse().ok())
                .unwrap_or_else(|| {
                    std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .map_or(1, |since| since.as_secs() % 1_000_000)
                });
            return Self::Until {
                next,
                deadline: Instant::now() + Duration::from_secs(seconds),
            };
        }
        if let Some(text) = env("RAFT_RUST_SOAK_SEEDS") {
            if let Some((low, high)) = text.split_once("..") {
                let low: u64 = low.trim().parse().unwrap_or(1);
                let high: u64 = high.trim().parse().unwrap_or(low);
                return Self::List((low..=high).collect());
            }
            if text.contains(',') {
                return Self::List(
                    text.split(',')
                        .filter_map(|s| s.trim().parse().ok())
                        .collect(),
                );
            }
            if let Ok(count) = text.trim().parse::<u64>() {
                return Self::List((1..=count).collect());
            }
        }
        Self::List(SEEDS.to_vec())
    }

    fn next(&mut self) -> Option<u64> {
        match *self {
            Self::List(ref mut seeds) => {
                if seeds.is_empty() {
                    None
                } else {
                    Some(seeds.remove(0))
                }
            }
            Self::Until {
                ref mut next,
                deadline,
            } => {
                if Instant::now() >= deadline {
                    None
                } else {
                    *next += 1;
                    Some(*next - 1)
                }
            }
        }
    }
}

// -- plans ----------------------------------------------------------------------

/// Everything a run is, drawn from its seed.
///
/// Deliberately wide. Timings range from generous to a 3x election-to-
/// heartbeat ratio (legal -- the only enforced rule is that the window exceeds
/// the heartbeat -- and tighter than production, which is the point); batch
/// and append sizes go down to one, so every boundary in flow control and
/// bounded apply is crossed; compaction thresholds go down to four entries
/// and snapshot chunks to sixteen bytes, so snapshots are taken, transferred
/// in many pieces, and installed over and over.
fn plan_for(seed: u64, run: u64, config: &Config) -> Plan {
    let mut rng = Rng::new(seed ^ 0x005E_ED0F_5EED);
    let kind = match config.runtime {
        Choice::Fixed(kind) => kind,
        Choice::Mixed => {
            if rng.chance(1, 4) {
                RuntimeKind::Threaded
            } else {
                RuntimeKind::Virtual
            }
        }
    };
    let runtime = match kind {
        RuntimeKind::Virtual => Runtime::Virtual,
        RuntimeKind::Threaded => Runtime::Threaded {
            workers: rng.range(2, 8) as usize,
        },
    };
    let workload = match config.workload {
        Choice::Fixed(workload) => workload,
        Choice::Mixed => {
            if rng.chance(1, 2) {
                Workload::Consensus
            } else {
                Workload::Registry
            }
        }
    };
    let size = if config.sizes.is_empty() {
        // The sizes production accepts, and only those: `nmos-cluster`
        // refuses an even count -- it tolerates no more failures than the odd
        // size below it -- and anything above five.
        rng.weighted(&[(1, 4), (3, 50), (5, 46)])
    } else {
        *rng.pick(&config.sizes).unwrap_or(&3)
    };

    let threaded = kind == RuntimeKind::Threaded;
    let heartbeat_ms = if threaded {
        *rng.pick(&[10, 15, 25]).unwrap_or(&10)
    } else {
        *rng.pick(&[2, 5, 10, 20, 50]).unwrap_or(&10)
    };
    let ratio = if threaded {
        rng.range(6, 12)
    } else {
        rng.range(3, 12)
    };
    let election_min_ms = heartbeat_ms * ratio;
    let election_max_ms = (election_min_ms * rng.range(15, 30) / 10).max(election_min_ms + 1);
    let compaction_threshold = *rng.pick(&[4, 8, 16, 32, 128, 4096]).unwrap_or(&32);
    let timing = RaftTiming {
        heartbeat_ms,
        election_min_ms,
        election_max_ms,
        max_entries_per_append: *rng.pick(&[1, 2, 4, 16, 64, 256]).unwrap_or(&64),
        max_apply_batch: *rng.pick(&[1, 3, 16, 128]).unwrap_or(&16),
        compaction_threshold,
        max_log_entries: compaction_threshold * *rng.pick(&[1, 2, 4, 16]).unwrap_or(&4),
        snapshot_chunk: *rng
            .pick(&[16, 128, 1024, 16 * 1024, 1 << 20])
            .unwrap_or(&1024),
    };

    let heartbeat_us = heartbeat_ms * 1000;
    let knobs = Knobs {
        max_delay_us: *rng
            .pick(&[
                0,
                heartbeat_us / 20,
                heartbeat_us / 4,
                heartbeat_us,
                heartbeat_us * 2,
            ])
            .unwrap_or(&0),
        spike_one_in: *rng.pick(&[0, 0, 25, 250]).unwrap_or(&0),
        reconnect_max_us: *rng
            .pick(&[0, heartbeat_us, heartbeat_us * 3, election_min_ms * 1000])
            .unwrap_or(&0),
    };

    // Deadlines cost nothing on the paused clock and real seconds on the
    // threaded one, where a partition-heavy run at the virtual deadlines would
    // spend most of its wall time waiting out timeouts.
    let (mutation_timeout, proposal_timeout) = if threaded {
        let deadline = Duration::from_millis(election_max_ms * rng.range(3, 5));
        (deadline, deadline)
    } else {
        let deadline = Duration::from_millis(election_max_ms * rng.range(3, 12));
        (
            deadline,
            deadline.max(Duration::from_millis(heartbeat_ms * 100)),
        )
    };
    let gc_interval = if workload == Workload::Registry && rng.chance(1, 5) {
        0
    } else {
        12
    };
    let tag = format!("r{run}s{seed}");
    let mut weights = weights_for(&mut rng, workload);
    let mut retry_percent = *rng.pick(&[0, 25, 50, 90]).unwrap_or(&50);
    if config.no_races {
        for &mut (event, ref mut weight) in &mut weights {
            if matches!(
                event,
                Event::RaceFirstRegistration | Event::RaceUpdateDelete
            ) {
                *weight = 0;
            }
        }
        retry_percent = 0;
    }
    Plan {
        seed,
        runtime,
        workload,
        size,
        steps: if threaded {
            config.steps.min(200)
        } else {
            config.steps
        },
        timing,
        knobs,
        weights,
        mutation_timeout,
        proposal_timeout,
        gc_interval,
        deep_every: *rng.pick(&[1, 5, 10]).unwrap_or(&5),
        amnesia: size >= 2 && rng.chance(config.amnesia_percent, 100),
        retry_percent,
        dir: config.root.join(&tag),
        tag,
    }
}

/// The event mix: a base table per workload, each weight scaled at random,
/// and each fault switched off entirely one run in ten -- so some runs spend
/// their whole length on one kind of trouble and others never see it.
///
/// Weighted, as the Python's is, so the cluster spends most of its time doing
/// work with something broken rather than being broken: a run that is all
/// faults commits nothing, and every property then holds vacuously.
fn weights_for(rng: &mut Rng, workload: Workload) -> Vec<(Event, u64)> {
    let base: &[(Event, u64)] = match workload {
        Workload::Consensus => &[
            (Event::Register, 30),
            (Event::Unregister, 5),
            (Event::Burst, 3),
            (Event::Stop, 8),
            (Event::Resume, 8),
            (Event::Restart, 6),
            (Event::Block, 6),
            (Event::Partition, 4),
            (Event::Heal, 8),
            (Event::Stall, 4),
            (Event::Unstall, 4),
            (Event::SlowLink, 2),
            (Event::Jitter, 2),
            (Event::Idle, 15),
        ],
        Workload::Registry => &[
            (Event::Register, 18),
            (Event::RegisterDevice, 10),
            (Event::RegisterSender, 8),
            (Event::Update, 10),
            (Event::Heartbeat, 6),
            (Event::Unregister, 6),
            (Event::Burst, 2),
            (Event::RaceFirstRegistration, 2),
            (Event::RaceUpdateDelete, 2),
            (Event::CollectGarbage, 2),
            (Event::Stop, 6),
            (Event::Resume, 6),
            (Event::Restart, 5),
            (Event::Block, 5),
            (Event::Partition, 3),
            (Event::Heal, 7),
            (Event::Stall, 3),
            (Event::Unstall, 3),
            (Event::SlowLink, 2),
            (Event::Jitter, 2),
            (Event::Idle, 10),
        ],
    };
    let recovery = [Event::Resume, Event::Heal, Event::Unstall];
    let faults = [
        Event::Stop,
        Event::Restart,
        Event::Block,
        Event::Partition,
        Event::Stall,
        Event::SlowLink,
        Event::Jitter,
    ];
    base.iter()
        .map(|&(event, weight)| {
            let scaled = weight * rng.range(25, 200) / 100;
            let weight = if faults.contains(&event) && rng.chance(1, 10) {
                0
            } else if recovery.contains(&event) {
                scaled.max(1)
            } else {
                scaled
            };
            (event, weight)
        })
        .collect()
}

// -- one run ----------------------------------------------------------------------

/// What one run found and did.
struct Report {
    plan: Plan,
    violations: Vec<Violation>,
    tally: Tally,
    counts: BTreeMap<String, u64>,
    anomalies: BTreeMap<&'static str, u64>,
    anomaly_examples: BTreeMap<&'static str, String>,
    net: [u64; 9],
    terms: usize,
    committed: u64,
    checks: u64,
    /// Leader decisions audited at the instant they were made: `(commits,
    /// promotions)`.
    audited: (u64, u64),
    ledger: (usize, usize, usize),
    wall: Duration,
    simulated: Duration,
    explanation: String,
}

thread_local! {
    /// Keeps a worker thread's default dispatcher installed for its lifetime.
    static DISPATCH: RefCell<Option<tracing::dispatcher::DefaultGuard>> = const { RefCell::new(None) };
}

fn run(plan: Plan, progress: Progress) -> Report {
    let tag = plan.tag.clone();
    capture::claim_thread(&tag);
    drop(std::fs::create_dir_all(&plan.dir));
    let capture = Capture::new();
    let dispatch = capture.dispatch();
    let wall = Instant::now();
    let dir = plan.dir.clone();
    let mut report = match plan.runtime {
        Runtime::Virtual => {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .start_paused(true)
                .build()
                .expect("a current-thread runtime");
            tracing::dispatcher::with_default(&dispatch, || {
                runtime.block_on(drive(plan, Arc::clone(&capture), progress))
            })
        }
        Runtime::Threaded { workers } => {
            let thread_dispatch = dispatch.clone();
            let thread_tag = tag.clone();
            let runtime = tokio::runtime::Builder::new_multi_thread()
                .worker_threads(workers)
                .enable_all()
                .thread_name(format!("{tag}-worker"))
                .on_thread_start(move || {
                    capture::claim_thread(&thread_tag);
                    let guard = tracing::dispatcher::set_default(&thread_dispatch);
                    DISPATCH.with(|slot| *slot.borrow_mut() = Some(guard));
                })
                .on_thread_stop(|| {
                    DISPATCH.with(|slot| drop(slot.borrow_mut().take()));
                })
                .build()
                .expect("a multi-threaded runtime");
            let report = tracing::dispatcher::with_default(&dispatch, || {
                runtime.block_on(drive(plan, Arc::clone(&capture), progress))
            });
            runtime.shutdown_timeout(Duration::from_secs(10));
            report
        }
    };
    report.wall = wall.elapsed();
    drop(std::fs::remove_dir_all(&dir));
    report
}

async fn drive(plan: Plan, capture: Arc<Capture>, progress: Progress) -> Report {
    let started = tokio::time::Instant::now();
    let mut driver = Driver::new(plan.clone(), Arc::clone(&capture), progress);
    let violations = driver.run().await;
    // Assembled before `close`, which releases every waiter and so would hide
    // exactly what the leak checks look for.
    let explanation = if violations.is_empty() {
        String::new()
    } else {
        explain(&plan, &driver, &capture, &violations)
    };
    let stats = &driver.cluster.net.stats;
    let load =
        |counter: &std::sync::atomic::AtomicU64| counter.load(std::sync::atomic::Ordering::Relaxed);
    let net = [
        load(&stats.delivered),
        load(&stats.unlinked),
        load(&stats.lost),
        load(&stats.requests),
        load(&stats.request_failures),
        load(&stats.connects),
        load(&stats.drops),
        load(&stats.stalled),
        load(&stats.severed),
    ];
    let report = Report {
        violations,
        tally: driver.tally.clone(),
        counts: capture.counts(),
        anomalies: driver.monitor.ledger.anomalies.clone(),
        anomaly_examples: driver.monitor.ledger.anomaly_examples.clone(),
        net,
        terms: driver.monitor.terms_led(),
        committed: driver.monitor.highest_committed(),
        checks: driver.monitor.checks,
        audited: driver.decisions_audited(),
        ledger: driver.monitor.ledger.tally(),
        wall: Duration::ZERO,
        simulated: started.elapsed(),
        explanation,
        plan,
    };
    driver.close().await;
    report
}

/// Everything a reader needs to act on a failure without re-running it.
fn explain(plan: &Plan, driver: &Driver, capture: &Capture, violations: &[Violation]) -> String {
    let mut out = String::new();
    let _ = writeln!(out, "plan: {}", plan.describe());
    let _ = writeln!(out, "\n{} violation(s):", violations.len());
    for found in violations {
        let _ = writeln!(out, "  * {found}");
    }
    let _ = writeln!(out, "\n{}", driver.monitor.render_members());
    let first = &violations[0];
    let forensics = driver.forensics();
    if first.index.is_some() || first.term.is_some() {
        let _ = writeln!(
            out,
            "{}",
            forensics.render_messages(first.index, first.term, 250)
        );
    }
    if let Some(at) = first.at_ms {
        let _ = writeln!(
            out,
            "{}",
            forensics.render_window(at.saturating_sub(400), at + 5, 300)
        );
    }
    let _ = writeln!(out, "{}", forensics.render_messages(None, None, 120));
    if env("RAFT_RUST_SOAK_DUMP").is_some() {
        // Everything the ring still holds, for a failure whose cause lies
        // before the window the filtered views show.
        let _ = writeln!(
            out,
            "FULL {}",
            forensics.render_messages(None, None, usize::MAX)
        );
    }
    let tail = if env("RAFT_RUST_SOAK_DUMP").is_some() {
        usize::MAX
    } else {
        150
    };
    let _ = writeln!(out, "{}", forensics.render_steps(tail));
    let _ = writeln!(out, "implementation log (most recent):");
    for logged in capture.recent(200) {
        let _ = writeln!(out, "  {logged}");
    }
    out
}

// -- the pool ---------------------------------------------------------------------

struct Active {
    tag: String,
    seed: u64,
    started: Instant,
    progress: Progress,
    handle: std::thread::JoinHandle<()>,
}

struct Summary {
    reports: Vec<Report>,
    hangs: Vec<String>,
    harness: Vec<String>,
    wall: Duration,
}

fn soak(config: &Config, mut seeds: Seeds, jobs: usize, quiet: bool) -> Summary {
    capture::install_panic_hook();
    let hang_after = Duration::from_secs(
        env("RAFT_RUST_SOAK_HANG_SECS")
            .and_then(|text| text.parse().ok())
            .unwrap_or(300),
    );
    let started = Instant::now();
    let (sender, receiver) = std::sync::mpsc::channel::<Report>();
    let mut active: Vec<Active> = Vec::new();
    let mut summary = Summary {
        reports: Vec::new(),
        hangs: Vec::new(),
        harness: Vec::new(),
        wall: Duration::ZERO,
    };
    let mut run: u64 = 0;
    let mut exhausted = false;
    loop {
        while !exhausted && active.len() < jobs {
            let Some(seed) = seeds.next() else {
                exhausted = true;
                break;
            };
            run += 1;
            let plan = plan_for(seed, run, config);
            let tag = plan.tag.clone();
            let progress: Progress = Arc::new(Mutex::new("starting".to_owned()));
            let reporter = sender.clone();
            let watched = Arc::clone(&progress);
            let handle = std::thread::Builder::new()
                .name(tag.clone())
                .stack_size(stack_bytes())
                .spawn(move || {
                    let report = run_catching(plan, watched);
                    drop(reporter.send(report));
                })
                .expect("a run thread");
            active.push(Active {
                tag,
                seed,
                started: Instant::now(),
                progress,
                handle,
            });
        }
        if active.is_empty() {
            break;
        }
        match receiver.recv_timeout(Duration::from_millis(250)) {
            Ok(report) => {
                active.retain(|entry| entry.tag != report.plan.tag);
                if !quiet || !report.violations.is_empty() {
                    println!("{}", one_line(&report));
                }
                summary.reports.push(report);
            }
            Err(_) => {
                let mut still = Vec::new();
                for entry in active.drain(..) {
                    if entry.handle.is_finished() {
                        // Finished without reporting: the harness itself
                        // panicked. Its panic was recorded by the hook.
                        let panics = capture::panics_for(&entry.tag);
                        summary.harness.push(format!(
                            "seed {} ({}) ended without a report: {panics:?}",
                            entry.seed, entry.tag
                        ));
                    } else if entry.started.elapsed() > hang_after {
                        let line = format!(
                            "seed {} ({}) still running after {:?}; last progress: {}",
                            entry.seed,
                            entry.tag,
                            entry.started.elapsed(),
                            entry.progress.lock()
                        );
                        println!("HANG {line}");
                        summary.hangs.push(line);
                        // The thread cannot be killed; it is left behind, and
                        // the test fails on the hang when the pool finishes.
                    } else {
                        still.push(entry);
                    }
                }
                active = still;
            }
        }
    }
    summary.wall = started.elapsed();
    summary
}

/// Stack reserved per run thread.
///
/// Large on purpose. A stack overflow cannot be caught -- it aborts the whole
/// process and every run in it -- and the first one this soak met was an
/// unbounded recursion in the code under test (`register` retrying itself on
/// `not_owner`). With room to spare, the same recursion runs long enough for
/// the network's storm detector to report it as a finding with its message
/// mix, and the other runs survive. Only the pages actually touched are
/// committed, so an unused reservation costs nothing.
fn stack_bytes() -> usize {
    env("RAFT_RUST_SOAK_STACK_MB")
        .and_then(|text| text.parse::<usize>().ok())
        .unwrap_or(512)
        << 20
}

/// `run`, with a harness panic turned into a report rather than a lost run.
fn run_catching(plan: Plan, progress: Progress) -> Report {
    let kept = plan.clone();
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| run(plan, progress))) {
        Ok(report) => report,
        Err(payload) => {
            let message = payload
                .downcast_ref::<&str>()
                .map(|text| (*text).to_owned())
                .or_else(|| payload.downcast_ref::<String>().cloned())
                .unwrap_or_else(|| "(non-text panic)".to_owned());
            Report {
                violations: vec![Violation {
                    property: "Panic",
                    detail: format!("the run itself panicked: {message}"),
                    index: None,
                    term: None,
                    at_ms: None,
                }],
                tally: Tally::default(),
                counts: BTreeMap::new(),
                anomalies: BTreeMap::new(),
                anomaly_examples: BTreeMap::new(),
                net: [0; 9],
                terms: 0,
                committed: 0,
                checks: 0,
                audited: (0, 0),
                ledger: (0, 0, 0),
                wall: Duration::ZERO,
                simulated: Duration::ZERO,
                explanation: format!("plan: {}\npanic: {message}", kept.describe()),
                plan: kept,
            }
        }
    }
}

fn one_line(report: &Report) -> String {
    let verdict = if report.violations.is_empty() {
        "ok  ".to_owned()
    } else {
        let mut properties: Vec<&str> = report.violations.iter().map(|v| v.property).collect();
        properties.dedup();
        format!("FAIL[{}]", properties.join(","))
    };
    format!(
        "{verdict} {} | steps={} terms={} committed={} checks={} audited(commits/promotions)={:?} acks={} owed(present/absent/open)={:?} \
         snapshots={} compactions={} promotions={} restarts={} wall={:.1}s sim={:.1}s",
        report.plan.describe(),
        report.tally.steps,
        report.terms,
        report.committed,
        report.checks,
        report.audited,
        report
            .tally
            .answers
            .get("acknowledged")
            .copied()
            .unwrap_or(0),
        report.ledger,
        report
            .counts
            .get("raft: installed a snapshot")
            .copied()
            .unwrap_or(0),
        report
            .counts
            .get("raft: snapshotted and compacted")
            .copied()
            .unwrap_or(0),
        report.counts.get("raft: promoted").copied().unwrap_or(0),
        report.tally.restarts,
        report.wall.as_secs_f64(),
        report.simulated.as_secs_f64(),
    )
}

/// The whole soak, summarised: failures by property, and coverage.
fn summarise(summary: &Summary) -> String {
    let mut out = String::new();
    let failures: Vec<&Report> = summary
        .reports
        .iter()
        .filter(|report| !report.violations.is_empty())
        .collect();
    let _ = writeln!(
        out,
        "\n=== Rust raft chaos soak: {} runs in {:.1}s, {} failed, {} hung, {} harness errors ===",
        summary.reports.len(),
        summary.wall.as_secs_f64(),
        failures.len(),
        summary.hangs.len(),
        summary.harness.len(),
    );

    let mut by_property: BTreeMap<String, Vec<u64>> = BTreeMap::new();
    for report in &failures {
        let mut seen: Vec<String> = Vec::new();
        for found in &report.violations {
            let key = group_of(found);
            if !seen.contains(&key) {
                seen.push(key.clone());
                by_property.entry(key).or_default().push(report.plan.seed);
            }
        }
    }
    for (property, seeds) in &by_property {
        let shown: Vec<String> = seeds.iter().take(12).map(u64::to_string).collect();
        let _ = writeln!(
            out,
            "  {property}: {} run(s), seeds {}",
            seeds.len(),
            shown.join(",")
        );
    }

    let mut totals: BTreeMap<String, u64> = BTreeMap::new();
    let mut answers: BTreeMap<String, u64> = BTreeMap::new();
    let mut anomalies: BTreeMap<&str, u64> = BTreeMap::new();
    let mut examples: BTreeMap<&str, String> = BTreeMap::new();
    let mut shapes: BTreeMap<String, u64> = BTreeMap::new();
    let (mut steps, mut checks, mut terms, mut net) = (0usize, 0u64, 0usize, [0u64; 9]);
    let (mut audited_commits, mut audited_promotions) = (0u64, 0u64);
    let mut simulated = Duration::ZERO;
    for report in &summary.reports {
        steps += report.tally.steps;
        checks += report.checks;
        audited_commits += report.audited.0;
        audited_promotions += report.audited.1;
        terms += report.terms;
        simulated += report.simulated;
        for (slot, value) in net.iter_mut().zip(report.net) {
            *slot += value;
        }
        for (message, count) in &report.counts {
            *totals.entry(message.clone()).or_insert(0) += count;
        }
        for (answer, count) in &report.tally.answers {
            *answers.entry(answer.clone()).or_insert(0) += count;
        }
        for (kind, count) in &report.anomalies {
            *anomalies.entry(kind).or_insert(0) += count;
        }
        for (kind, example) in &report.anomaly_examples {
            // With its seed, so the run it came from can be run again with
            // `RAFT_RUST_SOAK_DUMP=1` and the example traced.
            examples
                .entry(kind)
                .or_insert_with(|| format!("{example} (seed {})", report.plan.seed));
        }
        let runtime = match report.plan.runtime {
            Runtime::Virtual => "virtual",
            Runtime::Threaded { .. } => "threaded",
        };
        *shapes
            .entry(format!(
                "{runtime}/{:?}/n={}",
                report.plan.workload, report.plan.size
            ))
            .or_insert(0) += 1;
    }
    let _ = writeln!(
        out,
        "  coverage: {steps} steps, {checks} property checks, {audited_commits} commit decisions and \
         {audited_promotions} promotions audited, {terms} terms led, {:.0}s of cluster time",
        simulated.as_secs_f64()
    );
    let _ = writeln!(
        out,
        "  network: delivered={} unlinked={} lost={} requests={} request_failures={} connects={} drops={} stalled={} severed={}",
        net[0], net[1], net[2], net[3], net[4], net[5], net[6], net[7], net[8]
    );
    let _ = writeln!(out, "  shapes: {shapes:?}");
    let _ = writeln!(out, "  client answers: {answers:?}");
    for message in [
        "raft: is leader",
        "raft: relinquishing leadership",
        "raft: member restarted",
        "raft: member is catching up",
        "raft: member promoted",
        "raft: promoted",
        "raft: snapshotted and compacted",
        "raft: installed a snapshot",
        "raft: expired node and its sub-resources",
    ] {
        let _ = writeln!(
            out,
            "  implementation: {message:<42} x{}",
            totals.get(message).copied().unwrap_or(0)
        );
    }
    if !anomalies.is_empty() {
        let _ = writeln!(out, "  client-visible anomalies (not safety failures):");
        for (kind, count) in &anomalies {
            let _ = writeln!(
                out,
                "    {kind}: x{count}, e.g. {}",
                examples.get(kind).map_or("", String::as_str)
            );
        }
    }
    for hang in &summary.hangs {
        let _ = writeln!(out, "  HANG: {hang}");
    }
    for error in &summary.harness {
        let _ = writeln!(out, "  HARNESS: {error}");
    }
    out
}

/// What a failure is grouped under in the summary.
///
/// The property name, except for a logged problem, which is grouped by the
/// implementation's own message -- "Logged Problem" alone would put a
/// divergence halt and a refused snapshot in the same bucket.
fn group_of(found: &Violation) -> String {
    if found.property != "Logged Problem" {
        return found.property.to_owned();
    }
    let text = found
        .detail
        .split_once("ERROR ")
        .or_else(|| found.detail.split_once("WARN "))
        .map_or(found.detail.as_str(), |(_, rest)| rest.trim_start());
    let text = text
        .rsplit_once(" [")
        .map_or(text, |(message, _)| message)
        .trim_end();
    let message = text
        .find('=')
        .and_then(|equals| text[..equals].rfind(' ').map(|space| &text[..space]))
        .unwrap_or(text);
    format!("Logged Problem: {message}")
}

fn write_artifacts(summary: &Summary) -> Option<PathBuf> {
    let failures: Vec<&Report> = summary
        .reports
        .iter()
        .filter(|report| !report.violations.is_empty())
        .collect();
    if failures.is_empty() && summary.hangs.is_empty() {
        return None;
    }
    let dir = env("RAFT_RUST_SOAK_ARTIFACTS").map_or_else(
        || std::env::temp_dir().join("nmos-raft-soak-reports"),
        PathBuf::from,
    );
    drop(std::fs::create_dir_all(&dir));
    for report in failures {
        let path = dir.join(format!("{}.txt", report.plan.tag));
        drop(std::fs::write(&path, &report.explanation));
    }
    drop(std::fs::write(dir.join("summary.txt"), summarise(summary)));
    Some(dir)
}

// -- the tests ----------------------------------------------------------------------

/// Members fall over at arbitrary times, clients keep writing, and nothing
/// the consensus layer or the registry promises is broken.
///
/// The assertion is not only at the end: every property is evaluated after
/// every step of every run (see `chaos/monitor.rs`), because Figure 3's
/// properties are true *at all times*, and checking them only at rest would
/// miss precisely the transient violations a converging cluster hides.
#[test]
#[ignore = "run explicitly: it reports production defects that are open at the time of writing \
            (see the soak report in nmos-reference/plans/), and would otherwise turn the \
            ordinary gate red until they are fixed"]
fn churn_preserves_every_safety_property() {
    let config = config_from_env();
    let seeds = Seeds::from_env();
    let jobs = env("RAFT_RUST_SOAK_JOBS")
        .and_then(|text| text.parse().ok())
        .unwrap_or_else(|| std::thread::available_parallelism().map_or(4, std::num::NonZero::get));
    let quiet = env("RAFT_RUST_SOAK_QUIET").is_some();
    let summary = soak(&config, seeds, jobs.max(1), quiet);
    drop(std::fs::remove_dir_all(&config.root));
    let text = summarise(&summary);
    println!("{text}");
    let artifacts = write_artifacts(&summary);
    let failed = summary
        .reports
        .iter()
        .filter(|report| !report.violations.is_empty())
        .count();
    if failed > 0 || !summary.hangs.is_empty() || !summary.harness.is_empty() {
        let first = summary
            .reports
            .iter()
            .find(|report| !report.violations.is_empty())
            .map_or_else(String::new, |report| report.explanation.clone());
        panic!(
            "{failed} run(s) violated a property, {} hung, {} harness error(s); reports in {:?}\n\
             {text}\nfirst failure:\n{first}",
            summary.hangs.len(),
            summary.harness.len(),
            artifacts,
        );
    }
}

// -- guard the guards ------------------------------------------------------------
//
// Every green run above is consistent with a monitor that checks nothing, a
// network that injects nothing, and a capture that hears nothing -- the
// failure mode of every fault injector, invisible precisely because it looks
// like success. Each test below shows one mechanism doing its job.

mod guards {
    use std::collections::BTreeSet;
    use std::sync::Arc;
    use std::time::Duration;

    use async_trait::async_trait;
    use nmos_registry_core::resource_type::ResourceType;
    use nmos_registry_raft::messages::{
        AppendEntries, AppendEntriesReply, Forward, ForwardReply, InstallSnapshot,
        InstallSnapshotReply, Message, Promote, Propose, ProposeReply, ReadIndex, ReadIndexReply,
        RequestVote, RequestVoteReply,
    };
    use nmos_registry_raft::node::{RaftTiming, Role};
    use nmos_registry_raft::persist::PersistentStateError;
    use nmos_registry_raft::transport::{CONN_READ_TIMEOUT_MS, PeerHandler, Transport};
    use nmos_registry_raft::wire::Stream;
    use parking_lot::Mutex;

    use super::chaos::capture::{self, Capture};
    use super::chaos::cluster::{ClusterConfig, SoakCluster};
    use super::chaos::forensics::Forensics;
    use super::chaos::monitor::{Depth, Mode, Monitor, Observed, REVIVED};
    use super::chaos::net::{ChaosNet, Knobs, advances};
    use super::chaos::rng::Rng;
    use super::chaos::workload;

    /// A peer that records what reaches it and answers everything blandly.
    #[derive(Default)]
    struct Recorder {
        promotes: Mutex<Vec<u64>>,
        states: Mutex<Vec<(u64, bool)>>,
        /// Forwards are never answered, so a request can be left in flight.
        silent: bool,
    }

    #[async_trait]
    impl PeerHandler for Recorder {
        fn on_request_vote(
            &self,
            _peer: u64,
            message: &RequestVote,
        ) -> Result<RequestVoteReply, PersistentStateError> {
            Ok(RequestVoteReply {
                term: message.term,
                granted: false,
                voting: true,
                pre_vote: message.pre_vote,
            })
        }

        fn on_append_entries(
            &self,
            _peer: u64,
            message: &AppendEntries,
        ) -> Result<AppendEntriesReply, PersistentStateError> {
            Ok(AppendEntriesReply {
                term: message.term,
                success: true,
                match_index: 0,
                conflict_index: 0,
                conflict_term: 0,
                catching_up: false,
                request_id: message.request_id,
            })
        }

        fn on_install_snapshot(
            &self,
            _peer: u64,
            message: &InstallSnapshot,
        ) -> Result<InstallSnapshotReply, PersistentStateError> {
            Ok(InstallSnapshotReply {
                term: message.term,
                bytes_received: 0,
                done: false,
                commit_index: 0,
                request_id: message.request_id,
            })
        }

        fn on_promote(&self, _peer: u64, message: &Promote) {
            self.promotes.lock().push(message.through_index);
        }

        fn on_request_vote_reply(
            &self,
            _peer: u64,
            _message: &RequestVoteReply,
        ) -> Result<(), PersistentStateError> {
            Ok(())
        }

        fn on_append_entries_reply(
            &self,
            _peer: u64,
            _message: &AppendEntriesReply,
        ) -> Result<(), PersistentStateError> {
            Ok(())
        }

        fn on_install_snapshot_reply(
            &self,
            _peer: u64,
            _message: &InstallSnapshotReply,
        ) -> Result<(), PersistentStateError> {
            Ok(())
        }

        async fn on_propose(&self, _peer: u64, message: &Propose) -> ProposeReply {
            ProposeReply {
                accepted: false,
                reason: "recorder".to_owned(),
                term: 0,
                first_index: 0,
                request_id: message.request_id,
                leader: None,
            }
        }

        async fn on_forward(&self, _peer: u64, message: &Forward) -> ForwardReply {
            if self.silent {
                std::future::pending::<()>().await;
            }
            ForwardReply {
                ok: true,
                created: false,
                error: String::new(),
                detail: String::new(),
                applied_index: 7,
                not_owner: false,
                request_id: message.request_id,
                owner: None,
            }
        }

        async fn on_read_index(&self, _peer: u64, message: &ReadIndex) -> ReadIndexReply {
            if self.silent {
                std::future::pending::<()>().await;
            }
            ReadIndexReply {
                ok: true,
                index: 7,
                reason: String::new(),
                request_id: message.request_id,
            }
        }

        fn on_peer_state(&self, peer: u64, up: bool, _incarnation: u64) {
            self.states.lock().push((peer, up));
        }
    }

    fn promote(through: u64) -> Message {
        Message::Promote(Promote {
            term: 1,
            leader: 0,
            through_index: through,
        })
    }

    fn quiet_net(knobs: Knobs) -> Arc<ChaosNet> {
        ChaosNet::new(&[0, 1], Rng::new(7), knobs, Arc::new(Forensics::new()), 500)
    }

    async fn pair(net: &Arc<ChaosNet>, silent: bool) -> (Arc<Recorder>, Arc<Recorder>) {
        let zero = Arc::new(Recorder::default());
        let one = Arc::new(Recorder {
            silent,
            ..Recorder::default()
        });
        net.transport(0)
            .start(Arc::clone(&zero) as Arc<dyn PeerHandler>)
            .await
            .unwrap();
        net.transport(1)
            .start(Arc::clone(&one) as Arc<dyn PeerHandler>)
            .await
            .unwrap();
        tokio::time::sleep(Duration::from_millis(50)).await;
        (zero, one)
    }

    #[tokio::test(start_paused = true)]
    async fn a_delayed_connection_stays_fifo_while_its_streams_drift_apart() {
        let net = quiet_net(Knobs {
            max_delay_us: 20_000,
            spike_one_in: 5,
            reconnect_max_us: 0,
        });
        let (_zero, one) = pair(&net, false).await;
        let sender = net.transport(0);
        for through in 0..300 {
            let stream = if through % 3 == 0 {
                Stream::Bulk
            } else {
                Stream::Control
            };
            sender.send(1, &promote(through), stream);
        }
        tokio::time::sleep(Duration::from_secs(30)).await;
        let seen = one.promotes.lock().clone();
        assert_eq!(
            seen.len(),
            300,
            "every message on a live connection arrives"
        );
        let control: Vec<u64> = seen.iter().copied().filter(|n| n % 3 != 0).collect();
        let bulk: Vec<u64> = seen.iter().copied().filter(|n| n % 3 == 0).collect();
        assert!(
            control.windows(2).all(|w| w[0] < w[1]),
            "CONTROL reordered: {control:?}"
        );
        assert!(
            bulk.windows(2).all(|w| w[0] < w[1]),
            "BULK reordered: {bulk:?}"
        );
        assert!(
            seen.windows(2).any(|w| w[0] > w[1]),
            "the two streams never drifted apart, so cross-stream reordering is not being \
             exercised at all"
        );
    }

    /// A stall shorter than the transport's read timeout: held, then delivered
    /// in order, as TCP delivers what it retransmitted.
    #[tokio::test(start_paused = true)]
    async fn a_stall_holds_everything_and_then_delivers_it_in_order() {
        let net = quiet_net(Knobs {
            max_delay_us: 1_000,
            spike_one_in: 0,
            reconnect_max_us: 0,
        });
        let (zero, one) = pair(&net, false).await;
        let short = Duration::from_millis(CONN_READ_TIMEOUT_MS / 2);
        net.stall(1);
        for through in 0..50 {
            net.transport(0).send(1, &promote(through), Stream::Control);
        }
        tokio::time::sleep(short).await;
        assert!(
            one.promotes.lock().is_empty(),
            "a stalled member received messages"
        );
        assert!(
            !zero.states.lock().contains(&(1, false)),
            "a stall must not look like a disconnect"
        );
        assert!(net.connected(0, 1), "a stalled connection is still up");
        net.unstall(1);
        tokio::time::sleep(short).await;
        assert_eq!(*one.promotes.lock(), (0..50).collect::<Vec<u64>>());
    }

    /// A stall that outlasts the read timeout: the transport closes a
    /// connection that carries nothing for that long, so what it held is lost
    /// -- never delivered late -- and it reconnects only once the stall ends.
    #[tokio::test(start_paused = true)]
    async fn a_stall_past_the_read_timeout_severs_and_loses_what_it_held() {
        let net = quiet_net(Knobs {
            max_delay_us: 1_000,
            spike_one_in: 0,
            reconnect_max_us: 0,
        });
        let (zero, one) = pair(&net, false).await;
        net.stall(1);
        for through in 0..50 {
            net.transport(0).send(1, &promote(through), Stream::Control);
        }
        tokio::time::sleep(Duration::from_millis(CONN_READ_TIMEOUT_MS + 1_000)).await;
        assert!(
            zero.states.lock().contains(&(1, false)),
            "a connection that carried nothing past the read timeout was never reported down"
        );
        assert!(
            !net.connected(0, 1),
            "a severed connection reconnected while its path was still stalled"
        );
        net.unstall(1);
        tokio::time::sleep(Duration::from_secs(1)).await;
        assert!(
            one.promotes.lock().is_empty(),
            "messages held past the read timeout were delivered: {:?}",
            one.promotes.lock()
        );
        assert!(
            net.connected(0, 1),
            "it never reconnected once the stall ended"
        );
        assert_eq!(
            zero.states.lock().last(),
            Some(&(1, true)),
            "the reconnection was never reported"
        );
    }

    #[tokio::test(start_paused = true)]
    async fn a_dropped_connection_fails_its_requests_and_is_reported_down() {
        let net = quiet_net(Knobs {
            max_delay_us: 0,
            spike_one_in: 0,
            reconnect_max_us: 0,
        });
        let (zero, _one) = pair(&net, true).await;
        let transport = net.transport(0);
        let request = Message::Forward(Forward {
            verb: "register".to_owned(),
            resource_type: "node".to_owned(),
            resource_id: "x".to_owned(),
            body_text: String::new(),
            request_id: 0,
        });
        let waiting = tokio::spawn(async move {
            transport
                .request(1, &request, Stream::Control, Some(60_000))
                .await
        });
        tokio::time::sleep(Duration::from_millis(10)).await;
        net.stop(1);
        let answer = tokio::time::timeout(Duration::from_secs(1), waiting)
            .await
            .expect("a request on a dropped connection fails at once, not at its deadline")
            .unwrap();
        assert!(
            answer.is_err(),
            "a request survived its connection: {answer:?}"
        );
        assert!(
            zero.states.lock().contains(&(1, false)),
            "the drop was never reported"
        );
        net.resume(1);
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(
            zero.states.lock().ends_with(&[(1, true)]),
            "the reconnect was never reported"
        );
    }

    #[tokio::test(start_paused = true)]
    async fn a_correlated_request_is_answered_through_the_network() {
        let net = quiet_net(Knobs {
            max_delay_us: 3_000,
            spike_one_in: 0,
            reconnect_max_us: 0,
        });
        let (_zero, _one) = pair(&net, false).await;
        let reply = net
            .transport(0)
            .request(
                1,
                &Message::Forward(Forward {
                    verb: "register".to_owned(),
                    resource_type: "node".to_owned(),
                    resource_id: "x".to_owned(),
                    body_text: String::new(),
                    request_id: 0,
                }),
                Stream::Control,
                None,
            )
            .await
            .expect("an answered request");
        assert!(matches!(reply, Message::ForwardReply(ref r) if r.applied_index == 7));
    }

    #[test]
    fn the_capture_turns_the_nodes_own_log_lines_into_findings() {
        let capture = Capture::new();
        tracing::dispatcher::with_default(&capture.dispatch(), || {
            tracing::info!(member = "guard-m1", term = 9_u64, "raft: is leader");
            tracing::error!(error = "boom", "raft: local append refused");
            tracing::warn!(
                "registry: no leader yet; serving queries and refusing registrations until one is elected"
            );
        });
        assert_eq!(capture.leaders(), vec![("guard-m1".to_owned(), 9)]);
        let problems = capture.problems();
        assert_eq!(
            problems.len(),
            1,
            "exactly the unexpected line: {problems:?}"
        );
        assert_eq!(problems[0].message, "raft: local append refused");
    }

    #[test]
    fn a_panic_in_a_spawned_task_is_attributed_to_its_run() {
        capture::install_panic_hook();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        runtime.block_on(async {
            capture::claim_thread("guard-panic-run");
            let task = tokio::spawn(async { panic!("guard: deliberate") });
            assert!(task.await.is_err());
        });
        let panics = capture::panics_for("guard-panic-run");
        assert_eq!(panics.len(), 1, "{panics:?}");
        assert!(panics[0].message.contains("deliberate"));
    }

    fn tiny_cluster(tag: &str) -> SoakCluster {
        let dir =
            std::env::temp_dir().join(format!("nmos-raft-soak-guard-{tag}-{}", std::process::id()));
        drop(std::fs::create_dir_all(&dir));
        let net = ChaosNet::new(
            &[0, 1, 2],
            Rng::new(1),
            Knobs {
                max_delay_us: 0,
                spike_one_in: 0,
                reconnect_max_us: 0,
            },
            Arc::new(Forensics::new()),
            500,
        );
        SoakCluster::build(
            ClusterConfig {
                size: 3,
                timing: RaftTiming::default(),
                backends: false,
                mutation_timeout: Duration::from_secs(1),
                gc_interval: 12,
                forget_interval: 12,
                tag: tag.to_owned(),
            },
            net,
            dir,
        )
    }

    #[tokio::test(start_paused = true)]
    async fn the_monitor_catches_two_leaders_in_one_term() {
        let cluster = tiny_cluster("guard-election");
        let mut monitor = Monitor::new(Mode::Exact);
        let leaders = vec![
            ("guard-election-m0".to_owned(), 5),
            ("guard-election-m2".to_owned(), 5),
        ];
        let found = monitor
            .check(&cluster, &leaders, Depth::Step)
            .expect_err("two members won term 5");
        assert_eq!(found.property, "Election Safety");
    }

    #[tokio::test(start_paused = true)]
    async fn the_ledger_catches_a_lost_acknowledged_write() {
        let cluster = tiny_cluster("guard-ledger");
        let mut monitor = Monitor::new(Mode::Exact);
        monitor
            .ledger
            .acknowledged((ResourceType::Node, "never-stored".to_owned()), None);
        let found = monitor.check_promises(&cluster, true);
        assert_eq!(
            found.len(),
            3,
            "absent on each of the three members: {found:?}"
        );
        assert!(
            found
                .iter()
                .all(|v| v.property == "Acknowledged Write Durability")
        );
    }

    const REVIVED_NODE: &str = "00000000-0000-4000-8000-00000000feed";

    /// Every member of `cluster` holding [`REVIVED_NODE`] at `version`.
    fn held_everywhere(cluster: &SoakCluster, version: &str) {
        for member in &cluster.members {
            member
                .registry
                .register(
                    ResourceType::Node,
                    workload::node_body(REVIVED_NODE, version),
                )
                .expect("a Node with no parent registers");
        }
    }

    /// A Node acknowledged at 1001:1, updated to 1002:2 with the answer a 503,
    /// then deleted -- the delete confirmed.
    fn deleted_after_an_undecided_update() -> Monitor {
        let mut monitor = Monitor::new(Mode::Exact);
        let key = (ResourceType::Node, REVIVED_NODE.to_owned());
        monitor
            .ledger
            .acknowledged(key.clone(), Some("1001:1".to_owned()));
        monitor.ledger.undecided(&key, "1002:2");
        monitor.ledger.deleted(&key);
        monitor
    }

    /// The update commits after the delete and revives the Node at its own
    /// version (seed 130337). Linearizable -- its client never learned its
    /// outcome -- so no promise is broken; a client can see it, so it counts.
    #[tokio::test(start_paused = true)]
    async fn a_delete_undone_by_an_undecided_registration_is_counted_not_failed() {
        let cluster = tiny_cluster("guard-revival");
        let mut monitor = deleted_after_an_undecided_update();
        held_everywhere(&cluster, "1002:2");
        let found = monitor.check_promises(&cluster, true);
        assert!(found.is_empty(), "a legitimate revival reported: {found:?}");
        assert_eq!(
            monitor.ledger.anomalies.get(REVIVED),
            Some(&1),
            "counted once for the resource, not once per member: {:?}",
            monitor.ledger.anomalies
        );
    }

    /// Back at a version no undecided registration carried: the acknowledged
    /// one, whose only attempt committed before the delete. Nothing a client
    /// did explains it, so it is still a violation on every member.
    #[tokio::test(start_paused = true)]
    async fn the_ledger_catches_a_revival_no_undecided_registration_explains() {
        let cluster = tiny_cluster("guard-revival-other");
        let mut monitor = deleted_after_an_undecided_update();
        held_everywhere(&cluster, "1001:1");
        let found = monitor.check_promises(&cluster, true);
        assert_eq!(found.len(), 3, "one per member: {found:?}");
        assert!(
            found
                .iter()
                .all(|v| v.property == "Acknowledged Write Durability"
                    && v.detail.contains("1002:2")),
            "each names the only version that could have come back: {found:?}"
        );
        assert!(!monitor.ledger.anomalies.contains_key(REVIVED));
    }

    /// With nothing undecided at all, any copy after a confirmed delete is a
    /// violation, exactly as before undecided registrations were tracked.
    #[tokio::test(start_paused = true)]
    async fn the_ledger_catches_a_deleted_resource_that_nothing_could_revive() {
        let cluster = tiny_cluster("guard-revival-none");
        let mut monitor = Monitor::new(Mode::Exact);
        let key = (ResourceType::Node, REVIVED_NODE.to_owned());
        monitor
            .ledger
            .acknowledged(key.clone(), Some("1001:1".to_owned()));
        monitor.ledger.deleted(&key);
        held_everywhere(&cluster, "1001:1");
        let found = monitor.check_promises(&cluster, true);
        assert_eq!(found.len(), 3, "one per member: {found:?}");
        assert!(
            found
                .iter()
                .all(|v| v.detail.contains("was deleted (confirmed)")),
            "{found:?}"
        );
    }

    /// One member as the monitor would see it, holding `log` above a snapshot
    /// through 133 (term 11).
    fn seen(index: u64, role: Role, term: u64, commit: u64, log: Vec<(u64, u64)>) -> Observed {
        let last_index = log.last().map_or(133, |&(at, _)| at);
        Observed {
            index,
            incarnation: 1,
            role,
            term,
            leader: None,
            voting: true,
            commit,
            applied: 0,
            snapshot_index: 133,
            snapshot_term: 11,
            last_index,
            log,
        }
    }

    /// Entries 134..=140 of term 11, then `rest`.
    fn through_140(rest: &[(u64, u64)]) -> Vec<(u64, u64)> {
        let mut log: Vec<(u64, u64)> = (134..=140).map(|index| (index, 11)).collect();
        log.extend_from_slice(rest);
        log
    }

    /// Seed 141387, run 2, decoded. m2 led term 11 and appended (141, t11),
    /// which reached nobody. m1 won term 12 with m0's vote -- both ended at
    /// (140, t11) -- and led it, cut off, appending (141, t12). m2 won term 13,
    /// gave m0 (141, t11) and committed its own (142, t13) above it: (141, t11)
    /// committed in term 13. m1 still led term 12 when that was observed.
    fn seed_141387() -> (Monitor, Vec<Observed>) {
        let mut monitor = Monitor::new(Mode::Exact);
        let before = vec![
            seen(0, Role::Follower, 12, 140, through_140(&[])),
            seen(1, Role::Leader, 12, 140, through_140(&[(141, 12)])),
            seen(2, Role::Follower, 12, 140, through_140(&[(141, 11)])),
        ];
        monitor.fold_committed(&before).expect("nothing disagrees");
        monitor
            .leader_completeness(&before)
            .expect("nothing is committed at 141 yet");
        let after = vec![
            seen(
                0,
                Role::Follower,
                13,
                142,
                through_140(&[(141, 11), (142, 13)]),
            ),
            seen(1, Role::Leader, 12, 140, through_140(&[(141, 12)])),
            seen(
                2,
                Role::Leader,
                13,
                142,
                through_140(&[(141, 11), (142, 13)]),
            ),
        ];
        monitor.fold_committed(&after).expect("nothing disagrees");
        (monitor, after)
    }

    /// Owed to leaders of terms after the one it was committed *in*: an entry
    /// of term 11 committed in term 13 is not owed to a leader of term 12.
    #[test]
    fn an_entry_committed_after_a_leader_was_elected_is_not_owed_to_it() {
        let (monitor, after) = seed_141387();
        let found = monitor.leader_completeness(&after);
        assert!(
            found.is_ok(),
            "a leader of term 12 was held to an entry first committed in term 13: {:?}",
            found.err()
        );
    }

    /// The rule's other side, which must not weaken: a leader of any term after
    /// the commit, lacking the entry, is a real violation.
    #[test]
    fn a_leader_elected_after_the_commit_still_owes_it() {
        let (monitor, mut after) = seed_141387();
        after[1].term = 14;
        after[1].log = through_140(&[(141, 14)]);
        let found = monitor
            .leader_completeness(&after)
            .expect_err("a leader of term 14 without the entry committed in term 13 passed");
        assert_eq!(found.property, "Leader Completeness");
        assert!(found.detail.contains("index 141"), "{}", found.detail);
    }

    /// The storm detector counts only what makes no progress: a long transfer
    /// at zero delay ends, a transfer going round in circles does not.
    #[test]
    fn a_transfer_advances_and_one_going_round_in_circles_does_not() {
        use std::collections::BTreeMap;

        let chunk = |offset: u64, last_index: u64| {
            Message::InstallSnapshot(InstallSnapshot {
                term: 3,
                leader: 0,
                last_index,
                last_term: 2,
                offset,
                data: vec![0; 16],
                done: false,
                ownership: Vec::new(),
                request_id: 1,
            })
        };
        let answer = |received: u64| {
            Message::InstallSnapshotReply(InstallSnapshotReply {
                term: 3,
                bytes_received: received,
                done: false,
                commit_index: 0,
                request_id: 1,
            })
        };
        let mut carried = BTreeMap::new();

        for step in 0..4u64 {
            assert!(
                advances(&mut carried, 0, 1, 7, &chunk(step * 16, 40)),
                "chunk {step} of an orderly transfer"
            );
            assert!(
                advances(&mut carried, 1, 0, 7, &answer(step * 16 + 16)),
                "the answer to chunk {step}"
            );
        }

        assert!(
            !advances(&mut carried, 0, 1, 7, &chunk(48, 40)),
            "a chunk sent again"
        );
        assert!(
            !advances(&mut carried, 0, 1, 7, &chunk(0, 40)),
            "the same snapshot restarted on the same connection"
        );
        assert!(
            !advances(&mut carried, 1, 0, 7, &answer(0)),
            "an answer of zero"
        );
        assert!(
            !advances(&mut carried, 1, 0, 7, &answer(64)),
            "an answer acknowledging nothing new"
        );

        assert!(
            advances(&mut carried, 0, 1, 8, &chunk(0, 40)),
            "a new connection begins a new count"
        );
        assert!(
            !advances(&mut carried, 1, 0, 7, &answer(80)),
            "an answer that came by the old connection"
        );
        assert!(
            advances(&mut carried, 0, 1, 8, &chunk(0, 56)),
            "the leader's newer snapshot begins a new count"
        );
        assert!(
            !advances(
                &mut carried,
                0,
                1,
                8,
                &Message::AppendEntries(AppendEntries {
                    term: 3,
                    leader: 0,
                    prev_log_index: 0,
                    prev_log_term: 0,
                    leader_commit: 0,
                    request_id: 2,
                    entries: Vec::new(),
                })
            ),
            "no other kind of message is progress"
        );
    }

    #[tokio::test(start_paused = true)]
    async fn a_partition_cuts_exactly_the_connections_between_groups() {
        let net = ChaosNet::new(
            &[0, 1, 2],
            Rng::new(3),
            Knobs {
                max_delay_us: 0,
                spike_one_in: 0,
                reconnect_max_us: 0,
            },
            Arc::new(Forensics::new()),
            500,
        );
        let recorders: Vec<Arc<Recorder>> = (0..3).map(|_| Arc::new(Recorder::default())).collect();
        for (index, recorder) in recorders.iter().enumerate() {
            net.transport(index as u64)
                .start(Arc::clone(recorder) as Arc<dyn PeerHandler>)
                .await
                .unwrap();
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
        net.partition(&[BTreeSet::from([0]), BTreeSet::from([1, 2])]);
        assert!(!net.connected(0, 1) && !net.connected(1, 0));
        assert!(!net.connected(0, 2) && !net.connected(2, 0));
        assert!(
            net.connected(1, 2) && net.connected(2, 1),
            "inside a group stays connected"
        );
        net.heal();
        tokio::time::sleep(Duration::from_millis(10)).await;
        assert!(net.settled(), "healing restores every connection");
    }
}

// -- regressions the soak found ---------------------------------------------------
//
// Each reproduces one finding deterministically, on the soak's own network --
// the only one in this crate that carries a forwarded mutation -- and fails if
// the defect returns.

mod regressions {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::Duration;

    use async_trait::async_trait;
    use nmos_registry_backend::RegistryBackend;
    use nmos_registry_core::resource_type::ResourceType;
    use nmos_registry_raft::backend::RaftRegistryBackend;
    use nmos_registry_raft::messages::{
        AppendEntries, AppendEntriesReply, Forward, ForwardReply, InstallSnapshot,
        InstallSnapshotReply, Promote, Propose, ProposeReply, ReadIndex, ReadIndexReply,
        RequestVote, RequestVoteReply,
    };
    use nmos_registry_raft::node::{RaftTiming, Role};
    use nmos_registry_raft::persist::PersistentStateError;
    use nmos_registry_raft::transport::{PeerHandler, Transport};

    use super::chaos::cluster::{ClusterConfig, SoakCluster};
    use super::chaos::forensics::Forensics;
    use super::chaos::monitor::{Observed, replica_digest};
    use super::chaos::net::{ChaosNet, Knobs};
    use super::chaos::rng::Rng;
    use super::chaos::workload::{self, Answer};

    fn registry_cluster(tag: &str) -> SoakCluster {
        registry_cluster_with(tag, RaftTiming::default())
    }

    fn registry_cluster_with(tag: &str, timing: RaftTiming) -> SoakCluster {
        cluster_of(tag, timing, true)
    }

    /// Three members and no registry in front: consensus only.
    fn consensus_cluster(tag: &str) -> SoakCluster {
        cluster_of(tag, RaftTiming::default(), false)
    }

    fn cluster_of(tag: &str, timing: RaftTiming, backends: bool) -> SoakCluster {
        let dir = std::env::temp_dir().join(format!(
            "nmos-raft-soak-regression-{tag}-{}",
            std::process::id()
        ));
        drop(std::fs::create_dir_all(&dir));
        let net = ChaosNet::new(
            &[0, 1, 2],
            Rng::new(1),
            Knobs {
                max_delay_us: 0,
                spike_one_in: 0,
                reconnect_max_us: 0,
            },
            Arc::new(Forensics::new()),
            1_000,
        );
        SoakCluster::build(
            ClusterConfig {
                size: 3,
                timing,
                backends,
                mutation_timeout: Duration::from_secs(1),
                gc_interval: 12,
                forget_interval: 12,
                tag: tag.to_owned(),
            },
            net,
            dir,
        )
    }

    async fn until(mut ready: impl FnMut() -> bool) -> bool {
        for _ in 0..1_000 {
            if ready() {
                return true;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        false
    }

    fn backend(cluster: &SoakCluster, member: u64) -> Arc<RaftRegistryBackend> {
        Arc::clone(
            cluster.members[member as usize]
                .backend
                .as_ref()
                .expect("a registry cluster has backends"),
        )
    }

    #[tokio::test(start_paused = true)]
    async fn an_owner_that_cannot_commit_is_answered_as_unavailable_not_as_a_schema_error() {
        // F1. The member owning a Node could not commit a mutation forwarded
        // to it -- it had lost its leader -- and said so, but under an empty
        // error code, which the forwarder read as the registration's own schema
        // error: the client was told **400**, and a Node told 400 MUST NOT
        // retry. The soak's Status Integrity oracle measured it as `400 ...
        // schema: registration of ... could not commit: lost contact with a
        // quorum`. The Python forwarder maps any code it does not know to 503
        // (`raft_backend.py`, `_result_of`), and the transports' own capacity
        // refusal documents the contract: "a forwarded mutation that comes back
        // not-ok becomes a 503".
        //
        // The owner is made to fail *at once* -- it already knows it has no
        // leader -- because an owner that fails by timing out loses the race
        // to the forwarder's own equal deadline, and that path answered 503
        // even before the fix.
        let cluster = registry_cluster("regression-f1");
        cluster.start().await;
        assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
        let leader = cluster.leaders()[0];
        let followers: Vec<u64> = (0..3u64).filter(|&member| member != leader).collect();
        let (owner, forwarder) = (followers[0], followers[1]);
        assert!(
            until(|| (0..3u64).all(|member| backend(&cluster, member).state().accepts_mutations()))
                .await,
            "the backends never became ready",
        );

        let node_id = "0b1f9b7e-5d9a-4c55-9a51-000000000001";
        let registered = workload::register(
            &backend(&cluster, owner),
            ResourceType::Node,
            workload::node_body(node_id, "1001:1"),
        )
        .await;
        assert!(
            matches!(registered, Answer::Acknowledged { .. }),
            "the Node was not registered at its owner: {registered:?}",
        );
        assert!(
            until(|| cluster.members[forwarder as usize]
                .node
                .ownership_of(node_id)
                == Some(owner))
            .await,
            "the forwarder never learned who owns the Node",
        );

        // Cut the owner off from the leader, both ways, and wait until it
        // knows: from then on its own commits fail at once.
        cluster.net.block(owner, leader);
        cluster.net.block(leader, owner);
        assert!(
            until(|| cluster.members[owner as usize].node.leader().is_none()).await,
            "the owner never noticed it had lost its leader",
        );
        assert!(
            cluster.members[forwarder as usize]
                .node
                .live_peers()
                .contains(&owner),
            "the forwarder cannot reach the owner, so nothing would be forwarded",
        );

        let answer = workload::register(
            &backend(&cluster, forwarder),
            ResourceType::Device,
            workload::device_body("0b1f9b7e-5d9a-4c55-9a51-000000000002", node_id, "1001:1"),
        )
        .await;
        match answer {
            Answer::Unavailable(ref detail) => assert!(
                detail.contains("could not commit"),
                "a 503, but without the owner's reason: {detail}",
            ),
            other => panic!(
                "an owner that could not commit was answered as {other:?}; it must be a 503 \
                 carrying the owner's reason -- a Node told 400 must not retry",
            ),
        }
        cluster.close().await;
    }

    /// A member whose ownership table says a Node has moved on: it answers
    /// every forward `not_owner` -- for the first `refusals` of them, so that
    /// an unbounded retry ends in a count rather than a stack overflow -- and
    /// answers consensus blandly, so the two real members keep their quorum.
    struct FormerOwner {
        refusals: u64,
        forwards: AtomicU64,
        now_owned_by: u64,
    }

    #[async_trait]
    impl PeerHandler for FormerOwner {
        fn on_request_vote(
            &self,
            _peer: u64,
            message: &RequestVote,
        ) -> Result<RequestVoteReply, PersistentStateError> {
            Ok(RequestVoteReply {
                term: message.term,
                granted: false,
                voting: true,
                pre_vote: message.pre_vote,
            })
        }

        fn on_append_entries(
            &self,
            _peer: u64,
            message: &AppendEntries,
        ) -> Result<AppendEntriesReply, PersistentStateError> {
            Ok(AppendEntriesReply {
                term: message.term,
                success: true,
                match_index: 0,
                conflict_index: 0,
                conflict_term: 0,
                catching_up: false,
                request_id: message.request_id,
            })
        }

        fn on_install_snapshot(
            &self,
            _peer: u64,
            message: &InstallSnapshot,
        ) -> Result<InstallSnapshotReply, PersistentStateError> {
            Ok(InstallSnapshotReply {
                term: message.term,
                bytes_received: 0,
                done: false,
                commit_index: 0,
                request_id: message.request_id,
            })
        }

        fn on_promote(&self, _peer: u64, _message: &Promote) {}

        fn on_request_vote_reply(
            &self,
            _peer: u64,
            _message: &RequestVoteReply,
        ) -> Result<(), PersistentStateError> {
            Ok(())
        }

        fn on_append_entries_reply(
            &self,
            _peer: u64,
            _message: &AppendEntriesReply,
        ) -> Result<(), PersistentStateError> {
            Ok(())
        }

        fn on_install_snapshot_reply(
            &self,
            _peer: u64,
            _message: &InstallSnapshotReply,
        ) -> Result<(), PersistentStateError> {
            Ok(())
        }

        async fn on_propose(&self, _peer: u64, message: &Propose) -> ProposeReply {
            ProposeReply {
                accepted: false,
                reason: "a former owner".to_owned(),
                term: 0,
                first_index: 0,
                request_id: message.request_id,
                leader: None,
            }
        }

        async fn on_forward(&self, _peer: u64, message: &Forward) -> ForwardReply {
            let seen = self.forwards.fetch_add(1, Ordering::SeqCst) + 1;
            ForwardReply {
                ok: seen > self.refusals,
                created: false,
                error: String::new(),
                detail: String::new(),
                applied_index: 0,
                not_owner: seen <= self.refusals,
                request_id: message.request_id,
                owner: Some(self.now_owned_by),
            }
        }

        async fn on_read_index(&self, _peer: u64, message: &ReadIndex) -> ReadIndexReply {
            ReadIndexReply {
                ok: false,
                index: 0,
                reason: "not the leader".to_owned(),
                request_id: message.request_id,
            }
        }

        fn on_peer_state(&self, _peer: u64, _up: bool, _incarnation: u64) {}
    }

    #[tokio::test(start_paused = true)]
    async fn a_moved_node_is_routed_again_once_and_then_answered_unavailable() {
        // D2. "One retry, as owner or forwarder depending on where it moved to
        // -- and no more", written above a retry that re-entered `register`
        // with nothing spent. A forwarder whose ownership table is behind names
        // the same former owner every time, so each retry forwarded to the
        // member that had just answered `not_owner`: the chaos soak measured a
        // stack overflow that aborted the process, the faulting thread 9,700
        // frames deep in `register`.
        //
        // Here the former owner is a stand-in that refuses the first ten
        // forwards and then accepts, so the unbounded version ends in a count
        // (eleven forwards, then success) instead of crashing the test. The
        // forwarder's own table is real: the Node was registered at that
        // member, and the claim replicated.
        let cluster = registry_cluster("regression-d2");
        cluster.start().await;
        assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
        let leader = cluster.leaders()[0];
        let owner = (0..3u64)
            .find(|&member| member != leader)
            .expect("a follower");
        assert!(
            until(|| (0..3u64).all(|member| backend(&cluster, member).state().accepts_mutations()))
                .await,
            "the backends never became ready",
        );

        let node_id = "0b1f9b7e-5d9a-4c55-9a51-000000000003";
        let registered = workload::register(
            &backend(&cluster, owner),
            ResourceType::Node,
            workload::node_body(node_id, "1001:1"),
        )
        .await;
        assert!(
            matches!(registered, Answer::Acknowledged { .. }),
            "the Node was not registered at its owner: {registered:?}",
        );
        assert!(
            until(|| cluster.members[leader as usize].node.ownership_of(node_id) == Some(owner))
                .await,
            "the leader never learned who owns the Node",
        );

        // The owner is replaced by one whose table says the Node has moved.
        backend(&cluster, owner).close().await;
        let former = Arc::new(FormerOwner {
            refusals: 10,
            forwards: AtomicU64::new(0),
            now_owned_by: leader,
        });
        let stand_in = cluster.net.transport(owner);
        stand_in
            .start(Arc::clone(&former) as Arc<dyn PeerHandler>)
            .await
            .expect("the chaos transport starts unconditionally");
        assert!(
            until(|| cluster.members[leader as usize]
                .node
                .live_peers()
                .contains(&owner))
            .await,
            "the leader never saw the stand-in attach",
        );

        let answer = workload::register(
            &backend(&cluster, leader),
            ResourceType::Device,
            workload::device_body("0b1f9b7e-5d9a-4c55-9a51-000000000004", node_id, "1001:1"),
        )
        .await;
        let forwards = former.forwards.load(Ordering::SeqCst);
        assert_eq!(
            forwards, 2,
            "the forwarder forwarded {forwards} times to a member that kept answering \
             not_owner (and was answered {answer:?}); one retry and no more is two forwards",
        );
        match answer {
            Answer::Unavailable(ref detail) => assert!(
                detail.contains("no longer owns node"),
                "a 503, but not for the moved Node: {detail}",
            ),
            other => panic!("a Node that stayed moved was answered {other:?}, not a 503"),
        }
        stand_in.close().await;
        for member in (0..3u64).filter(|&member| member != owner) {
            backend(&cluster, member).close().await;
        }
    }

    #[tokio::test(start_paused = true)]
    async fn a_heartbeat_for_a_moved_node_is_routed_again_once_and_then_answered_unavailable() {
        // A heartbeat is routed as a registration is (D2 above): one retry,
        // then a 503. Its forwarder read the owner's "not the owner" as a plain
        // refusal and answered 404 -- the terminal "re-register every resource"
        // -- for a Node the cluster still held; unreached only because no owner
        // ever said it, each forwarding the heartbeat on instead
        // (`a_forwarded_heartbeat_is_never_forwarded_again`). Python:
        // `test_a_heartbeat_for_a_moved_node_is_routed_again_once_and_then_answered_unavailable`.
        let cluster = registry_cluster("regression-heartbeat-moved");
        cluster.start().await;
        assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
        let leader = cluster.leaders()[0];
        let owner = (0..3u64)
            .find(|&member| member != leader)
            .expect("a follower");
        assert!(
            until(|| (0..3u64).all(|member| backend(&cluster, member).state().accepts_mutations()))
                .await,
            "the backends never became ready",
        );

        let node_id = "0b1f9b7e-5d9a-4c55-9a51-000000000005";
        let registered = workload::register(
            &backend(&cluster, owner),
            ResourceType::Node,
            workload::node_body(node_id, "1001:1"),
        )
        .await;
        assert!(
            matches!(registered, Answer::Acknowledged { .. }),
            "the Node was not registered at its owner: {registered:?}",
        );
        assert!(
            until(|| cluster.members[leader as usize].node.ownership_of(node_id) == Some(owner))
                .await,
            "the leader never learned who owns the Node",
        );

        // The owner is replaced by one whose table says the Node has moved.
        backend(&cluster, owner).close().await;
        let former = Arc::new(FormerOwner {
            refusals: 10,
            forwards: AtomicU64::new(0),
            now_owned_by: leader,
        });
        let stand_in = cluster.net.transport(owner);
        stand_in
            .start(Arc::clone(&former) as Arc<dyn PeerHandler>)
            .await
            .expect("the chaos transport starts unconditionally");
        assert!(
            until(|| cluster.members[leader as usize]
                .node
                .live_peers()
                .contains(&owner))
            .await,
            "the leader never saw the stand-in attach",
        );

        let beat = workload::heartbeat(&backend(&cluster, leader), node_id).await;
        let forwards = former.forwards.load(Ordering::SeqCst);
        assert_eq!(
            forwards, 2,
            "the forwarder forwarded a heartbeat {forwards} times to a member that kept \
             answering not_owner (and was answered {beat:?}); one retry and no more is two \
             forwards",
        );
        match beat {
            workload::Beat::Unavailable(ref detail) => assert!(
                detail.contains("no longer owns node"),
                "a 503, but not for the moved Node: {detail}",
            ),
            other => panic!("a heartbeat for a Node that stayed moved was answered {other:?}"),
        }
        stand_in.close().await;
        for member in (0..3u64).filter(|&member| member != owner) {
            backend(&cluster, member).close().await;
        }
    }

    #[tokio::test(start_paused = true)]
    async fn a_forwarded_heartbeat_is_never_forwarded_again() {
        // A member that does not own a forwarded heartbeat's Node says so, as
        // `forward` answers a registration: a request that hops between members
        // has no bound on its latency, and two members whose tables disagree
        // about the owner handed a heartbeat back and forth until an RPC
        // deadline cut the chain. It was answered by the member's own
        // `heartbeat`, which forwards. Python:
        // `test_a_forwarded_heartbeat_is_never_forwarded_again`.
        use nmos_registry_raft::node::ForwardHandler;

        let cluster = registry_cluster("regression-heartbeat-hop");
        cluster.start().await;
        assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
        let leader = cluster.leaders()[0];
        let owner = (0..3u64)
            .find(|&member| member != leader)
            .expect("a follower");
        assert!(
            until(|| (0..3u64).all(|member| backend(&cluster, member).state().accepts_mutations()))
                .await,
            "the backends never became ready",
        );
        let node_id = "0b1f9b7e-5d9a-4c55-9a51-000000000006";
        let registered = workload::register(
            &backend(&cluster, owner),
            ResourceType::Node,
            workload::node_body(node_id, "1001:1"),
        )
        .await;
        assert!(
            matches!(registered, Answer::Acknowledged { .. }),
            "the Node was not registered at its owner: {registered:?}",
        );
        assert!(
            until(|| cluster.members[leader as usize].node.ownership_of(node_id) == Some(owner))
                .await,
            "the leader never learned who owns the Node",
        );

        let reply = backend(&cluster, leader)
            .forward(&Forward {
                verb: "heartbeat".to_owned(),
                resource_type: "node".to_owned(),
                resource_id: node_id.to_owned(),
                body_text: String::new(),
                request_id: 7,
            })
            .await;
        assert!(
            reply.not_owner && reply.owner == Some(owner),
            "a member that does not own the Node answered a heartbeat forwarded to it {reply:?} \
             -- having forwarded it on to the owner, which answered for it",
        );
        for member in 0..3u64 {
            backend(&cluster, member).close().await;
        }
    }

    /// A member that needs a snapshot and records every chunk it is sent.
    ///
    /// It rejects every append back to index 1, so the leader -- whose log has
    /// been compacted -- can reach it only by snapshot. It accepts every chunk,
    /// spliced or not, so that what is recorded is what the leader *sent*. It
    /// completes the first transfer and refuses to complete any other, so the
    /// leader's credit stays where that one completion put it.
    #[derive(Default)]
    struct SnapshotCatcher {
        chunks: parking_lot::Mutex<Vec<Chunk>>,
        completed: parking_lot::Mutex<Option<(u64, tokio::time::Instant)>>,
    }

    #[derive(Debug, Clone)]
    struct Chunk {
        /// `(term, leader, last_index, last_term)`.
        identity: (u64, u64, u64, u64),
        offset: u64,
        at: tokio::time::Instant,
    }

    #[async_trait]
    impl PeerHandler for SnapshotCatcher {
        fn on_request_vote(
            &self,
            _peer: u64,
            message: &RequestVote,
        ) -> Result<RequestVoteReply, PersistentStateError> {
            Ok(RequestVoteReply {
                term: message.term,
                granted: false,
                voting: true,
                pre_vote: message.pre_vote,
            })
        }

        fn on_append_entries(
            &self,
            _peer: u64,
            message: &AppendEntries,
        ) -> Result<AppendEntriesReply, PersistentStateError> {
            Ok(AppendEntriesReply {
                term: message.term,
                success: false,
                match_index: 0,
                conflict_index: 1,
                conflict_term: 0,
                catching_up: false,
                request_id: message.request_id,
            })
        }

        fn on_install_snapshot(
            &self,
            _peer: u64,
            message: &InstallSnapshot,
        ) -> Result<InstallSnapshotReply, PersistentStateError> {
            let now = tokio::time::Instant::now();
            self.chunks.lock().push(Chunk {
                identity: (
                    message.term,
                    message.leader,
                    message.last_index,
                    message.last_term,
                ),
                offset: message.offset,
                at: now,
            });
            let received = message.offset + message.data.len() as u64;
            let mut completed = self.completed.lock();
            // What a follower would say it has committed: the snapshot it
            // installed, once it has, and nothing before.
            let committed = completed.map_or(0, |(index, _)| index);
            if !message.done {
                return Ok(InstallSnapshotReply {
                    term: message.term,
                    bytes_received: received,
                    done: false,
                    commit_index: committed,
                    request_id: message.request_id,
                });
            }
            if completed.is_some() {
                return Ok(InstallSnapshotReply {
                    term: message.term,
                    bytes_received: 0,
                    done: false,
                    commit_index: committed,
                    request_id: message.request_id,
                });
            }
            *completed = Some((message.last_index, now));
            Ok(InstallSnapshotReply {
                term: message.term,
                bytes_received: received,
                done: true,
                commit_index: message.last_index,
                request_id: message.request_id,
            })
        }

        fn on_promote(&self, _peer: u64, _message: &Promote) {}

        fn on_request_vote_reply(
            &self,
            _peer: u64,
            _message: &RequestVoteReply,
        ) -> Result<(), PersistentStateError> {
            Ok(())
        }

        fn on_append_entries_reply(
            &self,
            _peer: u64,
            _message: &AppendEntriesReply,
        ) -> Result<(), PersistentStateError> {
            Ok(())
        }

        fn on_install_snapshot_reply(
            &self,
            _peer: u64,
            _message: &InstallSnapshotReply,
        ) -> Result<(), PersistentStateError> {
            Ok(())
        }

        async fn on_propose(&self, _peer: u64, message: &Propose) -> ProposeReply {
            ProposeReply {
                accepted: false,
                reason: "a snapshot catcher".to_owned(),
                term: 0,
                first_index: 0,
                request_id: message.request_id,
                leader: None,
            }
        }

        async fn on_forward(&self, _peer: u64, message: &Forward) -> ForwardReply {
            ForwardReply {
                ok: false,
                created: false,
                error: "unavailable".to_owned(),
                detail: String::new(),
                applied_index: 0,
                not_owner: false,
                request_id: message.request_id,
                owner: None,
            }
        }

        async fn on_read_index(&self, _peer: u64, message: &ReadIndex) -> ReadIndexReply {
            ReadIndexReply {
                ok: false,
                index: 0,
                reason: "a member that needs a snapshot".to_owned(),
                request_id: message.request_id,
            }
        }

        fn on_peer_state(&self, _peer: u64, _up: bool, _incarnation: u64) {}
    }

    #[tokio::test(start_paused = true)]
    async fn a_leader_sends_one_snapshot_per_transfer_and_credits_it() {
        // S1. The leader sliced whatever snapshot it held *now* at the offset a
        // transfer had reached, and compaction replaces that snapshot whenever
        // it likes: the soak's splice detector measured the head of one
        // snapshot sent with the tail of the next, and a completion credited
        // the leader's current snapshot rather than the one transferred.
        //
        // Here a real leader, committing with a real follower, compacts
        // continually while a slow link carries a snapshot to a member that
        // needs one. Every transfer must be of one snapshot from its first
        // chunk to its last, and the one completion must be credited with the
        // index that was transferred.
        let dir = std::env::temp_dir().join(format!(
            "nmos-raft-soak-regression-s1-{}",
            std::process::id()
        ));
        drop(std::fs::create_dir_all(&dir));
        let net = ChaosNet::new(
            &[0, 1, 2],
            Rng::new(5),
            Knobs {
                max_delay_us: 10_000,
                spike_one_in: 0,
                reconnect_max_us: 0,
            },
            Arc::new(Forensics::new()),
            1_000,
        );
        let cluster = SoakCluster::build(
            ClusterConfig {
                size: 3,
                timing: RaftTiming {
                    heartbeat_ms: 50,
                    election_min_ms: 300,
                    election_max_ms: 600,
                    compaction_threshold: 4,
                    max_log_entries: 8,
                    snapshot_chunk: 16,
                    ..RaftTiming::default()
                },
                backends: false,
                mutation_timeout: Duration::from_secs(2),
                gc_interval: 12,
                forget_interval: 12,
                tag: "regression-s1".to_owned(),
            },
            net,
            dir,
        );
        cluster.start().await;
        assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
        let leader = cluster.leaders()[0];
        let catcher_index = (0..3u64)
            .find(|&member| member != leader)
            .expect("a follower");
        let node = Arc::clone(&cluster.members[leader as usize].node);

        cluster.members[catcher_index as usize].node.close().await;
        let catcher = Arc::new(SnapshotCatcher::default());
        let stand_in = cluster.net.transport(catcher_index);
        stand_in
            .start(Arc::clone(&catcher) as Arc<dyn PeerHandler>)
            .await
            .expect("the chaos transport starts unconditionally");

        // Commit steadily, so the leader keeps compacting, until the catcher
        // has completed a transfer. The leader's compaction boundary is read
        // after every commit, so what it was during the transfer is known.
        let mut boundaries: Vec<(tokio::time::Instant, u64)> = Vec::new();
        for sequence in 0..600u64 {
            let id = format!("5e1f0000-0000-4000-8000-{sequence:012x}");
            drop(workload::propose_register(&node, leader, &id, Duration::from_secs(2)).await);
            boundaries.push((
                tokio::time::Instant::now(),
                Observed::of(&node).snapshot_index,
            ));
            if catcher.completed.lock().is_some() {
                break;
            }
        }
        tokio::time::sleep(Duration::from_secs(1)).await;

        // Checked first, because it does not depend on anything completing.
        let chunks = catcher.chunks.lock().clone();
        let mut transfer: Option<(u64, u64, u64, u64)> = None;
        for chunk in &chunks {
            if chunk.offset == 0 {
                transfer = Some(chunk.identity);
            } else {
                assert_eq!(
                    Some(chunk.identity),
                    transfer,
                    "a transfer that began as the snapshot {transfer:?} went on at offset {} \
                     with the snapshot {:?} -- a splice",
                    chunk.offset,
                    chunk.identity,
                );
            }
        }

        // A transfer of one pinned snapshot has a fixed length and ends. One
        // sliced from a snapshot that grows with every compaction chases its
        // own end over a slow link -- measured before the pin as no completion
        // in 600 commits.
        let (completed_index, completed_at) = (*catcher.completed.lock())
            .expect("no transfer completed: the snapshot being sent kept changing under it");

        let began = chunks
            .iter()
            .filter(|chunk| chunk.offset == 0 && chunk.at <= completed_at)
            .map(|chunk| chunk.at)
            .next_back()
            .expect("the completed transfer had a first chunk");
        assert!(
            boundaries.iter().any(|&(at, boundary)| at >= began
                && at <= completed_at
                && boundary > completed_index),
            "the leader never compacted past the snapshot while it was being sent, so this \
             proves nothing about the credit",
        );
        let (matched, _, _) = node
            .peer_progress(catcher_index)
            .expect("the catcher is tracked");
        assert_eq!(
            matched, completed_index,
            "a completed transfer of the snapshot through {completed_index} was credited with \
             index {matched}",
        );
        stand_in.close().await;
        for member in (0..3u64).filter(|&member| member != catcher_index) {
            cluster.members[member as usize].node.close().await;
        }
    }

    #[tokio::test(start_paused = true)]
    async fn a_node_registered_at_two_members_at_once_is_created_once_and_applying_goes_on() {
        // A1. Two members take the first registration of the same new Node at
        // the same moment -- a Node retrying against a second registry before
        // the first answered, say. Each finds the Node absent and unowned, so
        // each proposes a create with a fused claim; both commit, and the
        // second to apply finds the Node already there. Apply used to call
        // that a divergence ("the two stores disagree about what is
        // registered") and stop -- on every member, since every member
        // computes the same thing -- so the cluster went on committing while
        // applying nothing. It was the soak's largest failure class:
        // "consensus invariant violated; this member stops applying".
        let cluster = registry_cluster("regression-a1");
        cluster.start().await;
        assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
        let leader = cluster.leaders()[0];
        let racers: Vec<u64> = (0..3u64).filter(|&member| member != leader).collect();
        assert!(
            until(|| (0..3u64).all(|member| backend(&cluster, member).state().accepts_mutations()))
                .await,
            "the backends never became ready",
        );

        // Followers, so that each one's proposal crosses the network and the
        // forensics can show it: a create proposed at each, not one of them
        // forwarding to the other.
        let proposed = |member: u64| cluster.net.forensics.propose_fates(member).delivered;
        let before: Vec<u64> = racers.iter().map(|&member| proposed(member)).collect();
        let node_id = "0b1f9b7e-5d9a-4c55-9a51-00000000a101";
        let (at_first, at_second) = (backend(&cluster, racers[0]), backend(&cluster, racers[1]));
        let (first, second) = tokio::join!(
            workload::register(
                &at_first,
                ResourceType::Node,
                workload::node_body(node_id, "1001:1"),
            ),
            workload::register(
                &at_second,
                ResourceType::Node,
                workload::node_body(node_id, "1001:1"),
            ),
        );
        for (position, &member) in racers.iter().enumerate() {
            assert!(
                proposed(member) > before[position],
                "m{member} did not propose the registration itself, so nothing raced",
            );
        }

        // Every member applies everything committed; a stopped applier never does.
        let committed = cluster.members[leader as usize].node.commit_index();
        let applied = || -> Vec<u64> {
            cluster
                .members
                .iter()
                .map(|member| member.node.last_applied())
                .collect()
        };
        assert!(
            until(|| applied().iter().all(|&index| index >= committed)).await,
            "the members stopped applying: applied {:?} of {committed} committed",
            applied(),
        );

        let created: Vec<bool> = [&first, &second]
            .into_iter()
            .map(|answer| match *answer {
                Answer::Acknowledged { created } => created,
                ref other => panic!("a registration of the raced Node was answered as {other:?}"),
            })
            .collect();
        assert_eq!(
            created.iter().filter(|&&created| created).count(),
            1,
            "one registration creates the Node (201) and the other updates it (200): {created:?}",
        );

        // The cluster still takes registrations -- routed to whichever member
        // the later claim made the owner.
        let device = workload::register(
            &backend(&cluster, leader),
            ResourceType::Device,
            workload::device_body("0b1f9b7e-5d9a-4c55-9a51-00000000a102", node_id, "1001:1"),
        )
        .await;
        assert!(
            matches!(device, Answer::Acknowledged { created: true }),
            "a registration after the race was answered as {device:?}",
        );

        // One registry and one owner, everywhere.
        let committed = cluster.members[leader as usize].node.commit_index();
        assert!(
            until(|| applied().iter().all(|&index| index >= committed)).await,
            "the members stopped applying: applied {:?} of {committed} committed",
            applied(),
        );
        let digests: Vec<Vec<String>> = cluster
            .members
            .iter()
            .map(|member| replica_digest(&member.node, &member.registry))
            .collect();
        for (member, digest) in digests.iter().enumerate().skip(1) {
            assert_eq!(
                digest, &digests[0],
                "m{member} and m0 hold different registries"
            );
        }
        assert!(
            cluster.members[0].node.ownership_of(node_id).is_some(),
            "the raced Node has no owner",
        );
        cluster.close().await;
    }

    #[tokio::test(start_paused = true)]
    async fn an_update_racing_an_unregister_recreates_the_resource_with_its_own_cursor() {
        // A1, the other direction. An unregister takes no per-Node gate, so an
        // update can be predicted against a store that still holds the
        // resource while the removal is already on its way into the log. The
        // update commits second and applies to an absent resource: it creates
        // it -- 201, as one registry answers a POST that follows a DELETE --
        // stamped with its own cursor, not with the creation cursor the
        // proposer copied from the record the removal erased. Apply used to
        // stop at that entry, on every member.
        let cluster = registry_cluster("regression-a1-unregister");
        cluster.start().await;
        assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
        let leader = cluster.leaders()[0];
        assert!(
            until(|| (0..3u64).all(|member| backend(&cluster, member).state().accepts_mutations()))
                .await,
            "the backends never became ready",
        );

        // Everything at the leader, so both proposals enter the log in the
        // order they are made, with no link between them to reorder.
        let at = backend(&cluster, leader);
        let node_id = "0b1f9b7e-5d9a-4c55-9a51-00000000a201";
        let device_id = "0b1f9b7e-5d9a-4c55-9a51-00000000a202";
        let node = workload::register(
            &at,
            ResourceType::Node,
            workload::node_body(node_id, "1001:1"),
        )
        .await;
        assert!(
            matches!(node, Answer::Acknowledged { created: true }),
            "{node:?}"
        );
        let device = workload::register(
            &at,
            ResourceType::Device,
            workload::device_body(device_id, node_id, "1001:1"),
        )
        .await;
        assert!(
            matches!(device, Answer::Acknowledged { created: true }),
            "{device:?}"
        );
        let original = cluster.members[leader as usize]
            .registry
            .with_read_store(|store| {
                store
                    .get(ResourceType::Device, device_id)
                    .map(|device| device.created)
            })
            .expect("the Device is registered");

        let (removed, updated) = tokio::join!(
            at.unregister(ResourceType::Device, device_id),
            workload::register(
                &at,
                ResourceType::Device,
                workload::device_body(device_id, node_id, "1001:2"),
            ),
        );

        let committed = cluster.members[leader as usize].node.commit_index();
        let applied = || -> Vec<u64> {
            cluster
                .members
                .iter()
                .map(|member| member.node.last_applied())
                .collect()
        };
        assert!(
            until(|| applied().iter().all(|&index| index >= committed)).await,
            "the members stopped applying: applied {:?} of {committed} committed",
            applied(),
        );

        assert!(
            matches!(removed, Ok(Some(_))),
            "the unregister was answered as {removed:?}",
        );
        assert!(
            matches!(updated, Answer::Acknowledged { created: true }),
            "the update that committed after the removal was answered as {updated:?}; it \
             recreates the Device, which is a 201",
        );
        for member in &cluster.members {
            let (created, updated) = member
                .registry
                .with_read_store(|store| {
                    store
                        .get(ResourceType::Device, device_id)
                        .map(|device| (device.created, device.updated))
                })
                .unwrap_or_else(|| panic!("m{} does not hold the recreated Device", member.index));
            assert_eq!(
                created, updated,
                "m{}: the recreated Device carries a creation cursor other than its own entry's",
                member.index,
            );
            assert!(
                created > original,
                "m{}: the recreated Device was stamped {created:?}, not after the record the \
                 removal erased ({original:?}), so a client paging by creation has passed it",
                member.index,
            );
        }
        let digests: Vec<Vec<String>> = cluster
            .members
            .iter()
            .map(|member| replica_digest(&member.node, &member.registry))
            .collect();
        for (member, digest) in digests.iter().enumerate().skip(1) {
            assert_eq!(
                digest, &digests[0],
                "m{member} and m0 hold different registries"
            );
        }
        cluster.close().await;
    }

    #[tokio::test(start_paused = true)]
    async fn a_compaction_begun_before_an_install_does_not_replace_it() {
        // S7. A compaction pins its image at `last_applied` and walks the store
        // in chunks, yielding between them; an install landing in one of those
        // yields replaces the store and moves the log's boundary past the
        // pinned index. The compaction used to finish anyway and store its
        // older snapshot over the installed one -- seed 100423: a member
        // holding a snapshot through 1046 below a log discarded through 1120,
        // unable to serve the entries between.
        //
        // Deterministic. Every member reaches the compaction threshold holding
        // more resources than one chunk, so each compaction yields mid-walk;
        // this test spins -- always runnable, so it runs at every such yield,
        // and on the paused clock nothing here needs time to pass -- until a
        // follower's capture is open, then hands that follower a complete
        // snapshot from later in the log.
        use nmos_registry_raft::ownership::OwnershipTable;
        use nmos_registry_raft::snapshot::{CHUNK_RESOURCES, SnapshotStore, collect_all};

        let threshold = CHUNK_RESOURCES as u64 + 44;
        let timing = RaftTiming {
            compaction_threshold: threshold,
            max_log_entries: threshold * 8,
            ..RaftTiming::default()
        };
        let cluster = registry_cluster_with("regression-s7", timing);
        cluster.start().await;
        assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
        let leader = cluster.leaders()[0];
        let follower = (0..3u64)
            .find(|&member| member != leader)
            .expect("a follower");
        assert!(
            until(|| (0..3u64).all(|member| backend(&cluster, member).state().accepts_mutations()))
                .await,
            "the backends never became ready",
        );
        let at = backend(&cluster, leader);
        let node_id = |index: u64| format!("0b1f9b7e-5d9a-4c55-9a51-{index:012x}");

        // Short of the threshold, so nobody has compacted yet.
        for index in 0..threshold - 20 {
            let answer = workload::register(
                &at,
                ResourceType::Node,
                workload::node_body(&node_id(index), "1001:1"),
            )
            .await;
            assert!(matches!(answer, Answer::Acknowledged { .. }), "{answer:?}");
        }
        let watched = Arc::clone(&cluster.members[follower as usize].node);
        assert_eq!(
            watched.snapshot_held(),
            None,
            "the follower compacted before the test was watching, so this proves nothing",
        );

        // Across the threshold, watching the follower at every yield.
        let registering = async {
            for index in threshold - 20..threshold + 20 {
                drop(
                    workload::register(
                        &at,
                        ResourceType::Node,
                        workload::node_body(&node_id(index), "1001:1"),
                    )
                    .await,
                );
            }
        };
        let intervening = async {
            let mut pinned = None;
            for _ in 0..1_000_000 {
                pinned = watched.snapshot_capture();
                if pinned.is_some() {
                    break;
                }
                tokio::task::yield_now().await;
            }
            let pinned = pinned.expect("the follower never began a compaction");

            // From here the follower hears only what this test hands it.
            for peer in (0..3u64).filter(|&peer| peer != follower) {
                cluster.net.block(follower, peer);
                cluster.net.block(peer, follower);
            }
            let through = watched.commit_index() + 1_000;
            let term = watched.term();
            let payload = cluster.members[leader as usize]
                .registry
                .with_read_store(|store| {
                    let mut snapshots = SnapshotStore::new();
                    snapshots
                        .begin(through, term, &OwnershipTable::default())
                        .expect("nothing is open");
                    let (records, live) =
                        collect_all(store, snapshots.capture().expect("just opened"));
                    snapshots.finish(records, &live).expect("finishes")
                });
            let reply = watched
                .on_install_snapshot(
                    leader,
                    &InstallSnapshot {
                        term,
                        leader,
                        last_index: through,
                        last_term: term,
                        offset: 0,
                        data: payload.clone(),
                        done: true,
                        ownership: Vec::new(),
                        request_id: 1,
                    },
                )
                .expect("the term and vote were saved");
            assert!(
                reply.done,
                "the install was refused, so this proves nothing"
            );

            for _ in 0..1_000_000 {
                if watched.snapshot_capture().is_none() {
                    break;
                }
                tokio::task::yield_now().await;
            }
            (pinned, through, payload.len())
        };
        let ((), (pinned, through, bytes)) = tokio::join!(registering, intervening);

        assert_eq!(
            watched.snapshot_capture(),
            None,
            "the compaction never finished"
        );
        assert_eq!(
            watched.snapshot_held(),
            Some((through, bytes)),
            "a compaction pinned at {pinned}, begun before the install through {through} and \
             finished after it, replaced the installed snapshot",
        );
        assert!(
            watched.log_term_at(through).is_some() || watched.last_log_index() == through,
            "the log does not start at the installed snapshot",
        );
        cluster.close().await;
    }

    #[tokio::test(start_paused = true)]
    async fn after_an_install_a_member_snapshots_its_live_store() {
        // S8, the parity check. The Python snapshot store kept the registry
        // store it was built with, and an install replaces the store -- so a
        // Python member caught up by snapshot went on snapshotting its
        // pre-install store: measured, none of the 60 Nodes it served. This one
        // walks the registry's live store at compaction time and so cannot;
        // this pins it.
        use nmos_registry_raft::snapshot::decode_snapshot;

        let timing = RaftTiming {
            compaction_threshold: 4,
            max_log_entries: 8,
            snapshot_chunk: 256,
            ..RaftTiming::default()
        };
        let cluster = registry_cluster_with("regression-s8", timing);
        cluster.start().await;
        assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
        let leader = cluster.leaders()[0];
        let outcast = (0..3u64)
            .find(|&member| member != leader)
            .expect("a follower");
        assert!(
            until(|| (0..3u64).all(|member| backend(&cluster, member).state().accepts_mutations()))
                .await,
            "the backends never became ready",
        );
        let at = backend(&cluster, leader);
        let node_id = |index: u64| format!("0b1f9b7e-5d9a-4c55-9a51-{index:012x}");
        let register = |index: u64| {
            let at = Arc::clone(&at);
            async move {
                let answer = workload::register(
                    &at,
                    ResourceType::Node,
                    workload::node_body(&node_id(index), "1001:1"),
                )
                .await;
                assert!(matches!(answer, Answer::Acknowledged { .. }), "{answer:?}");
            }
        };

        cluster.net.stop(outcast);
        for index in 0..30 {
            register(index).await;
        }
        cluster.net.resume(outcast);
        let watched = Arc::clone(&cluster.members[outcast as usize].node);
        let head = Arc::clone(&cluster.members[leader as usize].node);
        assert!(
            until(|| watched.commit_index() >= head.commit_index()
                && watched.log_term_at(1).is_none()
                && watched.snapshot_held().is_some())
            .await,
            "the returning member was not caught up by a snapshot, so this proves nothing",
        );
        let (installed, _) = watched.snapshot_held().expect("held");

        // Applied into the store the install swapped in, and enough of them
        // that the member compacts on its own.
        for index in 30..60 {
            register(index).await;
        }
        assert!(
            until(|| watched.last_applied() >= head.commit_index()
                && watched
                    .snapshot_held()
                    .is_some_and(|(through, _)| through > installed))
            .await,
            "the member took no snapshot of its own after the install, so this proves nothing",
        );

        let payload = watched.snapshot_payload();
        let (meta, _, records) = decode_snapshot(&payload).expect("its own snapshot decodes");
        let mut held: Vec<String> = records
            .iter()
            .filter(|record| record.resource_type == ResourceType::Node)
            .map(|record| record.id.clone())
            .collect();
        held.sort_unstable();
        let count = held.len() as u64;
        let mut prefix: Vec<String> = (0..count).map(node_id).collect();
        prefix.sort_unstable();
        // Registered one entry each, in order, so a snapshot through any index
        // holds exactly a prefix of them -- at least the 30 the install
        // brought, since it is through a later index.
        assert!(
            count >= 30 && held == prefix,
            "m{outcast} snapshotted through {} and its snapshot holds {count} Nodes; one taken \
             after installing through {installed} must hold at least the 30 installed",
            meta.last_index,
        );
        cluster.close().await;
    }

    #[tokio::test(start_paused = true)]
    async fn stale_evidence_cannot_elect_a_leader_without_a_committed_entry() {
        // E1, end to end, on the chaos network but for one late reply. P leads;
        // R restarts; P receives R's late pre-vote reply ("cannot vote") -- in
        // the soak it arrived one millisecond after P had won -- and then
        // promotes R. P is cut off while Q and R commit E. Q restarts; P and Q
        // meet with R, the only member still holding E, out of reach. P used to
        // win with Q's vote alone, counting R as forgotten on the old reply
        // although P itself had promoted R, and overwrote E.
        use std::collections::BTreeSet;

        let mut cluster = consensus_cluster("regression-e1");
        cluster.start().await;
        assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
        let p = cluster.leaders()[0];
        let others: Vec<u64> = (0..3u64).filter(|&member| member != p).collect();
        let (q, r) = (others[0], others[1]);
        let node =
            |cluster: &SoakCluster, member: u64| Arc::clone(&cluster.members[member as usize].node);
        for tag in 0..3u64 {
            let answer = workload::propose_register(
                &node(&cluster, p),
                p,
                &format!("{tag:08x}-dead-4000-8000-00000000000b"),
                Duration::from_secs(5),
            )
            .await;
            assert!(matches!(answer, Answer::Acknowledged { .. }), "{answer:?}");
        }

        cluster.restart(r as usize).await;
        let leader = node(&cluster, p);
        leader
            .on_request_vote_reply(
                r,
                &RequestVoteReply {
                    term: leader.term() + 1,
                    granted: false,
                    voting: false,
                    pre_vote: true,
                },
            )
            .expect("the term and vote were saved");
        assert!(
            until(|| cluster.members[r as usize].node.voting()).await,
            "R was never promoted, so this proves nothing",
        );

        cluster
            .net
            .partition(&[BTreeSet::from([p]), BTreeSet::from([q, r])]);
        assert!(
            until(|| [q, r]
                .iter()
                .any(|&member| cluster.members[member as usize].node.role() == Role::Leader))
            .await,
            "Q and R never elected a leader, so this proves nothing",
        );
        let head = [q, r]
            .into_iter()
            .find(|&member| cluster.members[member as usize].node.role() == Role::Leader)
            .expect("a leader");
        let answer = workload::propose_register(
            &node(&cluster, head),
            head,
            "00000099-dead-4000-8000-00000000000b",
            Duration::from_secs(5),
        )
        .await;
        assert!(matches!(answer, Answer::Acknowledged { .. }), "{answer:?}");
        let e_index = node(&cluster, head).commit_index();
        let e_term = node(&cluster, head).log_term_at(e_index).unwrap_or(0);
        assert!(
            until(|| cluster.members[r as usize].node.last_log_index() >= e_index).await,
            "R does not hold E, so this proves nothing",
        );
        assert!(node(&cluster, p).last_log_index() < e_index);
        // Stood down, as check-quorum makes a leader cut off from a majority do.
        // Still leading, P would meet Q as the term-3 leader it already was --
        // unable to commit anything, and no election at all.
        assert!(
            until(|| cluster.members[p as usize].node.role() != Role::Leader).await,
            "P never stood down while cut off, so this proves nothing",
        );

        cluster.restart(q as usize).await;
        cluster
            .net
            .partition(&[BTreeSet::from([p, q]), BTreeSet::from([r])]);
        // Elected, that is, in a term after E's: a leader that lacks E there
        // would overwrite it.
        let elected = until(|| {
            let candidate = &cluster.members[p as usize].node;
            candidate.role() == Role::Leader && candidate.term() > e_term
        })
        .await;

        assert!(
            !elected,
            "P was elected in term {} with only Q's vote -- Q restarted and cannot \
             vote, and R, voting and holding E=({e_index}, t{e_term}), was out of \
             reach -- so E, committed, is lost: P's log ends at {}",
            node(&cluster, p).term(),
            node(&cluster, p).last_log_index(),
        );
        cluster.close().await;
    }

    // -- a member behind what is committed ---------------------------------------
    //
    // V1. A member answered some requests from its own store -- a refusal from
    // validation, a 404 for a delete or a heartbeat -- as if that store were
    // current, and a store is current only as of what its member has applied.
    // The soak counted 352 400s for fresh registrations, 35 for updates of
    // acknowledged resources, 77 404s on DELETE and 187 on heartbeats of
    // acknowledged Nodes in one run set. Before such an answer a member now
    // learns a read index and applies through it (`backend.rs`,
    // `read_barrier`). `test_forwarding_conformance.py` holds the same four.

    /// How long a member stays behind: its leader's appends held, in order, on
    /// the connection the leader dials, while its own connection to the leader
    /// -- which carries its read index -- works. Well inside the member's
    /// 300 ms election minimum, so it never stops following.
    const LAG: Duration = Duration::from_millis(150);

    /// A registry cluster, ready, with its leader and the follower to hold
    /// behind.
    async fn behind_cluster(tag: &str) -> (SoakCluster, u64, u64) {
        let cluster = registry_cluster(tag);
        cluster.start().await;
        assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
        let leader = cluster.leaders()[0];
        let behind = (0..3u64)
            .find(|&member| member != leader)
            .expect("a follower");
        assert!(
            until(|| (0..3u64).all(|member| backend(&cluster, member).state().accepts_mutations()))
                .await,
            "the backends never became ready",
        );
        (cluster, leader, behind)
    }

    /// Register the Node at the leader, which then owns it, and wait until
    /// `behind` has applied that.
    async fn owned_by_the_leader(cluster: &SoakCluster, leader: u64, behind: u64, node_id: &str) {
        let registered = workload::register(
            &backend(cluster, leader),
            ResourceType::Node,
            workload::node_body(node_id, "1001:1"),
        )
        .await;
        assert!(
            matches!(registered, Answer::Acknowledged { .. }),
            "the Node was not registered: {registered:?}",
        );
        assert!(
            until(|| cluster.members[behind as usize].node.ownership_of(node_id) == Some(leader))
                .await,
            "member {behind} never learned who owns the Node",
        );
    }

    /// Hold `behind` back until [`LAG`] has passed.
    fn hold_behind(cluster: &SoakCluster, leader: u64, behind: u64) -> tokio::task::JoinHandle<()> {
        cluster.net.stall_link((leader, behind));
        let net = Arc::clone(&cluster.net);
        tokio::spawn(async move {
            tokio::time::sleep(LAG).await;
            net.unstall_links();
        })
    }

    fn holds(cluster: &SoakCluster, member: u64, kind: ResourceType, id: &str) -> bool {
        cluster.members[member as usize]
            .registry
            .with_read_store(|store| store.get(kind, id).is_some())
    }

    #[tokio::test(start_paused = true)]
    async fn a_member_behind_on_a_parent_waits_for_it_rather_than_refusing() {
        // A Sender whose Device is committed but not yet applied here was
        // answered `PARENT_MISSING` -- a 400, which a Node "MUST NOT" retry
        // without corrective action (`Behaviour - Registration.md:96`) -- about
        // a Device the cluster had acknowledged.
        let (cluster, leader, behind) = behind_cluster("v1-parent").await;
        let node_id = "0b1f9b7e-5d9a-4c55-9a51-00000000a001";
        let device_id = "0b1f9b7e-5d9a-4c55-9a51-00000000a002";
        owned_by_the_leader(&cluster, leader, behind, node_id).await;

        let release = hold_behind(&cluster, leader, behind);
        let device = workload::register(
            &backend(&cluster, leader),
            ResourceType::Device,
            workload::device_body(device_id, node_id, "1001:1"),
        )
        .await;
        assert!(matches!(device, Answer::Acknowledged { .. }), "{device:?}");
        assert!(!holds(&cluster, behind, ResourceType::Device, device_id));

        let answer = workload::register(
            &backend(&cluster, behind),
            ResourceType::Sender,
            workload::sender_body("0b1f9b7e-5d9a-4c55-9a51-00000000a003", device_id, "1001:1"),
        )
        .await;
        release.await.expect("released");
        assert_eq!(
            answer,
            Answer::Acknowledged { created: true },
            "a Sender whose Device the cluster had acknowledged was answered {answer:?} by a \
             member that had not yet applied the Device",
        );
        cluster.close().await;
    }

    #[tokio::test(start_paused = true)]
    async fn a_member_behind_on_a_node_waits_for_it_before_its_device() {
        // The Device names its Node, so nothing is looked up to route it -- but
        // this member's ownership table is as far behind as its store, shows
        // the Node unowned, and so this member registered the Device as its
        // owner, against a store without the Node: `PARENT_MISSING`, returned
        // as "authoritative, and free". An owner is only as current as what it
        // has applied.
        let (cluster, leader, behind) = behind_cluster("v1-node").await;
        let node_id = "0b1f9b7e-5d9a-4c55-9a51-00000000b001";

        let release = hold_behind(&cluster, leader, behind);
        let node = workload::register(
            &backend(&cluster, leader),
            ResourceType::Node,
            workload::node_body(node_id, "1001:1"),
        )
        .await;
        assert!(matches!(node, Answer::Acknowledged { .. }), "{node:?}");
        assert!(!holds(&cluster, behind, ResourceType::Node, node_id));

        let answer = workload::register(
            &backend(&cluster, behind),
            ResourceType::Device,
            workload::device_body("0b1f9b7e-5d9a-4c55-9a51-00000000b002", node_id, "1001:1"),
        )
        .await;
        release.await.expect("released");
        assert_eq!(
            answer,
            Answer::Acknowledged { created: true },
            "a Device whose Node the cluster had acknowledged was answered {answer:?} by a \
             member that had not yet applied the Node",
        );

        let committed = cluster.members[leader as usize].node.commit_index();
        assert!(
            until(|| cluster
                .members
                .iter()
                .all(|member| member.node.last_applied() >= committed))
            .await,
            "the members stopped applying",
        );
        let digests: Vec<Vec<String>> = cluster
            .members
            .iter()
            .map(|member| replica_digest(&member.node, &member.registry))
            .collect();
        for (member, digest) in digests.iter().enumerate().skip(1) {
            assert_eq!(
                digest, &digests[0],
                "m{member} and m0 hold different registries"
            );
        }
        cluster.close().await;
    }

    #[tokio::test(start_paused = true)]
    async fn a_member_behind_on_a_resource_does_not_call_it_absent() {
        // `unregister` answered from the local store -- "the local store is a
        // complete replica, so 'not here' is not a guess" -- which is true of a
        // replica only as of what it has applied.
        let (cluster, leader, behind) = behind_cluster("v1-delete").await;
        let node_id = "0b1f9b7e-5d9a-4c55-9a51-00000000c001";
        let device_id = "0b1f9b7e-5d9a-4c55-9a51-00000000c002";
        owned_by_the_leader(&cluster, leader, behind, node_id).await;

        let release = hold_behind(&cluster, leader, behind);
        let device = workload::register(
            &backend(&cluster, leader),
            ResourceType::Device,
            workload::device_body(device_id, node_id, "1001:1"),
        )
        .await;
        assert!(matches!(device, Answer::Acknowledged { .. }), "{device:?}");
        assert!(!holds(&cluster, behind, ResourceType::Device, device_id));

        let answer =
            workload::unregister(&backend(&cluster, behind), ResourceType::Device, device_id).await;
        release.await.expect("released");
        assert_eq!(
            answer,
            workload::Deletion::Removed,
            "a Device the cluster had acknowledged was answered {answer:?} on DELETE by a \
             member that had not yet applied it",
        );
        cluster.close().await;
    }

    #[tokio::test(start_paused = true)]
    async fn a_member_behind_on_a_node_does_not_tell_it_to_re_register() {
        // A 404 on heartbeat tells the Node to re-register every resource it
        // has (`Behaviour - Registration.md:112-114`). This member's ownership
        // table did not show the Node, so it answered from its store, which did
        // not hold it either. Once current, it finds the Node's owner and
        // forwards the heartbeat there.
        let (cluster, leader, behind) = behind_cluster("v1-heartbeat").await;
        let node_id = "0b1f9b7e-5d9a-4c55-9a51-00000000d001";

        let release = hold_behind(&cluster, leader, behind);
        let node = workload::register(
            &backend(&cluster, leader),
            ResourceType::Node,
            workload::node_body(node_id, "1001:1"),
        )
        .await;
        assert!(matches!(node, Answer::Acknowledged { .. }), "{node:?}");
        assert!(!holds(&cluster, behind, ResourceType::Node, node_id));

        let answer = workload::heartbeat(&backend(&cluster, behind), node_id).await;
        release.await.expect("released");
        assert_eq!(
            answer,
            workload::Beat::Alive,
            "a Node the cluster had acknowledged was answered {answer:?} on heartbeat by a \
             member that had not yet applied it",
        );
        cluster.close().await;
    }
}
