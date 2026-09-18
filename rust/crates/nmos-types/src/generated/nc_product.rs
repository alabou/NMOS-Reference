//! Generated NMOS type: `NcProduct`. DO NOT EDIT.
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

/// `NcProduct`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcProduct {
    /// `name`. Required.
    #[serde(rename = "name")]
    pub name: String,
    /// `key`. Required.
    #[serde(rename = "key")]
    pub key: String,
    /// `revisionLevel`. Required.
    #[serde(rename = "revisionLevel")]
    pub revision_level: String,
    /// `brandName`. Required.
    #[serde(rename = "brandName")]
    pub brand_name: Nullable<String>,
    /// `uuid`. Required.
    #[serde(rename = "uuid")]
    pub uuid: String,
    /// `description`. Required.
    #[serde(rename = "description")]
    pub description: Nullable<String>,
}

impl NcProduct {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NcProduct"));
        };

        let name = match doc.get("name") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let key = match doc.get("key") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let revision_level = match doc.get("revisionLevel") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let brand_name = match doc.get("brandName") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let uuid = match doc.get("uuid") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let description = match doc.get("description") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let name = name.ok_or_else(|| Error::invalid_object("missing required member Name"))?;
        let key = key.ok_or_else(|| Error::invalid_object("missing required member Key"))?;
        let revision_level = revision_level
            .ok_or_else(|| Error::invalid_object("missing required member RevisionLevel"))?;
        let brand_name =
            brand_name.ok_or_else(|| Error::invalid_object("missing required member BrandName"))?;
        let uuid = uuid.ok_or_else(|| Error::invalid_object("missing required member Uuid"))?;
        let description = description
            .ok_or_else(|| Error::invalid_object("missing required member Description"))?;

        Ok(Self {
            name,
            key,
            revision_level,
            brand_name,
            uuid,
            description,
        })
    }
}
