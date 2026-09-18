// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The `Check*` assertions, ported one-for-one from `nmos/validators.py`.
//!
//! These run *after* a member has decoded, and they are what decides whether a
//! value that is the right JSON type is also an acceptable NMOS value. Decode
//! answers "is this a string?"; these answer "is it a resource id?".
//!
//! # Scope: 67 of 98
//!
//! `nmos/validators.py` defines **98** `Check*` functions. Exactly **67** are
//! referenced by a descriptor's `assertion=`, and the other 31 are referenced
//! by nothing at all -- not by the descriptors, not by the two hand-written
//! modules inside the generated tree. Only the 67 are ported; the dead 31 are
//! reported rather than carried across.
//!
//! # Anchors
//!
//! Every pattern here ends at `\z`. The Python originals ended at `$`, which in
//! Python also matches immediately before a trailing newline -- so
//! `"3b8be755-...-c9151eb21193\n"` was a valid resource id, and the 201
//! `Location` header built from it carried the newline into a response header.
//! That was fixed on the Python side as part of this port rather than
//! reproduced here, so both implementations now reject it.
//!
//! Note the Python spelling is `len(pattern.findall(value)) != 1`, not a plain
//! match. For these anchored patterns the two agree: an anchored alternation can
//! only match at offset zero and consumes to the end, so there is exactly one
//! match or none. The three unanchored prefix patterns (`transport`,
//! `device_type`, `service_type`) are the same story for the same reason -- each
//! alternative is anchored with `^`, so a second match cannot start later.
//!
//! # Quirks kept deliberately
//!
//! * [`check_activation_mode`] silently accepts a value that is neither a
//!   string nor null -- the Python has no `else` branch.
//! * [`check_null_integer`] accepts `true`, because `bool` subclasses `int` in
//!   Python and the check is `isinstance(v, int)`.
//! * `CheckDidSdid`'s `hasattr(item.Did.value, 'nstring')` branch is dead --
//!   nothing in the tree has that attribute -- so only the live branch is here.

use std::sync::LazyLock;

use regex::Regex;

use crate::enums::EnumId;
use crate::error::{Error, Result};

// ---------------------------------------------------------------------------
// Patterns
// ---------------------------------------------------------------------------

macro_rules! pattern {
    ($name:ident, $re:expr) => {
        static $name: LazyLock<Regex> =
            LazyLock::new(|| Regex::new($re).expect("validator pattern is valid"));
    };
}

pattern!(
    RESOURCE_ID,
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\z"
);
pattern!(CLOCK_NAME, r"^clk[0-9]+\z");
pattern!(
    CLOCK_GMID,
    r"^[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}\z"
);
pattern!(
    TRANSPORT,
    r"^urn:x-nmos:transport:|^urn:x-[a-z]+:transport:"
);
pattern!(
    FORMAT,
    r"^urn:x-nmos:format:video\z|^urn:x-nmos:format:audio\z|^urn:x-nmos:format:data\z|^urn:x-nmos:format:mux\z"
);
pattern!(DEVICE_TYPE, r"^urn:x-nmos:device:|^urn:x-[a-z]+:device:");
pattern!(SERVICE_TYPE, r"^urn:x-");
pattern!(DID, r"^0x[0-9a-fA-F]{2}\z");
pattern!(SDID, r"^0x[0-9a-fA-F]{2}\z");
pattern!(CHASSIS_ID, r"^([0-9a-f]{2}-){5}([0-9a-f]{2})\z|^.+\z");
pattern!(PORT_ID, r"^([0-9a-f]{2}-){5}([0-9a-f]{2})\z");
pattern!(NODE_API_VERSION, r"^v[0-9]+\.[0-9]+\z");
pattern!(HEALTH, r"^[0-9]+\z");
pattern!(VIDEO_MEDIA_TYPE, r"^video/[^\s/]+\z");
pattern!(AUDIO_MEDIA_TYPE, r"^audio/[^\s/]+\z");
pattern!(DATA_MEDIA_TYPE, r"^[^\s/]+/[^\s/]+\z");
pattern!(MUX_MEDIA_TYPE, r"^[^\s/]+/[^\s/]+\z");
pattern!(
    AUDIO_CHANNEL_SYMBOL,
    r"^NSC(0[0-9][0-9]|1[0-1][0-9]|12[0-8])\z|^U(0[1-9]|[1-5][0-9]|6[0-4])\z"
);

fn matches(pattern: &Regex, value: &str, message: &str) -> Result<()> {
    if pattern.is_match(value) {
        Ok(())
    } else {
        Err(Error::invalid_object(message))
    }
}

// ---------------------------------------------------------------------------
// Identifiers and names
// ---------------------------------------------------------------------------

/// A canonical lowercase RFC-4122 resource id.
pub fn check_resource_id_string(value: &str) -> Result<()> {
    matches(&RESOURCE_ID, value, "invalid resource id")
}

/// Every element must be a resource id.
pub fn check_array_of_resource_id_string<'a>(
    values: impl IntoIterator<Item = &'a str>,
) -> Result<()> {
    for value in values {
        check_resource_id_string(value)?;
    }
    Ok(())
}

/// A resource id, or null. Null passes.
pub fn check_resource_id_nullable_string(value: Option<&str>) -> Result<()> {
    value.map_or(Ok(()), check_resource_id_string)
}

/// The heartbeat time, carried as a decimal string rather than a number.
pub fn check_health_string(value: &str) -> Result<()> {
    matches(&HEALTH, value, "invalid health value")
}

/// A clock name of the form `clk<n>`.
pub fn check_clock_name_string(value: &str) -> Result<()> {
    matches(&CLOCK_NAME, value, "invalid clock name")
}

/// A clock name, or null. Null passes.
pub fn check_clock_name_nullable_string(value: Option<&str>) -> Result<()> {
    value.map_or(Ok(()), check_clock_name_string)
}

/// A PTP grandmaster id: eight colon-free hex octets joined by dashes.
pub fn check_clock_gmid_string(value: &str) -> Result<()> {
    matches(&CLOCK_GMID, value, "invalid clock gmid")
}

/// An ancillary data identifier, `0xNN`.
pub fn check_did(value: &str) -> Result<()> {
    matches(&DID, value, "invalid DID value")
}

/// A secondary ancillary data identifier, `0xNN`.
pub fn check_sdid(value: &str) -> Result<()> {
    matches(&SDID, value, "invalid SDID value")
}

/// DID/SDID pairs, each part optional and checked only when present.
///
/// Takes the already-decoded pairs rather than a typed array, so this crate
/// does not need to know the generated element type.
pub fn check_did_sdid<'a>(
    items: impl IntoIterator<Item = (Option<&'a str>, Option<&'a str>)>,
) -> Result<()> {
    for (did, sdid) in items {
        if let Some(did) = did {
            check_did(did)?;
        }
        if let Some(sdid) = sdid {
            check_sdid(sdid)?;
        }
    }
    Ok(())
}

/// An LLDP port id. The message carries the offending value.
pub fn check_port_id_string(value: &str) -> Result<()> {
    if PORT_ID.is_match(value) {
        Ok(())
    } else {
        Err(Error::invalid_object(format!("invalid port id {value}")))
    }
}

/// An LLDP chassis id, or null. The pattern's second arm accepts any non-empty
/// single-line string, so this rejects only null-adjacent shapes and text
/// containing a newline.
pub fn check_chassis_id_nullable_string(value: Option<&str>) -> Result<()> {
    value.map_or(Ok(()), |v| matches(&CHASSIS_ID, v, "invalid chassis_id"))
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

/// An endpoint host: an IP literal, or something with a parseable hostname.
///
/// Python tries `ipaddress.ip_address` first, then parses `http://{v}/` and
/// rejects only when `urlparse` yields an **empty** hostname. That is a much
/// weaker test than it looks, and this reproduces it rather than improving on
/// it -- a stricter Rust would reject endpoints the registry currently stores.
///
/// Measured against Python, these all **pass**: `"example.com/x"` (the `/x`
/// becomes path), `"exa mple.com"` (a space is not excluded), `"a@b"` (host is
/// `b`), `"host:99"`, `"[::1]"`, `".."`, `"-"`, a 300-character label, and even
/// `"a\nb"` -- because `urlparse` strips `\t`, `\r` and `\n` before parsing, so
/// the hostname becomes `ab`.
///
/// Only four shapes fail, all of them leaving the authority empty: `""`, a
/// value starting with `/`, one starting with `:`, and a bare `@`.
///
/// The weakness is reported separately. It is not this port's to decide.
pub fn check_endpoint_host_string(value: &str) -> Result<()> {
    if value.parse::<std::net::IpAddr>().is_ok() {
        return Ok(());
    }
    if urlparse_hostname_is_empty(value) {
        return Err(Error::invalid_object("invalid endpoint host"));
    }
    Ok(())
}

/// Would `urlparse("http://{value}/").hostname` be empty?
///
/// Follows CPython's `urllib.parse` for the authority component: strip the
/// unsafe bytes it removes up front, cut at the first path/query/fragment
/// delimiter, drop any userinfo before the last `@`, then take the bracketed
/// IPv6 literal or the text before the first `:`.
fn urlparse_hostname_is_empty(value: &str) -> bool {
    // CPython removes these anywhere in the URL before parsing (bpo-43882).
    let cleaned: String = value
        .chars()
        .filter(|c| !matches!(c, '\t' | '\r' | '\n'))
        .collect();

    let netloc = cleaned.split(['/', '?', '#']).next().unwrap_or_default();

    let after_userinfo = netloc.rsplit_once('@').map_or(netloc, |(_, host)| host);

    let hostname = if let Some(rest) = after_userinfo.strip_prefix('[') {
        rest.split_once(']').map_or("", |(inside, _)| inside)
    } else {
        after_userinfo.split(':').next().unwrap_or_default()
    };

    hostname.is_empty()
}

/// `http` or `https`.
pub fn check_endpoint_protocol(value: &EnumId) -> Result<()> {
    one_of(value, &["http", "https"], "invalid endpoint protocol")
}

/// A port number, `0..=65535`.
pub fn check_endpoint_port(value: i64) -> Result<()> {
    in_range(value, 0, 65535, "invalid endpoint port number")
}

// ---------------------------------------------------------------------------
// URNs
// ---------------------------------------------------------------------------

/// A transport URN, in the NMOS namespace or a vendor one.
pub fn check_transport(value: &EnumId) -> Result<()> {
    matches(&TRANSPORT, value.as_str(), "invalid transport")
}

/// One of the four NMOS format URNs.
pub fn check_format(value: &EnumId) -> Result<()> {
    matches(&FORMAT, value.as_str(), "invalid format")
}

/// A device type URN.
pub fn check_device_type(value: &EnumId) -> Result<()> {
    matches(&DEVICE_TYPE, value.as_str(), "invalid device type")
}

/// A service type URN. The message carries the offending value.
pub fn check_service_type(value: &EnumId) -> Result<()> {
    if SERVICE_TYPE.is_match(value.as_str()) {
        Ok(())
    } else {
        Err(Error::invalid_object(format!(
            "invalid service type {value}"
        )))
    }
}

// ---------------------------------------------------------------------------
// Enumerations
// ---------------------------------------------------------------------------

fn one_of(value: &EnumId, allowed: &[&str], message: &str) -> Result<()> {
    if allowed.contains(&value.as_str()) {
        Ok(())
    } else {
        Err(Error::invalid_object(message))
    }
}

/// A video colorspace.
pub fn check_colorspace(value: &EnumId) -> Result<()> {
    one_of(
        value,
        &[
            "BT601",
            "BT709",
            "BT2020",
            "BT2100",
            "UNSPECIFIED",
            "ST2065-1",
            "ST2065-3",
            "XYZ",
            "ALPHA",
        ],
        "invalid colorspace",
    )
}

/// A video interlace mode.
pub fn check_interlace_mode(value: &EnumId) -> Result<()> {
    one_of(
        value,
        &[
            "progressive",
            "interlaced_tff",
            "interlaced_bff",
            "interlaced_psf",
        ],
        "invalid interlace_mode",
    )
}

/// A video transfer characteristic.
pub fn check_transfer_characteristic(value: &EnumId) -> Result<()> {
    one_of(
        value,
        &["SDR", "HLG", "PQ"],
        "invalid transfer_characteristic",
    )
}

/// An IS-11 input status.
pub fn check_input_status_state(value: &EnumId) -> Result<()> {
    one_of(
        value,
        &["no_signal", "awaiting_signal", "signal_present"],
        "invalid input status state",
    )
}

/// An IS-11 output status.
pub fn check_output_status_state(value: &EnumId) -> Result<()> {
    one_of(
        value,
        &["no_signal", "signal_present"],
        "invalid output status state",
    )
}

/// A BCP-008 sender status.
pub fn check_sender_status_state(value: &EnumId) -> Result<()> {
    one_of(
        value,
        &[
            "unconstrained",
            "constrained",
            "active_constraints_violation",
            "no_essence",
            "awaiting_essence",
        ],
        "invalid sender status state",
    )
}

/// A BCP-008 receiver status.
pub fn check_receiver_status_state(value: &EnumId) -> Result<()> {
    one_of(
        value,
        &["unknown", "compliant_stream", "non_compliant_stream"],
        "invalid receiver status state",
    )
}

/// An IS-05 activation mode, or null.
///
/// Faithfully permissive: Python checks membership only when the value is a
/// string and has no `else`, so anything that is neither a string nor null
/// passes unchecked. Reproduced rather than tightened, because tightening would
/// reject bodies the registry currently stores.
pub fn check_activation_mode(value: Option<&str>) -> Result<()> {
    let Some(value) = value else { return Ok(()) };
    if matches!(
        value,
        "activate_immediate" | "activate_scheduled_absolute" | "activate_scheduled_relative"
    ) {
        Ok(())
    } else {
        Err(Error::invalid_object("invalid activation mode"))
    }
}

// ---------------------------------------------------------------------------
// Numbers
// ---------------------------------------------------------------------------

fn in_range(value: i64, low: i64, high: i64, message: &str) -> Result<()> {
    if value < low || value > high {
        Err(Error::invalid_object(message))
    } else {
        Ok(())
    }
}

/// Strictly greater than zero.
pub fn check_positive_integer(value: i64) -> Result<()> {
    if value <= 0 {
        Err(Error::invalid_object("invalid positive integer"))
    } else {
        Ok(())
    }
}

/// `1..=65535`.
pub fn check_positive_uint16(value: i64) -> Result<()> {
    if value <= 0 || value > 65535 {
        Err(Error::invalid_object(
            "invalid positive 16 bit unsigned integer",
        ))
    } else {
        Ok(())
    }
}

/// `0..=65535`.
pub fn check_uint16(value: i64) -> Result<()> {
    in_range(value, 0, 65535, "invalid 16 bit unsigned integer")
}

/// An HTTP error status, `400..=599`.
pub fn check_error_code(value: i64) -> Result<()> {
    in_range(value, 400, 599, "invalid error code")
}

/// An IS-11 constraint set preference, `-100..=100`.
pub fn check_constraint_set_preference(value: i64) -> Result<()> {
    in_range(value, -100, 100, "invalid constraint set preference")
}

/// Whether this JSON value is an integer the way Python's `isinstance(v, int)`
/// decides it.
///
/// Two things follow from that being the actual test, and neither is obvious:
///
/// * `bool` subclasses `int` in Python, so `true` **is** an integer there and
///   these checks accept it;
/// * a JSON `1.0` parses to a `float`, which is **not** an `int`, so it is
///   rejected even though it is integral. `serde_json` reaches the same answer
///   from the other direction -- `1.0` is an `f64` and `is_i64()` is false.
fn is_python_int(value: &serde_json::Value) -> bool {
    match value {
        serde_json::Value::Bool(_) => true,
        serde_json::Value::Number(n) => n.is_i64() || n.is_u64(),
        _ => false,
    }
}

/// An integer, or null. Null passes; anything that is not an integer does not.
///
/// Takes the JSON value rather than an `Option<i64>`. That signature looked
/// natural and was wrong: it turned every non-integer into the same `None` a
/// JSON null produces, so a string or an object was accepted. Python separates
/// the two -- `None` returns early, anything else meets `isinstance` -- and the
/// validator corpus now drives both.
pub fn check_null_integer(value: &serde_json::Value) -> Result<()> {
    if value.is_null() || is_python_int(value) {
        Ok(())
    } else {
        Err(Error::invalid_object("invalid null integer"))
    }
}

/// A non-negative integer, or null. Null passes.
///
/// Takes the JSON value for the same reason as [`check_null_integer`].
pub fn check_null_positive_integer(value: &serde_json::Value) -> Result<()> {
    if value.is_null() {
        return Ok(());
    }
    if !is_python_int(value) {
        return Err(Error::invalid_object("invalid null integer"));
    }
    // `false` is 0 and `true` is 1 in Python, so a bool is never negative.
    if value.as_i64().is_some_and(|v| v < 0) {
        return Err(Error::invalid_object("invalid null integer"));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// "auto" unions
// ---------------------------------------------------------------------------

/// Either the literal `"auto"` or a boolean.
pub fn check_auto_bool(value: &serde_json::Value) -> Result<()> {
    match value {
        serde_json::Value::String(s) if s == "auto" => Ok(()),
        serde_json::Value::String(_) => Err(Error::invalid_object("invalid string value")),
        serde_json::Value::Bool(_) => Ok(()),
        _ => Err(Error::invalid_object("invalid type")),
    }
}

/// Either the literal `"auto"` or a port number.
pub fn check_auto_port(value: &serde_json::Value) -> Result<()> {
    match value {
        serde_json::Value::String(s) if s == "auto" => Ok(()),
        serde_json::Value::String(_) => Err(Error::invalid_object("invalid string value")),
        serde_json::Value::Number(n) if !n.is_f64() => port_number(n.as_i64()),
        _ => Err(Error::invalid_object("invalid type")),
    }
}

/// Either null or a port number.
pub fn check_null_port(value: &serde_json::Value) -> Result<()> {
    match value {
        serde_json::Value::Null => Ok(()),
        serde_json::Value::Number(n) if !n.is_f64() => port_number(n.as_i64()),
        _ => Err(Error::invalid_object("invalid type")),
    }
}

/// Null, the literal `"auto"`, or a port number.
pub fn check_null_auto_port(value: &serde_json::Value) -> Result<()> {
    match value {
        serde_json::Value::Null => Ok(()),
        _ => check_auto_port(value),
    }
}

fn port_number(value: Option<i64>) -> Result<()> {
    match value {
        Some(v) if (0..=65535).contains(&v) => Ok(()),
        Some(_) => Err(Error::invalid_object("invalid port value")),
        None => Err(Error::invalid_object("invalid port value")),
    }
}

// ---------------------------------------------------------------------------
// IS-12 message discriminators
// ---------------------------------------------------------------------------
//
// The NMOS Control protocol tags every message with a numeric type, and these
// assert the tag matches the struct it was decoded into. They are the only
// validators that compare against a single literal.

fn exactly(value: i64, expected: i64, message: &str) -> Result<()> {
    if value == expected {
        Ok(())
    } else {
        Err(Error::invalid_object(message))
    }
}

/// Command message: type 0.
pub fn check_command_message_type(value: i64) -> Result<()> {
    exactly(value, 0, "invalid command message type")
}

/// Command response: type 1.
pub fn check_command_response_message_type(value: i64) -> Result<()> {
    exactly(value, 1, "invalid command response message type")
}

/// Notification: type 2.
pub fn check_notification_message_type(value: i64) -> Result<()> {
    exactly(value, 2, "invalid notification message type")
}

/// Subscription: type 3.
pub fn check_subscription_message_type(value: i64) -> Result<()> {
    exactly(value, 3, "invalid subscription message type")
}

/// Subscription response: type 4.
pub fn check_subscription_response_message_type(value: i64) -> Result<()> {
    exactly(value, 4, "invalid subscription response message type")
}

/// Error message: type 5.
pub fn check_error_message_type(value: i64) -> Result<()> {
    exactly(value, 5, "invalid error message type")
}

/// Why a device reset: 0 Unknown, 1 PowerOn, 2 InternalError, 3 Upgrade,
/// 4 ControllerRequest, 5 ManualReset.
pub fn check_reset_cause(value: i64) -> Result<()> {
    one_of_int(value, &[0, 1, 2, 3, 4, 5], "invalid reset cause")
}

/// How a property changed: 0 Value, 1 ItemAdded, 2 ItemChanged, 3 ItemRemoved.
pub fn check_property_change_type(value: i64) -> Result<()> {
    one_of_int(value, &[0, 1, 2, 3], "invalid change property type")
}

/// Device state: 0 Unknown, 1 NormalOperation, 2 Initializing, 3 Updating,
/// 4 LicensingError, 5 InternalError.
pub fn check_device_generic_state(value: i64) -> Result<()> {
    one_of_int(value, &[0, 1, 2, 3, 4, 5], "invalid device generic state")
}

fn one_of_int(value: i64, allowed: &[i64], message: &str) -> Result<()> {
    if allowed.contains(&value) {
        Ok(())
    } else {
        Err(Error::invalid_object(message))
    }
}

// ---------------------------------------------------------------------------
// Collections
// ---------------------------------------------------------------------------

/// At least one Node API version, each `vMAJOR.MINOR`.
pub fn check_node_api_versions<'a>(values: impl IntoIterator<Item = &'a str>) -> Result<()> {
    let mut seen = false;
    for value in values {
        seen = true;
        matches(&NODE_API_VERSION, value, "invalid node api version")?;
    }
    if seen {
        Ok(())
    } else {
        Err(Error::invalid_object("invalid empty node api versions"))
    }
}

/// Video media types. The message carries the offending value; the other three
/// media-type checks below do not, which is Python's inconsistency, kept.
pub fn check_video_media_types<'a>(values: impl IntoIterator<Item = &'a str>) -> Result<()> {
    for value in values {
        if !VIDEO_MEDIA_TYPE.is_match(value) {
            return Err(Error::invalid_object(format!(
                "invalid video media type {value}"
            )));
        }
    }
    Ok(())
}

/// Audio media types.
pub fn check_audio_media_types<'a>(values: impl IntoIterator<Item = &'a str>) -> Result<()> {
    for value in values {
        matches(&AUDIO_MEDIA_TYPE, value, "invalid audio media type")?;
    }
    Ok(())
}

/// Data media types.
pub fn check_data_media_types<'a>(values: impl IntoIterator<Item = &'a str>) -> Result<()> {
    for value in values {
        matches(&DATA_MEDIA_TYPE, value, "invalid data media type")?;
    }
    Ok(())
}

/// Mux media types.
pub fn check_mux_media_types<'a>(values: impl IntoIterator<Item = &'a str>) -> Result<()> {
    for value in values {
        matches(&MUX_MEDIA_TYPE, value, "invalid mux media type")?;
    }
    Ok(())
}

/// At least one data event type. Contents are not checked.
pub fn check_data_event_types(len: usize) -> Result<()> {
    non_empty(len, "invalid empty data event types array")
}

/// At least one constraint in a constraint set.
pub fn check_constraints_length(len: usize) -> Result<()> {
    non_empty(len, "invalid empty constraint set")
}

/// At least one value in a transport constraint enum.
pub fn check_transport_constraint_enum_length(len: usize) -> Result<()> {
    non_empty(len, "invalid empty transport constraint enum")
}

fn non_empty(len: usize, message: &str) -> Result<()> {
    if len == 0 {
        Err(Error::invalid_object(message))
    } else {
        Ok(())
    }
}

/// Audio channel labels: non-empty, and every declared symbol recognised.
///
/// A symbol passes if it is one of the standard labels, or matches the
/// `NSCnnn` / `Unn` numbered forms.
pub fn check_audio_channels<'a>(symbols: impl IntoIterator<Item = Option<&'a str>>) -> Result<()> {
    const KNOWN: &[&str] = &[
        "L", "R", "C", "LFE", "Ls", "Rs", "Lss", "Rss", "Lrs", "Rrs", "Lc", "Rc", "Cs", "HI",
        "VIN", "M1", "M2", "Lt", "Rt", "Lst", "Rst", "S",
    ];
    let mut seen = false;
    for symbol in symbols {
        seen = true;
        if let Some(symbol) = symbol
            && !KNOWN.contains(&symbol)
            && !AUDIO_CHANNEL_SYMBOL.is_match(symbol)
        {
            return Err(Error::invalid_object("invalid audio channel symbol"));
        }
    }
    if seen {
        Ok(())
    } else {
        Err(Error::invalid_object(
            "invalid empty audio channels label array",
        ))
    }
}

/// Video components: non-empty, and every declared name recognised.
pub fn check_video_components<'a>(names: impl IntoIterator<Item = Option<&'a str>>) -> Result<()> {
    const KNOWN: &[&str] = &[
        "Y", "Cb", "Cr", "I", "Ct", "Cp", "A", "R", "G", "B", "DepthMap",
    ];
    let mut seen = false;
    for name in names {
        seen = true;
        if let Some(name) = name
            && !KNOWN.contains(&name)
        {
            return Err(Error::invalid_object("invalid video component name"));
        }
    }
    if seen {
        Ok(())
    } else {
        Err(Error::invalid_object(
            "invalid empty video components array",
        ))
    }
}

/// A generic object member must be present and be an object.
///
/// Takes the raw form rather than a parsed `Value`, because that is what an
/// `NGeneric` member now holds -- the byte-preserving `RawJson`, so a grain's
/// `pre`/`post` reach the wire unchanged. The check itself is unaffected:
/// Python asks `isinstance(v, dict)`, and `RawJson::is_object` answers the same
/// question by looking at the first non-whitespace byte.
pub fn check_generic_object(value: &crate::value::RawJson) -> Result<()> {
    if value.is_object() {
        Ok(())
    } else {
        Err(Error::invalid_object("invalid generic object"))
    }
}

// ---------------------------------------------------------------------------
// Transport constraints
// ---------------------------------------------------------------------------

const RTP_PROPERTIES: &[&str] = &[
    "destination_ip",
    "destination_port",
    "fec1D_destination_port",
    "fec1D_source_port",
    "fec2D_destination_port",
    "fec2D_source_port",
    "fec_block_height",
    "fec_block_width",
    "fec_destination_ip",
    "fec_enabled",
    "fec_mode",
    "fec_type",
    "interface_ip",
    "multicast_ip",
    "rtcp_destination_ip",
    "rtcp_destination_port",
    "rtcp_enabled",
    "rtcp_source_port",
    "rtp_enabled",
    "source_ip",
    "source_port",
];

const RTP_TCP_PROPERTIES: &[&str] = &[
    "interface_ip",
    "rtcp_enabled",
    "rtcp_source_port",
    "rtp_enabled",
    "source_ip",
    "source_port",
];

const MQTT_PROPERTIES: &[&str] = &[
    "broker_authorization",
    "broker_protocol",
    "broker_topic",
    "connection_status_broker_topic",
    "destination_host",
    "source_host",
];

const WEBSOCKET_PROPERTIES: &[&str] = &["connection_authorization", "connection_uri"];

const NDI_PROPERTIES: &[&str] = &[
    "interface_ip",
    "machine_name",
    "source_ip",
    "source_name",
    "source_port",
];

const SRT_PROPERTIES: &[&str] = &[
    "destination_ip",
    "destination_port",
    "latency",
    "protocol",
    "source_ip",
    "source_port",
    "stream_id",
];

const USB_PROPERTIES: &[&str] = &["interface_ip", "source_ip", "source_port"];

const RTSP_PROPERTIES: &[&str] = &["interface_ip", "source_ip", "source_port"];

const UDP_PROPERTIES: &[&str] = &[
    "destination_ip",
    "destination_port",
    "fec1D_destination_port",
    "fec1D_source_port",
    "fec2D_destination_port",
    "fec2D_source_port",
    "fec_block_height",
    "fec_block_width",
    "fec_destination_ip",
    "fec_enabled",
    "fec_mode",
    "fec_type",
    "interface_ip",
    "multicast_ip",
    "source_ip",
    "source_port",
];

/// Shared logic: an optional required key, then every key either known or an
/// extension.
///
/// The `ext_` escape is why the Python property sets also list their own
/// `ext_privacy_*` members explicitly -- redundant, since the prefix rule would
/// admit them anyway. The sets here omit that redundancy; behaviour is
/// identical because any `ext_`-prefixed key passes on the prefix rule.
fn transport_constraints<'a>(
    keys: impl IntoIterator<Item = &'a str>,
    allowed: &[&str],
    transport: &str,
    required: Option<&str>,
) -> Result<()> {
    let keys: Vec<&str> = keys.into_iter().collect();

    if let Some(required) = required
        && !keys.contains(&required)
    {
        return Err(Error::invalid_object(format!(
            "invalid {transport} transport constraints, missing required constraints"
        )));
    }

    for key in keys {
        if !allowed.contains(&key) && !key.starts_with("ext_") {
            return Err(Error::invalid_object(format!(
                "invalid {transport} transport constraints, invalid property"
            )));
        }
    }
    Ok(())
}

/// RTP transport constraints. `rtp_enabled` is required.
pub fn check_rtp_transport_constraints<'a>(keys: impl IntoIterator<Item = &'a str>) -> Result<()> {
    transport_constraints(keys, RTP_PROPERTIES, "RTP", Some("rtp_enabled"))
}

/// RTP-over-TCP transport constraints. `rtp_enabled` is required.
pub fn check_rtp_tcp_transport_constraints<'a>(
    keys: impl IntoIterator<Item = &'a str>,
) -> Result<()> {
    transport_constraints(keys, RTP_TCP_PROPERTIES, "RTP", Some("rtp_enabled"))
}

/// MQTT transport constraints.
pub fn check_mqtt_transport_constraints<'a>(keys: impl IntoIterator<Item = &'a str>) -> Result<()> {
    transport_constraints(keys, MQTT_PROPERTIES, "MQTT", None)
}

/// WebSocket transport constraints.
pub fn check_websocket_transport_constraints<'a>(
    keys: impl IntoIterator<Item = &'a str>,
) -> Result<()> {
    transport_constraints(keys, WEBSOCKET_PROPERTIES, "WebSocket", None)
}

/// NDI transport constraints.
pub fn check_ndi_transport_constraints<'a>(keys: impl IntoIterator<Item = &'a str>) -> Result<()> {
    transport_constraints(keys, NDI_PROPERTIES, "NDI", None)
}

/// SRT transport constraints.
pub fn check_srt_transport_constraints<'a>(keys: impl IntoIterator<Item = &'a str>) -> Result<()> {
    transport_constraints(keys, SRT_PROPERTIES, "SRT", None)
}

/// USB transport constraints.
pub fn check_usb_transport_constraints<'a>(keys: impl IntoIterator<Item = &'a str>) -> Result<()> {
    transport_constraints(keys, USB_PROPERTIES, "USB", None)
}

/// RTSP transport constraints.
pub fn check_rtsp_transport_constraints<'a>(keys: impl IntoIterator<Item = &'a str>) -> Result<()> {
    transport_constraints(keys, RTSP_PROPERTIES, "RTSP", None)
}

/// UDP transport constraints.
pub fn check_udp_transport_constraints<'a>(keys: impl IntoIterator<Item = &'a str>) -> Result<()> {
    transport_constraints(keys, UDP_PROPERTIES, "UDP", None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const UUID: &str = "3b8be755-08ff-452b-b217-c9151eb21193";

    #[test]
    fn resource_ids_reject_uppercase_and_trailing_newline() {
        assert!(check_resource_id_string(UUID).is_ok());
        assert!(check_resource_id_string(&UUID.to_uppercase()).is_err());
        assert!(check_resource_id_string(&format!("{UUID}\n")).is_err());
        assert_eq!(
            check_resource_id_string("nope").unwrap_err().message(),
            "invalid resource id"
        );
    }

    #[test]
    fn transport_urns_accept_vendor_namespaces() {
        assert!(check_transport(&EnumId::new("urn:x-nmos:transport:rtp")).is_ok());
        assert!(check_transport(&EnumId::new("urn:x-matrox:transport:srt")).is_ok());
        assert!(check_transport(&EnumId::new("urn:other:transport:x")).is_err());
    }

    #[test]
    fn messages_carrying_the_value_use_no_quotes() {
        assert_eq!(
            check_port_id_string("zz").unwrap_err().message(),
            "invalid port id zz"
        );
        assert_eq!(
            check_service_type(&EnumId::new("nope"))
                .unwrap_err()
                .message(),
            "invalid service type nope"
        );
    }

    #[test]
    fn transport_constraints_require_their_key_and_allow_extensions() {
        assert!(check_rtp_transport_constraints(["rtp_enabled", "source_ip"]).is_ok());
        // Any ext_ key passes on the prefix rule, listed or not.
        assert!(check_rtp_transport_constraints(["rtp_enabled", "ext_anything"]).is_ok());
        assert_eq!(
            check_rtp_transport_constraints(["source_ip"])
                .unwrap_err()
                .message(),
            "invalid RTP transport constraints, missing required constraints"
        );
        assert_eq!(
            check_rtp_transport_constraints(["rtp_enabled", "bogus"])
                .unwrap_err()
                .message(),
            "invalid RTP transport constraints, invalid property"
        );
        // RTP-over-TCP reports itself as "RTP" too -- Python passes the same name.
        assert_eq!(
            check_rtp_tcp_transport_constraints(["bogus"])
                .unwrap_err()
                .message(),
            "invalid RTP transport constraints, missing required constraints"
        );
    }

    #[test]
    fn auto_unions_separate_their_three_failure_messages() {
        assert!(check_auto_port(&json!("auto")).is_ok());
        assert!(check_auto_port(&json!(8080)).is_ok());
        assert_eq!(
            check_auto_port(&json!("nope")).unwrap_err().message(),
            "invalid string value"
        );
        assert_eq!(
            check_auto_port(&json!(70000)).unwrap_err().message(),
            "invalid port value"
        );
        assert_eq!(
            check_auto_port(&json!(null)).unwrap_err().message(),
            "invalid type"
        );
        assert!(check_null_auto_port(&json!(null)).is_ok());
    }

    #[test]
    fn empty_collections_have_their_own_messages() {
        assert_eq!(
            check_audio_channels(Vec::<Option<&str>>::new())
                .unwrap_err()
                .message(),
            "invalid empty audio channels label array"
        );
        assert!(check_audio_channels([Some("L"), Some("NSC001"), None]).is_ok());
        assert!(check_audio_channels([Some("Zz")]).is_err());
        assert_eq!(
            check_node_api_versions(Vec::<&str>::new())
                .unwrap_err()
                .message(),
            "invalid empty node api versions"
        );
        assert!(check_node_api_versions(["v1.3"]).is_ok());
    }

    #[test]
    fn activation_mode_is_permissive_by_design() {
        assert!(check_activation_mode(None).is_ok());
        assert!(check_activation_mode(Some("activate_immediate")).is_ok());
        assert!(check_activation_mode(Some("nope")).is_err());
    }
}
