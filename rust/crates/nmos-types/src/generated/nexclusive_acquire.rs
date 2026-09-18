//! Generated NMOS type: `NExclusiveAcquire`. DO NOT EDIT.
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

/// `NExclusiveAcquire`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NExclusiveAcquire {
    /// `owner`. Required.
    #[serde(rename = "owner")]
    pub owner: String,
    /// `exclusive_key`. Required.
    #[serde(rename = "exclusive_key")]
    pub exclusive_key: String,
}

impl NExclusiveAcquire {
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
                "expected JSON object for NExclusiveAcquire",
            ));
        };

        let owner = match doc.get("owner") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let exclusive_key = match doc.get("exclusive_key") {
            Some(v) => decode::string(v)?,
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let owner = owner.ok_or_else(|| Error::invalid_object("missing required member Owner"))?;
        let exclusive_key = exclusive_key
            .ok_or_else(|| Error::invalid_object("missing required member ExclusiveKey"))?;

        Ok(Self {
            owner,
            exclusive_key,
        })
    }
}
