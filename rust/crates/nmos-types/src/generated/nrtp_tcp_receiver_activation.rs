//! Generated NMOS type: `NRtpTcpReceiverActivation`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nactivation::NActivation;
use crate::generated::narray_of_rtp_tcp_receiver_transport_params::NArrayOfRtpTcpReceiverTransportParams;
use crate::generated::ntransport_file::NTransportFile;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NRtpTcpReceiverActivation`.
///
/// Sealed: a property this type does not declare is an error, not something to
/// ignore. Checked after every member has decoded, at the position the Python
/// template puts it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NRtpTcpReceiverActivation {
    /// `sender_id`. Optional, so absent means the member was not present.
    #[serde(rename = "sender_id", skip_serializing_if = "Option::is_none")]
    pub sender_id: Option<Nullable<String>>,
    /// `master_enable`. Optional, so absent means the member was not present.
    #[serde(rename = "master_enable", skip_serializing_if = "Option::is_none")]
    pub master_enable: Option<bool>,
    /// `activation`. Optional, so absent means the member was not present.
    #[serde(rename = "activation", skip_serializing_if = "Option::is_none")]
    pub activation: Option<NActivation>,
    /// `transport_file`. Optional, so absent means the member was not present.
    #[serde(rename = "transport_file", skip_serializing_if = "Option::is_none")]
    pub transport_file: Option<NTransportFile>,
    /// `transport_params`. Optional, so absent means the member was not present.
    #[serde(rename = "transport_params", skip_serializing_if = "Option::is_none")]
    pub transport_params: Option<NArrayOfRtpTcpReceiverTransportParams>,
}

impl NRtpTcpReceiverActivation {
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
                "expected JSON object for NRtpTcpReceiverActivation",
            ));
        };

        let sender_id = match doc.get("sender_id") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let master_enable = match doc.get("master_enable") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let activation = match doc.get("activation") {
            Some(v) => Some(NActivation::decode(v)?),
            None => None,
        };
        let transport_file = match doc.get("transport_file") {
            Some(v) => Some(NTransportFile::decode(v)?),
            None => None,
        };
        let transport_params = match doc.get("transport_params") {
            Some(v) => Some(NArrayOfRtpTcpReceiverTransportParams::decode(v)?),
            None => None,
        };

        // Sealed, so an undeclared property is rejected. Embedded members are
        // excluded from the known set because their keys live in this scope.
        const KNOWN: &[&str] = &[
            "sender_id",
            "master_enable",
            "activation",
            "transport_file",
            "transport_params",
        ];
        for prop in doc.keys() {
            if !KNOWN.contains(&prop.as_str()) {
                return Err(Error::invalid_data(format!(
                    "unknown property '{prop}' in sealed type NRtpTcpReceiverActivation"
                )));
            }
        }

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        if let Some(v) = &sender_id {
            validators::check_resource_id_nullable_string(v.as_option().map(String::as_str))?;
        }

        Ok(Self {
            sender_id,
            master_enable,
            activation,
            transport_file,
            transport_params,
        })
    }
}
