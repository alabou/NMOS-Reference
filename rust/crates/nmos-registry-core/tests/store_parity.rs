// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Differential test: the Rust store must behave exactly as the Python one.
//!
//! `store_cases.json` is a recording of what `nmos/registry/store.py` actually
//! did across a 400-step randomised operation sequence, produced by
//! `nmos/registry/tests/_store_corpus.py`. This replays the same sequence and
//! compares, after **every** step:
//!
//! * the ordered ids per `(type, order)` -- what a Query pages over, and where
//!   the `BTreeSet` index redesign would silently diverge;
//! * the contents of a paging query, with a basic-query filter applied;
//! * the `X-Paging-*` and `Link` headers, **byte for byte**, because a client
//!   string-matches the `prev` link against the header it was handed;
//! * the statistics counters.
//!
//! Comparing after every step rather than at the end is what makes a failure
//! usable: the first mismatched step names the operation that caused it.
//!
//! # Why this is worth more than the unit tests beside it
//!
//! `store.rs` and `paging.rs` assert what someone thought to assert, against a
//! reading of the specification. This asserts agreement with an independent
//! implementation across sequences nobody designed -- which is how the
//! interactions get covered: a revive of a resource whose children are
//! tombstoned, a forget that lands between two pages, a filter that empties a
//! window whose ceiling still has to be reported.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

use std::collections::BTreeMap;

use nmos_registry_core::body::Body;
use nmos_registry_core::paging::{apply_paging, paging_headers, parse_paging};
use nmos_registry_core::query_filter::matches;
use nmos_registry_core::resource::Order;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::RegistryStore;
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct Corpus {
    base_url: String,
    default_limit: usize,
    max_limit: usize,
    steps: Vec<Step>,
}

#[derive(Debug, Deserialize)]
struct Step {
    op: String,
    resource_type: String,
    id: String,
    #[serde(default)]
    body: Option<String>,
    #[serde(default)]
    health: Option<i64>,
    #[serde(default)]
    cursor: Option<String>,
    outcome: serde_json::Value,
    state: State,
}

#[derive(Debug, Deserialize)]
struct State {
    ordered: BTreeMap<String, Vec<String>>,
    pages: Vec<PageObservation>,
    statistics: Statistics,
}

#[derive(Debug, Deserialize)]
struct PageObservation {
    resource_type: String,
    query: BTreeMap<String, String>,
    filters: Vec<Vec<String>>,
    ids: Vec<String>,
    headers: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
struct Statistics {
    total: usize,
    non_extant: usize,
    most_recent_update: String,
    per_type: BTreeMap<String, usize>,
}

fn resource_type(name: &str) -> ResourceType {
    ResourceType::from_singular(name).unwrap_or_else(|| panic!("unknown type {name}"))
}

/// Replay one recorded operation, returning what it produced.
///
/// The return value is the whole point. An earlier version discarded it and
/// compared only the state afterwards, which left the delete cascade's removal
/// list unchecked -- and a store that let a revived parent adopt its erased
/// children passed the whole 400-step corpus. The *events* a mutation emits are
/// as much of the contract as the state it leaves behind: they are what a
/// subscriber receives.
fn replay(store: &mut RegistryStore, step: &Step) -> serde_json::Value {
    let kind = resource_type(&step.resource_type);
    match step.op.as_str() {
        "register" => {
            let text = step
                .body
                .as_deref()
                .expect("a register step carries a body");
            let body = Body::new(text);
            // `prepare` then `apply_committed` with the recorded cursor, which
            // is the path a distributed backend takes. Allocating locally would
            // give the two sides different cursors and nothing to compare.
            let prepared = match store.prepare(kind, body.data()) {
                Ok(prepared) => prepared,
                Err(failure) => {
                    return serde_json::json!({
                        "ok": false,
                        "error": failure.error.as_str(),
                    });
                }
            };
            let cursor = step
                .cursor
                .as_deref()
                .and_then(nmos_registry_core::cursor::TaiCursor::parse)
                .expect("a register step carries a cursor");
            let created = prepared.creates.then_some(cursor);
            let applied = store.apply_committed(&prepared, body, created, Some(cursor), Some(500));
            serde_json::json!({"ok": true, "created": applied.created})
        }
        "delete" => {
            let events = store.delete(kind, &step.id);
            serde_json::json!({
                "found": events.is_some(),
                "removed": events
                    .unwrap_or_default()
                    .iter()
                    .map(|event| event.resource_id.clone())
                    .collect::<Vec<_>>(),
            })
        }
        "remove_one" => {
            serde_json::json!({"removed": store.remove_one(kind, &step.id).is_some()})
        }
        "forget" => {
            serde_json::json!({"dropped": store.forget(kind, &step.id)})
        }
        "set_health" => {
            let found = store.get_including_tombstoned(kind, &step.id);
            let applied = found.is_some();
            if let Some(resource) = found {
                resource.set_health(step.health.expect("a set_health step carries a value"));
            }
            serde_json::json!({"applied": applied})
        }
        other => panic!("unknown operation {other}"),
    }
}

fn observe_ordered(store: &RegistryStore) -> BTreeMap<String, Vec<String>> {
    let mut ordered = BTreeMap::new();
    for kind in ResourceType::ALL {
        for order in Order::ALL {
            let key = format!("{}/{}", kind.singular(), order.wire());
            let ids: Vec<String> = store
                .iter_ordered(kind, order)
                .map(|resource| resource.id.clone())
                .collect();
            ordered.insert(key, ids);
        }
    }
    ordered
}

fn observe_page(store: &RegistryStore, expected: &PageObservation, corpus: &Corpus) -> PageResult {
    let kind = resource_type(&expected.resource_type);
    let query = expected.query.clone();
    let request = parse_paging(
        move |name: &str| query.get(name).cloned(),
        corpus.default_limit,
        corpus.max_limit,
    )
    .expect("the corpus only records valid paging queries");

    let ordered: Vec<&nmos_registry_core::resource::RegisteredResource> =
        store.iter_ordered(kind, request.order).collect();
    let collection_max = ordered.last().map(|r| request.cursor_of(r));

    let filters: Vec<(String, String)> = expected
        .filters
        .iter()
        .map(|pair| (pair[0].clone(), pair[1].clone()))
        .collect();

    let matched: Vec<&nmos_registry_core::resource::RegisteredResource> = ordered
        .iter()
        .copied()
        .filter(|resource| matches(resource.body.data(), &filters))
        .collect();

    let page = apply_paging(&matched, collection_max, &request);
    let headers = paging_headers(page.window(), &corpus.base_url, &filters, request.order);

    PageResult {
        ids: page.resources.iter().map(|r| r.id.clone()).collect(),
        headers: headers.into_iter().collect(),
    }
}

struct PageResult {
    ids: Vec<String>,
    headers: BTreeMap<String, String>,
}

fn observe_statistics(store: &RegistryStore) -> Statistics {
    let stats = store.statistics(0, 0);
    Statistics {
        total: stats.total,
        non_extant: stats.non_extant,
        most_recent_update: stats.most_recent_update.to_string(),
        per_type: ResourceType::ALL
            .into_iter()
            .map(|kind| (kind.singular().to_owned(), *stats.per_type.get(kind)))
            .collect(),
    }
}

#[test]
fn the_store_agrees_with_python_step_for_step() {
    let corpus: Corpus = serde_json::from_str(include_str!("store_cases.json"))
        .expect("the store corpus is valid JSON");
    assert!(corpus.steps.len() >= 300, "corpus looks truncated");

    let mut store = RegistryStore::with_intervals(12, 60);

    for (number, step) in corpus.steps.iter().enumerate() {
        let context = format!(
            "step {number} ({} {} {})",
            step.op, step.resource_type, step.id
        );

        let outcome = replay(&mut store, step);
        assert_eq!(
            outcome, step.outcome,
            "{context}: the operation produced a different outcome from Python's",
        );

        store.check_indexes().unwrap_or_else(|why| {
            panic!("{context}: the indexes disagree with the buckets: {why}")
        });
        store.check_children().unwrap_or_else(|why| {
            panic!("{context}: the parent/child graph is inconsistent: {why}")
        });

        let ordered = observe_ordered(&store);
        assert_eq!(
            ordered, step.state.ordered,
            "{context}: the cursor-ordered view differs from Python's",
        );

        for expected in &step.state.pages {
            let actual = observe_page(&store, expected, &corpus);
            assert_eq!(
                actual.ids, expected.ids,
                "{context}: page contents differ for query {:?} filters {:?}",
                expected.query, expected.filters,
            );
            for (name, value) in &expected.headers {
                assert_eq!(
                    actual.headers.get(name),
                    Some(value),
                    "{context}: header {name} differs for query {:?}",
                    expected.query,
                );
            }
        }

        let statistics = observe_statistics(&store);
        assert_eq!(
            statistics.total, step.state.statistics.total,
            "{context}: total differs",
        );
        assert_eq!(
            statistics.non_extant, step.state.statistics.non_extant,
            "{context}: non_extant differs",
        );
        assert_eq!(
            statistics.most_recent_update, step.state.statistics.most_recent_update,
            "{context}: most_recent_update differs",
        );
        assert_eq!(
            statistics.per_type, step.state.statistics.per_type,
            "{context}: per-type counts differ",
        );
    }
}

#[test]
fn the_corpus_exercises_the_paths_it_was_built_for() {
    // Guard the guard. A sequence that only ever registers would compare a
    // great deal of nothing: the interactions are what this corpus is for.
    let corpus: Corpus = serde_json::from_str(include_str!("store_cases.json"))
        .expect("the store corpus is valid JSON");

    let count = |op: &str| corpus.steps.iter().filter(|s| s.op == op).count();
    assert!(
        count("register") > 100,
        "only {} registers",
        count("register")
    );
    assert!(count("delete") > 20, "only {} deletes", count("delete"));
    assert!(count("forget") > 10, "only {} forgets", count("forget"));
    assert!(count("remove_one") > 10);
    assert!(count("set_health") > 10);

    // Refusals, and more than one reason for them.
    let refusals: Vec<&str> = corpus
        .steps
        .iter()
        .filter(|s| s.op == "register")
        .filter_map(|s| s.outcome.get("error").and_then(serde_json::Value::as_str))
        .collect();
    assert!(refusals.len() > 20, "only {} refusals", refusals.len());
    let mut kinds: Vec<&str> = refusals.clone();
    kinds.sort_unstable();
    kinds.dedup();
    assert!(
        kinds.len() >= 3,
        "only {kinds:?} refusal reasons; the interesting ones are not being reached",
    );

    // Cascades, which is where the child ordering and the revive rules meet.
    let cascades = corpus
        .steps
        .iter()
        .filter(|s| s.op == "delete")
        .filter(|s| {
            s.outcome
                .get("removed")
                .and_then(serde_json::Value::as_array)
                .is_some_and(|removed| removed.len() > 1)
        })
        .count();
    assert!(cascades > 0, "no delete ever cascaded");

    // And pages that actually contain something.
    let with_records = corpus
        .steps
        .iter()
        .flat_map(|s| &s.state.pages)
        .filter(|p| !p.ids.is_empty())
        .count();
    assert!(with_records > 50, "only {with_records} non-empty pages");
}
