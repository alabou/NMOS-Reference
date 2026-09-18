//! Generated NMOS type: `Node`. DO NOT EDIT.
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

/// `Node`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Node {
    /// `manufacturer`. Required.
    #[serde(rename = "manufacturer")]
    pub manufacturer: String,
    /// `product`. Required.
    #[serde(rename = "product")]
    pub product: String,
    /// `sn`. Required.
    #[serde(rename = "sn")]
    pub serial_number: String,
    /// `authorized_users`. Required.
    #[serde(rename = "authorized_users")]
    pub authorized_users: Vec<String>,
}

impl Node {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for Node"));
        };

        let manufacturer = match doc.get("manufacturer") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let product = match doc.get("product") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let serial_number = match doc.get("sn") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let authorized_users = match doc.get("authorized_users") {
            Some(v) => Some(decode::array_of_string(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let manufacturer = manufacturer
            .ok_or_else(|| Error::invalid_object("missing required member Manufacturer"))?;
        let product =
            product.ok_or_else(|| Error::invalid_object("missing required member Product"))?;
        let serial_number = serial_number
            .ok_or_else(|| Error::invalid_object("missing required member SerialNumber"))?;
        let authorized_users = authorized_users
            .ok_or_else(|| Error::invalid_object("missing required member AuthorizedUsers"))?;

        Ok(Self {
            manufacturer,
            product,
            serial_number,
            authorized_users,
        })
    }
}
