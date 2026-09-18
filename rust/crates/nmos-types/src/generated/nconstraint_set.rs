//! Generated NMOS type: `NConstraintSet`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::handwritten::NConstraints;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NConstraintSet`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NConstraintSet {
    /// `urn:x-nmos:cap:meta:label`. Optional, so absent means the member was not present.
    #[serde(
        rename = "urn:x-nmos:cap:meta:label",
        skip_serializing_if = "Option::is_none"
    )]
    pub meta_label: Option<String>,
    /// `urn:x-matrox:cap:meta:format`. Optional, so absent means the member was not present.
    #[serde(
        rename = "urn:x-matrox:cap:meta:format",
        skip_serializing_if = "Option::is_none"
    )]
    pub meta_format: Option<EnumId>,
    /// `urn:x-matrox:cap:meta:layer`. Optional, so absent means the member was not present.
    #[serde(
        rename = "urn:x-matrox:cap:meta:layer",
        skip_serializing_if = "Option::is_none"
    )]
    pub meta_layer: Option<i64>,
    /// `urn:x-matrox:cap:meta:layer_enabled`. Optional, so absent means the member was not present.
    #[serde(
        rename = "urn:x-matrox:cap:meta:layer_enabled",
        skip_serializing_if = "Option::is_none"
    )]
    pub meta_layer_enabled: Option<bool>,
    /// `urn:x-matrox:cap:meta:layer_compatibility_groups`. Optional, so absent means the member was not present.
    #[serde(
        rename = "urn:x-matrox:cap:meta:layer_compatibility_groups",
        skip_serializing_if = "Option::is_none"
    )]
    pub meta_layer_compatibility_groups: Option<Vec<i64>>,
    /// `urn:x-nmos:cap:meta:enabled`. Optional, so absent means the member was not present.
    #[serde(
        rename = "urn:x-nmos:cap:meta:enabled",
        skip_serializing_if = "Option::is_none"
    )]
    pub meta_enabled: Option<bool>,
    /// `urn:x-nmos:cap:meta:preference`. Optional, so absent means the member was not present.
    #[serde(
        rename = "urn:x-nmos:cap:meta:preference",
        skip_serializing_if = "Option::is_none"
    )]
    pub meta_preference: Option<i64>,
    /// `urn:x-matrox:cap:meta:info_block`. Optional, so absent means the member was not present.
    #[serde(
        rename = "urn:x-matrox:cap:meta:info_block",
        skip_serializing_if = "Option::is_none"
    )]
    pub meta_info_block: Option<Vec<i64>>,
    /// `Constraints`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub constraints: NConstraints,
}

impl NConstraintSet {
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
                "expected JSON object for NConstraintSet",
            ));
        };

        let meta_label = match doc.get("urn:x-nmos:cap:meta:label") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let meta_format = match doc.get("urn:x-matrox:cap:meta:format") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let meta_layer = match doc.get("urn:x-matrox:cap:meta:layer") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let meta_layer_enabled = match doc.get("urn:x-matrox:cap:meta:layer_enabled") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let meta_layer_compatibility_groups =
            match doc.get("urn:x-matrox:cap:meta:layer_compatibility_groups") {
                Some(v) => Some(decode::array_of_int(v)?),
                None => None,
            };
        let meta_enabled = match doc.get("urn:x-nmos:cap:meta:enabled") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let meta_preference = match doc.get("urn:x-nmos:cap:meta:preference") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let meta_info_block = match doc.get("urn:x-matrox:cap:meta:info_block") {
            Some(v) => Some(decode::array_of_int(v)?),
            None => None,
        };
        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let constraints = NConstraints::decode(src)?;

        // Optional defaults, applied between decode and the required check --
        // Python's `set_optional_to_default()`, in the same position.
        //
        // The rule is `optional AND default`, and it is narrower than the
        // descriptors read: of 27 members carrying a default, 14 are NOT
        // optional and their default is therefore inert -- a body omitting one
        // is REJECTED, not filled in. `#[serde(default)]` would have quietly
        // accepted all 27, so this step is written rather than derived.
        let meta_enabled = meta_enabled.or(Some(true));
        let meta_preference = meta_preference.or(Some(0));

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        if let Some(v) = &meta_preference {
            validators::check_constraint_set_preference(*v)?;
        }

        Ok(Self {
            meta_label,
            meta_format,
            meta_layer,
            meta_layer_enabled,
            meta_layer_compatibility_groups,
            meta_enabled,
            meta_preference,
            meta_info_block,
            constraints,
        })
    }
}
