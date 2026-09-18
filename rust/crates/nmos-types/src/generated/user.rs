//! Generated NMOS type: `User`. DO NOT EDIT.
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

/// `User`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct User {
    /// `fullname`. Required.
    #[serde(rename = "fullname")]
    pub full_name: String,
    /// `username`. Required.
    #[serde(rename = "username")]
    pub user_name: String,
    /// `password`. Required.
    #[serde(rename = "password")]
    pub password: String,
    /// `country`. Required.
    #[serde(rename = "country")]
    pub country: String,
    /// `email`. Required.
    #[serde(rename = "email")]
    pub email: String,
    /// `recovery`. Required.
    #[serde(rename = "recovery")]
    pub recovery: String,
    /// `administrator`. Required.
    #[serde(rename = "administrator")]
    pub administrator: bool,
    /// `key`. Required.
    #[serde(rename = "key")]
    pub key: String,
}

impl User {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for User"));
        };

        let full_name = match doc.get("fullname") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let user_name = match doc.get("username") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let password = match doc.get("password") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let country = match doc.get("country") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let email = match doc.get("email") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let recovery = match doc.get("recovery") {
            Some(v) => decode::string(v)?,
            None => None,
        };
        let administrator = match doc.get("administrator") {
            Some(v) => Some(decode::bool(v)?),
            None => None,
        };
        let key = match doc.get("key") {
            Some(v) => decode::string(v)?,
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let full_name =
            full_name.ok_or_else(|| Error::invalid_object("missing required member FullName"))?;
        let user_name =
            user_name.ok_or_else(|| Error::invalid_object("missing required member UserName"))?;
        let password =
            password.ok_or_else(|| Error::invalid_object("missing required member Password"))?;
        let country =
            country.ok_or_else(|| Error::invalid_object("missing required member Country"))?;
        let email = email.ok_or_else(|| Error::invalid_object("missing required member Email"))?;
        let recovery =
            recovery.ok_or_else(|| Error::invalid_object("missing required member Recovery"))?;
        let administrator = administrator
            .ok_or_else(|| Error::invalid_object("missing required member Administrator"))?;
        let key = key.ok_or_else(|| Error::invalid_object("missing required member Key"))?;

        Ok(Self {
            full_name,
            user_name,
            password,
            country,
            email,
            recovery,
            administrator,
            key,
        })
    }
}
