// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The only thing in this backend that reaches the disk: about 24 bytes.
//!
//! Port of `nmos/raft/persist.py`.
//!
//! # Why any disk at all
//!
//! The premise of this package is that the replicated log lives in memory and
//! is never fsynced, because IS-04 state regenerates from Node re-registration
//! within the garbage-collection interval. That premise is sound for the *log*.
//! It is not sound for the *vote*, and the difference is worth stating exactly,
//! because "in-memory Raft" sounds like it should mean no disk at all.
//!
//! Raft's election safety argument requires `currentTerm` and `votedFor` to
//! survive a crash. Consider three members A, B and C:
//!
//! > A is leader in term 5 and replicates entry E to B. Quorum {A, B} commits
//! > it, `apply_committed` runs, and the client is told 201. B then crashes and
//! > restarts. C -- which never received E -- times out and campaigns in term 6
//! > with `lastLogIndex` behind A's.
//!
//! If B comes back with no memory of having voted in term 5, it votes for C. C
//! wins with {B, C}, and C's log does not contain E. **An acknowledged
//! registration is lost after a single, non-simultaneous failure** -- and a
//! rolling restart of a three-member cluster, which is how this design resizes
//! and upgrades, is exactly that scenario three times over.
//!
//! Persisting the vote is what closes it, and it is cheap in a way the log is
//! not: the term changes on elections, which are rare, whereas the log changes
//! on every registration. The expensive fsync goes; this one stays.
//!
//! # The second half of the fix lives elsewhere
//!
//! Persisting the vote alone is necessary but not sufficient, because a
//! restarted member also comes back with an *empty log*, and Raft's
//! up-to-dateness check makes an empty log vote for anybody. `incarnation` is
//! how the rest of the system notices: it increments on every start, travels in
//! the handshake, and tells a leader that this peer has been reset and must be
//! caught up and explicitly promoted before its vote or its acknowledgement
//! counts.
//!
//! # Why the write is synchronous
//!
//! [`TermStore::save`] blocks. That is deliberate and it is the one place this
//! package knowingly does so. It sits on the election path, it is a few bytes
//! to the page cache plus one fsync, and moving it to a blocking thread would
//! let the runtime run between "I decided to vote" and "that vote is durable"
//! -- which is precisely the window the whole mechanism exists to close.
//!
//! So this module is **not** async, and callers must not make it so. On a
//! multi-threaded runtime a `spawn_blocking` here would look like an
//! improvement and would reopen the gap silently.

use std::fs::{self, File, OpenOptions};
use std::io::Write as _;
use std::path::{Path, PathBuf};

use serde_json::Value;

/// Bumped only if the file's shape changes.
///
/// A member that finds a version it does not understand refuses to start rather
/// than guessing, because guessing here means guessing about whether it has
/// already voted.
pub const STATE_VERSION: u64 = 1;

/// The persisted term/vote file is unreadable or not ours.
///
/// Always fatal at startup. Continuing would mean starting with no memory of a
/// vote that may well have been cast, which is the exact failure this file
/// exists to prevent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PersistentStateError(pub String);

impl std::fmt::Display for PersistentStateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for PersistentStateError {}

/// What must survive a crash for elections to stay safe.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PersistentState {
    /// Raft's `currentTerm`.
    pub term: u64,
    /// Raft's `votedFor`. `None` is "has not voted in this term".
    ///
    /// Not zero: member 0 is a real member, and a vote for it must be
    /// distinguishable from no vote at all.
    pub voted_for: Option<u64>,
    /// How many times this member has started.
    pub incarnation: u64,
}

/// Reads and writes the term/vote file, atomically.
#[derive(Debug)]
pub struct TermStore {
    path: PathBuf,
    writes: u64,
    /// Distinguishes the temporary files this process creates from each other.
    ///
    /// The Python uses `tempfile.mkstemp`, which retries on collision. Here the
    /// exclusive create does the same job -- the file is only ever created with
    /// `create_new`, so a collision is an error rather than a silent overwrite
    /// -- and this counter is what makes a second attempt pick a different
    /// name.
    attempt: u64,
}

impl TermStore {
    /// A store over `path`.
    ///
    /// The parent directory must already exist; this does not create
    /// directories, so a typo in a deployment path fails loudly instead of
    /// quietly persisting state somewhere nobody will look for it.
    #[must_use]
    pub fn new(path: PathBuf) -> Self {
        Self {
            path,
            writes: 0,
            attempt: 0,
        }
    }

    /// The file this store reads and writes.
    #[must_use]
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// How many times state has been flushed since construction.
    ///
    /// Exposed for the benchmark and for tests: the claim "this design fsyncs
    /// on term changes, not on writes" is checkable, and a regression that
    /// started persisting per mutation would otherwise show up only as an
    /// unexplained loss of throughput.
    #[must_use]
    pub const fn writes(&self) -> u64 {
        self.writes
    }

    /// Read the stored state and bump the incarnation.
    ///
    /// A fresh member -- no file, or an empty directory -- starts at term 0
    /// with no vote and incarnation 1. Bumping on *load* rather than on first
    /// save is what makes the counter mean "how many times this member has
    /// started", which is what a leader needs in order to notice a peer that
    /// has been reset.
    ///
    /// # Errors
    ///
    /// [`PersistentStateError`] if the file is unreadable, is not JSON, carries
    /// a state version this build does not understand, or does not hold the
    /// three values. Every one of those is fatal at startup by design.
    pub fn load(&mut self) -> Result<PersistentState, PersistentStateError> {
        if !self.path.exists() {
            let state = PersistentState {
                term: 0,
                voted_for: None,
                incarnation: 1,
            };
            self.save(&state)?;
            return Ok(state);
        }

        let text = fs::read_to_string(&self.path).map_err(|e| self.unreadable(&e))?;
        let raw: Value = serde_json::from_str(&text).map_err(|e| self.unreadable(&e))?;

        // Named rather than left to the version check: a bare `[]` has no
        // `version` key, so it would otherwise refuse with "state version
        // None" and point whoever is reading at the wrong thing entirely.
        if !raw.is_object() {
            return Err(PersistentStateError(format!(
                "{} holds a JSON {}, not an object. Refusing to start with no \
                 memory of whether this member has already voted.",
                self.path.display(),
                python_type_name(&raw),
            )));
        }

        let version = raw.get("version").unwrap_or(&Value::Null);
        if version.as_u64() != Some(STATE_VERSION) {
            return Err(PersistentStateError(format!(
                "{} has state version {}, this member understands {STATE_VERSION}",
                self.path.display(),
                python_repr(version),
            )));
        }

        let state = PersistentState {
            term: self.integer(&raw, "term")?,
            voted_for: match raw.get("voted_for") {
                Some(&Value::Null) => None,
                _ => Some(self.integer(&raw, "voted_for")?),
            },
            incarnation: self.integer(&raw, "incarnation")?.saturating_add(1),
        };
        self.save(&state)?;
        Ok(state)
    }

    /// Write and fsync, atomically. Blocking, for the reason in the module docs.
    ///
    /// Written to a temporary file in the same directory and renamed over the
    /// target: a rename is atomic within a filesystem, so a crash midway leaves
    /// either the old state or the new one, never a half-written file that
    /// parses as term 0.
    ///
    /// The directory is fsynced as well as the file. Without that, the rename
    /// itself can be lost on a crash even though the data was flushed -- and
    /// the member would come back with the *previous* term, which is the state
    /// this is meant to rule out.
    ///
    /// # Errors
    ///
    /// [`PersistentStateError`] if any step fails. The original file is
    /// untouched in that case, because the rename is the only thing that
    /// publishes the new one.
    pub fn save(&mut self, state: &PersistentState) -> Result<(), PersistentStateError> {
        // Key order and two-space indent as the Python's `json.dumps(...,
        // indent=2)` writes them, so a member's state file reads the same
        // whichever implementation wrote it. Hand-built rather than derived:
        // `voted_for` is an `Option` that must appear as `null` and never be
        // skipped, and a `skip_serializing_if` added later by habit would make
        // the file's absent-versus-zero distinction vanish.
        let payload = format!(
            "{{\n  \"version\": {},\n  \"term\": {},\n  \"voted_for\": {},\n  \
             \"incarnation\": {}\n}}",
            STATE_VERSION,
            state.term,
            match state.voted_for {
                Some(member) => member.to_string(),
                None => "null".to_owned(),
            },
            state.incarnation,
        );

        let directory = self
            .path
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .to_path_buf();
        let (mut handle, temporary) = self.create_temporary(&directory)?;

        let written = (|| -> std::io::Result<()> {
            handle.write_all(payload.as_bytes())?;
            handle.flush()?;
            handle.sync_all()
        })();
        drop(handle);

        if let Err(error) = written.and_then(|()| fs::rename(&temporary, &self.path)) {
            // Best-effort cleanup; the original file is untouched either way.
            drop(fs::remove_file(&temporary));
            return Err(PersistentStateError(format!(
                "could not write {}: {error}",
                self.path.display(),
            )));
        }

        let opened = File::open(&directory).and_then(|dir| dir.sync_all());
        opened.map_err(|error| {
            PersistentStateError(format!(
                "could not fsync {}: {error}. The state was written but the \
                 rename may not survive a crash.",
                directory.display(),
            ))
        })?;

        self.writes = self.writes.saturating_add(1);
        Ok(())
    }

    fn create_temporary(
        &mut self,
        directory: &Path,
    ) -> Result<(File, PathBuf), PersistentStateError> {
        // `create_new` is `O_EXCL`: a name already in use is an error, never a
        // silent overwrite of someone else's temporary file.
        for _ in 0..16u8 {
            self.attempt = self.attempt.wrapping_add(1);
            let name = format!(".raft-state-{}-{}", std::process::id(), self.attempt);
            let candidate = directory.join(name);
            match OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&candidate)
            {
                Ok(file) => return Ok((file, candidate)),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => {
                    return Err(PersistentStateError(format!(
                        "could not create a temporary file in {}: {error}",
                        directory.display(),
                    )));
                }
            }
        }
        Err(PersistentStateError(format!(
            "could not find an unused temporary name in {}",
            directory.display(),
        )))
    }

    fn unreadable(&self, error: &dyn std::fmt::Display) -> PersistentStateError {
        PersistentStateError(format!(
            "{} is unreadable: {error}. Refusing to start with no memory of \
             whether this member has already voted; delete it only if this \
             member is genuinely new to the cluster.",
            self.path.display(),
        ))
    }

    /// One integer field, coerced the way Python's `int()` would.
    ///
    /// `int()` accepts an integer, truncates a float and parses a decimal
    /// string, and the file is only ever *written* with integers -- so this
    /// matters solely for a file someone has edited by hand. Matching it costs
    /// a few lines and removes a way for the two implementations to disagree
    /// about the same file in a mixed cluster.
    ///
    /// A key that is missing or holds something `int()` cannot take refuses
    /// with [`PersistentStateError`], the same way an unparseable file does.
    /// The Python used to raise `KeyError`/`ValueError` from *outside* its
    /// `try` here, so the member died with a traceback instead of the refusal;
    /// raised with the maintainer rather than silently mirrored, and fixed
    /// there, so the two now agree.
    fn integer(&self, raw: &Value, key: &str) -> Result<u64, PersistentStateError> {
        let missing = || {
            PersistentStateError(format!(
                "{} does not hold a usable term and vote: {key:?}. Refusing to \
                 start with no memory of whether this member has already \
                 voted; delete it only if this member is genuinely new to the \
                 cluster.",
                self.path.display(),
            ))
        };
        let value = raw.get(key).ok_or_else(missing)?;
        match *value {
            Value::Number(ref number) => number
                .as_u64()
                .or_else(|| number.as_f64().map(|f| f.trunc() as u64))
                .ok_or_else(missing),
            Value::String(ref text) => text.trim().parse::<u64>().map_err(|_| missing()),
            _ => Err(missing()),
        }
    }
}

/// A JSON value as Python's `repr` would print it.
///
/// Only for the state-version refusal, where the Python interpolates `{!r}` and
/// the quoting is the difference between `'1'` and `1` -- which is exactly the
/// distinction someone debugging a hand-edited file needs to see.
fn python_repr(value: &Value) -> String {
    match *value {
        Value::Null => "None".to_owned(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        Value::String(ref text) => format!("'{text}'"),
        ref other => other.to_string(),
    }
}

/// What Python's `type(value).__name__` calls this JSON value.
///
/// Only for the not-an-object refusal, where the whole point is telling the
/// reader what the file actually holds.
fn python_type_name(value: &Value) -> &'static str {
    match *value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(ref n) => {
            if n.is_f64() {
                "float"
            } else {
                "int"
            }
        }
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}
