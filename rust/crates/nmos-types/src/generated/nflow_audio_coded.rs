//! Generated NMOS type: `NFlowAudioCoded`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nflow_core::NFlowCore;
use crate::generated::nrational::NRational;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NFlowAudioCoded`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NFlowAudioCoded {
    /// `FlowCore`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub flow_core: NFlowCore,
    /// `format`. Required.
    #[serde(rename = "format")]
    pub format: EnumId,
    /// `media_type`. Required.
    #[serde(rename = "media_type")]
    pub media_type: EnumId,
    /// `sample_rate`. Required.
    #[serde(rename = "sample_rate")]
    pub sample_rate: NRational,
    /// `profile`. Optional, so absent means the member was not present.
    #[serde(rename = "profile", skip_serializing_if = "Option::is_none")]
    pub profile: Option<EnumId>,
    /// `level`. Optional, so absent means the member was not present.
    #[serde(rename = "level", skip_serializing_if = "Option::is_none")]
    pub level: Option<EnumId>,
    /// `bit_rate`. Optional, so absent means the member was not present.
    #[serde(rename = "bit_rate", skip_serializing_if = "Option::is_none")]
    pub bitrate: Option<i64>,
    /// `constant_bit_rate`. Optional, so absent means the member was not present.
    #[serde(rename = "constant_bit_rate", skip_serializing_if = "Option::is_none")]
    pub constant_bitrate: Option<bool>,
}

impl NFlowAudioCoded {
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
                "expected JSON object for NFlowAudioCoded",
            ));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let flow_core = NFlowCore::decode(src)?;
        let format = match doc.get("format") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let media_type = match doc.get("media_type") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let sample_rate = match doc.get("sample_rate") {
            Some(v) => Some(NRational::decode(v)?),
            None => None,
        };
        let profile = match doc.get("profile") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let level = match doc.get("level") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let bitrate = match doc.get("bit_rate") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let constant_bitrate = match doc.get("constant_bit_rate") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let format =
            format.ok_or_else(|| Error::invalid_object("missing required member Format"))?;
        let media_type =
            media_type.ok_or_else(|| Error::invalid_object("missing required member MediaType"))?;
        let sample_rate = sample_rate
            .ok_or_else(|| Error::invalid_object("missing required member SampleRate"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_format(&format)?;

        Ok(Self {
            flow_core,
            format,
            media_type,
            sample_rate,
            profile,
            level,
            bitrate,
            constant_bitrate,
        })
    }
}
