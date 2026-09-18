//! Generated NMOS type: `NcPropertychangedEvent`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::nc_property_id::NcPropertyId;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NcPropertychangedEvent`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NcPropertychangedEvent {
    /// `propertyId`. Required.
    #[serde(rename = "propertyId")]
    pub property_id: NcPropertyId,
    /// `changeType`. Required.
    #[serde(rename = "changeType")]
    pub change_type: i64,
    /// `value`. Required.
    #[serde(rename = "value")]
    pub generic_value: RawJson,
    /// `sequenceItemIndex`. Required.
    #[serde(rename = "sequenceItemIndex")]
    pub sequence_item_index: Nullable<Value>,
}

impl NcPropertychangedEvent {
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
                "expected JSON object for NcPropertychangedEvent",
            ));
        };

        let property_id = match doc.get("propertyId") {
            Some(v) => Some(NcPropertyId::decode(v)?),
            None => None,
        };
        let change_type = match doc.get("changeType") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let generic_value = doc.get("value").map(decode::raw_json);
        let sequence_item_index = doc.get("sequenceItemIndex").map(decode::null_value);

        // Required presence, for every member, before any assertion runs.
        let property_id = property_id
            .ok_or_else(|| Error::invalid_object("missing required member PropertyId"))?;
        let change_type = change_type
            .ok_or_else(|| Error::invalid_object("missing required member ChangeType"))?;
        let generic_value = generic_value
            .ok_or_else(|| Error::invalid_object("missing required member GenericValue"))?;
        let sequence_item_index = sequence_item_index
            .ok_or_else(|| Error::invalid_object("missing required member SequenceItemIndex"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_property_change_type(change_type)?;
        validators::check_null_positive_integer(sequence_item_index.as_json())?;

        Ok(Self {
            property_id,
            change_type,
            generic_value,
            sequence_item_index,
        })
    }
}
