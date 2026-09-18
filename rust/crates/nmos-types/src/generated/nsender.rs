//! Generated NMOS type: `NSender`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nresource_core::NResourceCore;
use crate::generated::nsender_capabilities::NSenderCapabilities;
use crate::generated::nsender_subscription::NSenderSubscription;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NSender`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NSender {
    /// `ResourceCore`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub resource_core: NResourceCore,
    /// `flow_id`. Required.
    #[serde(rename = "flow_id")]
    pub flow_id: Nullable<String>,
    /// `transport`. Required.
    #[serde(rename = "transport")]
    pub transport: EnumId,
    /// `device_id`. Required.
    #[serde(rename = "device_id")]
    pub device_id: String,
    /// `manifest_href`. Required.
    #[serde(rename = "manifest_href")]
    pub manifest_href: String,
    /// `interface_bindings`. Required.
    #[serde(rename = "interface_bindings")]
    pub interface_bindings: Vec<String>,
    /// `subscription`. Required.
    #[serde(rename = "subscription")]
    pub subscription: NSenderSubscription,
    /// `caps`. Optional, so absent means the member was not present.
    #[serde(rename = "caps", skip_serializing_if = "Option::is_none")]
    pub caps: Option<NSenderCapabilities>,
    /// `bit_rate`. Optional, so absent means the member was not present.
    #[serde(rename = "bit_rate", skip_serializing_if = "Option::is_none")]
    pub bitrate: Option<i64>,
    /// `st2110_21_sender_type`. Optional, so absent means the member was not present.
    #[serde(
        rename = "st2110_21_sender_type",
        skip_serializing_if = "Option::is_none"
    )]
    pub sender_type: Option<EnumId>,
    /// `packet_transmission_mode`. Optional, so absent means the member was not present.
    #[serde(
        rename = "packet_transmission_mode",
        skip_serializing_if = "Option::is_none"
    )]
    pub packet_transmission_mode: Option<EnumId>,
    /// `parameter_sets_transport_mode`. Optional, so absent means the member was not present.
    #[serde(
        rename = "parameter_sets_transport_mode",
        skip_serializing_if = "Option::is_none"
    )]
    pub parameter_sets_transport_mode: Option<EnumId>,
    /// `parameter_sets_flow_mode`. Optional, so absent means the member was not present.
    #[serde(
        rename = "parameter_sets_flow_mode",
        skip_serializing_if = "Option::is_none"
    )]
    pub parameter_sets_flow_mode: Option<EnumId>,
    /// `urn:x-matrox:info_block`. Optional, so absent means the member was not present.
    #[serde(
        rename = "urn:x-matrox:info_block",
        skip_serializing_if = "Option::is_none"
    )]
    pub info_block: Option<Vec<i64>>,
    /// `hkep`. Optional, so absent means the member was not present.
    #[serde(rename = "hkep", skip_serializing_if = "Option::is_none")]
    pub hkep: Option<bool>,
    /// `privacy`. Optional, so absent means the member was not present.
    #[serde(rename = "privacy", skip_serializing_if = "Option::is_none")]
    pub privacy: Option<bool>,
}

impl NSender {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NSender"));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let resource_core = NResourceCore::decode(src)?;
        let flow_id = match doc.get("flow_id") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let transport = match doc.get("transport") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let device_id = match doc.get("device_id") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let manifest_href = match doc.get("manifest_href") {
            Some(v) => Some(decode::url(v)?),
            None => None,
        };
        let interface_bindings = match doc.get("interface_bindings") {
            Some(v) => Some(decode::array_of_string(v)?),
            None => None,
        };
        let subscription = match doc.get("subscription") {
            Some(v) => Some(NSenderSubscription::decode(v)?),
            None => None,
        };
        let caps = match doc.get("caps") {
            Some(v) => Some(NSenderCapabilities::decode(v)?),
            None => None,
        };
        let bitrate = match doc.get("bit_rate") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let sender_type = match doc.get("st2110_21_sender_type") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let packet_transmission_mode = match doc.get("packet_transmission_mode") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let parameter_sets_transport_mode = match doc.get("parameter_sets_transport_mode") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let parameter_sets_flow_mode = match doc.get("parameter_sets_flow_mode") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let info_block = match doc.get("urn:x-matrox:info_block") {
            Some(v) => Some(decode::array_of_int(v)?),
            None => None,
        };
        let hkep = match doc.get("hkep") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let privacy = match doc.get("privacy") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let flow_id =
            flow_id.ok_or_else(|| Error::invalid_object("missing required member FlowId"))?;
        let transport =
            transport.ok_or_else(|| Error::invalid_object("missing required member Transport"))?;
        let device_id =
            device_id.ok_or_else(|| Error::invalid_object("missing required member DeviceId"))?;
        let manifest_href = manifest_href
            .ok_or_else(|| Error::invalid_object("missing required member ManifestHref"))?;
        let interface_bindings = interface_bindings
            .ok_or_else(|| Error::invalid_object("missing required member InterfaceBindings"))?;
        let subscription = subscription
            .ok_or_else(|| Error::invalid_object("missing required member Subscription"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_resource_id_nullable_string(flow_id.as_option().map(String::as_str))?;
        validators::check_transport(&transport)?;
        validators::check_resource_id_string(&device_id)?;

        Ok(Self {
            resource_core,
            flow_id,
            transport,
            device_id,
            manifest_href,
            interface_bindings,
            subscription,
            caps,
            bitrate,
            sender_type,
            packet_transmission_mode,
            parameter_sets_transport_mode,
            parameter_sets_flow_mode,
            info_block,
            hkep,
            privacy,
        })
    }
}
