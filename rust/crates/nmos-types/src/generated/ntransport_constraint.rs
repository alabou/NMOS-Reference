//! Generated NMOS type: `NTransportConstraint`. DO NOT EDIT.
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

/// `NTransportConstraint`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NTransportConstraint {
    /// `minimum`. Optional, so absent means the member was not present.
    #[serde(rename = "minimum", skip_serializing_if = "Option::is_none")]
    pub minimum: Option<f64>,
    /// `maximum`. Optional, so absent means the member was not present.
    #[serde(rename = "maximum", skip_serializing_if = "Option::is_none")]
    pub maximum: Option<f64>,
    /// `enum`. Optional, so absent means the member was not present.
    #[serde(rename = "enum", skip_serializing_if = "Option::is_none")]
    pub r#enum: Option<Nullable<Value>>,
    /// `pattern`. Optional, so absent means the member was not present.
    #[serde(rename = "pattern", skip_serializing_if = "Option::is_none")]
    pub pattern: Option<String>,
    /// `description`. Optional, so absent means the member was not present.
    #[serde(rename = "description", skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
}

impl NTransportConstraint {
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
                "expected JSON object for NTransportConstraint",
            ));
        };

        let minimum = match doc.get("minimum") {
            Some(v) => Some(decode::float(v)?),
            None => None,
        };
        let maximum = match doc.get("maximum") {
            Some(v) => Some(decode::float(v)?),
            None => None,
        };
        let r#enum = doc.get("enum").map(decode::null_value);
        let pattern = match doc.get("pattern") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let description = match doc.get("description") {
            Some(v) => decode::string(v)?,
            None => None,
        };

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        if let Some(v) = &r#enum {
            validators::check_transport_constraint_enum_length(
                v.as_option()
                    .and_then(serde_json::Value::as_array)
                    .map_or(0, Vec::len),
            )?;
        }

        Ok(Self {
            minimum,
            maximum,
            r#enum,
            pattern,
            description,
        })
    }
}
