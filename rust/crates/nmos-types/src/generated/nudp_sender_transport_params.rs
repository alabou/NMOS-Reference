//! Generated NMOS type: `NUdpSenderTransportParams`. DO NOT EDIT.
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

/// `NUdpSenderTransportParams`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NUdpSenderTransportParams {
    /// `source_ip`. Optional, so absent means the member was not present.
    #[serde(rename = "source_ip", skip_serializing_if = "Option::is_none")]
    pub source_ip: Option<String>,
    /// `destination_ip`. Optional, so absent means the member was not present.
    #[serde(rename = "destination_ip", skip_serializing_if = "Option::is_none")]
    pub destination_ip: Option<String>,
    /// `source_port`. Optional, so absent means the member was not present.
    #[serde(rename = "source_port", skip_serializing_if = "Option::is_none")]
    pub source_port: Option<Nullable<Value>>,
    /// `destination_port`. Optional, so absent means the member was not present.
    #[serde(rename = "destination_port", skip_serializing_if = "Option::is_none")]
    pub destination_port: Option<Nullable<Value>>,
    /// `fec_enabled`. Optional, so absent means the member was not present.
    #[serde(rename = "fec_enabled", skip_serializing_if = "Option::is_none")]
    pub fec_enabled: Option<bool>,
    /// `fec_destination_ip`. Optional, so absent means the member was not present.
    #[serde(rename = "fec_destination_ip", skip_serializing_if = "Option::is_none")]
    pub fec_destination_ip: Option<String>,
    /// `fec_type`. Optional, so absent means the member was not present.
    #[serde(rename = "fec_type", skip_serializing_if = "Option::is_none")]
    pub fec_type: Option<EnumId>,
    /// `fec_mode`. Optional, so absent means the member was not present.
    #[serde(rename = "fec_mode", skip_serializing_if = "Option::is_none")]
    pub fec_mode: Option<EnumId>,
    /// `fec_block_width`. Optional, so absent means the member was not present.
    #[serde(rename = "fec_block_width", skip_serializing_if = "Option::is_none")]
    pub fec_block_width: Option<i64>,
    /// `fec_block_height`. Optional, so absent means the member was not present.
    #[serde(rename = "fec_block_height", skip_serializing_if = "Option::is_none")]
    pub fec_block_height: Option<i64>,
    /// `fec1D_destination_port`. Optional, so absent means the member was not present.
    #[serde(
        rename = "fec1D_destination_port",
        skip_serializing_if = "Option::is_none"
    )]
    pub fec1_ddestination_port: Option<Nullable<Value>>,
    /// `fec2D_destination_port`. Optional, so absent means the member was not present.
    #[serde(
        rename = "fec2D_destination_port",
        skip_serializing_if = "Option::is_none"
    )]
    pub fec2_ddestination_port: Option<Nullable<Value>>,
    /// `fec1D_source_port`. Optional, so absent means the member was not present.
    #[serde(rename = "fec1D_source_port", skip_serializing_if = "Option::is_none")]
    pub fec1_dsource_port: Option<Nullable<Value>>,
    /// `fec2D_source_port`. Optional, so absent means the member was not present.
    #[serde(rename = "fec2D_source_port", skip_serializing_if = "Option::is_none")]
    pub fec2_dsource_port: Option<Nullable<Value>>,
    /// `enabled`. Optional, so absent means the member was not present.
    #[serde(rename = "enabled", skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,
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

impl NUdpSenderTransportParams {
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
                "expected JSON object for NUdpSenderTransportParams",
            ));
        };

        let source_ip = match doc.get("source_ip") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let destination_ip = match doc.get("destination_ip") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let source_port = doc.get("source_port").map(decode::null_value);
        let destination_port = doc.get("destination_port").map(decode::null_value);
        let fec_enabled = match doc.get("fec_enabled") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let fec_destination_ip = match doc.get("fec_destination_ip") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let fec_type = match doc.get("fec_type") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let fec_mode = match doc.get("fec_mode") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let fec_block_width = match doc.get("fec_block_width") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let fec_block_height = match doc.get("fec_block_height") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let fec1_ddestination_port = doc.get("fec1D_destination_port").map(decode::null_value);
        let fec2_ddestination_port = doc.get("fec2D_destination_port").map(decode::null_value);
        let fec1_dsource_port = doc.get("fec1D_source_port").map(decode::null_value);
        let fec2_dsource_port = doc.get("fec2D_source_port").map(decode::null_value);
        let enabled = match doc.get("enabled") {
            Some(v) => Some(decode::bool(v)?),
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
            validators::check_auto_port(v.as_json())?;
        }
        if let Some(v) = &destination_port {
            validators::check_auto_port(v.as_json())?;
        }
        if let Some(v) = &fec1_ddestination_port {
            validators::check_auto_port(v.as_json())?;
        }
        if let Some(v) = &fec2_ddestination_port {
            validators::check_auto_port(v.as_json())?;
        }
        if let Some(v) = &fec1_dsource_port {
            validators::check_auto_port(v.as_json())?;
        }
        if let Some(v) = &fec2_dsource_port {
            validators::check_auto_port(v.as_json())?;
        }

        Ok(Self {
            source_ip,
            destination_ip,
            source_port,
            destination_port,
            fec_enabled,
            fec_destination_ip,
            fec_type,
            fec_mode,
            fec_block_width,
            fec_block_height,
            fec1_ddestination_port,
            fec2_ddestination_port,
            fec1_dsource_port,
            fec2_dsource_port,
            enabled,
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
