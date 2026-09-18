// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Two log sinks at two verbosities, as `setup_logging` configures them.
//!
//! Port of `nmos_registry.py`'s `setup_logging` and `console_log_level`. The
//! arrangement is not incidental:
//!
//! | sink | level | format |
//! |---|---|---|
//! | stdout | `NMOS_LOG_LEVEL`, default INFO | the message alone |
//! | `--logFile` | DEBUG, always | timestamp, level, message |
//!
//! # Why the environment variable matters more than it looks
//!
//! `console_log_level` explains it: `--logFile ""` silences the *file* handler
//! only, so the console keeps writing regardless, and "comparing this registry
//! against nmos-cpp at its least-verbose setting is not a fair comparison
//! unless both are actually quiet". `bench_registry/compare.py` quietens every
//! target through `NMOS_LOG_LEVEL`, and a registry that ignored it would be
//! measured while logging every request against one that was not.
//!
//! `RUST_LOG` is deliberately **not** consulted. It is the Rust idiom and it
//! would be an extra way to configure this that the Python has no counterpart
//! for -- so a launch script or harness that set one variable would get
//! different behaviour from the two implementations.

use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use tracing::Level;
use tracing_subscriber::fmt::MakeWriter;
use tracing_subscriber::layer::SubscriberExt as _;
use tracing_subscriber::util::SubscriberInitExt as _;
use tracing_subscriber::{Layer, filter::LevelFilter};

/// Rotate once the file would pass this size.
const MAX_BYTES: u64 = 1_000_000;

/// How many rotated files to keep: `.1`, `.2`, `.3`.
const BACKUP_COUNT: u32 = 3;

/// Console verbosity, from `NMOS_LOG_LEVEL`.
///
/// An environment variable rather than a flag, "so the launch scripts and the
/// benchmark harness can quieten the registry without every deployment growing
/// another option to get wrong".
///
/// An unrecognised name warns and falls back to INFO rather than failing, which
/// is what Python does -- a typo in a harness variable should not stop a
/// registry from starting.
#[must_use]
pub fn console_log_level() -> Level {
    level_from(std::env::var("NMOS_LOG_LEVEL").ok().as_deref())
}

/// Resolve a level name, with the environment supplied rather than read.
///
/// Split out so the mapping can be tested without mutating process-wide state
/// -- which in Rust is `unsafe`, and which the workspace refuses.
#[must_use]
fn level_from(value: Option<&str>) -> Level {
    let Some(name) = value else {
        return Level::INFO;
    };
    let name = name.trim().to_ascii_uppercase();
    if name.is_empty() {
        return Level::INFO;
    }
    match name.as_str() {
        "TRACE" => Level::TRACE,
        "DEBUG" => Level::DEBUG,
        "INFO" => Level::INFO,
        // Python's `getLevelNamesMapping` knows WARNING; WARN is tracing's
        // spelling. Both are accepted so neither vocabulary is a trap.
        "WARNING" | "WARN" => Level::WARN,
        "ERROR" => Level::ERROR,
        // Python has CRITICAL and tracing has no level above ERROR; mapping it
        // there is the closest honest answer, and quieter than INFO either way.
        "CRITICAL" | "FATAL" => Level::ERROR,
        _ => {
            eprintln!("Warning: NMOS_LOG_LEVEL={name:?} is not a level name; using INFO");
            Level::INFO
        }
    }
}

/// A file that rolls over once it would exceed [`MAX_BYTES`].
///
/// `RotatingFileHandler(maxBytes=1_000_000, backupCount=3)`. Rotation is
/// size-based, which `tracing-appender` does not offer -- it rolls by time --
/// so this is written out rather than approximated with a different policy.
/// A registry left running for weeks is exactly the case the cap exists for.
#[derive(Debug)]
struct RotatingFile {
    path: PathBuf,
    file: Option<File>,
    written: u64,
}

impl RotatingFile {
    fn open(path: &Path) -> Self {
        let file = OpenOptions::new().create(true).append(true).open(path).ok();
        let written = file
            .as_ref()
            .and_then(|file| file.metadata().ok())
            .map_or(0, |metadata| metadata.len());
        Self {
            path: path.to_path_buf(),
            file,
            written,
        }
    }

    /// Shuffle `.2` to `.3`, `.1` to `.2`, the live file to `.1`, and reopen.
    ///
    /// Highest backup first, so nothing is overwritten before it has moved.
    fn rotate(&mut self) {
        self.file = None;

        for index in (1..BACKUP_COUNT).rev() {
            let from = self.path.with_extension(format!("log.{index}"));
            let to = self
                .path
                .with_extension(format!("log.{}", index.saturating_add(1)));
            let _ = std::fs::rename(&from, &to);
        }
        let _ = std::fs::rename(&self.path, self.path.with_extension("log.1"));

        self.file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .ok();
        self.written = 0;
    }
}

impl Write for RotatingFile {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        // Python rotates when the record *would* take the file past the cap, so
        // a line is never split across two files.
        if self.written.saturating_add(buf.len() as u64) > MAX_BYTES && self.written > 0 {
            self.rotate();
        }
        let Some(file) = self.file.as_mut() else {
            // The file could not be opened. Python warns once and carries on
            // logging to the console; dropping the bytes here is that, and a
            // registry must not stop serving because its log file went away.
            return Ok(buf.len());
        };
        let written = file.write(buf)?;
        self.written = self.written.saturating_add(written as u64);
        Ok(written)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.file.as_mut().map_or(Ok(()), Write::flush)
    }
}

/// Hands the subscriber a writer per event.
#[derive(Debug, Clone)]
struct SharedFile(Arc<Mutex<RotatingFile>>);

impl Write for SharedFile {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.0
            .lock()
            .map_or(Ok(buf.len()), |mut file| file.write(buf))
    }

    fn flush(&mut self) -> io::Result<()> {
        self.0.lock().map_or(Ok(()), |mut file| file.flush())
    }
}

impl<'a> MakeWriter<'a> for SharedFile {
    type Writer = Self;

    fn make_writer(&'a self) -> Self::Writer {
        self.clone()
    }
}

/// Install both sinks.
///
/// An empty `log_file` means "console only" -- `--logFile ""` in the Python,
/// which silences the file handler alone.
pub fn init(log_file: &Path) {
    let console = tracing_subscriber::fmt::layer()
        .with_writer(io::stdout)
        // `Formatter("%(message)s")`: no timestamp, no level, no target. The
        // console is for a person watching a terminal; the file is the record.
        .without_time()
        .with_level(false)
        .with_target(false)
        .with_filter(LevelFilter::from_level(console_log_level()));

    let file = (!log_file.as_os_str().is_empty()).then(|| {
        if let Some(parent) = log_file.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        tracing_subscriber::fmt::layer()
            .with_writer(SharedFile(Arc::new(Mutex::new(RotatingFile::open(
                log_file,
            )))))
            .with_ansi(false)
            .with_target(false)
            // DEBUG regardless of the console's level: the file is what
            // `--verify-log-volume` measures and what an incident is read from.
            .with_filter(LevelFilter::DEBUG)
    });

    tracing_subscriber::registry()
        .with(console)
        .with(file)
        .init();
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read as _;

    #[test]
    fn the_default_console_level_is_info() {
        assert_eq!(level_from(None), Level::INFO);
        assert_eq!(level_from(Some("")), Level::INFO);
    }

    #[test]
    fn the_harness_can_quieten_the_registry() {
        // What `compare.py` relies on: a registry that ignored this would be
        // measured while logging every request against one that was not.
        assert_eq!(level_from(Some("ERROR")), Level::ERROR);
        assert_eq!(level_from(Some("error")), Level::ERROR);
        assert_eq!(level_from(Some(" Error ")), Level::ERROR);
    }

    #[test]
    fn both_spellings_of_the_warning_level_are_understood() {
        // Python's vocabulary is WARNING, tracing's is WARN. A harness written
        // against either should not silently fall back to INFO.
        assert_eq!(level_from(Some("WARNING")), Level::WARN);
        assert_eq!(level_from(Some("WARN")), Level::WARN);
    }

    #[test]
    fn an_unknown_level_falls_back_to_info_rather_than_failing() {
        // A typo in a harness variable must not stop a registry from starting.
        assert_eq!(level_from(Some("LOUD")), Level::INFO);
    }

    // -- rotation ---------------------------------------------------------

    #[test]
    fn the_file_rolls_over_and_keeps_three_backups() {
        let dir = std::env::temp_dir().join(format!("nmos-rot-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("temp dir");
        let path = dir.join("registry.log");

        let mut file = RotatingFile::open(&path);
        // Four rollovers' worth, so `.4` would exist if the count were wrong.
        let chunk = vec![b'x'; 100_000];
        for _ in 0..50 {
            file.write_all(&chunk).expect("write");
        }
        file.flush().expect("flush");

        assert!(path.is_file(), "the live log is missing");
        for index in 1..=BACKUP_COUNT {
            assert!(
                path.with_extension(format!("log.{index}")).is_file(),
                "backup .{index} is missing",
            );
        }
        assert!(
            !path
                .with_extension(format!("log.{}", BACKUP_COUNT + 1))
                .is_file(),
            "a fourth backup was kept -- backupCount is not being honoured",
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_line_is_never_split_across_two_files() {
        // Python rotates when the record *would* exceed the cap, so a log file
        // never ends mid-record. A reader that split lines would corrupt every
        // rollover boundary.
        let dir = std::env::temp_dir().join(format!("nmos-split-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("temp dir");
        let path = dir.join("registry.log");

        let mut file = RotatingFile::open(&path);
        let line = format!("{}\n", "y".repeat(600_000));
        file.write_all(line.as_bytes()).expect("first");
        file.write_all(line.as_bytes()).expect("second");
        file.flush().expect("flush");

        let mut live = String::new();
        File::open(&path)
            .expect("live log")
            .read_to_string(&mut live)
            .expect("read");
        assert_eq!(
            live, line,
            "the live log does not hold exactly one whole record",
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_unwritable_path_does_not_stop_the_registry() {
        // Python warns and carries on. A registry that refused to serve because
        // its log directory was read-only would be the worse failure.
        let mut file = RotatingFile::open(Path::new("/nonexistent-dir/registry.log"));
        assert!(file.write_all(b"anything").is_ok());
        assert!(file.flush().is_ok());
    }
}
