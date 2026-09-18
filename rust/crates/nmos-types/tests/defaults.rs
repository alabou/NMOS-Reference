// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Optional defaults land; inert ones do not.
//!
//! The descriptors carry a default on 27 members, and only 13 of them are ever
//! applied. Python's rule is `optional AND default`: a member with a default
//! that is *not* optional is still required, and a body omitting it is
//! rejected rather than filled in.
//!
//! That asymmetry is the whole reason the Rust types do not use
//! `#[serde(default)]`, which would have accepted all 27. The decode parity
//! corpus proves the accept/reject half of it across every resource type; these
//! tests prove the other half -- that an applied default reaches the struct
//! with the right value, which a verdict comparison cannot see.

#![allow(clippy::unwrap_used, clippy::expect_used)]

use nmos_types::generated::nrational::NRational;
use serde_json::json;

#[test]
fn an_optional_default_is_applied_when_the_member_is_absent() {
    // NRational.Denominator is optional with default 1.
    let r = NRational::decode(&json!({"numerator": 30})).expect("decodes");
    assert_eq!(
        r.denominator,
        Some(1),
        "the default should have been applied"
    );
}

#[test]
fn a_present_value_is_not_overwritten_by_the_default() {
    let r = NRational::decode(&json!({"numerator": 30, "denominator": 1001})).expect("decodes");
    assert_eq!(r.denominator, Some(1001));
}

#[test]
fn a_default_does_not_make_a_required_member_optional() {
    // Numerator has no default and is required: omitting it is still an error,
    // and the default on its sibling does not change that.
    let err = NRational::decode(&json!({"denominator": 1001}))
        .expect_err("a missing required member is an error");
    assert_eq!(err.message(), "missing required member Numerator");
}

#[test]
fn the_applied_default_survives_re_encoding() {
    // Python's `set_optional_to_default` marks the member DEFINED, so it is
    // written out. An absent-but-defaulted member therefore appears in the
    // encoding, rather than being skipped as an absent optional would be.
    let r = NRational::decode(&json!({"numerator": 30})).expect("decodes");
    let encoded = serde_json::to_value(&r).expect("encodes");
    assert_eq!(
        encoded.get("denominator"),
        Some(&json!(1)),
        "a defaulted member must be encoded, not skipped",
    );
}
