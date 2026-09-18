//! Generated NMOS type: `MvEventCounter`. DO NOT EDIT.
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

/// `MvEventCounter`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct MvEventCounter {
    /// `event`. Required.
    #[serde(rename = "event")]
    pub event: i64,
    /// `eventCounter`. Required.
    #[serde(rename = "eventCounter")]
    pub event_counter: i64,
    /// `eventState`. Required.
    #[serde(rename = "eventState")]
    pub event_state: i64,
    /// `eventInfo`. Required.
    #[serde(rename = "eventInfo")]
    pub event_info: String,
    /// `interfaceName`. Required.
    #[serde(rename = "interfaceName")]
    pub interface_name: String,
}

impl MvEventCounter {
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
                "expected JSON object for MvEventCounter",
            ));
        };

        let event = match doc.get("event") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let event_counter = match doc.get("eventCounter") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let event_state = match doc.get("eventState") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let event_info = match doc.get("eventInfo") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let interface_name = match doc.get("interfaceName") {
            Some(v) => decode::string(v)?,
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let event = event.ok_or_else(|| Error::invalid_object("missing required member Event"))?;
        let event_counter = event_counter
            .ok_or_else(|| Error::invalid_object("missing required member EventCounter"))?;
        let event_state = event_state
            .ok_or_else(|| Error::invalid_object("missing required member EventState"))?;
        let event_info =
            event_info.ok_or_else(|| Error::invalid_object("missing required member EventInfo"))?;
        let interface_name = interface_name
            .ok_or_else(|| Error::invalid_object("missing required member InterfaceName"))?;

        Ok(Self {
            event,
            event_counter,
            event_state,
            event_info,
            interface_name,
        })
    }
}
