//! Generated NMOS type: `NRtspReceiverTransportParams`. DO NOT EDIT.
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

/// `NRtspReceiverTransportParams`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NRtspReceiverTransportParams {
    /// `source_ip`. Optional, so absent means the member was not present.
    #[serde(rename = "source_ip", skip_serializing_if = "Option::is_none")]
    pub source_ip: Option<Nullable<String>>,
    /// `source_port`. Optional, so absent means the member was not present.
    #[serde(rename = "source_port", skip_serializing_if = "Option::is_none")]
    pub source_port: Option<Nullable<Value>>,
    /// `interface_ip`. Optional, so absent means the member was not present.
    #[serde(rename = "interface_ip", skip_serializing_if = "Option::is_none")]
    pub interface_ip: Option<String>,
    /// `ext_privacy_protocol`. Optional, so absent means the member was not present.
    #[serde(
        rename = "ext_privacy_protocol",
        skip_serializing_if = "Option::is_none"
    )]
    pub ext_privacy_protocol: Option<EnumId>,
    /// `ext_privacy_mode`. Optional, so absent means the member was not present.
    #[serde(rename = "ext_privacy_mode", skip_serializing_if = "Option::is_none")]
    pub ext_privacy_mode: Option<EnumId>,
    /// `ext_privacy_iv`. Optional, so absent means the member was not present.
    #[serde(rename = "ext_privacy_iv", skip_serializing_if = "Option::is_none")]
    pub ext_privacy_iv: Option<String>,
    /// `ext_privacy_key_generator`. Optional, so absent means the member was not present.
    #[serde(
        rename = "ext_privacy_key_generator",
        skip_serializing_if = "Option::is_none"
    )]
    pub ext_privacy_key_generator: Option<String>,
    /// `ext_privacy_key_id`. Optional, so absent means the member was not present.
    #[serde(rename = "ext_privacy_key_id", skip_serializing_if = "Option::is_none")]
    pub ext_privacy_key_id: Option<String>,
    /// `ext_privacy_key_version`. Optional, so absent means the member was not present.
    #[serde(
        rename = "ext_privacy_key_version",
        skip_serializing_if = "Option::is_none"
    )]
    pub ext_privacy_key_version: Option<String>,
    /// `ext_privacy_ecdh_sender_public_key`. Optional, so absent means the member was not present.
    #[serde(
        rename = "ext_privacy_ecdh_sender_public_key",
        skip_serializing_if = "Option::is_none"
    )]
    pub ext_privacy_ecdh_sender_public_key: Option<String>,
    /// `ext_privacy_ecdh_receiver_public_key`. Optional, so absent means the member was not present.
    #[serde(
        rename = "ext_privacy_ecdh_receiver_public_key",
        skip_serializing_if = "Option::is_none"
    )]
    pub ext_privacy_ecdh_receiver_public_key: Option<String>,
    /// `ext_privacy_ecdh_curve`. Optional, so absent means the member was not present.
    #[serde(
        rename = "ext_privacy_ecdh_curve",
        skip_serializing_if = "Option::is_none"
    )]
    pub ext_privacy_ecdh_curve: Option<EnumId>,
}

impl NRtspReceiverTransportParams {
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
                "expected JSON object for NRtspReceiverTransportParams",
            ));
        };

        let source_ip = match doc.get("source_ip") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let source_port = doc.get("source_port").map(decode::null_value);
        let interface_ip = match doc.get("interface_ip") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let ext_privacy_protocol = match doc.get("ext_privacy_protocol") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let ext_privacy_mode = match doc.get("ext_privacy_mode") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let ext_privacy_iv = match doc.get("ext_privacy_iv") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let ext_privacy_key_generator = match doc.get("ext_privacy_key_generator") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let ext_privacy_key_id = match doc.get("ext_privacy_key_id") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let ext_privacy_key_version = match doc.get("ext_privacy_key_version") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let ext_privacy_ecdh_sender_public_key = match doc.get("ext_privacy_ecdh_sender_public_key")
        {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let ext_privacy_ecdh_receiver_public_key =
            match doc.get("ext_privacy_ecdh_receiver_public_key") {
                Some(v) => decode::string(v)?,
                None => None,
            };
        let ext_privacy_ecdh_curve = match doc.get("ext_privacy_ecdh_curve") {
            Some(v) => Some(decode::enum_id(v)?),
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
            source_ip,
            source_port,
            interface_ip,
            ext_privacy_protocol,
            ext_privacy_mode,
            ext_privacy_iv,
            ext_privacy_key_generator,
            ext_privacy_key_id,
            ext_privacy_key_version,
            ext_privacy_ecdh_sender_public_key,
            ext_privacy_ecdh_receiver_public_key,
            ext_privacy_ecdh_curve,
        })
    }
}
