//! Generated NMOS type: `NConstraintFloat`. DO NOT EDIT.
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

/// `NConstraintFloat`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NConstraintFloat {
    /// `enum`. Optional, so absent means the member was not present.
    #[serde(rename = "enum", skip_serializing_if = "Option::is_none")]
    pub r#enum: Option<Vec<f64>>,
    /// `minimum`. Optional, so absent means the member was not present.
    #[serde(rename = "minimum", skip_serializing_if = "Option::is_none")]
    pub minimum: Option<f64>,
    /// `maximum`. Optional, so absent means the member was not present.
    #[serde(rename = "maximum", skip_serializing_if = "Option::is_none")]
    pub maximum: Option<f64>,
}

impl NConstraintFloat {
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
                "expected JSON object for NConstraintFloat",
            ));
        };

        let r#enum = match doc.get("enum") {
            Some(v) => Some(decode::array_of_float(v)?),
            None => None,
        };
        let minimum = match doc.get("minimum") {
            Some(v) => Some(decode::float(v)?),
            None => None,
        };
        let maximum = match doc.get("maximum") {
            Some(v) => Some(decode::float(v)?),
            None => None,
        };

        Ok(Self {
            r#enum,
            minimum,
            maximum,
        })
    }
}
