//! Generated NMOS type: `NConstraint`. DO NOT EDIT.
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

use crate::generated::nconstraint_bool::NConstraintBool;
use crate::generated::nconstraint_float::NConstraintFloat;
use crate::generated::nconstraint_int::NConstraintInt;
use crate::generated::nconstraint_rational::NConstraintRational;
use crate::generated::nconstraint_string::NConstraintString;

/// `NConstraint`: a discriminated union over 5 concrete types.
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
pub enum NConstraint {
    /// `NConstraintBool`.
    Bool(NConstraintBool),
    /// `NConstraintInt`.
    Int(NConstraintInt),
    /// `NConstraintFloat`.
    Float(NConstraintFloat),
    /// `NConstraintString`.
    String(NConstraintString),
    /// `NConstraintRational`.
    Rational(NConstraintRational),
}

/// Does this object discriminate as `NConstraintBool`?
fn is_nconstraint_bool(doc: &serde_json::Map<String, Value>) -> bool {
    // Hardcoded to "enum", ignoring this predicate's json_key -- Python does
    // the same, and the difference is not observable because every caller
    // passes "enum" anyway.
    match doc.get("enum").and_then(Value::as_array) {
        Some(items) => match items.first() {
            Some(first) if first.is_boolean() => {}
            _ => return false,
        },
        None => return false,
    }
    true
}

/// Does this object discriminate as `NConstraintInt`?
fn is_nconstraint_int(doc: &serde_json::Map<String, Value>) -> bool {
    {
        // Python spells this `data.get("enum", data.get("minimum",
        // data.get("maximum")))`, and evaluates those defaults EAGERLY -- so it
        // selects on key PRESENCE, taking "enum" even when its value is null.
        let probe = if doc.contains_key("enum") {
            doc.get("enum")
        } else if doc.contains_key("minimum") {
            doc.get("minimum")
        } else {
            doc.get("maximum")
        };
        let ok = match probe {
            Some(Value::Array(items)) if !items.is_empty() => {
                let Some(first) = items.first() else {
                    return false;
                };
                // A bool is excluded: `isinstance(True, int)` is true in
                // Python, so it says so explicitly. Here the variants are
                // already distinct and the outcome is the same.
                first.is_i64() || first.is_u64()
            }
            Some(Value::Array(_)) => false,
            Some(v) => v.is_i64() || v.is_u64(),
            None => false,
        };
        if !ok {
            return false;
        }
    }
    true
}

/// Does this object discriminate as `NConstraintFloat`?
fn is_nconstraint_float(doc: &serde_json::Map<String, Value>) -> bool {
    {
        // Python spells this `data.get("enum", data.get("minimum",
        // data.get("maximum")))`, and evaluates those defaults EAGERLY -- so it
        // selects on key PRESENCE, taking "enum" even when its value is null.
        let probe = if doc.contains_key("enum") {
            doc.get("enum")
        } else if doc.contains_key("minimum") {
            doc.get("minimum")
        } else {
            doc.get("maximum")
        };
        let ok = match probe {
            Some(Value::Array(items)) if !items.is_empty() => {
                let Some(first) = items.first() else {
                    return false;
                };
                // An int is NOT a float to `isinstance`, so `5` fails here.
                first.is_f64()
            }
            Some(Value::Array(_)) => false,
            Some(v) => v.is_f64(),
            None => false,
        };
        if !ok {
            return false;
        }
    }
    true
}

/// Does this object discriminate as `NConstraintString`?
fn is_nconstraint_string(doc: &serde_json::Map<String, Value>) -> bool {
    match doc.get("enum").and_then(Value::as_array) {
        Some(items) => match items.first() {
            Some(first) if first.is_string() => {}
            _ => return false,
        },
        None => return false,
    }
    true
}

/// Does this object discriminate as `NConstraintRational`?
fn is_nconstraint_rational(doc: &serde_json::Map<String, Value>) -> bool {
    // Always matches; declared last so it is the last resort.
    let _ = doc;
    true
}

impl NConstraint {
    /// Decode by discriminating on the object's contents.
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data(
                "expected JSON object for polymorphic NConstraint",
            ));
        };

        if is_nconstraint_bool(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::Bool(NConstraintBool::decode(src)?));
        }
        if is_nconstraint_int(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::Int(NConstraintInt::decode(src)?));
        }
        if is_nconstraint_float(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::Float(NConstraintFloat::decode(src)?));
        }
        if is_nconstraint_string(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::String(NConstraintString::decode(src)?));
        }
        if is_nconstraint_rational(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::Rational(NConstraintRational::decode(src)?));
        }
        Err(Error::invalid_data(
            "no matching type for polymorphic NConstraint",
        ))
    }
}
