//! Generated NMOS type: `NWebSocketSenderTransportParams`. DO NOT EDIT.
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

/// `NWebSocketSenderTransportParams`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NWebSocketSenderTransportParams {
    /// `connection_uri`. Optional, so absent means the member was not present.
    #[serde(rename = "connection_uri", skip_serializing_if = "Option::is_none")]
    pub connection_uri: Option<Nullable<String>>,
    /// `connection_authorization`. Optional, so absent means the member was not present.
    #[serde(
        rename = "connection_authorization",
        skip_serializing_if = "Option::is_none"
    )]
    pub connection_authorization: Option<Nullable<Value>>,
}

impl NWebSocketSenderTransportParams {
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
                "expected JSON object for NWebSocketSenderTransportParams",
            ));
        };

        let connection_uri = match doc.get("connection_uri") {
            Some(v) => Some(decode::nullable_string(v)?),
            None => None,
        };
        let connection_authorization = doc.get("connection_authorization").map(decode::null_value);

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        if let Some(v) = &connection_authorization {
            validators::check_auto_bool(v.as_json())?;
        }

        Ok(Self {
            connection_uri,
            connection_authorization,
        })
    }
}
