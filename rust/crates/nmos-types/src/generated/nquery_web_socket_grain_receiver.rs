//! Generated NMOS type: `NQueryWebSocketGrainReceiver`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::narray_of_query_web_socket_grain_data_receiver::NArrayOfQueryWebSocketGrainDataReceiver;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NQueryWebSocketGrainReceiver`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NQueryWebSocketGrainReceiver {
    /// `type`. Required.
    #[serde(rename = "type")]
    pub r#type: String,
    /// `topic`. Required.
    #[serde(rename = "topic")]
    pub topic: String,
    /// `data`. Required.
    #[serde(rename = "data")]
    pub data: NArrayOfQueryWebSocketGrainDataReceiver,
}

impl NQueryWebSocketGrainReceiver {
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
                "expected JSON object for NQueryWebSocketGrainReceiver",
            ));
        };

        let r#type = match doc.get("type") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let topic = match doc.get("topic") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let data = match doc.get("data") {
            Some(v) => Some(NArrayOfQueryWebSocketGrainDataReceiver::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let r#type = r#type.ok_or_else(|| Error::invalid_object("missing required member Type"))?;
        let topic = topic.ok_or_else(|| Error::invalid_object("missing required member Topic"))?;
        let data = data.ok_or_else(|| Error::invalid_object("missing required member Data"))?;

        Ok(Self {
            r#type,
            topic,
            data,
        })
    }
}
