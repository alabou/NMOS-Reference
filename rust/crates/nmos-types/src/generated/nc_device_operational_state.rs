//! Generated NMOS type: `NcDeviceOperationalState`. DO NOT EDIT.
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

/// `NcDeviceOperationalState`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcDeviceOperationalState {
    /// `generic`. Required.
    #[serde(rename = "generic")]
    pub generic: i64,
    /// `deviceSpecificDetails`. Required.
    #[serde(rename = "deviceSpecificDetails")]
    pub device_specific_details: Nullable<String>,
}

impl NcDeviceOperationalState {
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
                "expected JSON object for NcDeviceOperationalState",
            ));
        };

        let generic = match doc.get("generic") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let device_specific_details = match doc.get("deviceSpecificDetails") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let generic =
            generic.ok_or_else(|| Error::invalid_object("missing required member Generic"))?;
        let device_specific_details = device_specific_details.ok_or_else(|| {
            Error::invalid_object("missing required member DeviceSpecificDetails")
        })?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_device_generic_state(generic)?;

        Ok(Self {
            generic,
            device_specific_details,
        })
    }
}
