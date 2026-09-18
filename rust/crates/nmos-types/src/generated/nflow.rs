//! Generated NMOS type: `NFlow`. DO NOT EDIT.
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

use crate::generated::nflow_audio_coded::NFlowAudioCoded;
use crate::generated::nflow_audio_raw::NFlowAudioRaw;
use crate::generated::nflow_data::NFlowData;
use crate::generated::nflow_data_json::NFlowDataJson;
use crate::generated::nflow_data_sdianc::NFlowDataSdianc;
use crate::generated::nflow_mux::NFlowMux;
use crate::generated::nflow_video_coded::NFlowVideoCoded;
use crate::generated::nflow_video_raw::NFlowVideoRaw;

/// `NFlow`: a discriminated union over 8 concrete types.
///
/// # Dispatch is ordered and committal
///
/// The variants are tried in the order the model declares them, and the FIRST
/// whose predicate matches decides the type -- its decode error is the answer,
/// even when a later variant would have decoded cleanly. Python behaves the
/// same way, and that is why this is not `#[serde(untagged)]` on the way in:
/// untagged picks the first variant that *deserialises*, not the first whose
/// *discriminator* matches, and it backtracks. A body whose `format` says
/// video but whose components are empty must report the empty components, not
/// fall through and be mistaken for something else.
///
/// Declaration order is load-bearing for a second reason: some predicates only
/// exclude (`notin`), so a variant declared later may match a superset of an
/// earlier one. The emitter never sorts these.
///
/// `Serialize` IS untagged, which is correct on the way out -- it writes the
/// inner value with no added tag, exactly as Python's type-switch encode does.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(untagged)]
// Variants differ in size because the concrete NMOS types do. Boxing them to
// even that out would change the public shape of every match arm, for a type
// that is decoded once per registration and then dropped.
#[allow(clippy::large_enum_variant)]
pub enum NFlow {
    /// `NFlowVideoRaw`.
    VideoRaw(NFlowVideoRaw),
    /// `NFlowVideoCoded`.
    VideoCoded(NFlowVideoCoded),
    /// `NFlowAudioRaw`.
    AudioRaw(NFlowAudioRaw),
    /// `NFlowAudioCoded`.
    AudioCoded(NFlowAudioCoded),
    /// `NFlowData`.
    Data(NFlowData),
    /// `NFlowDataSdianc`.
    DataSdianc(NFlowDataSdianc),
    /// `NFlowDataJson`.
    DataJson(NFlowDataJson),
    /// `NFlowMux`.
    Mux(NFlowMux),
}

/// Does this object discriminate as `NFlowVideoRaw`?
fn is_nflow_video_raw(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("format").and_then(Value::as_str) != Some("urn:x-nmos:format:video") {
        return false;
    }
    if doc.get("media_type").and_then(Value::as_str) != Some("video/raw") {
        return false;
    }
    true
}

/// Does this object discriminate as `NFlowVideoCoded`?
fn is_nflow_video_coded(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("format").and_then(Value::as_str) != Some("urn:x-nmos:format:video") {
        return false;
    }
    // Excluding, so the key must be PRESENT and different -- absence fails.
    if doc.get("media_type").and_then(Value::as_str) == Some("video/raw") {
        return false;
    }
    if !doc.contains_key("media_type") {
        return false;
    }
    true
}

/// Does this object discriminate as `NFlowAudioRaw`?
fn is_nflow_audio_raw(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("format").and_then(Value::as_str) != Some("urn:x-nmos:format:audio") {
        return false;
    }
    {
        const ALLOWED: &[&str] = &["audio/L8", "audio/L16", "audio/L20", "audio/L24"];
        match doc.get("media_type").and_then(Value::as_str) {
            Some(v) if ALLOWED.contains(&v) => {}
            _ => return false,
        }
    }
    true
}

/// Does this object discriminate as `NFlowAudioCoded`?
fn is_nflow_audio_coded(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("format").and_then(Value::as_str) != Some("urn:x-nmos:format:audio") {
        return false;
    }
    {
        const EXCLUDED: &[&str] = &["audio/L8", "audio/L16", "audio/L20", "audio/L24"];
        if let Some(v) = doc.get("media_type").and_then(Value::as_str)
            && EXCLUDED.contains(&v)
        {
            return false;
        }
        // Excluding, so absence fails as well as membership.
        if !doc.contains_key("media_type") {
            return false;
        }
    }
    true
}

/// Does this object discriminate as `NFlowData`?
fn is_nflow_data(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("format").and_then(Value::as_str) != Some("urn:x-nmos:format:data") {
        return false;
    }
    {
        const EXCLUDED: &[&str] = &["video/smpte291", "application/json"];
        if let Some(v) = doc.get("media_type").and_then(Value::as_str)
            && EXCLUDED.contains(&v)
        {
            return false;
        }
        // Excluding, so absence fails as well as membership.
        if !doc.contains_key("media_type") {
            return false;
        }
    }
    true
}

/// Does this object discriminate as `NFlowDataSdianc`?
fn is_nflow_data_sdianc(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("format").and_then(Value::as_str) != Some("urn:x-nmos:format:data") {
        return false;
    }
    if doc.get("media_type").and_then(Value::as_str) != Some("video/smpte291") {
        return false;
    }
    true
}

/// Does this object discriminate as `NFlowDataJson`?
fn is_nflow_data_json(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("format").and_then(Value::as_str) != Some("urn:x-nmos:format:data") {
        return false;
    }
    if doc.get("media_type").and_then(Value::as_str) != Some("application/json") {
        return false;
    }
    true
}

/// Does this object discriminate as `NFlowMux`?
fn is_nflow_mux(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("format").and_then(Value::as_str) != Some("urn:x-nmos:format:mux") {
        return false;
    }
    true
}

impl NFlow {
    /// Decode by discriminating on the object's contents.
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data(
                "expected JSON object for polymorphic NFlow",
            ));
        };

        if is_nflow_video_raw(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::VideoRaw(NFlowVideoRaw::decode(src)?));
        }
        if is_nflow_video_coded(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::VideoCoded(NFlowVideoCoded::decode(src)?));
        }
        if is_nflow_audio_raw(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::AudioRaw(NFlowAudioRaw::decode(src)?));
        }
        if is_nflow_audio_coded(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::AudioCoded(NFlowAudioCoded::decode(src)?));
        }
        if is_nflow_data(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::Data(NFlowData::decode(src)?));
        }
        if is_nflow_data_sdianc(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::DataSdianc(NFlowDataSdianc::decode(src)?));
        }
        if is_nflow_data_json(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::DataJson(NFlowDataJson::decode(src)?));
        }
        if is_nflow_mux(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::Mux(NFlowMux::decode(src)?));
        }
        Err(Error::invalid_data(
            "no matching type for polymorphic NFlow",
        ))
    }
}
