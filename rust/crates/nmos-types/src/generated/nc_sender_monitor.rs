//! Generated NMOS type: `NcSenderMonitor`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_status_monitor::NcStatusMonitor;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcSenderMonitor`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcSenderMonitor {
    /// `Base`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub base: NcStatusMonitor,
    /// `linkStatus`. Required.
    #[serde(rename = "linkStatus")]
    pub link_status: i64,
    /// `linkStatusMessage`. Required.
    #[serde(rename = "linkStatusMessage")]
    pub link_status_message: Nullable<String>,
    /// `linkStatusTransitionCounter`. Required.
    #[serde(rename = "linkStatusTransitionCounter")]
    pub link_status_transition_counter: i64,
    /// `transmissionStatus`. Required.
    #[serde(rename = "transmissionStatus")]
    pub transmission_status: i64,
    /// `transmissionStatusMessage`. Required.
    #[serde(rename = "transmissionStatusMessage")]
    pub transmission_status_message: Nullable<String>,
    /// `transmissionStatusTransitionCounter`. Required.
    #[serde(rename = "transmissionStatusTransitionCounter")]
    pub transmission_status_transition_counter: i64,
    /// `externalSynchronizationStatus`. Required.
    #[serde(rename = "externalSynchronizationStatus")]
    pub external_synchronization_status: i64,
    /// `externalSynchronizationStatusMessage`. Required.
    #[serde(rename = "externalSynchronizationStatusMessage")]
    pub external_synchronization_status_message: Nullable<String>,
    /// `externalSynchronizationStatusTransitionCounter`. Required.
    #[serde(rename = "externalSynchronizationStatusTransitionCounter")]
    pub external_synchronization_status_transition_counter: i64,
    /// `essenceStatus`. Required.
    #[serde(rename = "essenceStatus")]
    pub essence_status: i64,
    /// `essenceStatusMessage`. Required.
    #[serde(rename = "essenceStatusMessage")]
    pub essence_status_message: Nullable<String>,
    /// `essenceStatusTransitionCounter`. Required.
    #[serde(rename = "essenceStatusTransitionCounter")]
    pub essence_status_transition_counter: i64,
    /// `synchronizationSourceId`. Required.
    #[serde(rename = "synchronizationSourceId")]
    pub synchronization_source_id: Nullable<String>,
    /// `autoResetCountersAndMessages`. Required.
    #[serde(rename = "autoResetCountersAndMessages")]
    pub auto_reset_counters_and_messages: bool,
}

impl NcSenderMonitor {
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
                "expected JSON object for NcSenderMonitor",
            ));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let base = NcStatusMonitor::decode(src)?;
        let link_status = match doc.get("linkStatus") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let link_status_message = match doc.get("linkStatusMessage") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let link_status_transition_counter = match doc.get("linkStatusTransitionCounter") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let transmission_status = match doc.get("transmissionStatus") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let transmission_status_message = match doc.get("transmissionStatusMessage") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let transmission_status_transition_counter =
            match doc.get("transmissionStatusTransitionCounter") {
                Some(v) => Some(decode::int(v)?),
                None => None,
            };
        let external_synchronization_status = match doc.get("externalSynchronizationStatus") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let external_synchronization_status_message =
            match doc.get("externalSynchronizationStatusMessage") {
                Some(v) => Some(decode::nullable_string(v)?),
                None => None,
            };
        let external_synchronization_status_transition_counter =
            match doc.get("externalSynchronizationStatusTransitionCounter") {
                Some(v) => Some(decode::int(v)?),
                None => None,
            };
        let essence_status = match doc.get("essenceStatus") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let essence_status_message = match doc.get("essenceStatusMessage") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let essence_status_transition_counter = match doc.get("essenceStatusTransitionCounter") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let synchronization_source_id = match doc.get("synchronizationSourceId") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let auto_reset_counters_and_messages = match doc.get("autoResetCountersAndMessages") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let link_status = link_status
            .ok_or_else(|| Error::invalid_object("missing required member LinkStatus"))?;
        let link_status_message = link_status_message
            .ok_or_else(|| Error::invalid_object("missing required member LinkStatusMessage"))?;
        let link_status_transition_counter = link_status_transition_counter.ok_or_else(|| {
            Error::invalid_object("missing required member LinkStatusTransitionCounter")
        })?;
        let transmission_status = transmission_status
            .ok_or_else(|| Error::invalid_object("missing required member TransmissionStatus"))?;
        let transmission_status_message = transmission_status_message.ok_or_else(|| {
            Error::invalid_object("missing required member TransmissionStatusMessage")
        })?;
        let transmission_status_transition_counter = transmission_status_transition_counter
            .ok_or_else(|| {
                Error::invalid_object("missing required member TransmissionStatusTransitionCounter")
            })?;
        let external_synchronization_status = external_synchronization_status.ok_or_else(|| {
            Error::invalid_object("missing required member ExternalSynchronizationStatus")
        })?;
        let external_synchronization_status_message = external_synchronization_status_message
            .ok_or_else(|| {
                Error::invalid_object(
                    "missing required member ExternalSynchronizationStatusMessage",
                )
            })?;
        let external_synchronization_status_transition_counter =
            external_synchronization_status_transition_counter.ok_or_else(|| {
                Error::invalid_object(
                    "missing required member ExternalSynchronizationStatusTransitionCounter",
                )
            })?;
        let essence_status = essence_status
            .ok_or_else(|| Error::invalid_object("missing required member EssenceStatus"))?;
        let essence_status_message = essence_status_message
            .ok_or_else(|| Error::invalid_object("missing required member EssenceStatusMessage"))?;
        let essence_status_transition_counter =
            essence_status_transition_counter.ok_or_else(|| {
                Error::invalid_object("missing required member EssenceStatusTransitionCounter")
            })?;
        let synchronization_source_id = synchronization_source_id.ok_or_else(|| {
            Error::invalid_object("missing required member SynchronizationSourceId")
        })?;
        let auto_reset_counters_and_messages =
            auto_reset_counters_and_messages.ok_or_else(|| {
                Error::invalid_object("missing required member AutoResetCountersAndMessages")
            })?;

        Ok(Self {
            base,
            link_status,
            link_status_message,
            link_status_transition_counter,
            transmission_status,
            transmission_status_message,
            transmission_status_transition_counter,
            external_synchronization_status,
            external_synchronization_status_message,
            external_synchronization_status_transition_counter,
            essence_status,
            essence_status_message,
            essence_status_transition_counter,
            synchronization_source_id,
            auto_reset_counters_and_messages,
        })
    }
}
