//! Generated NMOS type: `MvAlertEventData`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::mv_alert_descriptor::MvAlertDescriptor;
use crate::generated::mv_event_counter::MvEventCounter;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `MvAlertEventData`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct MvAlertEventData {
    /// `alertDescriptorIndex`. Required.
    #[serde(rename = "alertDescriptorIndex")]
    pub alert_descriptor_index: i64,
    /// `alertDescriptor`. Required.
    #[serde(rename = "alertDescriptor")]
    pub alert_descriptor: MvAlertDescriptor,
    /// `eventCounter`. Required.
    #[serde(rename = "eventCounter")]
    pub event_counter: MvEventCounter,
}

impl MvAlertEventData {
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
                "expected JSON object for MvAlertEventData",
            ));
        };

        let alert_descriptor_index = match doc.get("alertDescriptorIndex") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let alert_descriptor = match doc.get("alertDescriptor") {
            Some(v) => Some(MvAlertDescriptor::decode(v)?),
            None => None,
        };
        let event_counter = match doc.get("eventCounter") {
            Some(v) => Some(MvEventCounter::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let alert_descriptor_index = alert_descriptor_index
            .ok_or_else(|| Error::invalid_object("missing required member AlertDescriptorIndex"))?;
        let alert_descriptor = alert_descriptor
            .ok_or_else(|| Error::invalid_object("missing required member AlertDescriptor"))?;
        let event_counter = event_counter
            .ok_or_else(|| Error::invalid_object("missing required member EventCounter"))?;

        Ok(Self {
            alert_descriptor_index,
            alert_descriptor,
            event_counter,
        })
    }
}
