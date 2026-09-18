//! Generated NMOS type: `NArrayOfRtspTransportConstraints`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nrtsp_transport_constraints::NRtspTransportConstraints;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NArrayOfRtspTransportConstraints`: a list carried without a wrapping object.
///
/// `#[serde(transparent)]` so it encodes as its contents, with no extra nesting,
/// matching the Python type's encode.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(transparent)]
pub struct NArrayOfRtspTransportConstraints(
    /// The wrapped value, encoded with no enclosing object.
    pub Vec<NRtspTransportConstraints>,
);

impl NArrayOfRtspTransportConstraints {
    /// Decode from a JSON value.
    pub fn decode(src: &Value) -> Result<Self> {
        // Named after the TYPE, not the value's type. A generated array says
        // "expected array for NArrayOfClock" where a base-type array says
        // "expected array, got str" -- two different messages from two
        // different places, and both reach an HTTP 400 body.
        let Value::Array(items) = src else {
            return Err(Error::invalid_data(
                "expected array for NArrayOfRtspTransportConstraints",
            ));
        };
        let mut out = Vec::with_capacity(items.len());
        for item in items {
            out.push(NRtspTransportConstraints::decode(item)?);
        }
        Ok(Self(out))
    }
}
