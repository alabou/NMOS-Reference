//! Generated NMOS type: `NNodeInterface`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nnetwork_device::NNetworkDevice;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NNodeInterface`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NNodeInterface {
    /// `chassis_id`. Required.
    #[serde(rename = "chassis_id")]
    pub chassis_id: Nullable<String>,
    /// `port_id`. Required.
    #[serde(rename = "port_id")]
    pub port_id: String,
    /// `name`. Required.
    #[serde(rename = "name")]
    pub name: String,
    /// `attached_network_device`. Optional, so absent means the member was not present.
    #[serde(
        rename = "attached_network_device",
        skip_serializing_if = "Option::is_none"
    )]
    pub attached_network_device: Option<NNetworkDevice>,
}

impl NNodeInterface {
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
                "expected JSON object for NNodeInterface",
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
        let name = match doc.get("name") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let attached_network_device = match doc.get("attached_network_device") {
            Some(v) => Some(NNetworkDevice::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let chassis_id =
            chassis_id.ok_or_else(|| Error::invalid_object("missing required member ChassisId"))?;
        let port_id =
            port_id.ok_or_else(|| Error::invalid_object("missing required member PortId"))?;
        let name = name.ok_or_else(|| Error::invalid_object("missing required member Name"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_chassis_id_nullable_string(chassis_id.as_option().map(String::as_str))?;
        validators::check_port_id_string(&port_id)?;

        Ok(Self {
            chassis_id,
            port_id,
            name,
            attached_network_device,
        })
    }
}
