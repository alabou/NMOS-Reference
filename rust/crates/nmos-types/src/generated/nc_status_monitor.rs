//! Generated NMOS type: `NcStatusMonitor`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_worker::NcWorker;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcStatusMonitor`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcStatusMonitor {
    /// `Base`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub base: NcWorker,
    /// `overallStatus`. Required.
    #[serde(rename = "overallStatus")]
    pub overall_status: i64,
    /// `overallStatusMessage`. Required.
    #[serde(rename = "overallStatusMessage")]
    pub overall_status_message: Nullable<String>,
    /// `statusReportingDelay`. Required.
    #[serde(rename = "statusReportingDelay")]
    pub status_reporting_delay: i64,
}

impl NcStatusMonitor {
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
                "expected JSON object for NcStatusMonitor",
            ));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let base = NcWorker::decode(src)?;
        let overall_status = match doc.get("overallStatus") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let overall_status_message = match doc.get("overallStatusMessage") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let status_reporting_delay = match doc.get("statusReportingDelay") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let overall_status = overall_status
            .ok_or_else(|| Error::invalid_object("missing required member OverallStatus"))?;
        let overall_status_message = overall_status_message
            .ok_or_else(|| Error::invalid_object("missing required member OverallStatusMessage"))?;
        let status_reporting_delay = status_reporting_delay
            .ok_or_else(|| Error::invalid_object("missing required member StatusReportingDelay"))?;

        Ok(Self {
            base,
            overall_status,
            overall_status_message,
            status_reporting_delay,
        })
    }
}
