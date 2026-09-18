//! Generated NMOS type: `NRegistrationResourcePost`. DO NOT EDIT.
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

use crate::generated::nregistration_post_device::NRegistrationPostDevice;
use crate::generated::nregistration_post_flow::NRegistrationPostFlow;
use crate::generated::nregistration_post_node::NRegistrationPostNode;
use crate::generated::nregistration_post_receiver::NRegistrationPostReceiver;
use crate::generated::nregistration_post_sender::NRegistrationPostSender;
use crate::generated::nregistration_post_source::NRegistrationPostSource;

/// `NRegistrationResourcePost`: a discriminated union over 6 concrete types.
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
pub enum NRegistrationResourcePost {
    /// `NRegistrationPostNode`.
    NRegistrationPostNode(NRegistrationPostNode),
    /// `NRegistrationPostDevice`.
    NRegistrationPostDevice(NRegistrationPostDevice),
    /// `NRegistrationPostSource`.
    NRegistrationPostSource(NRegistrationPostSource),
    /// `NRegistrationPostFlow`.
    NRegistrationPostFlow(NRegistrationPostFlow),
    /// `NRegistrationPostSender`.
    NRegistrationPostSender(NRegistrationPostSender),
    /// `NRegistrationPostReceiver`.
    NRegistrationPostReceiver(NRegistrationPostReceiver),
}

/// Does this object discriminate as `NRegistrationPostNode`?
fn is_nregistration_post_node(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("type").and_then(Value::as_str) != Some("node") {
        return false;
    }
    true
}

/// Does this object discriminate as `NRegistrationPostDevice`?
fn is_nregistration_post_device(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("type").and_then(Value::as_str) != Some("device") {
        return false;
    }
    true
}

/// Does this object discriminate as `NRegistrationPostSource`?
fn is_nregistration_post_source(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("type").and_then(Value::as_str) != Some("source") {
        return false;
    }
    true
}

/// Does this object discriminate as `NRegistrationPostFlow`?
fn is_nregistration_post_flow(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("type").and_then(Value::as_str) != Some("flow") {
        return false;
    }
    true
}

/// Does this object discriminate as `NRegistrationPostSender`?
fn is_nregistration_post_sender(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("type").and_then(Value::as_str) != Some("sender") {
        return false;
    }
    true
}

/// Does this object discriminate as `NRegistrationPostReceiver`?
fn is_nregistration_post_receiver(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("type").and_then(Value::as_str) != Some("receiver") {
        return false;
    }
    true
}

impl NRegistrationResourcePost {
    /// Decode by discriminating on the object's contents.
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data(
                "expected JSON object for polymorphic NRegistrationResourcePost",
            ));
        };

        if is_nregistration_post_node(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::NRegistrationPostNode(NRegistrationPostNode::decode(
                src,
            )?));
        }
        if is_nregistration_post_device(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::NRegistrationPostDevice(
                NRegistrationPostDevice::decode(src)?,
            ));
        }
        if is_nregistration_post_source(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::NRegistrationPostSource(
                NRegistrationPostSource::decode(src)?,
            ));
        }
        if is_nregistration_post_flow(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::NRegistrationPostFlow(NRegistrationPostFlow::decode(
                src,
            )?));
        }
        if is_nregistration_post_sender(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::NRegistrationPostSender(
                NRegistrationPostSender::decode(src)?,
            ));
        }
        if is_nregistration_post_receiver(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::NRegistrationPostReceiver(
                NRegistrationPostReceiver::decode(src)?,
            ));
        }
        Err(Error::invalid_data(
            "no matching type for polymorphic NRegistrationResourcePost",
        ))
    }
}
