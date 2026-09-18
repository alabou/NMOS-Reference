//! Generated NMOS type: `NClock`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

use crate::generated::nclock_internal::NClockInternal;
use crate::generated::nclock_ptp::NClockPtp;

/// `NClock`: a discriminated union over 2 concrete types.
///
/// # Dispatch is ordered and committal
///
/// The variants are tried in the order the model declares them, and the FIRST
/// whose predicate matches decides the type -- its decode error is the answer,
/// even when a later variant would have decoded cleanly. Python behaves the
/// same way, and that is why this is not `#[serde(untagged)]` on the way in:
/// untagged picks the first variant that *deserialises*, not the first whose
/// *discriminator* matches, and it backtracks. A body whose `format` says
/// video but whose components are empty must report the empty components, not
/// fall through and be mistaken for something else.
///
/// Declaration order is load-bearing for a second reason: some predicates only
/// exclude (`notin`), so a variant declared later may match a superset of an
/// earlier one. The emitter never sorts these.
///
/// `Serialize` IS untagged, which is correct on the way out -- it writes the
/// inner value with no added tag, exactly as Python's type-switch encode does.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(untagged)]
// Variants differ in size because the concrete NMOS types do. Boxing them to
// even that out would change the public shape of every match arm, for a type
// that is decoded once per registration and then dropped.
#[allow(clippy::large_enum_variant)]
pub enum NClock {
    /// `NClockInternal`.
    Internal(NClockInternal),
    /// `NClockPtp`.
    Ptp(NClockPtp),
}

/// Does this object discriminate as `NClockInternal`?
fn is_nclock_internal(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("ref_type").and_then(Value::as_str) != Some("internal") {
        return false;
    }
    true
}

/// Does this object discriminate as `NClockPtp`?
fn is_nclock_ptp(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("ref_type").and_then(Value::as_str) != Some("ptp") {
        return false;
    }
    true
}

impl NClock {
    /// Decode by discriminating on the object's contents.
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data(
                "expected JSON object for polymorphic NClock",
            ));
        };

        if is_nclock_internal(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::Internal(NClockInternal::decode(src)?));
        }
        if is_nclock_ptp(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::Ptp(NClockPtp::decode(src)?));
        }
        Err(Error::invalid_data(
            "no matching type for polymorphic NClock",
        ))
    }
}
