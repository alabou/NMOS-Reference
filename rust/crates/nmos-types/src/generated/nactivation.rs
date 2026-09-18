//! Generated NMOS type: `NActivation`. DO NOT EDIT.
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

/// `NActivation`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NActivation {
    /// `mode`. Required.
    #[serde(rename = "mode")]
    pub mode: Nullable<String>,
    /// `requested_time`. Optional, so absent means the member was not present.
    #[serde(rename = "requested_time", skip_serializing_if = "Option::is_none")]
    pub requested_time: Option<Nullable<String>>,
    /// `activation_time`. Optional, so absent means the member was not present.
    #[serde(rename = "activation_time", skip_serializing_if = "Option::is_none")]
    pub activation_time: Option<Nullable<String>>,
}

impl NActivation {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NActivation"));
        };

        let mode = match doc.get("mode") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let requested_time = match doc.get("requested_time") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let activation_time = match doc.get("activation_time") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let mode = mode.ok_or_else(|| Error::invalid_object("missing required member Mode"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_activation_mode(mode.as_option().map(String::as_str))?;

        Ok(Self {
            mode,
            requested_time,
            activation_time,
        })
    }
}
