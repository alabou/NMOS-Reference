//! Generated NMOS type: `NFlowVideoRaw`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::narray_of_video_component::NArrayOfVideoComponent;
use crate::generated::nflow_core::NFlowCore;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NFlowVideoRaw`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NFlowVideoRaw {
    /// `FlowCore`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub flow_core: NFlowCore,
    /// `format`. Required.
    #[serde(rename = "format")]
    pub format: EnumId,
    /// `media_type`. Required.
    #[serde(rename = "media_type")]
    pub media_type: EnumId,
    /// `frame_width`. Required.
    #[serde(rename = "frame_width")]
    pub frame_width: i64,
    /// `frame_height`. Required.
    #[serde(rename = "frame_height")]
    pub frame_height: i64,
    /// `colorspace`. Required.
    #[serde(rename = "colorspace")]
    pub colorspace: EnumId,
    /// `interlace_mode`. Optional, so absent means the member was not present.
    #[serde(rename = "interlace_mode", skip_serializing_if = "Option::is_none")]
    pub interlace_mode: Option<EnumId>,
    /// `transfer_characteristic`. Optional, so absent means the member was not present.
    #[serde(
        rename = "transfer_characteristic",
        skip_serializing_if = "Option::is_none"
    )]
    pub transfer_characteristic: Option<EnumId>,
    /// `components`. Required.
    #[serde(rename = "components")]
    pub components: NArrayOfVideoComponent,
}

impl NFlowVideoRaw {
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
                "expected JSON object for NFlowVideoRaw",
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
        let frame_width = match doc.get("frame_width") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let frame_height = match doc.get("frame_height") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let colorspace = match doc.get("colorspace") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let interlace_mode = match doc.get("interlace_mode") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let transfer_characteristic = match doc.get("transfer_characteristic") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let components = match doc.get("components") {
            Some(v) => Some(NArrayOfVideoComponent::decode(v)?),
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
        let interlace_mode = interlace_mode.or(Some(EnumId::new("progressive")));
        let transfer_characteristic = transfer_characteristic.or(Some(EnumId::new("SDR")));

        // Required presence, for every member, before any assertion runs.
        let format =
            format.ok_or_else(|| Error::invalid_object("missing required member Format"))?;
        let media_type =
            media_type.ok_or_else(|| Error::invalid_object("missing required member MediaType"))?;
        let frame_width = frame_width
            .ok_or_else(|| Error::invalid_object("missing required member FrameWidth"))?;
        let frame_height = frame_height
            .ok_or_else(|| Error::invalid_object("missing required member FrameHeight"))?;
        let colorspace = colorspace
            .ok_or_else(|| Error::invalid_object("missing required member Colorspace"))?;
        let components = components
            .ok_or_else(|| Error::invalid_object("missing required member Components"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_format(&format)?;
        validators::check_colorspace(&colorspace)?;
        if let Some(v) = &interlace_mode {
            validators::check_interlace_mode(v)?;
        }
        if let Some(v) = &transfer_characteristic {
            validators::check_transfer_characteristic(v)?;
        }
        validators::check_video_components(components.0.iter().map(|e| Some(e.name.as_str())))?;

        Ok(Self {
            flow_core,
            format,
            media_type,
            frame_width,
            frame_height,
            colorspace,
            interlace_mode,
            transfer_characteristic,
            components,
        })
    }
}
