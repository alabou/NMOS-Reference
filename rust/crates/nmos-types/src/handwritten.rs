// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The two map types the generator does not emit.
//!
//! `NConstraints` and `NTransportConstraints` are listed in
//! `generate._HAND_WRITTEN` and live as hand-written modules *inside* Python's
//! generated tree. They are hand-written for the same reason on both sides:
//! their decode does not read one JSON member, it consumes whatever keys the
//! parent object has left over, which no descriptor can express.
//!
//! Mirrored here so the arrangement matches -- the fingerprint test excludes
//! these names from its orphan check on both trees.

use indexmap::IndexMap;
use nmos_json::error::Result;
use serde::Serialize;
use serde_json::Value;

use crate::generated::nconstraint::NConstraint;
use crate::generated::ntransport_constraint::NTransportConstraint;

/// Keys that belong to the enclosing `NConstraintSet`, not to the constraints.
///
/// This set duplicates `NConstraintSet`'s own member keys, in Python and now
/// here, coupled to the model by nothing. `nmos/codegen/tests/
/// test_namespace_consistency.py` checks the namespace prefixes agree; the
/// membership itself is still maintained by hand on both sides.
///
/// The `urn:x-matrox:` entries are Matrox extensions and the `urn:x-nmos:` ones
/// are standard. That split is deliberate and load-bearing -- mixing the two
/// prefixes makes a constraint set silently fall back to defaults rather than
/// erroring.
const META_KEYS: &[&str] = &[
    "urn:x-nmos:cap:meta:label",
    "urn:x-nmos:cap:meta:enabled",
    "urn:x-nmos:cap:meta:preference",
    "urn:x-matrox:cap:meta:format",
    "urn:x-matrox:cap:meta:layer",
    "urn:x-matrox:cap:meta:layer_enabled",
    "urn:x-matrox:cap:meta:layer_compatibility_groups",
    "urn:x-matrox:cap:meta:info_block",
];

/// The constraints of a constraint set, keyed by constraint URN.
///
/// An `IndexMap` rather than a `HashMap` so the map preserves whatever order it
/// is given. Not because anything depends on it -- RFC 8259 gives object
/// members no ordering, and every consumer here treats the keys as a set -- but
/// because preserving order costs nothing and imposing one would be a choice
/// nobody asked for.
#[derive(Debug, Clone, PartialEq, Serialize, Default)]
#[serde(transparent)]
pub struct NConstraints(pub IndexMap<String, NConstraint>);

impl NConstraints {
    /// Decode from the **parent's** object, taking every key that is not one of
    /// the enclosing constraint set's own.
    ///
    /// A non-object is not an error: Python returns early leaving the map
    /// empty, and a body relying on that is one the registry accepts today.
    pub fn decode(value: &Value) -> Result<Self> {
        let Some(data) = value.as_object() else {
            return Ok(Self::default());
        };
        let mut out = IndexMap::new();
        for (key, item) in data {
            if META_KEYS.contains(&key.as_str()) {
                continue;
            }
            out.insert(key.clone(), NConstraint::decode(item)?);
        }
        Ok(Self(out))
    }

    /// The constraint URNs present, in document order.
    pub fn keys(&self) -> impl Iterator<Item = &str> {
        self.0.keys().map(String::as_str)
    }
}

/// Transport constraints, keyed by property name.
///
/// Unlike [`NConstraints`] this takes **every** key -- there are no meta keys to
/// skip at this level.
#[derive(Debug, Clone, PartialEq, Serialize, Default)]
#[serde(transparent)]
pub struct NTransportConstraints(pub IndexMap<String, NTransportConstraint>);

impl NTransportConstraints {
    /// Decode from the parent's object. A non-object yields an empty map, as in
    /// Python.
    pub fn decode(value: &Value) -> Result<Self> {
        let Some(data) = value.as_object() else {
            return Ok(Self::default());
        };
        let mut out = IndexMap::new();
        for (key, item) in data {
            out.insert(key.clone(), NTransportConstraint::decode(item)?);
        }
        Ok(Self(out))
    }

    /// The property names present, in document order. This is what the
    /// `Check*TransportConstraints` validators are given.
    pub fn keys(&self) -> impl Iterator<Item = &str> {
        self.0.keys().map(String::as_str)
    }
}

/// Re-exported so generated modules can name it without knowing where enum
/// identifiers live.
pub use nmos_json::EnumId as ConstraintKey;

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn constraints_skip_the_enclosing_sets_own_keys() {
        let v = json!({
            "urn:x-nmos:cap:meta:enabled": true,
            "urn:x-matrox:cap:meta:layer": 0,
            "urn:x-nmos:cap:format:color_sampling": {"enum": ["YCbCr-4:2:2"]},
        });
        let c = NConstraints::decode(&v).expect("decodes");
        assert_eq!(
            c.keys().collect::<Vec<_>>(),
            vec!["urn:x-nmos:cap:format:color_sampling"],
        );
    }

    #[test]
    fn a_non_object_yields_an_empty_map_rather_than_an_error() {
        assert!(
            NConstraints::decode(&json!("nope"))
                .expect("no error")
                .0
                .is_empty()
        );
        assert!(
            NTransportConstraints::decode(&json!(5))
                .expect("no error")
                .0
                .is_empty()
        );
    }

    #[test]
    fn transport_constraints_keep_every_key() {
        // Every property is taken, none invented. What is NOT asserted is the
        // order: RFC 8259 gives object members no ordering, so two decodes
        // listing the same properties differently are equally correct. The
        // validators this feeds consume the keys as a set.
        let v = json!({"rtp_enabled": {}, "source_ip": {}, "ext_x": {}});
        let c = NTransportConstraints::decode(&v).expect("decodes");
        let mut keys: Vec<_> = c.keys().collect();
        keys.sort_unstable();
        assert_eq!(keys, vec!["ext_x", "rtp_enabled", "source_ip"]);
    }
}
