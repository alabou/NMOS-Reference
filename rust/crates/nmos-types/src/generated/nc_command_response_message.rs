//! Generated NMOS type: `NcCommandResponseMessage`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_array_of_response::NcArrayOfResponse;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcCommandResponseMessage`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcCommandResponseMessage {
    /// `messageType`. Required.
    #[serde(rename = "messageType")]
    pub message_type: i64,
    /// `responses`. Required.
    #[serde(rename = "responses")]
    pub responses: NcArrayOfResponse,
}

impl NcCommandResponseMessage {
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
                "expected JSON object for NcCommandResponseMessage",
            ));
        };

        let message_type = match doc.get("messageType") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let responses = match doc.get("responses") {
            Some(v) => Some(NcArrayOfResponse::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let message_type = message_type
            .ok_or_else(|| Error::invalid_object("missing required member MessageType"))?;
        let responses =
            responses.ok_or_else(|| Error::invalid_object("missing required member Responses"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_command_response_message_type(message_type)?;

        Ok(Self {
            message_type,
            responses,
        })
    }
}
