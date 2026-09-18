//! Generated NMOS type: `NReceiverCore`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nreceiver_subscription::NReceiverSubscription;
use crate::generated::nresource_core::NResourceCore;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NReceiverCore`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NReceiverCore {
    /// `ResourceCore`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub resource_core: NResourceCore,
    /// `transport`. Required.
    #[serde(rename = "transport")]
    pub transport: EnumId,
    /// `device_id`. Required.
    #[serde(rename = "device_id")]
    pub device_id: String,
    /// `interface_bindings`. Required.
    #[serde(rename = "interface_bindings")]
    pub interface_bindings: Vec<String>,
    /// `subscription`. Required.
    #[serde(rename = "subscription")]
    pub subscription: NReceiverSubscription,
}

impl NReceiverCore {
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
                "expected JSON object for NReceiverCore",
            ));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let resource_core = NResourceCore::decode(src)?;
        let transport = match doc.get("transport") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let device_id = match doc.get("device_id") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let interface_bindings = match doc.get("interface_bindings") {
            Some(v) => Some(decode::array_of_string(v)?),
            None => None,
        };
        let subscription = match doc.get("subscription") {
            Some(v) => Some(NReceiverSubscription::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let transport =
            transport.ok_or_else(|| Error::invalid_object("missing required member Transport"))?;
        let device_id =
            device_id.ok_or_else(|| Error::invalid_object("missing required member DeviceId"))?;
        let interface_bindings = interface_bindings
            .ok_or_else(|| Error::invalid_object("missing required member InterfaceBindings"))?;
        let subscription = subscription
            .ok_or_else(|| Error::invalid_object("missing required member Subscription"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_transport(&transport)?;
        validators::check_resource_id_string(&device_id)?;

        Ok(Self {
            resource_core,
            transport,
            device_id,
            interface_bindings,
            subscription,
        })
    }
}
