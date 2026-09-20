// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Both implementations derive the same cluster from the same configuration.
//!
//! `layout_cases.json` is recorded from `nmos/cluster/layout.py` by
//! `nmos/cluster/tests/_layout_corpus.py`.
//!
//! The token is why this matters more than most parity checks. It travels in
//! the transport handshake and a member whose token differs is refused, so a
//! Rust member joining a Python cluster needs the same SHA-256 over the same
//! material string -- not an equivalent identity, the same sixteen hex
//! characters. Everything else here (the canonical order, the derived names,
//! which member is local) feeds that digest, so a difference anywhere shows up
//! as a cluster that will not form.
//!
//! Regenerate with `python -m nmos.cluster.tests._layout_corpus`.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use nmos_cluster::{Derivation, MemberSpec, derive_cluster};
use serde_json::Value;

fn corpus() -> Value {
    serde_json::from_str(include_str!("layout_cases.json")).expect("the corpus is valid JSON")
}

fn specs(case: &Value) -> Vec<MemberSpec> {
    case["specs"]
        .as_array()
        .expect("specs")
        .iter()
        .map(|raw| MemberSpec {
            host: raw["host"].as_str().expect("a host").to_owned(),
            client_port: raw
                .get("client_port")
                .and_then(Value::as_u64)
                .unwrap_or(2381) as u16,
            peer_port: raw.get("peer_port").and_then(Value::as_u64).unwrap_or(2382) as u16,
            // `null` and absent both mean "derive it", which is not the same as
            // an empty string -- an explicit empty name is a refusal, and one
            // of the corpus cases is exactly that.
            name: raw
                .get("name")
                .filter(|v| !v.is_null())
                .map(|v| v.as_str().expect("a name").to_owned()),
            bind_address: raw
                .get("bind_address")
                .filter(|v| !v.is_null())
                .map(|v| v.as_str().expect("an address").to_owned()),
        })
        .collect()
}

#[test]
fn every_configuration_derives_or_refuses_the_same_way() {
    let corpus = corpus();
    let cases = corpus["cases"].as_array().expect("cases");
    assert!(!cases.is_empty(), "the corpus is empty");

    for case in cases {
        let name = case["name"].as_str().expect("a case name");
        let local_host = case["local_host"].as_str().expect("a local host");
        let derivation = Derivation {
            local_host,
            local_peer_port: case["local_peer_port"].as_u64().map(|p| p as u16),
            namespace: case["namespace"].as_str().expect("a namespace"),
            tls: case["tls"].as_bool().unwrap_or(true),
            flavour: case["flavour"].as_str().unwrap_or(""),
        };

        let derived = derive_cluster(&specs(case), &derivation);

        match case["refused"].as_str() {
            Some(expected) => {
                let error = derived.as_ref().err().unwrap_or_else(|| {
                    panic!("{name}: the Python refuses this and the Rust accepted it")
                });
                assert_eq!(
                    error.0, expected,
                    "{name}: both refuse it, in different words",
                );
            }
            None => {
                let layout = derived.unwrap_or_else(|e| {
                    panic!("{name}: the Python derives this and the Rust refused it: {e}")
                });

                assert_eq!(
                    layout.token,
                    case["token"].as_str().expect("a token"),
                    "{name}: the tokens differ, so these two members would \
                     refuse each other's handshake",
                );
                assert_eq!(
                    layout.local.name,
                    case["local"].as_str().expect("a local name"),
                    "{name}: a different member is local",
                );

                let members: Vec<String> = layout.members.iter().map(|m| m.name.clone()).collect();
                let expected: Vec<String> = case["members"]
                    .as_array()
                    .expect("members")
                    .iter()
                    .map(|m| m["name"].as_str().expect("a name").to_owned())
                    .collect();
                assert_eq!(
                    members, expected,
                    "{name}: the canonical order or the derived names differ",
                );

                for (got, want) in layout
                    .members
                    .iter()
                    .zip(case["members"].as_array().expect("members"))
                {
                    assert_eq!(got.host, want["host"].as_str().expect("host"), "{name}");
                    assert_eq!(
                        u64::from(got.client_port),
                        want["client_port"].as_u64().expect("client"),
                        "{name}",
                    );
                    assert_eq!(
                        u64::from(got.peer_port),
                        want["peer_port"].as_u64().expect("peer"),
                        "{name}",
                    );
                    assert_eq!(
                        got.bind_address,
                        want["bind_address"].as_str().expect("bind"),
                        "{name}: the bind address differs, so this member \
                         listens somewhere its peers do not expect",
                    );
                }

                assert_eq!(
                    layout.quorum() as u64,
                    case["quorum"].as_u64().expect("quorum"),
                    "{name}",
                );
                assert_eq!(
                    layout.failures_tolerated() as u64,
                    case["failures_tolerated"].as_u64().expect("tolerated"),
                    "{name}",
                );
                assert_eq!(
                    layout.initial_cluster(None),
                    case["initial_cluster"].as_str().expect("initial"),
                    "{name}",
                );
                let endpoints: Vec<String> = case["client_endpoints"]
                    .as_array()
                    .expect("endpoints")
                    .iter()
                    .map(|v| v.as_str().expect("an endpoint").to_owned())
                    .collect();
                assert_eq!(layout.client_endpoints(), endpoints, "{name}");
            }
        }
    }
}

#[test]
fn the_corpus_holds_both_accepted_and_refused_configurations() {
    // A corpus of refusals alone would pass against an implementation that
    // refused everything, which would make the registry unstartable while
    // looking fully conformant.
    let corpus = corpus();
    let cases = corpus["cases"].as_array().expect("cases");
    let refused = cases.iter().filter(|c| !c["refused"].is_null()).count();
    assert!(refused >= 5, "only {refused} refusal cases");
    assert!(
        cases.len() - refused >= 5,
        "only {} accepted cases",
        cases.len() - refused,
    );
}

#[test]
fn the_flavour_separates_two_clusters_on_the_same_hosts() {
    // Without it, an etcd cluster and a consensus cluster deployed on the same
    // hosts under the same namespace derive the same token, and a member of
    // one presents credentials that look, to the other, like a peer it was
    // expecting.
    let corpus = corpus();
    let cases = corpus["cases"].as_array().expect("cases");
    let token_of = |name: &str| -> &str {
        cases
            .iter()
            .find(|c| c["name"] == name)
            .unwrap_or_else(|| panic!("the corpus has no {name:?} case"))["token"]
            .as_str()
            .expect("a token")
    };

    assert_ne!(
        token_of("three members, given out of order"),
        token_of("a raft flavoured token"),
        "the same hosts and namespace derive one token for both storage \
         layers, so the two clusters can mistake each other for peers",
    );
}

#[test]
fn the_bind_address_is_not_part_of_the_identity() {
    // Two members of one cluster may bind different local addresses -- one on
    // loopback, one on a routable interface -- and must still agree on the
    // token. If the bind address reached the digest, a rig that bound
    // 127.0.0.1 could not join a peer that did not.
    let corpus = corpus();
    let cases = corpus["cases"].as_array().expect("cases");
    let token_of = |name: &str| -> &str {
        cases
            .iter()
            .find(|c| c["name"] == name)
            .unwrap_or_else(|| panic!("the corpus has no {name:?} case"))["token"]
            .as_str()
            .expect("a token")
    };

    assert_eq!(
        token_of("three members, given out of order"),
        token_of("a bind address distinct from the advertised host"),
        "the bind address reached the token, so two members of one cluster \
         that listen on different local addresses refuse each other",
    );
}

#[test]
fn the_topology_constants_agree_with_the_python() {
    // Values neither implementation computes, so nothing else would catch a
    // drift between them. `DEFAULT_CERTIFICATE_NAME` is the one that matters
    // most: it is one string doing three jobs -- the gRPC target-name override
    // the etcd client verifies against, etcd's own allowed-hostname check, and
    // the raft transport's equivalent -- and a drifted copy leaves one side
    // accepting certificates the other rejects, silently, until a mixed
    // cluster meets one.
    //
    // It was a bare literal in `nmos-registry-bin/src/cli.rs` until the etcd
    // backend needed a second copy of it; lifting it here is what made this
    // assertion possible.
    let corpus = corpus();
    let constants = &corpus["constants"];

    assert_eq!(
        constants["default_certificate_name"].as_str(),
        Some(nmos_cluster::DEFAULT_CERTIFICATE_NAME),
    );
    assert_eq!(
        constants["default_client_port"].as_u64(),
        Some(u64::from(nmos_cluster::DEFAULT_CLIENT_PORT)),
    );
    assert_eq!(
        constants["default_peer_port"].as_u64(),
        Some(u64::from(nmos_cluster::DEFAULT_PEER_PORT)),
    );
    assert_eq!(
        constants["member_name_prefix"].as_str(),
        Some(nmos_cluster::MEMBER_NAME_PREFIX),
    );
    let sizes: Vec<u64> = constants["permitted_sizes"]
        .as_array()
        .expect("permitted_sizes")
        .iter()
        .map(|value| value.as_u64().expect("a size"))
        .collect();
    let mut ours: Vec<u64> = nmos_cluster::PERMITTED_SIZES
        .iter()
        .map(|size| *size as u64)
        .collect();
    ours.sort_unstable();
    assert_eq!(sizes, ours);
}
