//! Generated NMOS type: `NcBlockMemberDescriptor`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_descriptor::NcDescriptor;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcBlockMemberDescriptor`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcBlockMemberDescriptor {
    /// `Base`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub base: NcDescriptor,
    /// `role`. Required.
    #[serde(rename = "role")]
    pub role: String,
    /// `oid`. Required.
    #[serde(rename = "oid")]
    pub oid: i64,
    /// `constantOid`. Required.
    #[serde(rename = "constantOid")]
    pub constant_oid: bool,
    /// `classId`. Required.
    #[serde(rename = "classId")]
    pub class_id: Vec<i64>,
    /// `userLabel`. Required.
    #[serde(rename = "userLabel")]
    pub user_label: Nullable<String>,
    /// `owner`. Required.
    #[serde(rename = "owner")]
    pub owner: i64,
}

impl NcBlockMemberDescriptor {
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
                "expected JSON object for NcBlockMemberDescriptor",
            ));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let base = NcDescriptor::decode(src)?;
        let role = match doc.get("role") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let oid = match doc.get("oid") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let constant_oid = match doc.get("constantOid") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let class_id = match doc.get("classId") {
            Some(v) => Some(decode::array_of_int(v)?),
            None => None,
        };
        let user_label = match doc.get("userLabel") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let owner = match doc.get("owner") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let role = role.ok_or_else(|| Error::invalid_object("missing required member Role"))?;
        let oid = oid.ok_or_else(|| Error::invalid_object("missing required member OId"))?;
        let constant_oid = constant_oid
            .ok_or_else(|| Error::invalid_object("missing required member ConstantOId"))?;
        let class_id =
            class_id.ok_or_else(|| Error::invalid_object("missing required member ClassId"))?;
        let user_label =
            user_label.ok_or_else(|| Error::invalid_object("missing required member UserLabel"))?;
        let owner = owner.ok_or_else(|| Error::invalid_object("missing required member Owner"))?;

        Ok(Self {
            base,
            role,
            oid,
            constant_oid,
            class_id,
            user_label,
            owner,
        })
    }
}
