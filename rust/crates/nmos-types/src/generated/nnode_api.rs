//! Generated NMOS type: `NNodeApi`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::narray_of_node_endpoint::NArrayOfNodeEndpoint;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NNodeApi`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NNodeApi {
    /// `versions`. Required.
    #[serde(rename = "versions")]
    pub versions: Vec<String>,
    /// `endpoints`. Required.
    #[serde(rename = "endpoints")]
    pub endpoints: NArrayOfNodeEndpoint,
}

impl NNodeApi {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NNodeApi"));
        };

        let versions = match doc.get("versions") {
            Some(v) => Some(decode::array_of_string(v)?),
            None => None,
        };
        let endpoints = match doc.get("endpoints") {
            Some(v) => Some(NArrayOfNodeEndpoint::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let versions =
            versions.ok_or_else(|| Error::invalid_object("missing required member Versions"))?;
        let endpoints =
            endpoints.ok_or_else(|| Error::invalid_object("missing required member Endpoints"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_node_api_versions(versions.iter().map(String::as_str))?;

        Ok(Self {
            versions,
            endpoints,
        })
    }
}
