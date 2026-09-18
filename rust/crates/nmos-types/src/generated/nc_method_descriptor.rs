//! Generated NMOS type: `NcMethodDescriptor`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_array_of_parameter_descriptor::NcArrayOfParameterDescriptor;
use crate::generated::nc_descriptor::NcDescriptor;
use crate::generated::nc_method_id::NcMethodId;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcMethodDescriptor`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcMethodDescriptor {
    /// `Base`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub base: NcDescriptor,
    /// `id`. Required.
    #[serde(rename = "id")]
    pub id: NcMethodId,
    /// `name`. Required.
    #[serde(rename = "name")]
    pub name: String,
    /// `resultDatatype`. Required.
    #[serde(rename = "resultDatatype")]
    pub result_data_type: String,
    /// `isDeprecated`. Required.
    #[serde(rename = "isDeprecated")]
    pub is_deprecated: bool,
    /// `parameters`. Required.
    #[serde(rename = "parameters")]
    pub parameters: NcArrayOfParameterDescriptor,
}

impl NcMethodDescriptor {
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
                "expected JSON object for NcMethodDescriptor",
            ));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let base = NcDescriptor::decode(src)?;
        let id = match doc.get("id") {
            Some(v) => Some(NcMethodId::decode(v)?),
            None => None,
        };
        let name = match doc.get("name") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let result_data_type = match doc.get("resultDatatype") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let is_deprecated = match doc.get("isDeprecated") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let parameters = match doc.get("parameters") {
            Some(v) => Some(NcArrayOfParameterDescriptor::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let id = id.ok_or_else(|| Error::invalid_object("missing required member Id"))?;
        let name = name.ok_or_else(|| Error::invalid_object("missing required member Name"))?;
        let result_data_type = result_data_type
            .ok_or_else(|| Error::invalid_object("missing required member ResultDataType"))?;
        let is_deprecated = is_deprecated
            .ok_or_else(|| Error::invalid_object("missing required member IsDeprecated"))?;
        let parameters = parameters
            .ok_or_else(|| Error::invalid_object("missing required member Parameters"))?;

        Ok(Self {
            base,
            id,
            name,
            result_data_type,
            is_deprecated,
            parameters,
        })
    }
}
