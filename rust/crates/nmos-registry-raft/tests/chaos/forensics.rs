// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Record why a run reached a bad state, not only that it did.
//!
//! The Rust counterpart of `nmos/raft/tests/_forensics.py`, and it exists for
//! the same reason that file gives: a violation says *what* broke, and acting
//! on it needs the inputs the implementation branched on -- which vote was
//! granted on which logs, which append carried which window, who claimed an
//! index was committed and when.
//!
//! Everything here is recorded **at the network**, which is the one place
//! every consensus decision's input passes through without the node's code
//! being touched. Each message is kept as numbers, never formatted until a
//! failure asks for it, in a bounded ring: a long run over seven members
//! produces millions of messages, almost none of them relevant to whatever
//! eventually fails, and an unbounded recorder would turn the soak into a
//! memory test.

use std::collections::VecDeque;
use std::fmt::Write as _;

use nmos_registry_raft::messages::Message;
use nmos_registry_raft::wire::Stream;
use parking_lot::Mutex;

/// Messages kept per run.
const MESSAGES: usize = 60_000;

/// Driver steps kept per run.
const STEPS: usize = 4_000;

/// What happened to a message.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Fate {
    /// Refused at send: no connection.
    Unlinked,
    /// Lost in flight: the connection it was on went away.
    Lost,
    /// Handed to its recipient.
    Delivered,
}

/// The decision-driving fields of one message.
#[derive(Debug, Clone, Copy)]
pub enum Summary {
    /// `RequestVote`.
    Vote {
        /// Term stood in (prospective, for a pre-vote).
        term: u64,
        /// Last log index offered.
        last_index: u64,
        /// Last log term offered.
        last_term: u64,
        /// Pre-vote round.
        pre_vote: bool,
    },
    /// `RequestVoteReply`.
    VoteReply {
        /// The voter's term, or the prospective one when granting a pre-vote.
        term: u64,
        /// Granted.
        granted: bool,
        /// The voter's vote counts.
        voting: bool,
        /// Answers a pre-vote.
        pre_vote: bool,
    },
    /// `AppendEntries`.
    Append {
        /// Leader's term.
        term: u64,
        /// Anchor index.
        prev_index: u64,
        /// Anchor term.
        prev_term: u64,
        /// First carried entry, `(index, term)`.
        first: Option<(u64, u64)>,
        /// Last carried entry.
        last: Option<(u64, u64)>,
        /// Leader's commit index.
        commit: u64,
        /// Correlation id.
        request: u64,
    },
    /// `AppendEntriesReply`.
    AppendReply {
        /// Follower's term.
        term: u64,
        /// Accepted.
        success: bool,
        /// Index vouched for.
        matched: u64,
        /// Conflict hint.
        conflict_index: u64,
        /// Its term.
        conflict_term: u64,
        /// Still catching up.
        catching_up: bool,
        /// Correlation id.
        request: u64,
    },
    /// `InstallSnapshot`.
    Snapshot {
        /// Leader's term.
        term: u64,
        /// Index the snapshot covers.
        last_index: u64,
        /// Its term.
        last_term: u64,
        /// Byte offset of this chunk.
        offset: u64,
        /// Chunk length.
        length: u64,
        /// Final chunk.
        done: bool,
    },
    /// `InstallSnapshotReply`.
    SnapshotReply {
        /// Follower's term.
        term: u64,
        /// Bytes assembled.
        received: u64,
        /// Installed.
        done: bool,
    },
    /// `Promote`.
    Promote {
        /// Leader's term.
        term: u64,
        /// Index the member had to reach.
        through: u64,
    },
    /// `ReadIndex`.
    ReadIndex,
    /// `ReadIndexReply`.
    ReadIndexReply {
        /// Confirmed.
        ok: bool,
        /// The confirmed read index.
        index: u64,
    },
    /// `Propose`.
    Propose {
        /// Proposals carried.
        count: u64,
    },
    /// `ProposeReply`.
    ProposeReply {
        /// Appended.
        accepted: bool,
        /// Leader's term.
        term: u64,
        /// First index appended.
        first: u64,
    },
    /// `Forward`.
    Forward {
        /// Heartbeat rather than register.
        heartbeat: bool,
    },
    /// `ForwardReply`.
    ForwardReply {
        /// Succeeded.
        ok: bool,
        /// The addressee no longer owned the Node.
        not_owner: bool,
        /// The owner's applied index when it answered.
        applied: u64,
    },
    /// Anything else.
    Other,
}

impl Summary {
    /// The numbers worth keeping from a message.
    #[must_use]
    pub fn of(message: &Message) -> Self {
        match *message {
            Message::RequestVote(ref m) => Self::Vote {
                term: m.term,
                last_index: m.last_log_index,
                last_term: m.last_log_term,
                pre_vote: m.pre_vote,
            },
            Message::RequestVoteReply(ref m) => Self::VoteReply {
                term: m.term,
                granted: m.granted,
                voting: m.voting,
                pre_vote: m.pre_vote,
            },
            Message::AppendEntries(ref m) => Self::Append {
                term: m.term,
                prev_index: m.prev_log_index,
                prev_term: m.prev_log_term,
                first: m.entries.first().map(|e| (e.index, e.term)),
                last: m.entries.last().map(|e| (e.index, e.term)),
                commit: m.leader_commit,
                request: m.request_id,
            },
            Message::AppendEntriesReply(ref m) => Self::AppendReply {
                term: m.term,
                success: m.success,
                matched: m.match_index,
                conflict_index: m.conflict_index,
                conflict_term: m.conflict_term,
                catching_up: m.catching_up,
                request: m.request_id,
            },
            Message::InstallSnapshot(ref m) => Self::Snapshot {
                term: m.term,
                last_index: m.last_index,
                last_term: m.last_term,
                offset: m.offset,
                length: m.data.len() as u64,
                done: m.done,
            },
            Message::InstallSnapshotReply(ref m) => Self::SnapshotReply {
                term: m.term,
                received: m.bytes_received,
                done: m.done,
            },
            Message::Promote(ref m) => Self::Promote {
                term: m.term,
                through: m.through_index,
            },
            Message::ReadIndex(_) => Self::ReadIndex,
            Message::ReadIndexReply(ref m) => Self::ReadIndexReply {
                ok: m.ok,
                index: m.index,
            },
            Message::Propose(ref m) => Self::Propose {
                count: m.proposals.len() as u64,
            },
            Message::ProposeReply(ref m) => Self::ProposeReply {
                accepted: m.accepted,
                term: m.term,
                first: m.first_index,
            },
            Message::Forward(ref m) => Self::Forward {
                heartbeat: m.verb == "heartbeat",
            },
            Message::ForwardReply(ref m) => Self::ForwardReply {
                ok: m.ok,
                not_owner: m.not_owner,
                applied: m.applied_index,
            },
            _ => Self::Other,
        }
    }

    /// Does this message bear on `index` or `term`?
    fn concerns(&self, index: Option<u64>, term: Option<u64>) -> bool {
        let term_matches = |t: u64| term.is_some_and(|wanted| t == wanted);
        let index_matches = |low: u64, high: u64| index.is_some_and(|i| low <= i && i <= high);
        match *self {
            Self::Vote {
                term: t,
                last_index,
                ..
            } => term_matches(t) || index.is_some_and(|i| last_index >= i),
            Self::VoteReply { term: t, .. } | Self::Promote { term: t, .. } => term_matches(t),
            Self::Append {
                term: t,
                prev_index,
                last,
                commit,
                ..
            } => {
                let high = last.map_or(prev_index, |(i, _)| i).max(commit);
                term_matches(t) || index_matches(prev_index, high)
            }
            Self::AppendReply {
                term: t,
                matched,
                conflict_index,
                ..
            } => term_matches(t) || index.is_some_and(|i| matched >= i || conflict_index == i),
            Self::Snapshot {
                term: t,
                last_index,
                ..
            } => term_matches(t) || index.is_some_and(|i| last_index >= i),
            Self::SnapshotReply { term: t, .. } | Self::ProposeReply { term: t, .. } => {
                term_matches(t)
            }
            Self::ReadIndexReply { index: at, .. } => index.is_some_and(|i| at >= i),
            _ => false,
        }
    }
}

impl std::fmt::Display for Summary {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match *self {
            Self::Vote {
                term,
                last_index,
                last_term,
                pre_vote,
            } => {
                let kind = if pre_vote { "PreVote" } else { "Vote" };
                write!(f, "{kind} t{term} last=({last_index},t{last_term})")
            }
            Self::VoteReply {
                term,
                granted,
                voting,
                pre_vote,
            } => write!(
                f,
                "{}Reply t{term} granted={granted} voting={voting}",
                if pre_vote { "PreVote" } else { "Vote" }
            ),
            Self::Append {
                term,
                prev_index,
                prev_term,
                first,
                last,
                commit,
                request,
            } => match (first, last) {
                (Some((fi, ft)), Some((li, lt))) => write!(
                    f,
                    "Append t{term} prev=({prev_index},t{prev_term}) [{fi}t{ft}..{li}t{lt}] commit={commit} rq={request}"
                ),
                _ => write!(
                    f,
                    "Heartbeat t{term} prev=({prev_index},t{prev_term}) commit={commit} rq={request}"
                ),
            },
            Self::AppendReply {
                term,
                success,
                matched,
                conflict_index,
                conflict_term,
                catching_up,
                request,
            } => write!(
                f,
                "AppendReply t{term} ok={success} match={matched} conflict=({conflict_index},t{conflict_term}) catching_up={catching_up} rq={request}"
            ),
            Self::Snapshot {
                term,
                last_index,
                last_term,
                offset,
                length,
                done,
            } => write!(
                f,
                "Snapshot t{term} through=({last_index},t{last_term}) offset={offset} len={length} done={done}"
            ),
            Self::SnapshotReply {
                term,
                received,
                done,
            } => write!(f, "SnapshotReply t{term} received={received} done={done}"),
            Self::Promote { term, through } => write!(f, "Promote t{term} through={through}"),
            Self::ReadIndex => write!(f, "ReadIndex"),
            Self::ReadIndexReply { ok, index } => write!(f, "ReadIndexReply ok={ok} index={index}"),
            Self::Propose { count } => write!(f, "Propose x{count}"),
            Self::ProposeReply {
                accepted,
                term,
                first,
            } => write!(f, "ProposeReply t{term} accepted={accepted} first={first}"),
            Self::Forward { heartbeat } => {
                write!(
                    f,
                    "Forward {}",
                    if heartbeat { "heartbeat" } else { "register" }
                )
            }
            Self::ForwardReply {
                ok,
                not_owner,
                applied,
            } => write!(
                f,
                "ForwardReply ok={ok} not_owner={not_owner} applied={applied}"
            ),
            Self::Other => write!(f, "other"),
        }
    }
}

/// One message event.
#[derive(Debug, Clone, Copy)]
pub struct Record {
    /// Microseconds on the run's clock.
    pub at_us: u64,
    /// Sender.
    pub from: u64,
    /// Recipient.
    pub to: u64,
    /// Which connection of the pair.
    pub stream: Stream,
    /// What became of it.
    pub fate: Fate,
    /// What it said.
    pub summary: Summary,
}

/// The run's recorder.
pub struct Forensics {
    origin: tokio::time::Instant,
    messages: Mutex<VecDeque<Record>>,
    steps: Mutex<VecDeque<String>>,
    step_count: std::sync::atomic::AtomicU64,
}

impl Forensics {
    /// An empty recorder whose clock starts now.
    #[must_use]
    pub fn new() -> Self {
        Self {
            origin: tokio::time::Instant::now(),
            messages: Mutex::new(VecDeque::with_capacity(1024)),
            steps: Mutex::new(VecDeque::with_capacity(256)),
            step_count: std::sync::atomic::AtomicU64::new(0),
        }
    }

    /// Microseconds since the recorder was made.
    #[must_use]
    pub fn now_us(&self) -> u64 {
        let elapsed = tokio::time::Instant::now().saturating_duration_since(self.origin);
        u64::try_from(elapsed.as_micros()).unwrap_or(u64::MAX)
    }

    /// Note one message event.
    pub fn note(&self, from: u64, to: u64, stream: Stream, fate: Fate, message: &Message) {
        let record = Record {
            at_us: self.now_us(),
            from,
            to,
            stream,
            fate,
            summary: Summary::of(message),
        };
        let mut messages = self.messages.lock();
        if messages.len() == MESSAGES {
            messages.pop_front();
        }
        messages.push_back(record);
    }

    /// Note one driver step, already formatted -- steps are few.
    pub fn step(&self, line: String) {
        self.step_count
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let at = self.now_us() / 1000;
        let mut steps = self.steps.lock();
        if steps.len() == STEPS {
            steps.pop_front();
        }
        steps.push_back(format!("{at:>9}ms {line}"));
    }

    /// Every message delivered or lost within `[from_ms, to_ms]`, oldest first.
    #[must_use]
    pub fn render_window(&self, from_ms: u64, to_ms: u64, limit: usize) -> String {
        let messages = self.messages.lock();
        let (from_us, to_us) = (from_ms.saturating_mul(1000), to_ms.saturating_mul(1000));
        let chosen: Vec<&Record> = messages
            .iter()
            .filter(|record| record.at_us >= from_us && record.at_us <= to_us)
            .collect();
        let skip = chosen.len().saturating_sub(limit);
        let mut out = String::new();
        let _ = writeln!(
            out,
            "network record from {from_ms}ms to {to_ms}ms ({} messages, last {} shown)",
            chosen.len(),
            chosen.len() - skip
        );
        for record in chosen.into_iter().skip(skip) {
            let _ = writeln!(
                out,
                "  {:>10.3}ms m{}->m{} {:<7} {:<9} {}",
                record.at_us as f64 / 1000.0,
                record.from,
                record.to,
                format!("{:?}", record.stream),
                format!("{:?}", record.fate),
                record.summary,
            );
        }
        out
    }

    /// The last `tail` driver steps.
    #[must_use]
    pub fn render_steps(&self, tail: usize) -> String {
        let steps = self.steps.lock();
        let skip = steps.len().saturating_sub(tail);
        let mut out = String::new();
        let total = self.step_count.load(std::sync::atomic::Ordering::Relaxed);
        let _ = writeln!(
            out,
            "driver trace: {total} steps recorded, last {} shown",
            steps.len() - skip
        );
        for line in steps.iter().skip(skip) {
            let _ = writeln!(out, "  {line}");
        }
        out
    }

    /// The messages bearing on `index` / `term`, newest `limit` of them.
    ///
    /// Filtered, as the Python's `render` is, because the unfiltered ring is
    /// tens of thousands of lines and the handful that explain a violation
    /// would be lost in it. With neither given, the newest `limit` messages.
    #[must_use]
    pub fn render_messages(&self, index: Option<u64>, term: Option<u64>, limit: usize) -> String {
        let messages = self.messages.lock();
        let unfiltered = index.is_none() && term.is_none();
        let chosen: Vec<&Record> = messages
            .iter()
            .rev()
            .filter(|record| unfiltered || record.summary.concerns(index, term))
            .take(limit)
            .collect();
        let mut out = String::new();
        let _ = writeln!(
            out,
            "network record ({} messages held; showing {} {})",
            messages.len(),
            chosen.len(),
            match (index, term) {
                (None, None) => "most recent".to_owned(),
                _ => format!("bearing on index {index:?} / term {term:?}"),
            }
        );
        for record in chosen.into_iter().rev() {
            let _ = writeln!(
                out,
                "  {:>10.3}ms m{}->m{} {:<7} {:<9} {}",
                record.at_us as f64 / 1000.0,
                record.from,
                record.to,
                format!("{:?}", record.stream),
                format!("{:?}", record.fate),
                record.summary,
            );
        }
        out
    }
}

/// How one member's forwarded proposals fared, for attributing a leak.
#[derive(Debug, Default, Clone, Copy)]
pub struct ProposeFates {
    /// `Propose` messages it sent that were delivered.
    pub delivered: u64,
    /// Lost in flight with their connection.
    pub lost: u64,
    /// Refused at send for want of a connection.
    pub unlinked: u64,
    /// Answered `accepted=false` -- delivered to a member no longer leading.
    pub rejected: u64,
}

impl Forensics {
    /// Count the fates of `member`'s `Propose` messages still in the ring.
    ///
    /// A follower's proposal is sent once, fire-and-forget, and its waiter is
    /// released only when the entry applies here. So every proposal that
    /// never became an entry is a waiter with nothing left to release it --
    /// and these four numbers are the ways that can happen at the network.
    #[must_use]
    pub fn propose_fates(&self, member: u64) -> ProposeFates {
        let mut fates = ProposeFates::default();
        for record in self.messages.lock().iter() {
            match record.summary {
                Summary::Propose { count } if record.from == member => match record.fate {
                    Fate::Delivered => fates.delivered += count,
                    Fate::Lost => fates.lost += count,
                    Fate::Unlinked => fates.unlinked += count,
                },
                Summary::ProposeReply {
                    accepted: false, ..
                } if record.to == member && record.fate == Fate::Delivered => {
                    fates.rejected += 1;
                }
                _ => {}
            }
        }
        fates
    }
}

impl Default for Forensics {
    fn default() -> Self {
        Self::new()
    }
}
