//! Generated NMOS type: `NSourceCore`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nrational::NRational;
use crate::generated::nresource_core::NResourceCore;
use crate::generated::nsource_capabilities::NSourceCapabilities;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NSourceCore`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NSourceCore {
    /// `ResourceCore`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub resource_core: NResourceCore,
    /// `caps`. Required.
    #[serde(rename = "caps")]
    pub caps: NSourceCapabilities,
    /// `urn:x-matrox:receiver_id`. Optional, so absent means the member was not present.
    #[serde(
        rename = "urn:x-matrox:receiver_id",
        skip_serializing_if = "Option::is_none"
    )]
    pub receiver_id: Option<Nullable<String>>,
    /// `device_id`. Required.
    #[serde(rename = "device_id")]
    pub device_id: String,
    /// `parents`. Required.
    #[serde(rename = "parents")]
    pub parents: Vec<String>,
    /// `clock_name`. Required.
    #[serde(rename = "clock_name")]
    pub clock_name: Nullable<String>,
    /// `grain_rate`. Optional, so absent means the member was not present.
    #[serde(rename = "grain_rate", skip_serializing_if = "Option::is_none")]
    pub grain_rate: Option<NRational>,
    /// `urn:x-matrox:layer`. Optional, so absent means the member was not present.
    #[serde(rename = "urn:x-matrox:layer", skip_serializing_if = "Option::is_none")]
    pub layer: Option<i64>,
    /// `urn:x-matrox:synchronous_media`. Optional, so absent means the member was not present.
    #[serde(
        rename = "urn:x-matrox:synchronous_media",
        skip_serializing_if = "Option::is_none"
    )]
    pub synchronous_media: Option<bool>,
}

impl NSourceCore {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NSourceCore"));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let resource_core = NResourceCore::decode(src)?;
        let caps = match doc.get("caps") {
            Some(v) => Some(NSourceCapabilities::decode(v)?),
            None => None,
        };
        let receiver_id = match doc.get("urn:x-matrox:receiver_id") {
            Some(v) => Some(decode::nullable_string(v)?),
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
        let clock_name = match doc.get("clock_name") {
            Some(v) => Some(decode::nullable_string(v)?),
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
        let synchronous_media = match doc.get("urn:x-matrox:synchronous_media") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };

        // Optional defaults, applied between decode and the required check --
        // Python's `set_optional_to_default()`, in the same position.
        //
        // The rule is `optional AND default`, and it is narrower than the
        // descriptors read: of 27 members carrying a default, 14 are NOT
        // optional and their default is therefore inert -- a body omitting one
        // is REJECTED, not filled in. `#[serde(default)]` would have quietly
        // accepted all 27, so this step is written rather than derived.
        let receiver_id = receiver_id.or(Some(Nullable::Null));

        // Required presence, for every member, before any assertion runs.
        let caps = caps.ok_or_else(|| Error::invalid_object("missing required member Caps"))?;
        let device_id =
            device_id.ok_or_else(|| Error::invalid_object("missing required member DeviceId"))?;
        let parents =
            parents.ok_or_else(|| Error::invalid_object("missing required member Parents"))?;
        let clock_name =
            clock_name.ok_or_else(|| Error::invalid_object("missing required member ClockName"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        if let Some(v) = &receiver_id {
            validators::check_resource_id_nullable_string(v.as_option().map(String::as_str))?;
        }
        validators::check_resource_id_string(&device_id)?;
        validators::check_array_of_resource_id_string(parents.iter().map(String::as_str))?;
        validators::check_clock_name_nullable_string(clock_name.as_option().map(String::as_str))?;

        Ok(Self {
            resource_core,
            caps,
            receiver_id,
            device_id,
            parents,
            clock_name,
            grain_rate,
            layer,
            synchronous_media,
        })
    }
}
