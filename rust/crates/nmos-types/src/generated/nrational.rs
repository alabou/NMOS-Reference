//! Generated NMOS type: `NRational`. DO NOT EDIT.
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

/// `NRational`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NRational {
    /// `numerator`. Required.
    #[serde(rename = "numerator")]
    pub numerator: i64,
    /// `denominator`. Optional, so absent means the member was not present.
    #[serde(rename = "denominator", skip_serializing_if = "Option::is_none")]
    pub denominator: Option<i64>,
}

impl NRational {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NRational"));
        };

        let numerator = match doc.get("numerator") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let denominator = match doc.get("denominator") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };

        // Optional defaults, applied between decode and the required check --
        // Python's `set_optional_to_default()`, in the same position.
        //
        // The rule is `optional AND default`, and it is narrower than the
        // descriptors read: of 27 members carrying a default, 14 are NOT
        // optional and their default is therefore inert -- a body omitting one
        // is REJECTED, not filled in. `#[serde(default)]` would have quietly
        // accepted all 27, so this step is written rather than derived.
        let denominator = denominator.or(Some(1));

        // Required presence, for every member, before any assertion runs.
        let numerator =
            numerator.ok_or_else(|| Error::invalid_object("missing required member Numerator"))?;

        Ok(Self {
            numerator,
            denominator,
        })
    }
}
