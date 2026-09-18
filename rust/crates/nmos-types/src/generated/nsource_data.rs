//! Generated NMOS type: `NSourceData`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nmonitor_state::NMonitorState;
use crate::generated::nsource_core::NSourceCore;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NSourceData`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NSourceData {
    /// `SourceCore`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub source_core: NSourceCore,
    /// `format`. Required.
    #[serde(rename = "format")]
    pub format: EnumId,
    /// `event_type`. Optional, so absent means the member was not present.
    #[serde(rename = "event_type", skip_serializing_if = "Option::is_none")]
    pub event_type: Option<String>,
    /// `monitor_type`. Optional, so absent means the member was not present.
    #[serde(rename = "monitor_type", skip_serializing_if = "Option::is_none")]
    pub monitor_type: Option<String>,
    /// `monitor_sibling_id`. Optional, so absent means the member was not present.
    #[serde(rename = "monitor_sibling_id", skip_serializing_if = "Option::is_none")]
    pub monitor_sibling_id: Option<String>,
    /// `monitor_auto_reset_counters`. Optional, so absent means the member was not present.
    #[serde(
        rename = "monitor_auto_reset_counters",
        skip_serializing_if = "Option::is_none"
    )]
    pub monitor_auto_reset_counters: Option<bool>,
    /// `monitor_reporting_delay`. Optional, so absent means the member was not present.
    #[serde(
        rename = "monitor_reporting_delay",
        skip_serializing_if = "Option::is_none"
    )]
    pub monitor_status_reporting_delay: Option<i64>,
    /// `monitor_state`. Optional, so absent means the member was not present.
    #[serde(rename = "monitor_state", skip_serializing_if = "Option::is_none")]
    pub monitor_state: Option<NMonitorState>,
}

impl NSourceData {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NSourceData"));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let source_core = NSourceCore::decode(src)?;
        let format = match doc.get("format") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let event_type = match doc.get("event_type") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let monitor_type = match doc.get("monitor_type") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let monitor_sibling_id = match doc.get("monitor_sibling_id") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let monitor_auto_reset_counters = match doc.get("monitor_auto_reset_counters") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let monitor_status_reporting_delay = match doc.get("monitor_reporting_delay") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_state = match doc.get("monitor_state") {
            Some(v) => Some(NMonitorState::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let format =
            format.ok_or_else(|| Error::invalid_object("missing required member Format"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_format(&format)?;
        if let Some(v) = &monitor_sibling_id {
            validators::check_resource_id_string(v)?;
        }

        Ok(Self {
            source_core,
            format,
            event_type,
            monitor_type,
            monitor_sibling_id,
            monitor_auto_reset_counters,
            monitor_status_reporting_delay,
            monitor_state,
        })
    }
}
