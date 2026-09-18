//! Generated NMOS type: `NNode`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

use crate::generated::narray_of_clock::NArrayOfClock;
use crate::generated::narray_of_node_interface::NArrayOfNodeInterface;
use crate::generated::narray_of_node_service::NArrayOfNodeService;
use crate::generated::nempty::NEmpty;
use crate::generated::nnode_api::NNodeApi;
use crate::generated::nresource_core::NResourceCore;
#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NNode`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NNode {
    /// `ResourceCore`, flattened into this type's own JSON scope.
    #[serde(flatten)]
    pub resource_core: NResourceCore,
    /// `href`. Required.
    #[serde(rename = "href")]
    pub href: String,
    /// `caps`. Required.
    #[serde(rename = "caps")]
    pub caps: NEmpty,
    /// `api`. Required.
    #[serde(rename = "api")]
    pub api: NNodeApi,
    /// `services`. Required.
    #[serde(rename = "services")]
    pub services: NArrayOfNodeService,
    /// `clocks`. Required.
    #[serde(rename = "clocks")]
    pub clocks: NArrayOfClock,
    /// `interfaces`. Required.
    #[serde(rename = "interfaces")]
    pub interfaces: NArrayOfNodeInterface,
}

impl NNode {
    /// Decode from a JSON value, in the order the descriptor declares.
    ///
    /// Members are read in declaration order regardless of how the document
    /// orders its keys, then required presence is checked for every member,
    /// then the assertions run. Two bodies differing only in key order must
    /// therefore produce the same error.
    #[allow(clippy::too_many_lines, unused_variables)]
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data("expected JSON object for NNode"));
        };

        // Embedded: decoded from the parent's own map, and its required-member
        // checks fire here, at this position in the member order.
        let resource_core = NResourceCore::decode(src)?;
        let href = match doc.get("href") {
            Some(v) => Some(decode::url(v)?),
            None => None,
        };
        let caps = match doc.get("caps") {
            Some(v) => Some(NEmpty::decode(v)?),
            None => None,
        };
        let api = match doc.get("api") {
            Some(v) => Some(NNodeApi::decode(v)?),
            None => None,
        };
        let services = match doc.get("services") {
            Some(v) => Some(NArrayOfNodeService::decode(v)?),
            None => None,
        };
        let clocks = match doc.get("clocks") {
            Some(v) => Some(NArrayOfClock::decode(v)?),
            None => None,
        };
        let interfaces = match doc.get("interfaces") {
            Some(v) => Some(NArrayOfNodeInterface::decode(v)?),
            None => None,
        };

        // Required presence, for every member, before any assertion runs.
        let href = href.ok_or_else(|| Error::invalid_object("missing required member Href"))?;
        let caps = caps.ok_or_else(|| Error::invalid_object("missing required member Caps"))?;
        let api = api.ok_or_else(|| Error::invalid_object("missing required member Api"))?;
        let services =
            services.ok_or_else(|| Error::invalid_object("missing required member Services"))?;
        let clocks =
            clocks.ok_or_else(|| Error::invalid_object("missing required member Clocks"))?;
        let interfaces = interfaces
            .ok_or_else(|| Error::invalid_object("missing required member Interfaces"))?;

        Ok(Self {
            resource_core,
            href,
            caps,
            api,
            services,
            clocks,
            interfaces,
        })
    }
}
