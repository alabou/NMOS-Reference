//! Generated NMOS type: `NDevice`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::narray_of_device_control::NArrayOfDeviceControl;
use crate::generated::nresource_core::NResourceCore;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NDevice`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NDevice {
    /// `ResourceCore`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub resource_core: NResourceCore,
    /// `type`. Required.
    #[serde(rename = "type")]
    pub r#type: EnumId,
    /// `node_id`. Required.
    #[serde(rename = "node_id")]
    pub node_id: String,
    /// `senders`. Required.
    #[serde(rename = "senders")]
    pub senders: Vec<String>,
    /// `receivers`. Required.
    #[serde(rename = "receivers")]
    pub receivers: Vec<String>,
    /// `controls`. Required.
    #[serde(rename = "controls")]
    pub controls: NArrayOfDeviceControl,
}

impl NDevice {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NDevice"));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let resource_core = NResourceCore::decode(src)?;
        let r#type = match doc.get("type") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let node_id = match doc.get("node_id") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let senders = match doc.get("senders") {
            Some(v) => Some(decode::array_of_string(v)?),
            None => None,
        };
        let receivers = match doc.get("receivers") {
            Some(v) => Some(decode::array_of_string(v)?),
            None => None,
        };
        let controls = match doc.get("controls") {
            Some(v) => Some(NArrayOfDeviceControl::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let r#type = r#type.ok_or_else(|| Error::invalid_object("missing required member Type"))?;
        let node_id =
            node_id.ok_or_else(|| Error::invalid_object("missing required member NodeId"))?;
        let senders =
            senders.ok_or_else(|| Error::invalid_object("missing required member Senders"))?;
        let receivers =
            receivers.ok_or_else(|| Error::invalid_object("missing required member Receivers"))?;
        let controls =
            controls.ok_or_else(|| Error::invalid_object("missing required member Controls"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_device_type(&r#type)?;
        validators::check_resource_id_string(&node_id)?;
        validators::check_array_of_resource_id_string(senders.iter().map(String::as_str))?;
        validators::check_array_of_resource_id_string(receivers.iter().map(String::as_str))?;

        Ok(Self {
            resource_core,
            r#type,
            node_id,
            senders,
            receivers,
            controls,
        })
    }
}
