//! Generated NMOS type: `NcMessage`. DO NOT EDIT.
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

use crate::generated::nc_command_message::NcCommandMessage;
use crate::generated::nc_command_response_message::NcCommandResponseMessage;
use crate::generated::nc_error_message::NcErrorMessage;
use crate::generated::nc_notification_message::NcNotificationMessage;
use crate::generated::nc_subscription_message::NcSubscriptionMessage;
use crate::generated::nc_subscription_response_message::NcSubscriptionResponseMessage;

/// `NcMessage`: a discriminated union over 6 concrete types.
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
pub enum NcMessage {
    /// `NcCommandMessage`.
    NcCommandMessage(NcCommandMessage),
    /// `NcCommandResponseMessage`.
    NcCommandResponseMessage(NcCommandResponseMessage),
    /// `NcNotificationMessage`.
    NcNotificationMessage(NcNotificationMessage),
    /// `NcSubscriptionMessage`.
    NcSubscriptionMessage(NcSubscriptionMessage),
    /// `NcSubscriptionResponseMessage`.
    NcSubscriptionResponseMessage(NcSubscriptionResponseMessage),
    /// `NcErrorMessage`.
    NcErrorMessage(NcErrorMessage),
}

/// Does this object discriminate as `NcCommandMessage`?
fn is_nc_command_message(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("message_type").and_then(Value::as_f64) != Some(0_f64) {
        return false;
    }
    true
}

/// Does this object discriminate as `NcCommandResponseMessage`?
fn is_nc_command_response_message(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("message_type").and_then(Value::as_f64) != Some(1_f64) {
        return false;
    }
    true
}

/// Does this object discriminate as `NcNotificationMessage`?
fn is_nc_notification_message(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("message_type").and_then(Value::as_f64) != Some(2_f64) {
        return false;
    }
    true
}

/// Does this object discriminate as `NcSubscriptionMessage`?
fn is_nc_subscription_message(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("message_type").and_then(Value::as_f64) != Some(3_f64) {
        return false;
    }
    true
}

/// Does this object discriminate as `NcSubscriptionResponseMessage`?
fn is_nc_subscription_response_message(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("message_type").and_then(Value::as_f64) != Some(4_f64) {
        return false;
    }
    true
}

/// Does this object discriminate as `NcErrorMessage`?
fn is_nc_error_message(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("message_type").and_then(Value::as_f64) != Some(5_f64) {
        return false;
    }
    true
}

impl NcMessage {
    /// Decode by discriminating on the object's contents.
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data(
                "expected JSON object for polymorphic NcMessage",
            ));
        };

        if is_nc_command_message(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::NcCommandMessage(NcCommandMessage::decode(src)?));
        }
        if is_nc_command_response_message(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::NcCommandResponseMessage(
                NcCommandResponseMessage::decode(src)?,
            ));
        }
        if is_nc_notification_message(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::NcNotificationMessage(NcNotificationMessage::decode(
                src,
            )?));
        }
        if is_nc_subscription_message(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::NcSubscriptionMessage(NcSubscriptionMessage::decode(
                src,
            )?));
        }
        if is_nc_subscription_response_message(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::NcSubscriptionResponseMessage(
                NcSubscriptionResponseMessage::decode(src)?,
            ));
        }
        if is_nc_error_message(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::NcErrorMessage(NcErrorMessage::decode(src)?));
        }
        Err(Error::invalid_data(
            "no matching type for polymorphic NcMessage",
        ))
    }
}
