//! Generated NMOS type: `NReceiverVideo`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nreceiver_core::NReceiverCore;
use crate::generated::nreceiver_video_capabilities::NReceiverVideoCapabilities;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NReceiverVideo`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NReceiverVideo {
    /// `ReceiverCore`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub receiver_core: NReceiverCore,
    /// `format`. Required.
    #[serde(rename = "format")]
    pub format: EnumId,
    /// `caps`. Required.
    #[serde(rename = "caps")]
    pub caps: NReceiverVideoCapabilities,
}

impl NReceiverVideo {
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
                "expected JSON object for NReceiverVideo",
            ));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let receiver_core = NReceiverCore::decode(src)?;
        let format = match doc.get("format") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let caps = match doc.get("caps") {
            Some(v) => Some(NReceiverVideoCapabilities::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let format =
            format.ok_or_else(|| Error::invalid_object("missing required member Format"))?;
        let caps = caps.ok_or_else(|| Error::invalid_object("missing required member Caps"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_format(&format)?;

        Ok(Self {
            receiver_core,
            format,
            caps,
        })
    }
}
