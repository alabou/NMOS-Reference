//! Generated NMOS type: `MvAlertCapabilityDescriptor`. DO NOT EDIT.
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

/// `MvAlertCapabilityDescriptor`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct MvAlertCapabilityDescriptor {
    /// `alertDomain`. Required.
    #[serde(rename = "alertDomain")]
    pub alert_domain: i64,
    /// `alertScope`. Required.
    #[serde(rename = "alertScope")]
    pub alert_scope: i64,
    /// `resourceIds`. Required.
    #[serde(rename = "resourceIds")]
    pub resource_ids: Vec<String>,
    /// `interfaceNames`. Required.
    #[serde(rename = "interfaceNames")]
    pub interface_names: Vec<String>,
    /// `events`. Required.
    #[serde(rename = "events")]
    pub events: Vec<i64>,
}

impl MvAlertCapabilityDescriptor {
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
                "expected JSON object for MvAlertCapabilityDescriptor",
            ));
        };

        let alert_domain = match doc.get("alertDomain") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let alert_scope = match doc.get("alertScope") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let resource_ids = match doc.get("resourceIds") {
            Some(v) => Some(decode::array_of_string(v)?),
            None => None,
        };
        let interface_names = match doc.get("interfaceNames") {
            Some(v) => Some(decode::array_of_string(v)?),
            None => None,
        };
        let events = match doc.get("events") {
            Some(v) => Some(decode::array_of_int(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let alert_domain = alert_domain
            .ok_or_else(|| Error::invalid_object("missing required member AlertDomain"))?;
        let alert_scope = alert_scope
            .ok_or_else(|| Error::invalid_object("missing required member AlertScope"))?;
        let resource_ids = resource_ids
            .ok_or_else(|| Error::invalid_object("missing required member ResourceIds"))?;
        let interface_names = interface_names
            .ok_or_else(|| Error::invalid_object("missing required member InterfaceNames"))?;
        let events =
            events.ok_or_else(|| Error::invalid_object("missing required member Events"))?;

        Ok(Self {
            alert_domain,
            alert_scope,
            resource_ids,
            interface_names,
            events,
        })
    }
}
