//! Generated NMOS type: `NReceiverSubscription`. DO NOT EDIT.
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

/// `NReceiverSubscription`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NReceiverSubscription {
    /// `sender_id`. Required.
    #[serde(rename = "sender_id")]
    pub sender_id: Nullable<String>,
    /// `active`. Required.
    #[serde(rename = "active")]
    pub active: bool,
}

impl NReceiverSubscription {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data(
                "expected JSON object for NReceiverSubscription",
            ));
        };

        let sender_id = match doc.get("sender_id") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let active = match doc.get("active") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let sender_id =
            sender_id.ok_or_else(|| Error::invalid_object("missing required member SenderId"))?;
        let active =
            active.ok_or_else(|| Error::invalid_object("missing required member Active"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_resource_id_nullable_string(sender_id.as_option().map(String::as_str))?;

        Ok(Self { sender_id, active })
    }
}
