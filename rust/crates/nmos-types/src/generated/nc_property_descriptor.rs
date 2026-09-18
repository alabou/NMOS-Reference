//! Generated NMOS type: `NcPropertyDescriptor`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_descriptor::NcDescriptor;
use crate::generated::nc_property_id::NcPropertyId;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcPropertyDescriptor`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcPropertyDescriptor {
    /// `Base`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub base: NcDescriptor,
    /// `id`. Required.
    #[serde(rename = "id")]
    pub id: NcPropertyId,
    /// `name`. Required.
    #[serde(rename = "name")]
    pub name: String,
    /// `typeName`. Required.
    #[serde(rename = "typeName")]
    pub type_name: Nullable<String>,
    /// `isReadOnly`. Required.
    #[serde(rename = "isReadOnly")]
    pub is_read_only: bool,
    /// `isNullable`. Required.
    #[serde(rename = "isNullable")]
    pub is_nullable: bool,
    /// `isSequence`. Required.
    #[serde(rename = "isSequence")]
    pub is_sequence: bool,
    /// `isDeprecated`. Required.
    #[serde(rename = "isDeprecated")]
    pub is_deprecated: bool,
    /// `constraints`. Required.
    #[serde(rename = "constraints")]
    pub constraints: RawJson,
}

impl NcPropertyDescriptor {
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
                "expected JSON object for NcPropertyDescriptor",
            ));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let base = NcDescriptor::decode(src)?;
        let id = match doc.get("id") {
            Some(v) => Some(NcPropertyId::decode(v)?),
            None => None,
        };
        let name = match doc.get("name") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let type_name = match doc.get("typeName") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let is_read_only = match doc.get("isReadOnly") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let is_nullable = match doc.get("isNullable") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let is_sequence = match doc.get("isSequence") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let is_deprecated = match doc.get("isDeprecated") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let constraints = doc.get("constraints").map(decode::raw_json);

        // Required presence, for every member, before any assertion runs.
        let id = id.ok_or_else(|| Error::invalid_object("missing required member Id"))?;
        let name = name.ok_or_else(|| Error::invalid_object("missing required member Name"))?;
        let type_name =
            type_name.ok_or_else(|| Error::invalid_object("missing required member TypeName"))?;
        let is_read_only = is_read_only
            .ok_or_else(|| Error::invalid_object("missing required member IsReadOnly"))?;
        let is_nullable = is_nullable
            .ok_or_else(|| Error::invalid_object("missing required member IsNullable"))?;
        let is_sequence = is_sequence
            .ok_or_else(|| Error::invalid_object("missing required member IsSequence"))?;
        let is_deprecated = is_deprecated
            .ok_or_else(|| Error::invalid_object("missing required member IsDeprecated"))?;
        let constraints = constraints
            .ok_or_else(|| Error::invalid_object("missing required member Constraints"))?;

        Ok(Self {
            base,
            id,
            name,
            type_name,
            is_read_only,
            is_nullable,
            is_sequence,
            is_deprecated,
            constraints,
        })
    }
}
