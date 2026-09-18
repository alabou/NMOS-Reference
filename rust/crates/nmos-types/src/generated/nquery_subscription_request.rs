//! Generated NMOS type: `NQuerySubscriptionRequest`. DO NOT EDIT.
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

/// `NQuerySubscriptionRequest`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NQuerySubscriptionRequest {
    /// `max_update_rate_ms`. Required.
    #[serde(rename = "max_update_rate_ms")]
    pub max_update_rate_ms: i64,
    /// `persist`. Required.
    #[serde(rename = "persist")]
    pub persist: bool,
    /// `resource_path`. Required.
    #[serde(rename = "resource_path")]
    pub resource_path: String,
    /// `params`. Required.
    #[serde(rename = "params")]
    pub params: RawJson,
    /// `secure`. Optional, so absent means the member was not present.
    #[serde(rename = "secure", skip_serializing_if = "Option::is_none")]
    pub secure: Option<bool>,
    /// `authorization`. Optional, so absent means the member was not present.
    #[serde(rename = "authorization", skip_serializing_if = "Option::is_none")]
    pub authorization: Option<bool>,
}

impl NQuerySubscriptionRequest {
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
                "expected JSON object for NQuerySubscriptionRequest",
            ));
        };

        let max_update_rate_ms = match doc.get("max_update_rate_ms") {
            Some(v) => Some(decode::int(v)?),
            None => None,
        };
        let persist = match doc.get("persist") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let resource_path = match doc.get("resource_path") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let params = doc.get("params").map(decode::raw_json);
        let secure = match doc.get("secure") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let authorization = match doc.get("authorization") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let max_update_rate_ms = max_update_rate_ms
            .ok_or_else(|| Error::invalid_object("missing required member MaxUpdateRate_ms"))?;
        let persist =
            persist.ok_or_else(|| Error::invalid_object("missing required member Persist"))?;
        let resource_path = resource_path
            .ok_or_else(|| Error::invalid_object("missing required member ResourcePath"))?;
        let params =
            params.ok_or_else(|| Error::invalid_object("missing required member Params"))?;

        Ok(Self {
            max_update_rate_ms,
            persist,
            resource_path,
            params,
            secure,
            authorization,
        })
    }
}
