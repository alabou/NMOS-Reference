//! Generated NMOS type: `NResourceCore`. DO NOT EDIT.
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

/// `NResourceCore`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NResourceCore {
    /// `id`. Required.
    #[serde(rename = "id")]
    pub id: String,
    /// `version`. Required.
    #[serde(rename = "version")]
    pub version: Tai,
    /// `label`. Required.
    #[serde(rename = "label")]
    pub label: String,
    /// `description`. Required.
    #[serde(rename = "description")]
    pub description: String,
    /// `tags`. Required.
    #[serde(rename = "tags")]
    pub tags: Tags,
}

impl NResourceCore {
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
                "expected JSON object for NResourceCore",
            ));
        };

        let id = match doc.get("id") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let version = match doc.get("version") {
            Some(v) => Some(decode::tai(v)?),
            None => None,
        };
        let label = match doc.get("label") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let description = match doc.get("description") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let tags = match doc.get("tags") {
            Some(v) => Some(decode::tags(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let id = id.ok_or_else(|| Error::invalid_object("missing required member Id"))?;
        let version =
            version.ok_or_else(|| Error::invalid_object("missing required member Version"))?;
        let label = label.ok_or_else(|| Error::invalid_object("missing required member Label"))?;
        let description = description
            .ok_or_else(|| Error::invalid_object("missing required member Description"))?;
        let tags = tags.ok_or_else(|| Error::invalid_object("missing required member Tags"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_resource_id_string(&id)?;

        Ok(Self {
            id,
            version,
            label,
            description,
            tags,
        })
    }
}
