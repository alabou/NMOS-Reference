//! Generated NMOS type: `NInput`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::ninput_status::NInputStatus;
use crate::generated::nresource_core::NResourceCore;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NInput`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NInput {
    /// `ResourceCore`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub resource_core: NResourceCore,
    /// `connected`. Required.
    #[serde(rename = "connected")]
    pub connected: bool,
    /// `edid_support`. Required.
    #[serde(rename = "edid_support")]
    pub edid_support: bool,
    /// `status`. Required.
    #[serde(rename = "status")]
    pub status: NInputStatus,
    /// `source_id`. Optional, so absent means the member was not present.
    #[serde(rename = "source_id", skip_serializing_if = "Option::is_none")]
    pub source_id: Option<String>,
    /// `device_id`. Required.
    #[serde(rename = "device_id")]
    pub device_id: String,
}

impl NInput {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NInput"));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let resource_core = NResourceCore::decode(src)?;
        let connected = match doc.get("connected") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let edid_support = match doc.get("edid_support") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let status = match doc.get("status") {
            Some(v) => Some(NInputStatus::decode(v)?),
            None => None,
        };
        let source_id = match doc.get("source_id") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let device_id = match doc.get("device_id") {
            Some(v) => decode::string(v)?,
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let connected =
            connected.ok_or_else(|| Error::invalid_object("missing required member Connected"))?;
        let edid_support = edid_support
            .ok_or_else(|| Error::invalid_object("missing required member EdidSupport"))?;
        let status =
            status.ok_or_else(|| Error::invalid_object("missing required member Status"))?;
        let device_id =
            device_id.ok_or_else(|| Error::invalid_object("missing required member DeviceId"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        if let Some(v) = &source_id {
            validators::check_resource_id_string(v)?;
        }
        validators::check_resource_id_string(&device_id)?;

        Ok(Self {
            resource_core,
            connected,
            edid_support,
            status,
            source_id,
            device_id,
        })
    }
}
