//! Generated NMOS type: `NNodeEndpoint`. DO NOT EDIT.
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

/// `NNodeEndpoint`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NNodeEndpoint {
    /// `host`. Required.
    #[serde(rename = "host")]
    pub host: String,
    /// `port`. Required.
    #[serde(rename = "port")]
    pub port: i64,
    /// `protocol`. Required.
    #[serde(rename = "protocol")]
    pub protocol: EnumId,
    /// `authorization`. Optional, so absent means the member was not present.
    #[serde(rename = "authorization", skip_serializing_if = "Option::is_none")]
    pub authorization: Option<bool>,
}

impl NNodeEndpoint {
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
                "expected JSON object for NNodeEndpoint",
            ));
        };

        let host = match doc.get("host") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let port = match doc.get("port") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let protocol = match doc.get("protocol") {
            Some(v) => Some(decode::enum_id(v)?),
            None => None,
        };
        let authorization = match doc.get("authorization") {
            Some(v) => Some(decode::bool(v)?),
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
        let authorization = authorization.or(Some(false));

        // Required presence, for every member, before any assertion runs.
        let host = host.ok_or_else(|| Error::invalid_object("missing required member Host"))?;
        let port = port.ok_or_else(|| Error::invalid_object("missing required member Port"))?;
        let protocol =
            protocol.ok_or_else(|| Error::invalid_object("missing required member Protocol"))?;

        // Assertions, in descriptor order, after every required-presence check.
        // Python runs them in exactly this position: a body missing a required
        // member reports that, not an assertion failure on a member that is
        // present.
        validators::check_endpoint_host_string(&host)?;
        validators::check_endpoint_port(port)?;
        validators::check_endpoint_protocol(&protocol)?;

        Ok(Self {
            host,
            port,
            protocol,
            authorization,
        })
    }
}
