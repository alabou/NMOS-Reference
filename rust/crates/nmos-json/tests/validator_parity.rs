// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Differential test: the Rust validators must agree with the Python ones.
//!
//! `validator_cases.json` is not written by hand. It records what
//! `nmos/validators.py` *actually did* for each case, produced by
//! `nmos/codegen/tests/_validator_corpus.py`. Asserting against it is therefore
//! asserting against the specification rather than against a second opinion.
//!
//! Three levels are checked, which is the same ladder the full parity harness
//! will use:
//!
//! * **verdict** -- accepted or rejected. Non-negotiable.
//! * **kind** -- the Python exception class. Two implementations rejecting the
//!   same body for different stated reasons have not really agreed.
//! * **message** -- byte-identical, because
//!   `handlers_registration.py:158` puts it in the HTTP 400 body a Node reads.
//!
//! Regenerate the corpus after any change to `nmos/validators.py`:
//!
//! ```text
//! python -m nmos.codegen.tests._validator_corpus
//! ```

// An integration test is a separate crate, so the crate-level exemption in
// lib.rs does not reach it.
#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use std::collections::BTreeSet;

use nmos_json::enums::EnumId;
use nmos_json::error::Result;
use nmos_json::validators as v;
use serde_json::Value;

#[derive(Debug, serde::Deserialize)]
struct Case {
    validator: String,
    label: String,
    ok: bool,
    #[serde(default)]
    kind: Option<String>,
    #[serde(default)]
    message: Option<String>,
    input: Value,
}

fn strs(value: &Value) -> Vec<&str> {
    value.as_array().map_or_else(Vec::new, |items| {
        items.iter().filter_map(Value::as_str).collect()
    })
}

fn keys(value: &Value) -> Vec<&str> {
    value
        .as_object()
        .map_or_else(Vec::new, |map| map.keys().map(String::as_str).collect())
}

fn len_of(value: &Value) -> usize {
    match value {
        Value::Array(items) => items.len(),
        Value::Object(map) => map.len(),
        _ => 0,
    }
}

/// Run the Rust validator that corresponds to one recorded case.
///
/// `None` means this crate has no counterpart to dispatch -- which must never
/// happen for a case in the corpus, and the test below fails loudly if it does
/// rather than skipping quietly.
#[allow(clippy::too_many_lines)]
fn dispatch(case: &Case) -> Option<Result<()>> {
    let s = || case.input.as_str().unwrap_or_default();
    let opt_s = || case.input.as_str();
    let i = || case.input.as_i64().unwrap_or_default();
    let e = || EnumId::new(case.input.as_str().unwrap_or_default());

    let result = match case.validator.as_str() {
        "CheckResourceIdString" => v::check_resource_id_string(s()),
        "CheckArrayOfResourceIdString" => v::check_array_of_resource_id_string(strs(&case.input)),
        "CheckResourceIdNullableString" => v::check_resource_id_nullable_string(opt_s()),
        "CheckHealthString" => v::check_health_string(s()),
        "CheckClockNameString" => v::check_clock_name_string(s()),
        "CheckClockNameNullableString" => v::check_clock_name_nullable_string(opt_s()),
        "CheckClockGmidString" => v::check_clock_gmid_string(s()),
        "CheckDid" => v::check_did(s()),
        "CheckSdid" => v::check_sdid(s()),
        "CheckPortIdString" => v::check_port_id_string(s()),
        "CheckChassisIdNullableString" => v::check_chassis_id_nullable_string(opt_s()),
        "CheckEndpointHostString" => v::check_endpoint_host_string(s()),
        "CheckEndpointProtocol" => v::check_endpoint_protocol(&e()),
        "CheckEndpointPort" => v::check_endpoint_port(i()),
        "CheckTransport" => v::check_transport(&e()),
        "CheckFormat" => v::check_format(&e()),
        "CheckDeviceType" => v::check_device_type(&e()),
        "CheckServiceType" => v::check_service_type(&e()),
        "CheckColorspace" => v::check_colorspace(&e()),
        "CheckInterlaceMode" => v::check_interlace_mode(&e()),
        "CheckTransferCharacteristic" => v::check_transfer_characteristic(&e()),
        "CheckInputStatusState" => v::check_input_status_state(&e()),
        "CheckOutputStatusState" => v::check_output_status_state(&e()),
        "CheckSenderStatusState" => v::check_sender_status_state(&e()),
        "CheckReceiverStatusState" => v::check_receiver_status_state(&e()),
        "CheckPositiveInteger" => v::check_positive_integer(i()),
        "CheckPositiveUint16" => v::check_positive_uint16(i()),
        "CheckUint16" => v::check_uint16(i()),
        "CheckErrorCode" => v::check_error_code(i()),
        "CheckConstraintSetPreference" => v::check_constraint_set_preference(i()),
        "CheckNullInteger" => v::check_null_integer(&case.input),
        "CheckNullPositiveInteger" => v::check_null_positive_integer(&case.input),
        "CheckAutoBool" => v::check_auto_bool(&case.input),
        "CheckAutoPort" => v::check_auto_port(&case.input),
        "CheckNullPort" => v::check_null_port(&case.input),
        "CheckNullAutoPort" => v::check_null_auto_port(&case.input),
        "CheckActivationMode" => v::check_activation_mode(opt_s()),
        "CheckNodeApiVersions" => v::check_node_api_versions(strs(&case.input)),
        "CheckVideoMediaTypes" => v::check_video_media_types(strs(&case.input)),
        "CheckAudioMediaTypes" => v::check_audio_media_types(strs(&case.input)),
        "CheckDataMediaTypes" => v::check_data_media_types(strs(&case.input)),
        "CheckMuxMediaTypes" => v::check_mux_media_types(strs(&case.input)),
        "CheckDataEventTypes" => v::check_data_event_types(len_of(&case.input)),
        "CheckConstraintsLength" => v::check_constraints_length(len_of(&case.input)),
        "CheckTransportConstraintEnumLength" => {
            v::check_transport_constraint_enum_length(len_of(&case.input))
        }
        "CheckGenericObject" => {
            // The member holds `RawJson` now, so the corpus input is wrapped
            // the same way a decoded member would be.
            v::check_generic_object(
                &nmos_json::RawJson::from_value(&case.input).unwrap_or_default(),
            )
        }
        "CheckRtpTransportConstraints" => v::check_rtp_transport_constraints(keys(&case.input)),
        "CheckRtpTcpTransportConstraints" => {
            v::check_rtp_tcp_transport_constraints(keys(&case.input))
        }
        "CheckMqttTransportConstraints" => v::check_mqtt_transport_constraints(keys(&case.input)),
        "CheckWebSocketTransportConstraints" => {
            v::check_websocket_transport_constraints(keys(&case.input))
        }
        "CheckNdiTransportConstraints" => v::check_ndi_transport_constraints(keys(&case.input)),
        "CheckSrtTransportConstraints" => v::check_srt_transport_constraints(keys(&case.input)),
        "CheckUsbTransportConstraints" => v::check_usb_transport_constraints(keys(&case.input)),
        "CheckRtspTransportConstraints" => v::check_rtsp_transport_constraints(keys(&case.input)),
        "CheckUdpTransportConstraints" => v::check_udp_transport_constraints(keys(&case.input)),

        // The structured cases, whose elements have sub-members. The corpus
        // records the input as "<structured>", so they dispatch on the label.
        "CheckDidSdid" => match case.label.as_str() {
            "both_valid" => v::check_did_sdid([(Some("0x60"), Some("0x01"))]),
            "bad_did" => v::check_did_sdid([(Some("zz"), Some("0x01"))]),
            "bad_sdid" => v::check_did_sdid([(Some("0x60"), Some("zz"))]),
            "absent" => v::check_did_sdid([(None, None)]),
            _ => return None,
        },
        "CheckAudioChannels" => match case.label.as_str() {
            "known" => v::check_audio_channels([Some("L")]),
            "numbered" => v::check_audio_channels([Some("NSC001")]),
            "unknown" => v::check_audio_channels([Some("Zz")]),
            "empty" => v::check_audio_channels(Vec::<Option<&str>>::new()),
            _ => return None,
        },
        "CheckVideoComponents" => match case.label.as_str() {
            "known" => v::check_video_components([Some("Y")]),
            "unknown" => v::check_video_components([Some("Q")]),
            "empty" => v::check_video_components(Vec::<Option<&str>>::new()),
            _ => return None,
        },
        _ => return None,
    };
    Some(result)
}

fn load() -> Vec<Case> {
    let raw = include_str!("validator_cases.json");
    serde_json::from_str(raw).expect("the validator corpus is valid JSON")
}

#[test]
fn every_case_has_a_rust_counterpart() {
    // Guards the guard: a validator the dispatch table forgot would otherwise
    // make its cases silently vanish from the comparison below.
    let missing: BTreeSet<String> = load()
        .iter()
        .filter(|case| dispatch(case).is_none())
        .map(|case| format!("{}/{}", case.validator, case.label))
        .collect();
    assert!(missing.is_empty(), "no Rust counterpart for: {missing:?}");
}

#[test]
fn rust_and_python_agree_on_every_case() {
    let cases = load();
    assert!(
        cases.len() > 100,
        "corpus looks truncated: {} cases",
        cases.len()
    );

    let mut disagreements = Vec::new();
    for case in &cases {
        let Some(actual) = dispatch(case) else {
            continue;
        };

        match (case.ok, &actual) {
            (true, Ok(())) => {}
            (false, Err(error)) => {
                let want_kind = case.kind.as_deref().unwrap_or_default();
                let want_message = case.message.as_deref().unwrap_or_default();
                if error.kind().python_name() != want_kind {
                    disagreements.push(format!(
                        "{}/{}: kind {} != python {want_kind}",
                        case.validator,
                        case.label,
                        error.kind().python_name(),
                    ));
                } else if error.message() != want_message {
                    disagreements.push(format!(
                        "{}/{}: message {:?} != python {want_message:?}",
                        case.validator,
                        case.label,
                        error.message(),
                    ));
                }
            }
            (true, Err(error)) => disagreements.push(format!(
                "{}/{}: rust rejected ({}) but python accepted",
                case.validator,
                case.label,
                error.message(),
            )),
            (false, Ok(())) => disagreements.push(format!(
                "{}/{}: rust accepted but python rejected ({})",
                case.validator,
                case.label,
                case.message.as_deref().unwrap_or_default(),
            )),
        }
    }

    assert!(
        disagreements.is_empty(),
        "{} of {} cases disagree with Python:\n  {}",
        disagreements.len(),
        cases.len(),
        disagreements.join("\n  "),
    );
}
