//! Generated NMOS type: `NMonitorState`. DO NOT EDIT.
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

/// `NMonitorState`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NMonitorState {
    /// `overall_status`. Optional, so absent means the member was not present.
    #[serde(rename = "overall_status", skip_serializing_if = "Option::is_none")]
    pub monitor_overall_status: Option<i64>,
    /// `overall_message`. Optional, so absent means the member was not present.
    #[serde(rename = "overall_message", skip_serializing_if = "Option::is_none")]
    pub monitor_overall_status_message: Option<String>,
    /// `link_status`. Optional, so absent means the member was not present.
    #[serde(rename = "link_status", skip_serializing_if = "Option::is_none")]
    pub monitor_link_status: Option<i64>,
    /// `synchronization_status`. Optional, so absent means the member was not present.
    #[serde(
        rename = "synchronization_status",
        skip_serializing_if = "Option::is_none"
    )]
    pub monitor_synchronization_status: Option<i64>,
    /// `transmission_status`. Optional, so absent means the member was not present.
    #[serde(
        rename = "transmission_status",
        skip_serializing_if = "Option::is_none"
    )]
    pub monitor_transmission_status: Option<i64>,
    /// `connection_status`. Optional, so absent means the member was not present.
    #[serde(rename = "connection_status", skip_serializing_if = "Option::is_none")]
    pub monitor_connection_status: Option<i64>,
    /// `essence_status`. Optional, so absent means the member was not present.
    #[serde(rename = "essence_status", skip_serializing_if = "Option::is_none")]
    pub monitor_essence_status: Option<i64>,
    /// `stream_status`. Optional, so absent means the member was not present.
    #[serde(rename = "stream_status", skip_serializing_if = "Option::is_none")]
    pub monitor_stream_status: Option<i64>,
    /// `link_counter`. Optional, so absent means the member was not present.
    #[serde(rename = "link_counter", skip_serializing_if = "Option::is_none")]
    pub monitor_link_status_counter: Option<i64>,
    /// `synchronization_counter`. Optional, so absent means the member was not present.
    #[serde(
        rename = "synchronization_counter",
        skip_serializing_if = "Option::is_none"
    )]
    pub monitor_synchronization_status_counter: Option<i64>,
    /// `transmission_counter`. Optional, so absent means the member was not present.
    #[serde(
        rename = "transmission_counter",
        skip_serializing_if = "Option::is_none"
    )]
    pub monitor_transmission_status_counter: Option<i64>,
    /// `connection_counter`. Optional, so absent means the member was not present.
    #[serde(rename = "connection_counter", skip_serializing_if = "Option::is_none")]
    pub monitor_connection_status_counter: Option<i64>,
    /// `essence_counter`. Optional, so absent means the member was not present.
    #[serde(rename = "essence_counter", skip_serializing_if = "Option::is_none")]
    pub monitor_essence_status_counter: Option<i64>,
    /// `stream_counter`. Optional, so absent means the member was not present.
    #[serde(rename = "stream_counter", skip_serializing_if = "Option::is_none")]
    pub monitor_stream_status_counter: Option<i64>,
}

impl NMonitorState {
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
                "expected JSON object for NMonitorState",
            ));
        };

        let monitor_overall_status = match doc.get("overall_status") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_overall_status_message = match doc.get("overall_message") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let monitor_link_status = match doc.get("link_status") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_synchronization_status = match doc.get("synchronization_status") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_transmission_status = match doc.get("transmission_status") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_connection_status = match doc.get("connection_status") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_essence_status = match doc.get("essence_status") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_stream_status = match doc.get("stream_status") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_link_status_counter = match doc.get("link_counter") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_synchronization_status_counter = match doc.get("synchronization_counter") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_transmission_status_counter = match doc.get("transmission_counter") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_connection_status_counter = match doc.get("connection_counter") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_essence_status_counter = match doc.get("essence_counter") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let monitor_stream_status_counter = match doc.get("stream_counter") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };

        Ok(Self {
            monitor_overall_status,
            monitor_overall_status_message,
            monitor_link_status,
            monitor_synchronization_status,
            monitor_transmission_status,
            monitor_connection_status,
            monitor_essence_status,
            monitor_stream_status,
            monitor_link_status_counter,
            monitor_synchronization_status_counter,
            monitor_transmission_status_counter,
            monitor_connection_status_counter,
            monitor_essence_status_counter,
            monitor_stream_status_counter,
        })
    }
}
