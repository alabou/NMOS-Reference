//! Generated NMOS type: `NcCommandMessage`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_array_of_command::NcArrayOfCommand;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcCommandMessage`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcCommandMessage {
    /// `messageType`. Required.
    #[serde(rename = "messageType")]
    pub message_type: i64,
    /// `commands`. Required.
    #[serde(rename = "commands")]
    pub commands: NcArrayOfCommand,
}

impl NcCommandMessage {
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
                "expected JSON object for NcCommandMessage",
            ));
        };

        let message_type = match doc.get("messageType") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let commands = match doc.get("commands") {
            Some(v) => Some(NcArrayOfCommand::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let message_type = message_type
            .ok_or_else(|| Error::invalid_object("missing required member MessageType"))?;
        let commands =
            commands.ok_or_else(|| Error::invalid_object("missing required member Commands"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_command_message_type(message_type)?;

        Ok(Self {
            message_type,
            commands,
        })
    }
}
