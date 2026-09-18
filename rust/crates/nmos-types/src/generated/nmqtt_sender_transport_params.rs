//! Generated NMOS type: `NMqttSenderTransportParams`. DO NOT EDIT.
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

/// `NMqttSenderTransportParams`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NMqttSenderTransportParams {
    /// `destination_host`. Optional, so absent means the member was not present.
    #[serde(rename = "destination_host", skip_serializing_if = "Option::is_none")]
    pub destination_host: Option<Nullable<String>>,
    /// `destination_port`. Optional, so absent means the member was not present.
    #[serde(rename = "destination_port", skip_serializing_if = "Option::is_none")]
    pub destination_port: Option<Nullable<Value>>,
    /// `broker_protocol`. Optional, so absent means the member was not present.
    #[serde(rename = "broker_protocol", skip_serializing_if = "Option::is_none")]
    pub broker_protocol: Option<EnumId>,
    /// `broker_authorization`. Optional, so absent means the member was not present.
    #[serde(
        rename = "broker_authorization",
        skip_serializing_if = "Option::is_none"
    )]
    pub broker_authorization: Option<Nullable<Value>>,
    /// `broker_topic`. Optional, so absent means the member was not present.
    #[serde(rename = "broker_topic", skip_serializing_if = "Option::is_none")]
    pub broker_topic: Option<Nullable<String>>,
    /// `connection_status_broker_topic`. Optional, so absent means the member was not present.
    #[serde(
        rename = "connection_status_broker_topic",
        skip_serializing_if = "Option::is_none"
    )]
    pub connection_status_broker_topic: Option<Nullable<String>>,
}

impl NMqttSenderTransportParams {
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
                "expected JSON object for NMqttSenderTransportParams",
            ));
        };

        let destination_host = match doc.get("destination_host") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let destination_port = doc.get("destination_port").map(decode::null_value);
        let broker_protocol = match doc.get("broker_protocol") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let broker_authorization = doc.get("broker_authorization").map(decode::null_value);
        let broker_topic = match doc.get("broker_topic") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let connection_status_broker_topic = match doc.get("connection_status_broker_topic") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        if let Some(v) = &destination_port {
            validators::check_auto_port(v.as_json())?;
        }
        if let Some(v) = &broker_authorization {
            validators::check_auto_bool(v.as_json())?;
        }

        Ok(Self {
            destination_host,
            destination_port,
            broker_protocol,
            broker_authorization,
            broker_topic,
            connection_status_broker_topic,
        })
    }
}
