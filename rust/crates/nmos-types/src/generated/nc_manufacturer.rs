//! Generated NMOS type: `NcManufacturer`. DO NOT EDIT.
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

/// `NcManufacturer`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcManufacturer {
    /// `name`. Required.
    #[serde(rename = "name")]
    pub name: String,
    /// `organizationId`. Required.
    #[serde(rename = "organizationId")]
    pub organization_id: Nullable<Value>,
    /// `website`. Required.
    #[serde(rename = "website")]
    pub web_site: Nullable<String>,
}

impl NcManufacturer {
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
                "expected JSON object for NcManufacturer",
            ));
        };

        let name = match doc.get("name") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let organization_id = doc.get("organizationId").map(decode::null_value);
        let web_site = match doc.get("website") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let name = name.ok_or_else(|| Error::invalid_object("missing required member Name"))?;
        let organization_id = organization_id
            .ok_or_else(|| Error::invalid_object("missing required member OrganizationId"))?;
        let web_site =
            web_site.ok_or_else(|| Error::invalid_object("missing required member WebSite"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_null_integer(organization_id.as_json())?;

        Ok(Self {
            name,
            organization_id,
            web_site,
        })
    }
}
