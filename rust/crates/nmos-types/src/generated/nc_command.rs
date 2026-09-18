//! Generated NMOS type: `NcCommand`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_method_id::NcMethodId;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcCommand`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcCommand {
    /// `handle`. Required.
    #[serde(rename = "handle")]
    pub handle: i64,
    /// `oid`. Optional, so absent means the member was not present.
    #[serde(rename = "oid", skip_serializing_if = "Option::is_none")]
    pub oid: Option<i64>,
    /// `object`. Optional, so absent means the member was not present.
    #[serde(rename = "object", skip_serializing_if = "Option::is_none")]
    pub object: Option<String>,
    /// `methodId`. Optional, so absent means the member was not present.
    #[serde(rename = "methodId", skip_serializing_if = "Option::is_none")]
    pub method_id: Option<NcMethodId>,
    /// `method`. Optional, so absent means the member was not present.
    #[serde(rename = "method", skip_serializing_if = "Option::is_none")]
    pub method: Option<String>,
    /// `arguments`. Optional, so absent means the member was not present.
    #[serde(rename = "arguments", skip_serializing_if = "Option::is_none")]
    pub arguments: Option<RawJson>,
}

impl NcCommand {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NcCommand"));
        };

        let handle = match doc.get("handle") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let oid = match doc.get("oid") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let object = match doc.get("object") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let method_id = match doc.get("methodId") {
            Some(v) => Some(NcMethodId::decode(v)?),
            None => None,
        };
        let method = match doc.get("method") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let arguments = doc.get("arguments").map(decode::raw_json);

        // Required presence, for every member, before any assertion runs.
        let handle =
            handle.ok_or_else(|| Error::invalid_object("missing required member Handle"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_positive_uint16(handle)?;
        if let Some(v) = &oid {
            validators::check_positive_integer(*v)?;
        }
        if let Some(v) = &arguments {
            validators::check_generic_object(v)?;
        }

        Ok(Self {
            handle,
            oid,
            object,
            method_id,
            method,
            arguments,
        })
    }
}
