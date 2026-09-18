//! Generated NMOS type: `NQueryPayloadNode`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nquery_web_socket_grain_node::NQueryWebSocketGrainNode;
use crate::generated::nrational::NRational;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NQueryPayloadNode`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NQueryPayloadNode {
    /// `grain_type`. Required.
    #[serde(rename = "grain_type")]
    pub grain_type: String,
    /// `source_id`. Required.
    #[serde(rename = "source_id")]
    pub source_id: String,
    /// `flow_id`. Required.
    #[serde(rename = "flow_id")]
    pub flow_id: String,
    /// `origin_timestamp`. Required.
    #[serde(rename = "origin_timestamp")]
    pub origin_timestamp: Tai,
    /// `sync_timestamp`. Required.
    #[serde(rename = "sync_timestamp")]
    pub sync_timestamp: Tai,
    /// `creation_timestamp`. Required.
    #[serde(rename = "creation_timestamp")]
    pub creation_timestamp: Tai,
    /// `rate`. Required.
    #[serde(rename = "rate")]
    pub rate: NRational,
    /// `duration`. Required.
    #[serde(rename = "duration")]
    pub duration: NRational,
    /// `grain`. Required.
    #[serde(rename = "grain")]
    pub grain: NQueryWebSocketGrainNode,
}

impl NQueryPayloadNode {
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
                "expected JSON object for NQueryPayloadNode",
            ));
        };

        let grain_type = match doc.get("grain_type") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let source_id = match doc.get("source_id") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let flow_id = match doc.get("flow_id") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let origin_timestamp = match doc.get("origin_timestamp") {
            Some(v) => Some(decode::tai(v)?),
            None => None,
        };
        let sync_timestamp = match doc.get("sync_timestamp") {
            Some(v) => Some(decode::tai(v)?),
            None => None,
        };
        let creation_timestamp = match doc.get("creation_timestamp") {
            Some(v) => Some(decode::tai(v)?),
            None => None,
        };
        let rate = match doc.get("rate") {
            Some(v) => Some(NRational::decode(v)?),
            None => None,
        };
        let duration = match doc.get("duration") {
            Some(v) => Some(NRational::decode(v)?),
            None => None,
        };
        let grain = match doc.get("grain") {
            Some(v) => Some(NQueryWebSocketGrainNode::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let grain_type =
            grain_type.ok_or_else(|| Error::invalid_object("missing required member GrainType"))?;
        let source_id =
            source_id.ok_or_else(|| Error::invalid_object("missing required member SourceId"))?;
        let flow_id =
            flow_id.ok_or_else(|| Error::invalid_object("missing required member FlowId"))?;
        let origin_timestamp = origin_timestamp
            .ok_or_else(|| Error::invalid_object("missing required member OriginTimestamp"))?;
        let sync_timestamp = sync_timestamp
            .ok_or_else(|| Error::invalid_object("missing required member SyncTimestamp"))?;
        let creation_timestamp = creation_timestamp
            .ok_or_else(|| Error::invalid_object("missing required member CreationTimestamp"))?;
        let rate = rate.ok_or_else(|| Error::invalid_object("missing required member Rate"))?;
        let duration =
            duration.ok_or_else(|| Error::invalid_object("missing required member Duration"))?;
        let grain = grain.ok_or_else(|| Error::invalid_object("missing required member Grain"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_resource_id_string(&source_id)?;
        validators::check_resource_id_string(&flow_id)?;

        Ok(Self {
            grain_type,
            source_id,
            flow_id,
            origin_timestamp,
            sync_timestamp,
            creation_timestamp,
            rate,
            duration,
            grain,
        })
    }
}
