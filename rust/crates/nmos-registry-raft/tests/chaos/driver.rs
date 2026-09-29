// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! One seeded run: faults, client traffic, and the checks after every step.
//!
//! The shape is `ChurnDriver` from `nmos/raft/tests/test_chaos_soak.py`, and
//! so is the one rule that makes a soak honest: **the fault budget is counted
//! in amnesia, not in downtime.** This backend's log is in memory, so a member
//! that restarts has forgotten everything it acknowledged, and a quorum's
//! worth of forgetful members loses committed entries without any two of them
//! ever being down together. A restart is therefore allowed only while the
//! members that have restarted and not yet been promoted back stay within the
//! budget -- which is what the node itself reports as `voting == false`.
//! Within that budget nothing is off limits. Beyond it, only liveness is owed,
//! and an `amnesia` plan says so explicitly rather than by accident.
//!
//! Everything the driver *chooses* comes from the plan's seed. What it cannot
//! choose -- the node's election jitter, and on a multi-threaded runtime the
//! scheduler -- is why a failure carries its trace instead of relying on a
//! re-run.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::sync::Arc;
use std::time::Duration;

use nmos_registry_backend::RegistryBackend;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::health_now;
use nmos_registry_raft::node::{RaftNode, RaftTiming, Role};
use parking_lot::Mutex;

use super::audit::{DecisionAudit, PROMOTION_JUSTIFICATION};
use super::capture::{self, Capture};
use super::cluster::{ClusterConfig, SoakCluster};
use super::forensics::Forensics;
use super::monitor::{Depth, Mode, Monitor, Owed, Violation};
use super::net::{ChaosNet, Knobs};
use super::rng::Rng;
use super::workload::{self, Answer, Beat, Deletion};

/// Phrases that only ever describe the cluster being unable to commit --
/// the backend's own `MutationUnavailable` texts (`backend.rs::commit`,
/// `::register`, `::await_applied`) and the node's `RaftUnavailable` ones. A
/// refusal carrying one is a 503 that has been mislabelled.
const AVAILABILITY: &[&str] = &[
    "could not commit",
    "did not commit within",
    "did not answer",
    "did not catch up",
    "no leader elected",
    "lost contact with a quorum",
    "member is shutting down",
];

/// Which level of the stack the client drives.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Workload {
    /// Proposals straight to the node, as the Python soak makes them.
    Consensus,
    /// The `RegistryBackend` seam the HTTP layer calls.
    Registry,
}

/// What the runtime is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Runtime {
    /// Current-thread, clock paused: exact checks, maximum speed.
    Virtual,
    /// Multi-threaded, real clock: real parallelism, real races.
    Threaded {
        /// Worker threads.
        workers: usize,
    },
}

/// What the driver may do at each step. An enum rather than strings so the
/// weight table cannot drift from the dispatch, and so a trace reads as names.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Event {
    /// Register a new Node.
    Register,
    /// Delete a registered resource.
    Unregister,
    /// Many registrations at once.
    Burst,
    /// Register a Device under a Node.
    RegisterDevice,
    /// Register a Sender under a Device.
    RegisterSender,
    /// Re-register an existing resource at a new version.
    Update,
    /// Refresh a Node's health.
    Heartbeat,
    /// The same new Node registered at two members at once.
    RaceFirstRegistration,
    /// A Node updated and deleted at once.
    RaceUpdateDelete,
    /// Run a collection pass.
    CollectGarbage,
    /// Take a member off the network.
    Stop,
    /// Put one back.
    Resume,
    /// Restart a member with an empty log.
    Restart,
    /// Cut one connection, one way.
    Block,
    /// Split the members into groups.
    Partition,
    /// Undo every block and partition.
    Heal,
    /// Freeze a member's connections without breaking them.
    Stall,
    /// Thaw one.
    Unstall,
    /// Make one connection slow.
    SlowLink,
    /// Change the network's delay profile.
    Jitter,
    /// Let time pass.
    Idle,
}

/// Everything a run is, decided before it starts.
#[derive(Debug, Clone)]
pub struct Plan {
    /// The seed everything else was drawn from.
    pub seed: u64,
    /// Runtime flavour.
    pub runtime: Runtime,
    /// Client level.
    pub workload: Workload,
    /// Members.
    pub size: usize,
    /// Driver steps before convergence.
    pub steps: usize,
    /// Node timings.
    pub timing: RaftTiming,
    /// Network profile at the start.
    pub knobs: Knobs,
    /// Event weights.
    pub weights: Vec<(Event, u64)>,
    /// Backend commit deadline.
    pub mutation_timeout: Duration,
    /// How long a consensus-level proposal is given.
    pub proposal_timeout: Duration,
    /// Store garbage-collection interval, seconds. Zero expires a Node one
    /// second after it was last heard from.
    pub gc_interval: i64,
    /// Run the registry-level checks every this many steps.
    pub deep_every: usize,
    /// May exceed the amnesia budget; only liveness is then owed.
    pub amnesia: bool,
    /// Percent chance that a client retries a 503, possibly elsewhere.
    pub retry_percent: u64,
    /// Where the term files live.
    pub dir: std::path::PathBuf,
    /// Names this run in member names, thread names and reports.
    pub tag: String,
}

impl Plan {
    /// One line describing the run, for reports.
    #[must_use]
    pub fn describe(&self) -> String {
        let runtime = match self.runtime {
            Runtime::Virtual => "virtual".to_owned(),
            Runtime::Threaded { workers } => format!("threaded({workers})"),
        };
        format!(
            "seed={} {runtime} {:?} n={} steps={} hb={}ms election={}..{}ms append<={} apply<={} \
             compact@{} cap={} chunk={} delay<={}us spike=1/{} reconnect<={}us mutation={}ms \
             gc={}s amnesia={} retry={}%",
            self.seed,
            self.workload,
            self.size,
            self.steps,
            self.timing.heartbeat_ms,
            self.timing.election_min_ms,
            self.timing.election_max_ms,
            self.timing.max_entries_per_append,
            self.timing.max_apply_batch,
            self.timing.compaction_threshold,
            self.timing.max_log_entries,
            self.timing.snapshot_chunk,
            self.knobs.max_delay_us,
            self.knobs.spike_one_in,
            self.knobs.reconnect_max_us,
            self.mutation_timeout.as_millis(),
            self.gc_interval,
            self.amnesia,
            self.retry_percent,
        )
    }
}

/// What happened, for the report.
#[derive(Debug, Default, Clone)]
pub struct Tally {
    /// Steps completed.
    pub steps: usize,
    /// Events applied, by name.
    pub events: BTreeMap<String, u64>,
    /// Client answers, by kind.
    pub answers: BTreeMap<String, u64>,
    /// Restarts performed.
    pub restarts: u64,
}

impl Tally {
    fn answer(&mut self, what: &str) {
        *self.answers.entry(what.to_owned()).or_insert(0) += 1;
    }
}

/// A progress line the runner's watchdog can read if a run hangs.
pub type Progress = Arc<Mutex<String>>;

/// The run.
pub struct Driver {
    plan: Plan,
    rng: Rng,
    /// The cluster.
    pub cluster: SoakCluster,
    /// The monitor.
    pub monitor: Monitor,
    capture: Arc<Capture>,
    forensics: Arc<Forensics>,
    progress: Progress,
    down: BTreeSet<u64>,
    stalled: BTreeSet<u64>,
    stalled_links: bool,
    /// Client-side timeouts and 503s, by the member that gave them.
    timeouts: BTreeMap<u64, u64>,
    /// Acknowledged resources the client still believes exist, by type.
    nodes: Vec<String>,
    devices: Vec<(String, String)>,
    senders: Vec<(String, String)>,
    versions: BTreeMap<String, u64>,
    problems_seen: usize,
    panics_seen: usize,
    splices_seen: usize,
    storms_seen: usize,
    loops_seen: usize,
    unjustified_seen: usize,
    /// A caller's future resolved with another operation's outcome.
    mismatch: Option<String>,
    /// An acknowledged write found missing mid-run.
    lost: Option<String>,
    /// An availability failure answered as a terminal 400.
    miscoded: Option<String>,
    /// Every cursor the consensus workload allocated (`workload::Allocation`),
    /// for a Cursor Uniqueness failure to explain itself.
    allocations: Arc<Mutex<Vec<workload::Allocation>>>,
    /// When the run began, on its own clock.
    began: tokio::time::Instant,
    /// What happened.
    pub tally: Tally,
}

/// What a follower's commit point says against its leader's log.
enum Contradiction {
    /// The leader holds the follower's committed entry at the same term.
    None,
    /// The leader holds it at another term, or not at all.
    Found(String),
    /// Not decidable: nothing committed yet, or the leader has compacted past
    /// the point.
    Unknown,
}

impl Contradiction {
    fn of(node: &RaftNode, head: &RaftNode) -> Self {
        let committed = node.commit_index();
        let Some(held) = node.log_term_at(committed).filter(|_| committed > 0) else {
            return Self::Unknown;
        };
        let (last, commit) = (head.last_log_index(), head.commit_index());
        let theirs = if committed > last {
            None
        } else if let Some(term) = head.log_term_at(committed) {
            Some(term)
        } else {
            return Self::Unknown;
        };
        if theirs == Some(held) {
            return Self::None;
        }
        let theirs = theirs.map_or_else(|| "nothing".to_owned(), |term| format!("t{term}"));
        Self::Found(format!(
            "committed {committed} at t{held} (applied {}); its leader holds {theirs} there \
             (log ends {last}, commit {commit})",
            node.last_applied(),
        ))
    }
}

impl Driver {
    /// Build the cluster and the network. Nothing starts until [`Self::run`].
    #[must_use]
    pub fn new(plan: Plan, capture: Arc<Capture>, progress: Progress) -> Self {
        let mut rng = Rng::new(plan.seed);
        let forensics = Arc::new(Forensics::new());
        let members: Vec<u64> = (0..plan.size as u64).collect();
        let net = ChaosNet::new(
            &members,
            rng.fork(),
            plan.knobs,
            Arc::clone(&forensics),
            plan.mutation_timeout
                .as_millis()
                .try_into()
                .unwrap_or(u64::MAX),
        );
        // Only where a handler call runs alone; see `DecisionAudit`.
        if plan.runtime == Runtime::Virtual {
            net.install_audit(DecisionAudit::new());
        }
        let cluster = SoakCluster::build(
            ClusterConfig {
                size: plan.size,
                timing: plan.timing,
                backends: plan.workload == Workload::Registry,
                mutation_timeout: plan.mutation_timeout,
                gc_interval: plan.gc_interval,
                forget_interval: 12,
                tag: plan.tag.clone(),
            },
            net,
            plan.dir.clone(),
        );
        let mode = match plan.runtime {
            Runtime::Virtual => Mode::Exact,
            Runtime::Threaded { .. } => Mode::Robust,
        };
        Self {
            plan,
            rng,
            cluster,
            monitor: Monitor::new(mode),
            capture,
            forensics,
            progress,
            down: BTreeSet::new(),
            stalled: BTreeSet::new(),
            stalled_links: false,
            timeouts: BTreeMap::new(),
            nodes: Vec::new(),
            devices: Vec::new(),
            senders: Vec::new(),
            versions: BTreeMap::new(),
            problems_seen: 0,
            panics_seen: 0,
            splices_seen: 0,
            storms_seen: 0,
            loops_seen: 0,
            unjustified_seen: 0,
            mismatch: None,
            lost: None,
            miscoded: None,
            allocations: Arc::new(Mutex::new(Vec::new())),
            began: tokio::time::Instant::now(),
            tally: Tally::default(),
        }
    }

    /// The run's recorder, for the report.
    #[must_use]
    pub fn forensics(&self) -> &Arc<Forensics> {
        &self.forensics
    }

    fn heartbeat(&self) -> Duration {
        Duration::from_millis(self.plan.timing.heartbeat_ms)
    }

    /// Wait until no member holds anything a caller could still be waiting
    /// on, or until every such caller has run out of time.
    ///
    /// A waiter lives until its entry applies or its caller stops waiting, and
    /// every caller here has a deadline -- so a waiter still held once the
    /// longest one has passed, plus a tick for the sweep, is a leak, and one
    /// held before then may be a request still being served. A read waiting for
    /// its index is the same: held until a quorum confirms it, leadership ends,
    /// or -- its caller gone -- the leader hears its next reply. Converged is not
    /// enough: the run's last unstall can deliver a `Forward` held by the stall,
    /// whose owner then serves it with a fresh deadline. Measured: every leak
    /// left after the waiter fix (2 in 718 runs; 17 in 32 runs of those seeds)
    /// was exactly that, its caller still waiting when the check ran.
    async fn let_callers_finish(&self) {
        let limit = self
            .plan
            .mutation_timeout
            .max(self.plan.proposal_timeout)
            .saturating_add(self.heartbeat().saturating_mul(2));
        let started = tokio::time::Instant::now();
        while started.elapsed() < limit
            && self.cluster.members.iter().any(|member| {
                let node = &member.node;
                node.pending_waiters() > 0
                    || node.pending_reads() > 0
                    || node.snapshot_buffers() > 0
                    || node.batcher().pending() > 0
                    || node.fence().waiters() > 0
            })
        {
            tokio::time::sleep(self.heartbeat()).await;
        }
    }

    fn election_window(&self) -> Duration {
        Duration::from_millis(self.plan.timing.election_max_ms)
    }

    /// How many members may be unavailable, or forgetful, at once.
    fn budget(&self) -> usize {
        self.plan.size / 2
    }

    /// Members that have restarted and not yet been promoted back.
    ///
    /// Read from the nodes, because promotion is the cluster's decision and
    /// not the driver's.
    fn forgetful(&self) -> usize {
        self.cluster
            .members
            .iter()
            .filter(|member| !member.node.voting())
            .count()
    }

    fn unavailable(&self) -> usize {
        self.down.union(&self.stalled).count()
    }

    fn live(&self) -> Vec<u64> {
        (0..self.plan.size as u64)
            .filter(|index| !self.down.contains(index) && !self.stalled.contains(index))
            .collect()
    }

    fn note(&mut self, step: usize, event: Event, detail: &str) {
        let line = format!("{step:5} {event:?} {detail}");
        *self.progress.lock() = line.clone();
        self.forensics.step(line);
    }

    /// Start, churn for the plan's steps, converge, and check the settled
    /// state. Returns every violation found, most diagnostic first.
    pub async fn run(&mut self) -> Vec<Violation> {
        self.began = tokio::time::Instant::now();
        self.cluster.start().await;
        let steps = self.plan.steps;
        for step in 0..steps {
            let event = self.choose();
            self.apply(step, event).await;
            *self.tally.events.entry(format!("{event:?}")).or_insert(0) += 1;
            self.tally.steps = step + 1;
            // One heartbeat per step: enough for a message to cross a link,
            // far too little for the cluster to quiesce -- which is the point.
            // The properties must hold mid-flight, not only at rest.
            tokio::time::sleep(self.heartbeat()).await;
            let depth = if self.plan.deep_every > 0 && step % self.plan.deep_every == 0 {
                Depth::Deep
            } else {
                Depth::Step
            };
            if let Err(found) = self.check(depth) {
                return vec![found];
            }
        }

        let mut found = Vec::new();
        let converged = match self.converge().await {
            Ok(()) => true,
            Err(stuck) => {
                found.push(stuck);
                false
            }
        };
        if (found.is_empty() || self.plan.amnesia)
            && let Err(broken) = self.check(Depth::Settled)
        {
            found.push(broken);
        }
        if !self.plan.amnesia {
            // On a cluster that never settled, a member missing a write may
            // simply not have received it yet; only a write missing from
            // every replica is known to be lost. Likewise a waiter may still
            // be legitimately pending, so leaks are only judged at rest.
            found.extend(self.monitor.check_promises(&self.cluster, converged));
            if converged {
                self.let_callers_finish().await;
                found.extend(self.leaks());
            }
        }
        found.extend(self.problems());
        found
    }

    /// Close every member. Separate from `run` so the leak checks above see
    /// the cluster before `close` empties what they look at.
    pub async fn close(&self) {
        self.cluster.close().await;
    }

    fn check(&mut self, depth: Depth) -> Result<(), Violation> {
        if let Some(problem) = self.problems().into_iter().next() {
            return Err(problem);
        }
        if let Some(what) = self.mismatch.take() {
            return Err(Violation {
                property: "Outcome Integrity",
                detail: format!(
                    "a caller's future resolved with an outcome belonging to another operation: {what}"
                ),
                index: None,
                term: None,
                at_ms: None,
            });
        }
        if let Some(what) = self.miscoded.take() {
            return Err(Violation {
                property: "Status Integrity",
                detail: format!("an availability failure was answered as a terminal 400: {what}"),
                index: None,
                term: None,
                at_ms: None,
            });
        }
        if let Some(what) = self.lost.take() {
            return Err(Violation {
                property: "Acknowledged Write Durability",
                detail: what,
                index: None,
                term: None,
                at_ms: None,
            });
        }
        if let Some(diverged) = self.divergence() {
            return Err(diverged);
        }
        let leaders = self.capture.leaders();
        let verdict = if self.plan.amnesia {
            // Beyond the budget committed entries may legitimately be lost, so
            // only the properties that survive amnesia are asked: one leader
            // per term, and a member never contradicting itself.
            self.monitor.check_amnesiac(&self.cluster, &leaders)
        } else {
            self.monitor.check(&self.cluster, &leaders, depth)
        };
        verdict.map_err(|mut violation| {
            if violation.property == "Cursor Uniqueness" {
                let context = self.allocation_context(&violation.detail);
                violation.detail.push_str(&context);
            }
            violation
        })
    }

    /// The allocations behind a Cursor Uniqueness failure: every allocation of
    /// the cursor it names, and each allocating member's allocations around
    /// them, with the clock each was made against and how far ahead of that
    /// clock the allocator placed it.
    fn allocation_context(&self, detail: &str) -> String {
        let Some(cursor) = detail
            .split("cursor ")
            .nth(1)
            .and_then(|rest| rest.split_whitespace().next())
        else {
            return String::new();
        };
        let allocations = self.allocations.lock();
        let hits: Vec<usize> = allocations
            .iter()
            .enumerate()
            .filter(|(_, allocation)| allocation.cursor.to_string() == cursor)
            .map(|(position, _)| position)
            .collect();
        let mut out = format!(
            "\n  allocations of {cursor}: {} of {} recorded\n",
            hits.len(),
            allocations.len()
        );
        let members: BTreeSet<u64> = hits
            .iter()
            .filter_map(|&position| allocations.get(position).map(|a| a.member))
            .collect();
        for member in members {
            let theirs: Vec<(usize, &workload::Allocation)> = allocations
                .iter()
                .enumerate()
                .filter(|(_, allocation)| allocation.member == member)
                .collect();
            let near = |position: usize| {
                hits.iter()
                    .any(|&hit| position.abs_diff(hit) <= 6 && allocations[hit].member == member)
            };
            let _ = writeln!(out, "  member {member}'s allocations near them:");
            for (position, allocation) in theirs.into_iter().filter(|&(position, _)| near(position))
            {
                let ahead = i128::from(allocation.cursor.seconds) * 1_000_000_000
                    + i128::from(allocation.cursor.nanoseconds)
                    - (i128::from(allocation.clock.seconds) * 1_000_000_000
                        + i128::from(allocation.clock.nanoseconds));
                let _ = writeln!(
                    out,
                    "    {:>9}ms inc={} node {} clock={} cursor={}{} ahead={ahead}ns applied={} commit={}",
                    allocation.at.duration_since(self.began).as_millis(),
                    allocation.incarnation,
                    allocation.node_id.get(..8).unwrap_or(&allocation.node_id),
                    allocation.clock,
                    allocation.cursor,
                    if hits.contains(&position) { " <-" } else { "" },
                    allocation.applied,
                    allocation.commit,
                );
            }
        }
        out
    }

    /// New WARN/ERROR lines from the implementation, and panics.
    fn problems(&mut self) -> Vec<Violation> {
        let problems = self.capture.problems();
        let mut found = Vec::new();
        for logged in problems.iter().skip(self.problems_seen) {
            // No exemption for "raft: replicated append refused" in amnesia
            // runs any more. It rested on a member holding committed entries
            // the new leader never had, which the recovery election's veto
            // rules out even beyond the budget (`divergence`), and a refusal to
            // truncate committed state now stops the member instead.
            found.push(Violation {
                property: "Logged Problem",
                detail: logged.to_string(),
                index: None,
                term: None,
                at_ms: Some(logged.at_ms),
            });
        }
        self.problems_seen = problems.len();
        let panics = capture::panics_for(&self.plan.tag);
        for panicked in panics.iter().skip(self.panics_seen) {
            found.push(Violation {
                property: "Panic",
                detail: format!("{} at {}", panicked.message, panicked.location),
                index: None,
                term: None,
                at_ms: None,
            });
        }
        self.panics_seen = panics.len();
        let loops = self.cluster.net.forward_loops();
        for line in loops.iter().skip(self.loops_seen) {
            found.push(Violation {
                property: "Forwarding Loop",
                detail: line.clone(),
                index: None,
                term: None,
                at_ms: None,
            });
        }
        self.loops_seen = loops.len();
        let storms = self.cluster.net.storms();
        for line in storms.iter().skip(self.storms_seen) {
            found.push(Violation {
                property: "Message Storm",
                detail: line.clone(),
                index: None,
                term: None,
                at_ms: None,
            });
        }
        self.storms_seen = storms.len();
        let splices = self.cluster.net.splices();
        for line in splices.iter().skip(self.splices_seen) {
            found.push(Violation {
                property: "Snapshot Integrity",
                detail: format!("a follower was sent two snapshots as one transfer: {line}"),
                index: None,
                term: None,
                at_ms: None,
            });
        }
        self.splices_seen = splices.len();
        if let Some(audit) = self.cluster.net.audit() {
            let unjustified = audit.findings();
            for decision in unjustified.iter().skip(self.unjustified_seen) {
                if self.plan.amnesia && decision.property == PROMOTION_JUSTIFICATION {
                    // Beyond the budget a committed entry may be held by no
                    // member at all, and then no promotion can reach it.
                    continue;
                }
                found.push(Violation {
                    property: decision.property,
                    detail: decision.detail.clone(),
                    index: Some(decision.index),
                    term: Some(decision.term),
                    at_ms: Some(decision.at_ms),
                });
            }
            self.unjustified_seen = unjustified.len();
        }
        found
    }

    /// `(commits, promotions)` the audit checked, for the run report. Zero in
    /// threaded runs, which have no audit.
    #[must_use]
    pub fn decisions_audited(&self) -> (u64, u64) {
        self.cluster
            .net
            .audit()
            .map_or((0, 0), |audit| (audit.commits(), audit.promotions()))
    }

    fn choose(&mut self) -> Event {
        let weights = self.plan.weights.clone();
        self.rng.weighted(&weights)
    }

    // -- events -----------------------------------------------------------

    async fn apply(&mut self, step: usize, event: Event) {
        let live = self.live();
        let budget = self.budget();
        match event {
            Event::Register => {
                if let Some(&member) = self.rng.pick(&live) {
                    self.register_node(step, member).await;
                }
            }
            Event::Unregister => {
                if let Some(&member) = self.rng.pick(&live) {
                    self.unregister(step, member).await;
                }
            }
            Event::Burst => self.burst(step, &live).await,
            Event::RegisterDevice => {
                if let Some(&member) = self.rng.pick(&live) {
                    self.register_device(step, member).await;
                }
            }
            Event::RegisterSender => {
                if let Some(&member) = self.rng.pick(&live) {
                    self.register_sender(step, member).await;
                }
            }
            Event::Update => {
                if let Some(&member) = self.rng.pick(&live) {
                    self.update(step, member).await;
                }
            }
            Event::Heartbeat => {
                if let Some(&member) = self.rng.pick(&live) {
                    self.beat(step, member).await;
                }
            }
            Event::RaceFirstRegistration => self.race_first_registration(step, &live).await,
            Event::RaceUpdateDelete => self.race_update_delete(step, &live).await,
            Event::CollectGarbage => {
                if let Some(&member) = self.rng.pick(&live) {
                    self.collect(step, member).await;
                }
            }
            Event::Stop => {
                if self.unavailable() < budget
                    && let Some(&victim) = self.rng.pick(&live)
                {
                    self.cluster.net.stop(victim);
                    self.down.insert(victim);
                    self.note(step, event, &format!("member {victim}"));
                }
            }
            Event::Resume => {
                let down: Vec<u64> = self.down.iter().copied().collect();
                if let Some(&back) = self.rng.pick(&down) {
                    self.cluster.net.resume(back);
                    self.down.remove(&back);
                    self.note(step, event, &format!("member {back}"));
                }
            }
            Event::Restart => {
                let allowed =
                    self.plan.amnesia || (self.unavailable() < budget && self.forgetful() < budget);
                if allowed && let Some(&victim) = self.rng.pick(&live) {
                    let was = self.cluster.members[victim as usize].node.incarnation();
                    self.cluster.restart(victim as usize).await;
                    self.tally.restarts += 1;
                    self.note(
                        step,
                        event,
                        &format!("member {victim} (incarnation {was} -> {})", was + 1),
                    );
                }
            }
            Event::Block => {
                if live.len() >= 2 {
                    let mut pair = live.clone();
                    self.rng.shuffle(&mut pair);
                    self.cluster.net.block(pair[0], pair[1]);
                    self.note(step, event, &format!("{} -/-> {}", pair[0], pair[1]));
                }
            }
            Event::Partition => {
                if self.plan.size >= 3 {
                    let groups = self.groups();
                    self.cluster.net.partition(&groups);
                    self.note(step, event, &format!("{groups:?}"));
                }
            }
            Event::Heal => {
                self.cluster.net.heal();
                self.note(step, event, "");
            }
            Event::Stall => {
                if self.plan.size >= 2 && self.rng.chance(1, 3) {
                    // One connection frozen, in one direction of dialing: the
                    // half-stalled pair, where a member hears a peer it can no
                    // longer get through to. Not counted against the budget --
                    // it makes no member unavailable.
                    let a = self.rng.below(self.plan.size as u64);
                    let b =
                        (a + 1 + self.rng.below(self.plan.size as u64 - 1)) % self.plan.size as u64;
                    self.cluster.net.stall_link((a, b));
                    self.stalled_links = true;
                    self.note(step, event, &format!("connection {a}->{b}"));
                } else if self.unavailable() < budget
                    && let Some(&victim) = self.rng.pick(&live)
                {
                    self.cluster.net.stall(victim);
                    self.stalled.insert(victim);
                    self.note(step, event, &format!("member {victim}"));
                }
            }
            Event::Unstall => {
                let stalled: Vec<u64> = self.stalled.iter().copied().collect();
                if self.stalled_links && (stalled.is_empty() || self.rng.chance(1, 2)) {
                    self.cluster.net.unstall_links();
                    self.stalled_links = false;
                    self.note(step, event, "every stalled connection");
                } else if let Some(&back) = self.rng.pick(&stalled) {
                    self.cluster.net.unstall(back);
                    self.stalled.remove(&back);
                    self.note(step, event, &format!("member {back}"));
                }
            }
            Event::SlowLink => {
                if self.plan.size >= 2 {
                    let a = self.rng.below(self.plan.size as u64);
                    let b =
                        (a + 1 + self.rng.below(self.plan.size as u64 - 1)) % self.plan.size as u64;
                    let percent = self.rng.range(200, 3000);
                    self.cluster.net.slow((a, b), percent);
                    self.note(step, event, &format!("{a}->{b} x{}%", percent));
                }
            }
            Event::Jitter => {
                let heartbeat_us = self.plan.timing.heartbeat_ms * 1000;
                let knobs = Knobs {
                    max_delay_us: *self
                        .rng
                        .pick(&[
                            0,
                            heartbeat_us / 10,
                            heartbeat_us / 2,
                            heartbeat_us,
                            heartbeat_us * 2,
                        ])
                        .unwrap_or(&0),
                    spike_one_in: *self.rng.pick(&[0, 10, 50, 500]).unwrap_or(&0),
                    reconnect_max_us: *self
                        .rng
                        .pick(&[0, heartbeat_us, heartbeat_us * 5])
                        .unwrap_or(&0),
                };
                self.cluster.net.unslow_all();
                self.cluster.net.set_knobs(knobs);
                self.note(step, event, &format!("{knobs:?}"));
            }
            Event::Idle => {
                let windows = self.rng.range(1, 4) as u32;
                let pause = self.election_window() * windows;
                self.note(step, event, &format!("{pause:?}"));
                tokio::time::sleep(pause).await;
            }
        }
    }

    /// Two or three groups, never all members in one.
    fn groups(&mut self) -> Vec<BTreeSet<u64>> {
        let mut members: Vec<u64> = (0..self.plan.size as u64).collect();
        self.rng.shuffle(&mut members);
        let count = if self.plan.size >= 5 && self.rng.chance(1, 3) {
            3
        } else {
            2
        };
        let mut cuts: Vec<usize> = (1..members.len()).collect();
        self.rng.shuffle(&mut cuts);
        let mut cuts: Vec<usize> = cuts.into_iter().take(count - 1).collect();
        cuts.sort_unstable();
        let mut groups = Vec::new();
        let mut start = 0;
        for cut in cuts.into_iter().chain(std::iter::once(members.len())) {
            groups.push(members[start..cut].iter().copied().collect());
            start = cut;
        }
        groups
    }

    // -- client traffic ---------------------------------------------------

    fn next_version(&mut self, id: &str) -> String {
        let counter = self.versions.entry(id.to_owned()).or_insert(0);
        *counter += 1;
        format!("{}:{}", 1_000 + *counter, *counter)
    }

    async fn register_node(&mut self, step: usize, member: u64) {
        let id = self.rng.uuid();
        match self.plan.workload {
            Workload::Consensus => {
                let node = Arc::clone(&self.cluster.members[member as usize].node);
                let answer = workload::propose_register_recorded(
                    &node,
                    member,
                    &id,
                    self.plan.proposal_timeout,
                    Some(&self.allocations),
                )
                .await;
                self.settle_registration(
                    step,
                    member,
                    (ResourceType::Node, id),
                    None,
                    answer,
                    false,
                );
            }
            Workload::Registry => {
                let version = self.next_version(&id);
                let body = workload::node_body(&id, &version);
                self.submit(
                    step,
                    member,
                    (ResourceType::Node, id),
                    Some(version),
                    body,
                    None,
                )
                .await;
            }
        }
    }

    /// A registry-level registration, retried as a real Node retries.
    ///
    /// A Node that gets a 503 retries -- and, per IS-04, may fail over to
    /// another registry. Both happen here with the plan's probability, which
    /// is what turns "a commit that took longer than the mutation timeout"
    /// into two proposals for the same resource.
    async fn submit(
        &mut self,
        step: usize,
        member: u64,
        key: (ResourceType, String),
        version: Option<String>,
        body: nmos_registry_core::body::Body,
        parent: Option<(ResourceType, String)>,
    ) {
        let mut at = member;
        let mut open = false;
        for attempt in 0..3 {
            let Some(backend) = self.cluster.members[at as usize].backend.clone() else {
                return;
            };
            let answer = workload::register(&backend, key.0, body.clone()).await;
            let retry = matches!(answer, Answer::Unavailable(_) | Answer::NotReady)
                && self.rng.chance(self.plan.retry_percent, 100);
            if matches!(answer, Answer::Unavailable(_)) {
                open = true;
            }
            if !retry || attempt == 2 {
                if let Some(ref parent) = parent
                    && matches!(answer, Answer::Acknowledged { .. })
                {
                    self.monitor.ledger.parent(key.clone(), parent.clone());
                }
                self.settle_registration(step, at, key, version, answer, open);
                return;
            }
            let live = self.live();
            if let Some(&elsewhere) = self.rng.pick(&live) {
                self.note(
                    step,
                    Event::Register,
                    &format!(
                        "{} {} retried at m{elsewhere} after 503 at m{at}",
                        key.0,
                        short(&key.1)
                    ),
                );
                at = elsewhere;
            }
        }
    }

    /// Fold one registration's answer into the ledger and the lists.
    fn settle_registration(
        &mut self,
        step: usize,
        member: u64,
        key: (ResourceType, String),
        version: Option<String>,
        answer: Answer,
        earlier_attempt_open: bool,
    ) {
        let label = format!("{} {}", key.0, short(&key.1));
        let existed = matches!(self.monitor.ledger.owed(&key), Some(&Owed::Present(_)));
        // Every attempt answered 503 may still commit, whatever the others were
        // told -- a later acknowledgement of the same body included -- and may
        // commit after a delete the client goes on to make. Without a version
        // (the consensus workload) there is nothing to record, and nothing
        // needed: those registrations are tried once, for a fresh id, and one
        // left undecided is owed nothing, so it is never deleted.
        if let Some(ref version) = version
            && (earlier_attempt_open || matches!(answer, Answer::Unavailable(_)))
        {
            self.monitor.ledger.undecided(&key, version);
        }
        match answer {
            Answer::Acknowledged { created } => {
                self.tally.answer("acknowledged");
                self.note(
                    step,
                    Event::Register,
                    &format!("m{member} ACK {label} created={created}"),
                );
                self.monitor.ledger.acknowledged(key.clone(), version);
                if !existed {
                    self.remember(&key);
                }
            }
            Answer::Refused(why) => {
                self.tally.answer("refused (400)");
                self.note(
                    step,
                    Event::Register,
                    &format!("m{member} 400 {label}: {why}"),
                );
                if AVAILABILITY.iter().any(|phrase| why.contains(phrase)) {
                    // A 400 is terminal -- a Node "MUST NOT" retry it -- so an
                    // availability failure reported as one loses the
                    // registration for good, on a cluster that would have
                    // accepted it moments later.
                    self.miscoded = Some(format!("m{member} answered 400 for {label}: {why}"));
                }
                if existed && why.starts_with("parent") {
                    self.monitor.ledger.anomaly(
                        "400 for an acknowledged resource's update",
                        format!("{label}: {why}"),
                    );
                } else if !existed {
                    self.monitor
                        .ledger
                        .anomaly("400 for a fresh registration", format!("{label}: {why}"));
                }
                if earlier_attempt_open {
                    self.open(&key, existed);
                }
            }
            Answer::Unavailable(why) => {
                self.tally.answer("unavailable (503)");
                *self.timeouts.entry(member).or_insert(0) += 1;
                self.note(
                    step,
                    Event::Register,
                    &format!("m{member} 503 {label}: {why}"),
                );
                self.open(&key, existed);
            }
            Answer::NotReady => {
                self.tally.answer("not ready (503)");
                self.note(
                    step,
                    Event::Register,
                    &format!("m{member} not-ready {label}"),
                );
                if earlier_attempt_open {
                    self.open(&key, existed);
                }
            }
            Answer::Mismatched(what) => {
                self.tally.answer("MISMATCHED");
                self.forensics.step(format!("MISMATCH {label}: {what}"));
                self.mismatch = Some(format!("{label}: {what}"));
            }
        }
    }

    /// The outcome is unknown. An update cannot delete, so an existing
    /// resource is still owed presence; a new one is owed nothing.
    fn open(&mut self, key: &(ResourceType, String), existed: bool) {
        if existed {
            self.monitor.ledger.update_unknown(key);
        } else {
            self.monitor.ledger.open(key);
        }
    }

    fn remember(&mut self, key: &(ResourceType, String)) {
        if key.0 == ResourceType::Node {
            self.nodes.push(key.1.clone());
        }
    }

    async fn register_device(&mut self, step: usize, member: u64) {
        let Some(node_id) = self.present_node() else {
            return;
        };
        let id = self.rng.uuid();
        let version = self.next_version(&id);
        let body = workload::device_body(&id, &node_id, &version);
        let key = (ResourceType::Device, id.clone());
        self.submit(
            step,
            member,
            key.clone(),
            Some(version),
            body,
            Some((ResourceType::Node, node_id.clone())),
        )
        .await;
        if matches!(self.monitor.ledger.owed(&key), Some(&Owed::Present(_))) {
            self.devices.push((id, node_id));
        }
    }

    async fn register_sender(&mut self, step: usize, member: u64) {
        let candidates: Vec<(String, String)> = self
            .devices
            .iter()
            .filter(|(device, _)| {
                matches!(
                    self.monitor
                        .ledger
                        .owed(&(ResourceType::Device, device.clone())),
                    Some(&Owed::Present(_))
                )
            })
            .cloned()
            .collect();
        let Some((device_id, _)) = self.rng.pick(&candidates).cloned() else {
            return;
        };
        let id = self.rng.uuid();
        let version = self.next_version(&id);
        let body = workload::sender_body(&id, &device_id, &version);
        let key = (ResourceType::Sender, id.clone());
        self.submit(
            step,
            member,
            key.clone(),
            Some(version),
            body,
            Some((ResourceType::Device, device_id.clone())),
        )
        .await;
        if matches!(self.monitor.ledger.owed(&key), Some(&Owed::Present(_))) {
            self.senders.push((id, device_id));
        }
    }

    fn present_node(&mut self) -> Option<String> {
        let present: Vec<String> = self
            .nodes
            .iter()
            .filter(|node| {
                matches!(
                    self.monitor
                        .ledger
                        .owed(&(ResourceType::Node, (*node).clone())),
                    Some(&Owed::Present(_))
                )
            })
            .cloned()
            .collect();
        self.rng.pick(&present).cloned()
    }

    /// An acknowledged resource the client may still address, of any type.
    fn present_resource(&mut self) -> Option<(ResourceType, String, Option<String>)> {
        let mut choices: Vec<(ResourceType, String, Option<String>)> = Vec::new();
        for node in &self.nodes {
            choices.push((ResourceType::Node, node.clone(), None));
        }
        for (device, node) in &self.devices {
            choices.push((ResourceType::Device, device.clone(), Some(node.clone())));
        }
        for (sender, device) in &self.senders {
            choices.push((ResourceType::Sender, sender.clone(), Some(device.clone())));
        }
        choices.retain(|(kind, id, _)| {
            matches!(
                self.monitor.ledger.owed(&(*kind, id.clone())),
                Some(&Owed::Present(_))
            )
        });
        self.rng.pick(&choices).cloned()
    }

    async fn update(&mut self, step: usize, member: u64) {
        if self.plan.workload != Workload::Registry {
            return;
        }
        let Some((kind, id, parent)) = self.present_resource() else {
            return;
        };
        let version = self.next_version(&id);
        let body = match kind {
            ResourceType::Node => workload::node_body(&id, &version),
            ResourceType::Device => {
                workload::device_body(&id, parent.as_deref().unwrap_or(""), &version)
            }
            _ => workload::sender_body(&id, parent.as_deref().unwrap_or(""), &version),
        };
        self.submit(step, member, (kind, id), Some(version), body, None)
            .await;
    }

    async fn unregister(&mut self, step: usize, member: u64) {
        match self.plan.workload {
            Workload::Consensus => {
                let Some(id) = self.present_node() else {
                    return;
                };
                let node = Arc::clone(&self.cluster.members[member as usize].node);
                let answer =
                    workload::propose_unregister(&node, member, &id, self.plan.proposal_timeout)
                        .await;
                self.settle_deletion(step, member, (ResourceType::Node, id), answer, true);
            }
            Workload::Registry => {
                let Some((kind, id, _)) = self.present_resource() else {
                    return;
                };
                let Some(backend) = self.cluster.members[member as usize].backend.clone() else {
                    return;
                };
                let answer = workload::unregister(&backend, kind, &id).await;
                self.settle_deletion(step, member, (kind, id), answer, false);
            }
        }
    }

    fn settle_deletion(
        &mut self,
        step: usize,
        member: u64,
        key: (ResourceType, String),
        answer: Deletion,
        through_the_log: bool,
    ) {
        let label = format!("{} {}", key.0, short(&key.1));
        match answer {
            Deletion::Removed => {
                self.tally.answer("deleted");
                self.note(
                    step,
                    Event::Unregister,
                    &format!("m{member} DELETED {label}"),
                );
                self.monitor.ledger.deleted(&key);
            }
            Deletion::NotFound => {
                self.tally.answer("not found (404)");
                self.note(step, Event::Unregister, &format!("m{member} 404 {label}"));
                if through_the_log && !self.plan.amnesia {
                    // Decided by apply, against the replicated log: an
                    // acknowledged, never-deleted resource was not there.
                    // Not asked of an amnesia run, where committed entries
                    // may genuinely be lost -- that is what "past the budget"
                    // means, and the ledger is not checked there either.
                    self.lost = Some(format!(
                        "deleting acknowledged {label} through the log found nothing to remove"
                    ));
                } else {
                    // Decided by the backend against its *local* store
                    // (`backend.rs::unregister`), which may simply be behind.
                    self.monitor.ledger.anomaly(
                        "404 on DELETE of an acknowledged resource",
                        format!("{label} at m{member}"),
                    );
                }
            }
            Deletion::Unavailable(why) => {
                self.tally.answer("unavailable (503)");
                self.note(
                    step,
                    Event::Unregister,
                    &format!("m{member} 503 {label}: {why}"),
                );
                // A delete that was neither confirmed nor refused may still
                // commit: its fate is undecided in both directions.
                self.monitor.ledger.open(&key);
            }
            Deletion::NotReady => {
                self.tally.answer("not ready (503)");
            }
            Deletion::Mismatched(what) => {
                self.tally.answer("MISMATCHED");
                self.forensics.step(format!("MISMATCH {label}: {what}"));
                self.mismatch = Some(what);
            }
        }
    }

    async fn beat(&mut self, step: usize, member: u64) {
        if self.plan.workload != Workload::Registry {
            return;
        }
        let Some(node_id) = self.present_node() else {
            return;
        };
        let Some(backend) = self.cluster.members[member as usize].backend.clone() else {
            return;
        };
        match workload::heartbeat(&backend, &node_id).await {
            Beat::Alive => self.tally.answer("heartbeat ok"),
            Beat::Unknown => {
                self.tally.answer("heartbeat 404");
                self.note(
                    step,
                    Event::Heartbeat,
                    &format!("m{member} 404 node {}", short(&node_id)),
                );
                self.monitor.ledger.anomaly(
                    "404 on heartbeat of an acknowledged Node",
                    format!("node {node_id} at m{member}"),
                );
            }
            Beat::Unavailable(_) => self.tally.answer("heartbeat 503"),
            Beat::NotReady => self.tally.answer("heartbeat not ready"),
        }
    }

    async fn burst(&mut self, step: usize, live: &[u64]) {
        if live.is_empty() {
            return;
        }
        let count = self.rng.range(4, 48) as usize;
        let mut jobs = Vec::with_capacity(count);
        for _ in 0..count {
            let member = *self.rng.pick(live).unwrap_or(&0);
            let id = self.rng.uuid();
            jobs.push((member, id));
        }
        self.note(step, Event::Burst, &format!("{count} registrations"));
        match self.plan.workload {
            Workload::Consensus => {
                let timeout = self.plan.proposal_timeout;
                let futures = jobs.iter().map(|(member, id)| {
                    let node = Arc::clone(&self.cluster.members[*member as usize].node);
                    let id = id.clone();
                    let member = *member;
                    let allocations = Arc::clone(&self.allocations);
                    async move {
                        workload::propose_register_recorded(
                            &node,
                            member,
                            &id,
                            timeout,
                            Some(&allocations),
                        )
                        .await
                    }
                });
                let answers = futures_util::future::join_all(futures).await;
                for ((member, id), answer) in jobs.into_iter().zip(answers) {
                    self.settle_registration(
                        step,
                        member,
                        (ResourceType::Node, id),
                        None,
                        answer,
                        false,
                    );
                }
            }
            Workload::Registry => {
                let futures = jobs.iter().map(|(member, id)| {
                    let backend = self.cluster.members[*member as usize].backend.clone();
                    let body = workload::node_body(id, "1001:1");
                    async move {
                        match backend {
                            Some(backend) => {
                                workload::register(&backend, ResourceType::Node, body).await
                            }
                            None => Answer::NotReady,
                        }
                    }
                });
                let answers = futures_util::future::join_all(futures).await;
                for ((member, id), answer) in jobs.into_iter().zip(answers) {
                    self.versions.insert(id.clone(), 1);
                    self.settle_registration(
                        step,
                        member,
                        (ResourceType::Node, id),
                        Some("1001:1".to_owned()),
                        answer,
                        false,
                    );
                }
            }
        }
    }

    /// The same new Node, registered at two members at once.
    ///
    /// What a Node does when its first attempt times out and it fails over to
    /// another registry before the first has answered -- and what a load
    /// balancer does to a Node that retries. Neither member owns the Node yet,
    /// so each may validate it as new and claim it.
    async fn race_first_registration(&mut self, step: usize, live: &[u64]) {
        if self.plan.workload != Workload::Registry || live.len() < 2 {
            return;
        }
        let mut pair = live.to_vec();
        self.rng.shuffle(&mut pair);
        let (a, b) = (pair[0], pair[1]);
        let id = self.rng.uuid();
        let version = self.next_version(&id);
        let body = workload::node_body(&id, &version);
        let (Some(left), Some(right)) = (
            self.cluster.members[a as usize].backend.clone(),
            self.cluster.members[b as usize].backend.clone(),
        ) else {
            return;
        };
        let (first, second) = tokio::join!(
            workload::register(&left, ResourceType::Node, body.clone()),
            workload::register(&right, ResourceType::Node, body),
        );
        self.note(
            step,
            Event::RaceFirstRegistration,
            &format!(
                "node {} at m{a} -> {first:?}; at m{b} -> {second:?}",
                short(&id)
            ),
        );
        let key = (ResourceType::Node, id);
        let undecided =
            matches!(first, Answer::Unavailable(_)) || matches!(second, Answer::Unavailable(_));
        if undecided {
            self.monitor.ledger.undecided(&key, &version);
        }
        // Both may have committed; either may have been refused. What is owed
        // is decided by what the client was told: one acknowledgement at this
        // version is enough for presence.
        if matches!(first, Answer::Acknowledged { .. })
            || matches!(second, Answer::Acknowledged { .. })
        {
            if undecided {
                self.monitor.ledger.acknowledged(key.clone(), None);
            } else {
                self.monitor.ledger.acknowledged(key.clone(), Some(version));
            }
            self.remember(&key);
        } else {
            self.monitor.ledger.open(&key);
        }
    }

    /// A Node updated at one member while it is deleted at another.
    async fn race_update_delete(&mut self, step: usize, live: &[u64]) {
        if self.plan.workload != Workload::Registry || live.len() < 2 {
            return;
        }
        let Some(node_id) = self.present_node() else {
            return;
        };
        let mut pair = live.to_vec();
        self.rng.shuffle(&mut pair);
        let (a, b) = (pair[0], pair[1]);
        let version = self.next_version(&node_id);
        let body = workload::node_body(&node_id, &version);
        let (Some(updater), Some(deleter)) = (
            self.cluster.members[a as usize].backend.clone(),
            self.cluster.members[b as usize].backend.clone(),
        ) else {
            return;
        };
        let (updated, deleted) = tokio::join!(
            workload::register(&updater, ResourceType::Node, body),
            workload::unregister(&deleter, ResourceType::Node, &node_id),
        );
        self.note(
            step,
            Event::RaceUpdateDelete,
            &format!(
                "node {} update at m{a} -> {updated:?}; delete at m{b} -> {deleted:?}",
                short(&node_id)
            ),
        );
        // Concurrent, so neither order is owed: the subtree's fate is open.
        self.monitor.ledger.open(&(ResourceType::Node, node_id));
    }

    async fn collect(&mut self, step: usize, member: u64) {
        if self.plan.workload != Workload::Registry {
            return;
        }
        let Some(backend) = self.cluster.members[member as usize].backend.clone() else {
            return;
        };
        // `collect_garbage` answers the same whether or not its expiries
        // committed: an Expire whose commit timed out stays in the log and can
        // commit long after this returns. Measured (seed 60175): an expiry
        // proposed by the collection at 33.7s applied at 37.1s, after the heal,
        // and the Node it removed -- silent since before 21.7s, so rightly
        // expired -- was still owed presence here, and reported lost on every
        // member. So the candidates are computed first, by the backend's own
        // rule, and any still present when the call returns has an open fate.
        let candidates = self.gc_candidates(member);
        let before = self.capture.expired_nodes().len();
        drop(backend.collect_garbage().await);
        let expired = self.capture.expired_nodes();
        for node in expired.iter().skip(before) {
            // Expiry is the registry doing its job, not a lost write: an
            // expired Node's fate is out of the client's hands.
            self.monitor
                .ledger
                .open(&(ResourceType::Node, node.clone()));
        }
        let registry = Arc::clone(&self.cluster.members[member as usize].registry);
        let mut in_doubt = 0usize;
        for node in candidates {
            if registry.with_read_store(|store| store.get(ResourceType::Node, &node).is_some()) {
                self.monitor.ledger.open(&(ResourceType::Node, node));
                in_doubt += 1;
            }
        }
        self.note(
            step,
            Event::CollectGarbage,
            &format!(
                "at m{member}, {} expired, {in_doubt} in doubt",
                expired.len() - before
            ),
        );
    }

    /// The Nodes a collection at `member` may expire.
    ///
    /// The backend's rule (`backend.rs`, `collect_garbage`): extant, silent
    /// past the store's interval, and owned by the collecting member. One
    /// second of slack on the threshold, because the two clock reads are not
    /// the same instant and health has one-second resolution -- so this is a
    /// superset of what the backend chooses, never a subset.
    fn gc_candidates(&self, member: u64) -> Vec<String> {
        let target = &self.cluster.members[member as usize];
        let silent: Vec<String> = target.registry.with_read_store(|store| {
            let threshold = health_now()
                .saturating_sub(store.gc_interval())
                .saturating_add(1);
            store
                .iter_extant(ResourceType::Node)
                .filter(|node| node.health() < threshold)
                .map(|node| node.id.clone())
                .collect()
        });
        silent
            .into_iter()
            .filter(|node| target.node.ownership_of(node) == Some(member))
            .collect()
    }

    // -- the end of the run -------------------------------------------------

    /// Heal everything, bring everyone back, and wait for one settled state.
    ///
    /// Convergence is only promised to a connected cluster, so the final
    /// durability claim is made after this and not a moment before.
    async fn converge(&mut self) -> Result<(), Violation> {
        let net = Arc::clone(&self.cluster.net);
        net.heal();
        net.unstall_all();
        net.unslow_all();
        let mut knobs = net.knobs();
        knobs.max_delay_us = 0;
        knobs.spike_one_in = 0;
        net.set_knobs(knobs);
        for member in std::mem::take(&mut self.down) {
            net.resume(member);
        }
        self.stalled.clear();
        self.stalled_links = false;
        *self.progress.lock() = "converging".to_owned();

        // Generously long in election windows: a member that spent the run
        // stopped may need a snapshot, then a promotion. This is a liveness
        // check, not a timing one -- but it is a check: a cluster that never
        // settles is a cluster answering 503 forever.
        let rounds = 400;
        for _ in 0..rounds {
            for _ in 0..10 {
                tokio::time::sleep(self.heartbeat()).await;
            }
            self.check(Depth::Step)?;
            if self.converged() {
                return Ok(());
            }
        }
        let mut detail =
            format!("the cluster did not converge within {rounds} x 10 heartbeats after healing\n");
        let backlog = self.cluster.net.backlog();
        let _ = writeln!(
            detail,
            "  network backlog: {} queue(s) not empty",
            backlog.len()
        );
        for row in backlog.iter().take(20) {
            let _ = writeln!(detail, "    {row}");
        }
        for member in &self.cluster.members {
            let observed = super::monitor::Observed::of(&member.node);
            let _ = writeln!(
                detail,
                "  {} live={:?} waiters={} reads={} buffers={}",
                observed.line(),
                member.node.live_peers(),
                member.node.pending_waiters(),
                member.node.pending_reads(),
                member.node.snapshot_buffers(),
            );
        }
        Err(Violation {
            property: "Liveness",
            detail,
            index: None,
            term: None,
            at_ms: None,
        })
    }

    fn converged(&self) -> bool {
        if !self.cluster.net.settled() {
            return false;
        }
        let leaders = self.cluster.leaders();
        let &[leader] = leaders.as_slice() else {
            return false;
        };
        let head = &self.cluster.members[leader as usize].node;
        let (term, commit, last) = (head.term(), head.commit_index(), head.last_log_index());
        if self.plan.amnesia {
            // Beyond the budget the data may be gone; the cluster may not.
            return head.voting();
        }
        self.cluster.members.iter().all(|member| {
            let node = &member.node;
            node.leader() == Some(leader)
                && node.term() == term
                && node.voting()
                && node.commit_index() == commit
                && node.last_applied() == commit
                && node.last_log_index() == last
                && member
                    .backend
                    .as_ref()
                    .is_none_or(|backend| backend.state().accepts_mutations())
        })
    }

    /// A follower whose committed prefix its own leader contradicts: it holds
    /// as committed an entry the leader has at another term, or does not have
    /// at all.
    ///
    /// Raft makes that impossible -- a leader holds every entry committed
    /// before its term and commits every one of its own -- and so does the
    /// recovery election even beyond an amnesia budget: every real election
    /// follows a won pre-vote, which counts a member as forgotten only on its
    /// *grant*, so a member holding a committed entry keeps a veto over every
    /// candidate lacking it. Beyond the budget data can be lost only by being
    /// forgotten everywhere, which leaves nobody holding it. So it fails every
    /// run, amnesia or not. It once counted as an anomaly, in amnesia runs
    /// only: 0 in 4,323 runs (part 7 of the fix record), measured before the
    /// veto argument was checked.
    ///
    /// Judged only against the leader the follower itself follows, in the same
    /// term. A deposed leader still acting in an older term holds a log the
    /// cluster has moved past, and measured against it every up-to-date
    /// follower would look diverged.
    fn divergence(&self) -> Option<Violation> {
        self.cluster.members.iter().find_map(|member| {
            let node = &member.node;
            let leader = node.leader().filter(|&leader| leader != member.index)?;
            let head = &self.cluster.members[leader as usize].node;
            if head.role() != Role::Leader || head.term() != node.term() {
                return None;
            }
            match Contradiction::of(node, head) {
                Contradiction::Found(example) => Some(Violation {
                    property: "Leader Completeness (a follower's view)",
                    detail: format!("m{} {example}", member.index),
                    index: None,
                    term: None,
                    at_ms: None,
                }),
                Contradiction::None | Contradiction::Unknown => None,
            }
        })
    }

    /// The per-operation structures must return to empty once nothing is in
    /// flight: each entry is a caller never answered and memory never
    /// released, which no leak detector reports because it is reachable.
    fn leaks(&self) -> Vec<Violation> {
        let mut found = Vec::new();
        for member in &self.cluster.members {
            let node = &member.node;
            let checks = [
                ("pending_waiters", node.pending_waiters()),
                ("pending reads", node.pending_reads()),
                ("snapshot_buffers", node.snapshot_buffers()),
                ("batcher pending", node.batcher().pending()),
                ("fence waiters", node.fence().waiters()),
            ];
            for (what, count) in checks {
                if count > 0 {
                    let fates = self.forensics.propose_fates(member.index);
                    let name = &self.cluster.names[member.index as usize];
                    let led = self
                        .capture
                        .leaders()
                        .iter()
                        .filter(|(leader, _)| leader == name)
                        .count();
                    let installs = self.capture.count_for("raft: installed a snapshot", name);
                    found.push(Violation {
                        property: "Resource Leak",
                        detail: format!(
                            "member {} holds {count} {what} after the cluster converged with \
                             nothing in flight (its Propose messages: delivered={} lost={} \
                             unlinked={} answered-not-leader={}; client timeouts at it: {}; \
                             terms it led: {led}; snapshots it installed: {installs})",
                            member.index,
                            fates.delivered,
                            fates.lost,
                            fates.unlinked,
                            fates.rejected,
                            self.timeouts.get(&member.index).copied().unwrap_or(0),
                        ),
                        index: None,
                        term: None,
                        at_ms: None,
                    });
                }
            }
        }
        found
    }
}

fn short(id: &str) -> &str {
    id.get(..8).unwrap_or(id)
}
