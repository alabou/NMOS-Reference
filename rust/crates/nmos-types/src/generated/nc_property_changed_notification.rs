//! Generated NMOS type: `NcPropertyChangedNotification`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_event_id::NcEventId;
use crate::generated::nc_propertychanged_event::NcPropertychangedEvent;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcPropertyChangedNotification`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcPropertyChangedNotification {
    /// `oid`. Required.
    #[serde(rename = "oid")]
    pub oid: i64,
    /// `eventId`. Required.
    #[serde(rename = "eventId")]
    pub event_id: NcEventId,
    /// `eventData`. Required.
    #[serde(rename = "eventData")]
    pub event_data: NcPropertychangedEvent,
}

impl NcPropertyChangedNotification {
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
                "expected JSON object for NcPropertyChangedNotification",
            ));
        };

        let oid = match doc.get("oid") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let event_id = match doc.get("eventId") {
            Some(v) => Some(NcEventId::decode(v)?),
            None => None,
        };
        let event_data = match doc.get("eventData") {
            Some(v) => Some(NcPropertychangedEvent::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let oid = oid.ok_or_else(|| Error::invalid_object("missing required member OId"))?;
        let event_id =
            event_id.ok_or_else(|| Error::invalid_object("missing required member EventId"))?;
        let event_data =
            event_data.ok_or_else(|| Error::invalid_object("missing required member EventData"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_positive_integer(oid)?;

        Ok(Self {
            oid,
            event_id,
            event_data,
        })
    }
}
