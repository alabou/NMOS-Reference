//! Generated NMOS type: `NQueryWebSocketGrainDataSource`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nsource::NSource;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NQueryWebSocketGrainDataSource`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NQueryWebSocketGrainDataSource {
    /// `path`. Required.
    #[serde(rename = "path")]
    pub path: String,
    /// `pre`. Optional, so absent means the member was not present.
    #[serde(rename = "pre", skip_serializing_if = "Option::is_none")]
    pub pre: Option<NSource>,
    /// `post`. Optional, so absent means the member was not present.
    #[serde(rename = "post", skip_serializing_if = "Option::is_none")]
    pub post: Option<NSource>,
}

impl NQueryWebSocketGrainDataSource {
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
                "expected JSON object for NQueryWebSocketGrainDataSource",
            ));
        };

        let path = match doc.get("path") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let pre = match doc.get("pre") {
            Some(v) => Some(NSource::decode(v)?),
            None => None,
        };
        let post = match doc.get("post") {
            Some(v) => Some(NSource::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let path = path.ok_or_else(|| Error::invalid_object("missing required member Path"))?;

        Ok(Self { path, pre, post })
    }
}
