//! Generated NMOS type: `NFlowCore`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nrational::NRational;
use crate::generated::nresource_core::NResourceCore;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NFlowCore`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NFlowCore {
    /// `ResourceCore`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub resource_core: NResourceCore,
    /// `source_id`. Required.
    #[serde(rename = "source_id")]
    pub source_id: String,
    /// `device_id`. Required.
    #[serde(rename = "device_id")]
    pub device_id: String,
    /// `parents`. Required.
    #[serde(rename = "parents")]
    pub parents: Vec<String>,
    /// `grain_rate`. Optional, so absent means the member was not present.
    #[serde(rename = "grain_rate", skip_serializing_if = "Option::is_none")]
    pub grain_rate: Option<NRational>,
    /// `urn:x-matrox:layer`. Optional, so absent means the member was not present.
    #[serde(rename = "urn:x-matrox:layer", skip_serializing_if = "Option::is_none")]
    pub layer: Option<i64>,
    /// `urn:x-matrox:layer_compatibility_groups`. Optional, so absent means the member was not present.
    #[serde(
        rename = "urn:x-matrox:layer_compatibility_groups",
        skip_serializing_if = "Option::is_none"
    )]
    pub layer_compatibility_groups: Option<Vec<i64>>,
}

impl NFlowCore {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NFlowCore"));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let resource_core = NResourceCore::decode(src)?;
        let source_id = match doc.get("source_id") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let device_id = match doc.get("device_id") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let parents = match doc.get("parents") {
            Some(v) => Some(decode::array_of_string(v)?),
            None => None,
        };
        let grain_rate = match doc.get("grain_rate") {
            Some(v) => Some(NRational::decode(v)?),
            None => None,
        };
        let layer = match doc.get("urn:x-matrox:layer") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let layer_compatibility_groups = match doc.get("urn:x-matrox:layer_compatibility_groups") {
            Some(v) => Some(decode::array_of_int(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let source_id =
            source_id.ok_or_else(|| Error::invalid_object("missing required member SourceId"))?;
        let device_id =
            device_id.ok_or_else(|| Error::invalid_object("missing required member DeviceId"))?;
        let parents =
            parents.ok_or_else(|| Error::invalid_object("missing required member Parents"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_resource_id_string(&source_id)?;
        validators::check_resource_id_string(&device_id)?;
        validators::check_array_of_resource_id_string(parents.iter().map(String::as_str))?;

        Ok(Self {
            resource_core,
            source_id,
            device_id,
            parents,
            grain_rate,
            layer,
            layer_compatibility_groups,
        })
    }
}
