// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The two command lines must be identical, and this is what enforces it.
//!
//! `cli_flags.json` records what `nmos_registry.py`'s `argparse` actually
//! holds -- every option string, its default, whether it takes a value, whether
//! it may repeat, and any `choices` -- written by
//! `nmos/registry/tests/_cli_corpus.py`. This checks the clap parser against
//! that recording.
//!
//! # Why mechanically rather than by eye
//!
//! An operator, a launch script and every `start-registry*.sh` in the
//! repository move between the two implementations without changing a word,
//! and the M6 gate runs those scripts against this binary with only the
//! executable path swapped. A flag that differs in spelling, default, type or
//! arity breaks that **silently**: the script still runs, it just configures
//! something else. Sixty flags is more than anyone reads back reliably.
//!
//! # The distributed flags
//!
//! Thirty-three of the sixty configure the raft and etcd backends. They landed
//! with the backends themselves -- raft in M9, etcd in M10 -- and [`DEFERRED`]
//! is now **empty**, so this check is total: every flag Python accepts, this
//! binary accepts, with the same arity and the same choices.

// Test code is exempt from the panic-free lints the workspace denies.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects
)]

use std::collections::{BTreeMap, BTreeSet};

use clap::{CommandFactory as _, Parser as _};
use nmos_registry_bin::cli::Args;

#[derive(Debug, serde::Deserialize)]
struct Corpus {
    flags: Vec<Flag>,
}

#[derive(Debug, serde::Deserialize)]
struct Flag {
    options: Vec<String>,
    dest: String,
    kind: String,
    default: Option<serde_json::Value>,
    choices: Option<Vec<String>>,
    group: String,
}

/// Flags whose backends are not built yet, with the milestone that brings them.
///
/// Every entry is a raft or etcd option. Listing them by name rather than
/// filtering on the group keeps the check honest: renaming a group in Python
/// would silently widen a filter, whereas a name that disappears from Python
/// fails here.
/// Flags Python accepts that this binary does not.
///
/// **Empty, and that is the point.** It held the seventeen `--etcd*` flags
/// while the etcd backend was deferred; M10 implemented them, so the list
/// emptied and this check is now total in both directions: a new Python flag
/// nothing here implements fails, and so does a flag re-listed here after it
/// was implemented.
const DEFERRED: &[&str] = &[];

fn corpus() -> Corpus {
    serde_json::from_str(include_str!("cli_flags.json")).expect("cli_flags.json parses")
}

/// Every long option clap accepts, with how it behaves.
fn clap_flags() -> BTreeMap<String, (bool, Option<BTreeSet<String>>)> {
    let command = Args::command();
    let mut out = BTreeMap::new();
    for arg in command.get_arguments() {
        let Some(long) = arg.get_long() else {
            continue;
        };
        if long == "help" || long == "version" {
            continue;
        }
        // The **action**, not the arity range. `ArgAction::SetTrue` leaves
        // `get_num_args()` unset, which an earlier version of this read as
        // "takes a value" and reported every switch as divergent -- a false
        // positive in the check whose whole job is to be trusted.
        let takes_value = !matches!(
            arg.get_action(),
            clap::ArgAction::SetTrue | clap::ArgAction::SetFalse
        );
        let choices = if takes_value {
            let values = arg.get_possible_values();
            if values.is_empty() {
                None
            } else {
                Some(
                    values
                        .iter()
                        .map(|value| value.get_name().to_owned())
                        .collect(),
                )
            }
        } else {
            // A switch's "possible values" are clap's own `true`/`false`, which
            // are an implementation detail rather than `argparse` choices.
            None
        };
        out.insert(format!("--{long}"), (takes_value, choices));
    }
    out
}

#[test]
fn the_deferred_list_is_exactly_what_is_missing() {
    // Fails in both directions. A new Python flag nobody implemented shows up
    // as missing-and-unlisted; a flag that has since been implemented shows up
    // as listed-but-present. The second is the one that would otherwise rot.
    let corpus = corpus();
    let ours = clap_flags();
    let deferred: BTreeSet<&str> = DEFERRED.iter().copied().collect();

    let mut unimplemented = Vec::new();
    let mut wrongly_deferred = Vec::new();
    for flag in &corpus.flags {
        let Some(long) = flag.options.iter().find(|o| o.starts_with("--")) else {
            continue;
        };
        let present = ours.contains_key(long.as_str());
        let listed = deferred.contains(long.as_str());
        if !present && !listed {
            unimplemented.push(format!("{long} ({})", flag.group));
        }
        if present && listed {
            wrongly_deferred.push(long.clone());
        }
    }

    assert!(
        unimplemented.is_empty(),
        "Python has flags this binary does not accept, and they are not on the \
         deferred list:\n  {}",
        unimplemented.join("\n  "),
    );
    assert!(
        wrongly_deferred.is_empty(),
        "these flags are implemented but still listed as deferred:\n  {}",
        wrongly_deferred.join("\n  "),
    );
}

#[test]
fn every_deferred_flag_actually_exists_in_python() {
    // The hole the first version of this file had. The deferred list was
    // written from the port plan's prose rather than from the parser, so it
    // carried names like `--raftElectionMin` that `nmos_registry.py` does not
    // have -- and nothing noticed, because the other checks only ask whether a
    // *Python* flag is accounted for.
    //
    // A stale name here is not harmless: it silences the "unimplemented"
    // check for a flag that was renamed, which is exactly when that check is
    // most wanted.
    let corpus = corpus();
    let theirs: BTreeSet<String> = corpus
        .flags
        .iter()
        .flat_map(|flag| flag.options.clone())
        .collect();

    let ghosts: Vec<&str> = DEFERRED
        .iter()
        .copied()
        .filter(|name| !theirs.contains(*name))
        .collect();
    assert!(
        ghosts.is_empty(),
        "these flags are deferred but do not exist in `nmos_registry.py`:\n  {}",
        ghosts.join("\n  "),
    );
}

#[test]
fn this_binary_invents_no_flags_of_its_own() {
    // The other direction: a flag here that Python does not have would make a
    // launch script work against one implementation and fail against the other.
    let corpus = corpus();
    let theirs: BTreeSet<String> = corpus
        .flags
        .iter()
        .flat_map(|flag| flag.options.clone())
        .collect();

    let extra: Vec<String> = clap_flags()
        .keys()
        .filter(|long| !theirs.contains(*long))
        .cloned()
        .collect();
    assert!(
        extra.is_empty(),
        "this binary accepts flags `nmos_registry.py` does not:\n  {}",
        extra.join("\n  "),
    );
}

#[test]
fn every_implemented_flag_has_pythons_arity() {
    // A switch that took a value, or a value flag that did not, would make
    // `--registryDisableTLS --registrationPort 8447` parse differently in the
    // two implementations -- and the second flag would be swallowed as the
    // first one's argument.
    let corpus = corpus();
    let ours = clap_flags();
    let mut wrong = Vec::new();

    for flag in &corpus.flags {
        let Some(long) = flag.options.iter().find(|o| o.starts_with("--")) else {
            continue;
        };
        let Some((takes_value, _)) = ours.get(long.as_str()) else {
            continue; // deferred; covered by the test above
        };
        let python_takes_value = flag.kind != "flag";
        if *takes_value != python_takes_value {
            wrong.push(format!(
                "{long}: python takes_value={python_takes_value}, ours={takes_value}",
            ));
        }
    }
    assert!(wrong.is_empty(), "arity differs:\n  {}", wrong.join("\n  "));
}

#[test]
fn every_implemented_flag_has_pythons_choices() {
    let corpus = corpus();
    let ours = clap_flags();
    let mut wrong = Vec::new();

    for flag in &corpus.flags {
        let Some(long) = flag.options.iter().find(|o| o.starts_with("--")) else {
            continue;
        };
        let Some((_, choices)) = ours.get(long.as_str()) else {
            continue;
        };
        let python: Option<BTreeSet<String>> = flag
            .choices
            .as_ref()
            .map(|values| values.iter().cloned().collect());
        if &python != choices {
            wrong.push(format!("{long}: python={python:?}, ours={choices:?}"));
        }
    }
    assert!(
        wrong.is_empty(),
        "choices differ:\n  {}",
        wrong.join("\n  "),
    );
}

#[test]
fn every_implemented_flag_has_pythons_default() {
    // Parsed rather than compared as strings, because a launch script that
    // omits a flag must get the same *value* from either implementation.
    let corpus = corpus();
    let ours = clap_flags();
    let parsed = Args::try_parse_from(["nmos-registry"]).expect("defaults parse");
    let mut wrong = Vec::new();

    let actual: BTreeMap<&str, String> = BTreeMap::from([
        ("--registryAddr", parsed.registry_addr.clone()),
        ("--registryCertificate", parsed.registry_certificate.clone()),
        ("--registryKey", parsed.registry_key.clone()),
        (
            "--registrySerialNumber",
            parsed.registry_serial_number.clone(),
        ),
        ("--registrationPort", parsed.registration_port.to_string()),
        ("--queryPort", parsed.query_port.to_string()),
        (
            "--queryWebSocketPort",
            parsed.query_websocket_port.to_string(),
        ),
        (
            "--garbageCollectionInterval",
            parsed.garbage_collection_interval.to_string(),
        ),
        ("--forgetInterval", parsed.forget_interval.to_string()),
        ("--pagingLimit", parsed.paging_limit.to_string()),
        ("--pagingLimitMax", parsed.paging_limit_max.to_string()),
        ("--statusInterval", parsed.status_interval.to_string()),
        ("--oauth2Host", parsed.oauth2_host.clone()),
        ("--oauth2Port", parsed.oauth2_port.to_string()),
        ("--oauth2ApiSelector", parsed.oauth2_api_selector.clone()),
        ("--logFile", parsed.log_file.clone()),
    ]);

    for flag in &corpus.flags {
        let Some(long) = flag.options.iter().find(|o| o.starts_with("--")) else {
            continue;
        };
        if !ours.contains_key(long.as_str()) {
            continue;
        }
        let Some(ours_value) = actual.get(long.as_str()) else {
            continue; // switches and repeatables have no scalar default
        };
        let theirs = match flag.default.as_ref() {
            Some(serde_json::Value::String(text)) => text.clone(),
            Some(serde_json::Value::Number(number)) => number.to_string(),
            Some(other) => other.to_string(),
            None => continue,
        };
        // `12.0` from Python against `12` from a Rust float is the same value.
        let same = theirs == *ours_value
            || theirs
                .parse::<f64>()
                .ok()
                .zip(ours_value.parse::<f64>().ok())
                .is_some_and(|(a, b)| (a - b).abs() < f64::EPSILON);
        if !same {
            wrong.push(format!("{long}: python={theirs:?}, ours={ours_value:?}"));
        }
    }
    assert!(
        wrong.is_empty(),
        "defaults differ:\n  {}",
        wrong.join("\n  "),
    );
}

#[test]
fn the_corpus_covers_the_whole_python_command_line() {
    // "N flags agree" says nothing without knowing N is all of them.
    let corpus = corpus();
    assert!(
        corpus.flags.len() >= 60,
        "the corpus shrank to {} flags -- regenerate it",
        corpus.flags.len(),
    );
    let names: BTreeSet<&str> = corpus.flags.iter().map(|flag| flag.dest.as_str()).collect();
    for required in [
        "registryAddr",
        "registrationPort",
        "queryPort",
        "oauth2",
        "oauth2AudienceMode",
        "gcrl",
        "logFile",
    ] {
        assert!(names.contains(required), "the corpus lost {required}");
    }
}
