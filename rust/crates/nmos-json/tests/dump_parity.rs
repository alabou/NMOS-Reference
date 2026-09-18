// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Differential test: synthesised JSON must come out byte-identical to Python.
//!
//! Everything the registry *makes up* rather than stores -- the discovery
//! ladders, subscription responses, health responses, error bodies -- goes out
//! through `JsonEngine.dump_any`, which is `json.dumps` with its DEFAULT
//! separators. So the bytes carry `", "` and `": "` where `serde_json` writes
//! neither.
//!
//! `dump_cases.json` records what Python actually wrote, from
//! `nmos/codegen/tests/_dump_corpus.py`.
//!
//! # Why this test carries its own value type
//!
//! Comparing bytes means the input has to keep the key order Python used.
//! `serde_json::Value` only does that with the `preserve_order` feature, which
//! this workspace deliberately does not enable -- it costs 25% on every parse
//! and nothing in the registry needs it.
//!
//! Turning it on just for tests would be worse than not testing: features
//! unify across a build, so the test binary would exercise a `Value` the
//! shipping code never sees. So the ordering this test needs lives in the test,
//! in `Ordered` below, and the library stays as it ships.

#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use indexmap::IndexMap;
use nmos_json::engine::dump_any;
use serde::de::{MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer, Serialize};
use std::fmt;

/// A JSON value that remembers the order its object keys arrived in.
#[derive(Debug, Serialize)]
#[serde(untagged)]
enum Ordered {
    Null,
    Bool(bool),
    Number(serde_json::Number),
    String(String),
    Array(Vec<Ordered>),
    Object(IndexMap<String, Ordered>),
}

impl<'de> Deserialize<'de> for Ordered {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct V;

        impl<'de> Visitor<'de> for V {
            type Value = Ordered;

            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("any JSON value")
            }

            fn visit_unit<E>(self) -> Result<Ordered, E> {
                Ok(Ordered::Null)
            }
            fn visit_none<E>(self) -> Result<Ordered, E> {
                Ok(Ordered::Null)
            }
            fn visit_bool<E>(self, v: bool) -> Result<Ordered, E> {
                Ok(Ordered::Bool(v))
            }
            fn visit_i64<E>(self, v: i64) -> Result<Ordered, E> {
                Ok(Ordered::Number(v.into()))
            }
            fn visit_u64<E>(self, v: u64) -> Result<Ordered, E> {
                Ok(Ordered::Number(v.into()))
            }
            fn visit_f64<E>(self, v: f64) -> Result<Ordered, E> {
                Ok(serde_json::Number::from_f64(v).map_or(Ordered::Null, Ordered::Number))
            }
            fn visit_str<E>(self, v: &str) -> Result<Ordered, E> {
                Ok(Ordered::String(v.to_owned()))
            }

            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Ordered, A::Error> {
                let mut items = Vec::new();
                while let Some(item) = seq.next_element()? {
                    items.push(item);
                }
                Ok(Ordered::Array(items))
            }

            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Ordered, A::Error> {
                // The whole reason this type exists: entries are kept in the
                // order the document listed them.
                let mut entries = IndexMap::new();
                while let Some((key, value)) = map.next_entry()? {
                    entries.insert(key, value);
                }
                Ok(Ordered::Object(entries))
            }
        }

        deserializer.deserialize_any(V)
    }
}

#[derive(Debug, Deserialize)]
struct Case {
    label: String,
    value: Ordered,
    dumped: String,
}

#[test]
fn synthesised_json_matches_python_byte_for_byte() {
    let cases: Vec<Case> = serde_json::from_str(include_str!("dump_cases.json"))
        .expect("the dump corpus is valid JSON");
    assert!(cases.len() >= 10, "corpus looks truncated: {}", cases.len());

    let mut wrong = Vec::new();
    for case in &cases {
        let got = dump_any(&case.value).expect("serialises");
        if got != case.dumped {
            wrong.push(format!(
                "{}:\n      rust   {got}\n      python {}",
                case.label, case.dumped,
            ));
        }
    }

    assert!(
        wrong.is_empty(),
        "{} of {} structures differ:\n    {}",
        wrong.len(),
        cases.len(),
        wrong.join("\n    "),
    );
}

#[test]
fn the_ordered_type_really_preserves_order() {
    // Guard the guard. If `Ordered` ever sorted its keys, every case above
    // would still compare cleanly against a corpus that had also been sorted,
    // and the test would prove nothing.
    let parsed: Ordered = serde_json::from_str(r#"{"z": 1, "a": 2, "m": 3}"#).expect("parses");
    let Ordered::Object(map) = &parsed else {
        panic!("expected an object")
    };
    assert_eq!(map.keys().collect::<Vec<_>>(), vec!["z", "a", "m"]);
}
