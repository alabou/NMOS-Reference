//! Generated NMOS type: `MvAlertManager`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::mv_alert_event_data::MvAlertEventData;
use crate::generated::mv_array_of_alert_capability_descriptor::MvArrayOfAlertCapabilityDescriptor;
use crate::generated::mv_array_of_alert_descriptor::MvArrayOfAlertDescriptor;
use crate::generated::nc_manager::NcManager;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `MvAlertManager`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct MvAlertManager {
    /// `Base`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub base: NcManager,
    /// `alertPeriod`. Required.
    #[serde(rename = "alertPeriod")]
    pub alert_period: i64,
    /// `refreshPeriod`. Required.
    #[serde(rename = "refreshPeriod")]
    pub refresh_period: i64,
    /// `clearPeriod`. Required.
    #[serde(rename = "clearPeriod")]
    pub clear_period: i64,
    /// `alertCapabilities`. Required.
    #[serde(rename = "alertCapabilities")]
    pub alert_capabilities: MvArrayOfAlertCapabilityDescriptor,
    /// `alertDescriptors`. Required.
    #[serde(rename = "alertDescriptors")]
    pub alert_descriptors: MvArrayOfAlertDescriptor,
    /// `alert`. Required.
    #[serde(rename = "alert")]
    pub alert: MvAlertEventData,
}

impl MvAlertManager {
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
                "expected JSON object for MvAlertManager",
            ));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let base = NcManager::decode(src)?;
        let alert_period = match doc.get("alertPeriod") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let refresh_period = match doc.get("refreshPeriod") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let clear_period = match doc.get("clearPeriod") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let alert_capabilities = match doc.get("alertCapabilities") {
            Some(v) => Some(MvArrayOfAlertCapabilityDescriptor::decode(v)?),
            None => None,
        };
        let alert_descriptors = match doc.get("alertDescriptors") {
            Some(v) => Some(MvArrayOfAlertDescriptor::decode(v)?),
            None => None,
        };
        let alert = match doc.get("alert") {
            Some(v) => Some(MvAlertEventData::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let alert_period = alert_period
            .ok_or_else(|| Error::invalid_object("missing required member AlertPeriod"))?;
        let refresh_period = refresh_period
            .ok_or_else(|| Error::invalid_object("missing required member RefreshPeriod"))?;
        let clear_period = clear_period
            .ok_or_else(|| Error::invalid_object("missing required member ClearPeriod"))?;
        let alert_capabilities = alert_capabilities
            .ok_or_else(|| Error::invalid_object("missing required member AlertCapabilities"))?;
        let alert_descriptors = alert_descriptors
            .ok_or_else(|| Error::invalid_object("missing required member AlertDescriptors"))?;
        let alert = alert.ok_or_else(|| Error::invalid_object("missing required member Alert"))?;

        Ok(Self {
            base,
            alert_period,
            refresh_period,
            clear_period,
            alert_capabilities,
            alert_descriptors,
            alert,
        })
    }
}
