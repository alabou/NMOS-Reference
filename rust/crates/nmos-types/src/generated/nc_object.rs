//! Generated NMOS type: `NcObject`. DO NOT EDIT.
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

/// `NcObject`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcObject {
    /// `id`. Required.
    #[serde(rename = "id")]
    pub id: Vec<i64>,
    /// `oid`. Required.
    #[serde(rename = "oid")]
    pub oid: i64,
    /// `constantOid`. Required.
    #[serde(rename = "constantOid")]
    pub constant_oid: bool,
    /// `owner`. Required.
    #[serde(rename = "owner")]
    pub owner: Nullable<Value>,
    /// `role`. Required.
    #[serde(rename = "role")]
    pub role: String,
    /// `userLabel`. Required.
    #[serde(rename = "userLabel")]
    pub user_label: Nullable<String>,
    /// `touchpoints`. Required.
    #[serde(rename = "touchpoints")]
    pub touchpoints: Vec<Value>,
    /// `runtimePropertyConstraints`. Required.
    #[serde(rename = "runtimePropertyConstraints")]
    pub runtime_property_constraints: Vec<Value>,
}

impl NcObject {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NcObject"));
        };

        let id = match doc.get("id") {
            Some(v) => Some(decode::array_of_int(v)?),
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
        let owner = doc.get("owner").map(decode::null_value);
        let role = match doc.get("role") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let user_label = match doc.get("userLabel") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let touchpoints = match doc.get("touchpoints") {
            Some(v) => Some(decode::array_of_generic(v)?),
            None => None,
        };
        let runtime_property_constraints = match doc.get("runtimePropertyConstraints") {
            Some(v) => Some(decode::array_of_generic(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let id = id.ok_or_else(|| Error::invalid_object("missing required member Id"))?;
        let oid = oid.ok_or_else(|| Error::invalid_object("missing required member OId"))?;
        let constant_oid = constant_oid
            .ok_or_else(|| Error::invalid_object("missing required member ConstantOId"))?;
        let owner = owner.ok_or_else(|| Error::invalid_object("missing required member Owner"))?;
        let role = role.ok_or_else(|| Error::invalid_object("missing required member Role"))?;
        let user_label =
            user_label.ok_or_else(|| Error::invalid_object("missing required member UserLabel"))?;
        let touchpoints = touchpoints
            .ok_or_else(|| Error::invalid_object("missing required member Touchpoints"))?;
        let runtime_property_constraints = runtime_property_constraints.ok_or_else(|| {
            Error::invalid_object("missing required member RuntimePropertyConstraints")
        })?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_null_integer(owner.as_json())?;

        Ok(Self {
            id,
            oid,
            constant_oid,
            owner,
            role,
            user_label,
            touchpoints,
            runtime_property_constraints,
        })
    }
}
