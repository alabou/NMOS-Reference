//! Generated NMOS type: `NcClassDescriptor`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_array_of_event_descriptor::NcArrayOfEventDescriptor;
use crate::generated::nc_array_of_method_descriptor::NcArrayOfMethodDescriptor;
use crate::generated::nc_array_of_property_descriptor::NcArrayOfPropertyDescriptor;
use crate::generated::nc_descriptor::NcDescriptor;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcClassDescriptor`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcClassDescriptor {
    /// `Base`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub base: NcDescriptor,
    /// `classId`. Required.
    #[serde(rename = "classId")]
    pub class_id: Vec<i64>,
    /// `name`. Required.
    #[serde(rename = "name")]
    pub name: String,
    /// `fixedRole`. Required.
    #[serde(rename = "fixedRole")]
    pub fixed_role: Nullable<String>,
    /// `properties`. Required.
    #[serde(rename = "properties")]
    pub properties: NcArrayOfPropertyDescriptor,
    /// `methods`. Required.
    #[serde(rename = "methods")]
    pub methods: NcArrayOfMethodDescriptor,
    /// `events`. Required.
    #[serde(rename = "events")]
    pub events: NcArrayOfEventDescriptor,
}

impl NcClassDescriptor {
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
                "expected JSON object for NcClassDescriptor",
            ));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let base = NcDescriptor::decode(src)?;
        let class_id = match doc.get("classId") {
            Some(v) => Some(decode::array_of_int(v)?),
            None => None,
        };
        let name = match doc.get("name") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let fixed_role = match doc.get("fixedRole") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let properties = match doc.get("properties") {
            Some(v) => Some(NcArrayOfPropertyDescriptor::decode(v)?),
            None => None,
        };
        let methods = match doc.get("methods") {
            Some(v) => Some(NcArrayOfMethodDescriptor::decode(v)?),
            None => None,
        };
        let events = match doc.get("events") {
            Some(v) => Some(NcArrayOfEventDescriptor::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let class_id =
            class_id.ok_or_else(|| Error::invalid_object("missing required member ClassId"))?;
        let name = name.ok_or_else(|| Error::invalid_object("missing required member Name"))?;
        let fixed_role =
            fixed_role.ok_or_else(|| Error::invalid_object("missing required member FixedRole"))?;
        let properties = properties
            .ok_or_else(|| Error::invalid_object("missing required member Properties"))?;
        let methods =
            methods.ok_or_else(|| Error::invalid_object("missing required member Methods"))?;
        let events =
            events.ok_or_else(|| Error::invalid_object("missing required member Events"))?;

        Ok(Self {
            base,
            class_id,
            name,
            fixed_role,
            properties,
            methods,
            events,
        })
    }
}
