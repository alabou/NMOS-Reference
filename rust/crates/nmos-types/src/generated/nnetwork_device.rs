//! Generated NMOS type: `NNetworkDevice`. DO NOT EDIT.
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

/// `NNetworkDevice`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NNetworkDevice {
    /// `chassis_id`. Required.
    #[serde(rename = "chassis_id")]
    pub chassis_id: Nullable<String>,
    /// `port_id`. Required.
    #[serde(rename = "port_id")]
    pub port_id: String,
}

impl NNetworkDevice {
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
                "expected JSON object for NNetworkDevice",
            ));
        };

        let chassis_id = match doc.get("chassis_id") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let port_id = match doc.get("port_id") {
            Some(v) => decode::string(v)?,
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let chassis_id =
            chassis_id.ok_or_else(|| Error::invalid_object("missing required member ChassisId"))?;
        let port_id =
            port_id.ok_or_else(|| Error::invalid_object("missing required member PortId"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_chassis_id_nullable_string(chassis_id.as_option().map(String::as_str))?;

        Ok(Self {
            chassis_id,
            port_id,
        })
    }
}
