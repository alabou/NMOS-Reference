//! Generated NMOS type: `NcBlock`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_array_of_block_member_descriptor::NcArrayOfBlockMemberDescriptor;
use crate::generated::nc_object::NcObject;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcBlock`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcBlock {
    /// `Base`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub base: NcObject,
    /// `enabled`. Required.
    #[serde(rename = "enabled")]
    pub enabled: bool,
    /// `members`. Required.
    #[serde(rename = "members")]
    pub members: NcArrayOfBlockMemberDescriptor,
}

impl NcBlock {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NcBlock"));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let base = NcObject::decode(src)?;
        let enabled = match doc.get("enabled") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let members = match doc.get("members") {
            Some(v) => Some(NcArrayOfBlockMemberDescriptor::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let enabled =
            enabled.ok_or_else(|| Error::invalid_object("missing required member Enabled"))?;
        let members =
            members.ok_or_else(|| Error::invalid_object("missing required member Members"))?;

        Ok(Self {
            base,
            enabled,
            members,
        })
    }
}
