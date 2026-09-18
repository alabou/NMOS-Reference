//! Generated NMOS type: `NNdiReceiverTransportParams`. DO NOT EDIT.
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

/// `NNdiReceiverTransportParams`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NNdiReceiverTransportParams {
    /// `interface_ip`. Optional, so absent means the member was not present.
    #[serde(rename = "interface_ip", skip_serializing_if = "Option::is_none")]
    pub interface_ip: Option<String>,
    /// `source_ip`. Optional, so absent means the member was not present.
    #[serde(rename = "source_ip", skip_serializing_if = "Option::is_none")]
    pub source_ip: Option<Nullable<String>>,
    /// `source_port`. Optional, so absent means the member was not present.
    #[serde(rename = "source_port", skip_serializing_if = "Option::is_none")]
    pub source_port: Option<Nullable<Value>>,
    /// `source_name`. Optional, so absent means the member was not present.
    #[serde(rename = "source_name", skip_serializing_if = "Option::is_none")]
    pub source_name: Option<Nullable<String>>,
    /// `machine_name`. Optional, so absent means the member was not present.
    #[serde(rename = "machine_name", skip_serializing_if = "Option::is_none")]
    pub machine_name: Option<Nullable<String>>,
}

impl NNdiReceiverTransportParams {
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
                "expected JSON object for NNdiReceiverTransportParams",
            ));
        };

        let interface_ip = match doc.get("interface_ip") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let source_ip = match doc.get("source_ip") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let source_port = doc.get("source_port").map(decode::null_value);
        let source_name = match doc.get("source_name") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let machine_name = match doc.get("machine_name") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        if let Some(v) = &source_port {
            validators::check_null_port(v.as_json())?;
        }

        Ok(Self {
            interface_ip,
            source_ip,
            source_port,
            source_name,
            machine_name,
        })
    }
}
