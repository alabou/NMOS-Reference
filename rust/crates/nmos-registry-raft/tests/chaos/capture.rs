// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! What the implementation said while it ran, captured per run.
//!
//! Three things the soak cannot see any other way, all of which the node
//! already reports through `tracing`:
//!
//! * **Every election won, exactly.** `become_leader` logs `raft: is leader`
//!   with `member` and `term` while it holds the state lock, so the capture
//!   is a complete, race-free record of who led which term. Sampling roles
//!   after each step -- which is all the Python monitor can do -- misses a
//!   leadership that begins and ends between two samples, and on a
//!   multi-threaded runtime cannot even read role and term atomically.
//! * **The node's own bug detectors.** `check_applied_within_committed`, a
//!   refused local append, a term that could not be persisted: every one of
//!   them *logs* rather than panicking -- the first then stops the member
//!   (`RaftNode::fail`), the others carry on. Without a capture they are
//!   invisible to a test; with one, each is a failure. (The state machine's `DivergenceDetected` tripwire was
//!   one of them until A1 removed it: it fired on ordinary client races, and
//!   was the largest single failure class this capture ever reported.)
//! * **Coverage.** Whether a run installed a snapshot, promoted a member or
//!   expired a Node is visible here and nowhere else, and a soak that never
//!   reached the code it claims to exercise is a soak that proves nothing.
//!
//! Installed as the *thread* default on every thread of a run's runtime
//! rather than globally, so concurrent runs cannot hear each other.
//!
//! Panics are the fourth thing, handled by [`install_panic_hook`]: tokio
//! catches a panic inside a spawned task and turns it into a `JoinError`
//! nobody awaits, so a node whose apply loop panicked would otherwise look
//! like a node that merely went quiet.

use std::cell::RefCell;
use std::collections::{BTreeMap, VecDeque};
use std::fmt::Write as _;
use std::sync::{Arc, Once};

use parking_lot::Mutex;
use tracing::field::{Field, Visit};
use tracing::level_filters::LevelFilter;
use tracing::span::{Attributes, Id, Record};
use tracing::{Dispatch, Event, Level, Metadata, Subscriber};

/// Messages logged at WARN or above that are **expected** in a healthy run.
///
/// Deliberately a short, exact list. Anything else at WARN or ERROR from the
/// implementation is reported as a finding, so adding a line here is a claim
/// that the condition is benign -- and it should be made with the reason
/// written beside it.
const EXPECTED: &[&str] = &[
    // `backend.rs::start`: a member started before an election finished. The
    // backend's own documentation calls this "ordinary" and "not sticky".
    "registry: no leader yet; serving queries and refusing registrations until one is elected",
];

/// How many recent INFO-or-above events are kept for a failure report.
const RECENT: usize = 400;

/// One captured log line.
#[derive(Debug, Clone)]
pub struct Logged {
    /// Its level.
    pub level: Level,
    /// The message text.
    pub message: String,
    /// Every other field, rendered `name=value`.
    pub fields: String,
    /// The thread it was emitted on.
    pub thread: String,
    /// Milliseconds on the run's clock -- virtual time when the clock is paused.
    pub at_ms: u64,
}

impl std::fmt::Display for Logged {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{:>9}ms {:<5} {} {} [{}]",
            self.at_ms, self.level, self.message, self.fields, self.thread
        )
    }
}

#[derive(Default)]
struct Inner {
    leaders: Vec<(String, u64)>,
    by_member: BTreeMap<(String, String), u64>,
    problems: Vec<Logged>,
    counts: BTreeMap<String, u64>,
    recent: VecDeque<Logged>,
    expired: Vec<String>,
}

/// One run's captured log.
pub struct Capture {
    inner: Mutex<Inner>,
    origin: tokio::time::Instant,
}

impl Capture {
    /// An empty capture whose clock starts now.
    #[must_use]
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            inner: Mutex::new(Inner::default()),
            origin: tokio::time::Instant::now(),
        })
    }

    /// A dispatcher that records into this capture.
    #[must_use]
    pub fn dispatch(self: &Arc<Self>) -> Dispatch {
        Dispatch::new(CaptureSubscriber {
            capture: Arc::clone(self),
            debug: false,
        })
    }

    /// As [`Self::dispatch`], hearing DEBUG as well.
    ///
    /// For a test that watches what a transport does between the lines it
    /// says at INFO -- each connection attempt it gives up on, say. The soak
    /// stays at INFO: DEBUG is a per-message volume it has no use for, which
    /// is why this is unused in the soak's own binary and used in
    /// `tests/transport.rs`, which includes this file too.
    #[allow(dead_code)]
    #[must_use]
    pub fn dispatch_with_debug(self: &Arc<Self>) -> Dispatch {
        Dispatch::new(CaptureSubscriber {
            capture: Arc::clone(self),
            debug: true,
        })
    }

    /// Every `raft: is leader` announcement, as `(member name, term)`, in order.
    #[must_use]
    pub fn leaders(&self) -> Vec<(String, u64)> {
        self.inner.lock().leaders.clone()
    }

    /// WARN and ERROR lines that are not on the expected list.
    #[must_use]
    pub fn problems(&self) -> Vec<Logged> {
        self.inner.lock().problems.clone()
    }

    /// Every message seen, with its count.
    #[must_use]
    pub fn counts(&self) -> BTreeMap<String, u64> {
        self.inner.lock().counts.clone()
    }

    /// How many times `member` logged `message`.
    #[must_use]
    pub fn count_for(&self, message: &str, member: &str) -> u64 {
        self.inner
            .lock()
            .by_member
            .get(&(message.to_owned(), member.to_owned()))
            .copied()
            .unwrap_or(0)
    }

    /// Nodes the state machine reported expiring.
    #[must_use]
    pub fn expired_nodes(&self) -> Vec<String> {
        self.inner.lock().expired.clone()
    }

    /// The most recent `limit` events, oldest first.
    #[must_use]
    pub fn recent(&self, limit: usize) -> Vec<Logged> {
        let inner = self.inner.lock();
        let skip = inner.recent.len().saturating_sub(limit);
        inner.recent.iter().skip(skip).cloned().collect()
    }

    fn record(
        &self,
        logged: Logged,
        member: Option<String>,
        term: Option<u64>,
        node: Option<String>,
    ) {
        let mut inner = self.inner.lock();
        *inner.counts.entry(logged.message.clone()).or_insert(0) += 1;
        if let Some(ref member) = member {
            *inner
                .by_member
                .entry((logged.message.clone(), member.clone()))
                .or_insert(0) += 1;
        }
        if logged.message == "raft: is leader"
            && let (Some(member), Some(term)) = (member, term)
        {
            inner.leaders.push((member, term));
        }
        if logged.message == "raft: expired node and its sub-resources"
            && let Some(node) = node
        {
            inner.expired.push(node);
        }
        let serious = matches!(logged.level, Level::ERROR | Level::WARN);
        if serious && !EXPECTED.contains(&logged.message.as_str()) {
            inner.problems.push(logged.clone());
        }
        if inner.recent.len() == RECENT {
            inner.recent.pop_front();
        }
        inner.recent.push_back(logged);
    }
}

/// The subscriber itself. Spans are not tracked -- the node emits events
/// only, and a span id nobody reads is not worth the bookkeeping.
struct CaptureSubscriber {
    capture: Arc<Capture>,
    /// Hear DEBUG too (`Capture::dispatch_with_debug`).
    debug: bool,
}

impl Subscriber for CaptureSubscriber {
    fn enabled(&self, metadata: &Metadata<'_>) -> bool {
        // Spelled out rather than compared: `tracing`'s `Level` orders the
        // verbose levels as the *greater* ones, and a `<=` written from memory
        // is exactly how a filter ends up capturing TRACE and dropping ERROR.
        match *metadata.level() {
            Level::ERROR | Level::WARN | Level::INFO => true,
            Level::DEBUG => self.debug,
            Level::TRACE => false,
        }
    }

    fn max_level_hint(&self) -> Option<LevelFilter> {
        Some(if self.debug {
            LevelFilter::DEBUG
        } else {
            LevelFilter::INFO
        })
    }

    fn new_span(&self, _attributes: &Attributes<'_>) -> Id {
        Id::from_u64(1)
    }

    fn record(&self, _span: &Id, _values: &Record<'_>) {}

    fn record_follows_from(&self, _span: &Id, _follows: &Id) {}

    fn event(&self, event: &Event<'_>) {
        let mut fields = Fields::default();
        event.record(&mut fields);
        let at_ms = tokio::time::Instant::now()
            .saturating_duration_since(self.capture.origin)
            .as_millis();
        let logged = Logged {
            level: *event.metadata().level(),
            message: fields.message,
            fields: fields.rest,
            thread: std::thread::current().name().unwrap_or("?").to_owned(),
            at_ms: u64::try_from(at_ms).unwrap_or(u64::MAX),
        };
        self.capture
            .record(logged, fields.member, fields.term, fields.node);
    }

    fn enter(&self, _span: &Id) {}

    fn exit(&self, _span: &Id) {}
}

/// Collects an event's fields, pulling out the few the soak reasons about.
#[derive(Default)]
struct Fields {
    message: String,
    rest: String,
    member: Option<String>,
    term: Option<u64>,
    node: Option<String>,
}

impl Fields {
    fn text(&mut self, name: &str, value: String) {
        match name {
            "message" => self.message = value,
            "member" => {
                self.member = Some(value.clone());
                let _ = write!(self.rest, "member={value} ");
            }
            "node" => {
                self.node = Some(value.clone());
                let _ = write!(self.rest, "node={value} ");
            }
            _ => {
                let _ = write!(self.rest, "{name}={value} ");
            }
        }
    }
}

impl Visit for Fields {
    fn record_str(&mut self, field: &Field, value: &str) {
        self.text(field.name(), value.to_owned());
    }

    fn record_u64(&mut self, field: &Field, value: u64) {
        if field.name() == "term" {
            self.term = Some(value);
        }
        let _ = write!(self.rest, "{}={value} ", field.name());
    }

    fn record_i64(&mut self, field: &Field, value: i64) {
        if field.name() == "term" {
            self.term = u64::try_from(value).ok();
        }
        let _ = write!(self.rest, "{}={value} ", field.name());
    }

    fn record_debug(&mut self, field: &Field, value: &dyn std::fmt::Debug) {
        // `message` arrives as `format_args!`, whose Debug is its Display, so
        // it renders without quotes. A `String` field arrives here too on
        // `tracing` versions that do not route it through `record_str`, and
        // its Debug form is quoted -- stripped so both paths read the same.
        let rendered = format!("{value:?}");
        let rendered = rendered
            .strip_prefix('"')
            .and_then(|inner| inner.strip_suffix('"'))
            .map_or(rendered.clone(), ToOwned::to_owned);
        if field.name() == "term" {
            self.term = rendered.parse().ok();
        }
        self.text(field.name(), rendered);
    }
}

// -- panics -------------------------------------------------------------------

/// A panic observed on one of a run's threads.
#[derive(Debug, Clone)]
pub struct Panicked {
    /// The thread it happened on.
    pub thread: String,
    /// What it said.
    pub message: String,
    /// Where.
    pub location: String,
}

/// Every panic recorded in this process. `std`'s mutex rather than
/// `parking_lot`'s because it must be a `static`; poisoning is recovered from
/// explicitly below, since a hook that panicked on a poisoned lock would turn
/// one panic into an abort.
static PANICS: std::sync::Mutex<Vec<Panicked>> = std::sync::Mutex::new(Vec::new());
static HOOK: Once = Once::new();

fn panics() -> std::sync::MutexGuard<'static, Vec<Panicked>> {
    PANICS
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

thread_local! {
    /// The run a thread belongs to, set on every thread of a run's runtime.
    static RUN: RefCell<Option<String>> = const { RefCell::new(None) };
}

/// Mark the current thread as belonging to `run`, for panic attribution.
pub fn claim_thread(run: &str) {
    RUN.with(|slot| *slot.borrow_mut() = Some(run.to_owned()));
}

/// Record every panic, on any thread, with the run it belonged to.
///
/// The previous hook still runs, so a panic is printed as usual; this only
/// adds a record the runner can attribute. Installed once per process.
pub fn install_panic_hook() {
    HOOK.call_once(|| {
        let previous = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |info| {
            let run = RUN.with(|slot| slot.borrow().clone());
            let thread = std::thread::current().name().unwrap_or("?").to_owned();
            let message = info
                .payload()
                .downcast_ref::<&str>()
                .map(|text| (*text).to_owned())
                .or_else(|| info.payload().downcast_ref::<String>().cloned())
                .unwrap_or_else(|| "(non-text panic payload)".to_owned());
            let location = info
                .location()
                .map(|at| format!("{}:{}", at.file(), at.line()))
                .unwrap_or_default();
            panics().push(Panicked {
                thread: run.unwrap_or(thread),
                message,
                location,
            });
            previous(info);
        }));
    });
}

/// Panics recorded for `run` so far.
#[must_use]
pub fn panics_for(run: &str) -> Vec<Panicked> {
    panics()
        .iter()
        .filter(|panicked| panicked.thread == run)
        .cloned()
        .collect()
}
