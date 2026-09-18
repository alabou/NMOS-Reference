// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Enum identifiers, and why decoding one can never fail.
//!
//! Python's `EnumRegistry.auto_lookup(s)` registers an unseen string rather
//! than rejecting it, so `NEnum.decode_value` fails only when the JSON value is
//! not a string at all -- never because the string was not a known member. That
//! is deliberate: a Node may carry a vendor-defined transport or format URN the
//! registry has no constant for, and rejecting at decode would make the registry
//! refuse resources it is supposed to store and serve back untouched.
//!
//! Validity is decided later and elsewhere, by the `Check*` assertions in
//! `crate::validators`, which test membership in an explicit set. Keeping those
//! two steps apart is what lets an unknown enum be *stored* while a known-but-
//! wrong one is *rejected*.
//!
//! Python compares `EnumId` by identity (`is`) in hot paths and by string
//! equality elsewhere, and documents both as correct. Only the string equality
//! is semantics, so that is what is implemented here.

use std::fmt;

use serde::{Serialize, Serializer};

/// An enum value from a JSON document.
///
/// Holds the string as written. Generated code compares against literals that
/// the Python emitter has already inlined at generation time -- `nclock.py`
/// contains `!= "internal"`, not a reference to a constant -- so no registry of
/// known members is needed to decode or dispatch.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct EnumId(Box<str>);

impl EnumId {
    /// Take a string as an enum value. Cannot fail, by design -- see the module
    /// documentation.
    #[must_use]
    pub fn new(value: impl Into<Box<str>>) -> Self {
        Self(value.into())
    }

    /// The value as written in the document.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for EnumId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl PartialEq<str> for EnumId {
    fn eq(&self, other: &str) -> bool {
        &*self.0 == other
    }
}

impl PartialEq<&str> for EnumId {
    fn eq(&self, other: &&str) -> bool {
        &*self.0 == *other
    }
}

impl Serialize for EnumId {
    /// Encodes as the string it holds. Python's `EnumId` is an identity object
    /// over a string; only the string reaches JSON.
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_unknown_value_is_accepted() {
        // The whole point: a vendor URN the registry has no constant for must
        // still decode, so the resource can be stored and served back.
        let id = EnumId::new("urn:x-acme:transport:something-new");
        assert_eq!(id.as_str(), "urn:x-acme:transport:something-new");
    }

    #[test]
    fn equality_is_by_string() {
        assert_eq!(EnumId::new("internal"), EnumId::new("internal"));
        assert!(EnumId::new("internal") == "internal");
        assert_ne!(EnumId::new("internal"), EnumId::new("ptp"));
    }
}
