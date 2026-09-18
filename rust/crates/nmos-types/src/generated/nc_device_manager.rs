//! Generated NMOS type: `NcDeviceManager`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_device_operational_state::NcDeviceOperationalState;
use crate::generated::nc_manager::NcManager;
use crate::generated::nc_manufacturer::NcManufacturer;
use crate::generated::nc_product::NcProduct;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcDeviceManager`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcDeviceManager {
    /// `Base`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub base: NcManager,
    /// `ncVersion`. Required.
    #[serde(rename = "ncVersion")]
    pub nc_version: String,
    /// `manufacturer`. Required.
    #[serde(rename = "manufacturer")]
    pub manufacturer: NcManufacturer,
    /// `product`. Required.
    #[serde(rename = "product")]
    pub product: NcProduct,
    /// `serialNumber`. Required.
    #[serde(rename = "serialNumber")]
    pub serial_number: String,
    /// `userInventoryCode`. Required.
    #[serde(rename = "userInventoryCode")]
    pub user_inventory_code: Nullable<String>,
    /// `deviceName`. Required.
    #[serde(rename = "deviceName")]
    pub device_name: Nullable<String>,
    /// `deviceRole`. Required.
    #[serde(rename = "deviceRole")]
    pub device_role: Nullable<String>,
    /// `operationalState`. Required.
    #[serde(rename = "operationalState")]
    pub operational_state: NcDeviceOperationalState,
    /// `resetCause`. Required.
    #[serde(rename = "resetCause")]
    pub reset_cause: i64,
    /// `message`. Required.
    #[serde(rename = "message")]
    pub message: Nullable<String>,
}

impl NcDeviceManager {
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
                "expected JSON object for NcDeviceManager",
            ));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let base = NcManager::decode(src)?;
        let nc_version = match doc.get("ncVersion") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let manufacturer = match doc.get("manufacturer") {
            Some(v) => Some(NcManufacturer::decode(v)?),
            None => None,
        };
        let product = match doc.get("product") {
            Some(v) => Some(NcProduct::decode(v)?),
            None => None,
        };
        let serial_number = match doc.get("serialNumber") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let user_inventory_code = match doc.get("userInventoryCode") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let device_name = match doc.get("deviceName") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let device_role = match doc.get("deviceRole") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let operational_state = match doc.get("operationalState") {
            Some(v) => Some(NcDeviceOperationalState::decode(v)?),
            None => None,
        };
        let reset_cause = match doc.get("resetCause") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let message = match doc.get("message") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let nc_version =
            nc_version.ok_or_else(|| Error::invalid_object("missing required member NcVersion"))?;
        let manufacturer = manufacturer
            .ok_or_else(|| Error::invalid_object("missing required member Manufacturer"))?;
        let product =
            product.ok_or_else(|| Error::invalid_object("missing required member Product"))?;
        let serial_number = serial_number
            .ok_or_else(|| Error::invalid_object("missing required member SerialNumber"))?;
        let user_inventory_code = user_inventory_code
            .ok_or_else(|| Error::invalid_object("missing required member UserInventoryCode"))?;
        let device_name = device_name
            .ok_or_else(|| Error::invalid_object("missing required member DeviceName"))?;
        let device_role = device_role
            .ok_or_else(|| Error::invalid_object("missing required member DeviceRole"))?;
        let operational_state = operational_state
            .ok_or_else(|| Error::invalid_object("missing required member OperationalState"))?;
        let reset_cause = reset_cause
            .ok_or_else(|| Error::invalid_object("missing required member ResetCause"))?;
        let message =
            message.ok_or_else(|| Error::invalid_object("missing required member Message"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_reset_cause(reset_cause)?;

        Ok(Self {
            base,
            nc_version,
            manufacturer,
            product,
            serial_number,
            user_inventory_code,
            device_name,
            device_role,
            operational_state,
            reset_cause,
            message,
        })
    }
}
