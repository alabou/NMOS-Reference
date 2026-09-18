// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Subscriptions, and what each one should see for a given change.
//!
//! # The four event shapes are decided, not declared
//!
//! `Behaviour - Querying.md:85-210` defines the four grain shapes by which of
//! `pre` and `post` are present. A subscription may also carry filters, and
//! then the question is not "what happened to the resource" but "what happened
//! to *this subscription's view* of it" -- which is a different question with
//! the same four answers:
//!
//! | `pre` matches | `post` matches | the subscriber sees |
//! |---|---|---|
//! | yes | yes | modified |
//! | no | yes | **added** |
//! | yes | no | **removed** |
//! | no | no | nothing |
//!
//! The two emphasised rows are why this is a table rather than a pass-through.
//! A resource that merely stopped matching a filter is reported as *removed*,
//! and one that started matching as *added* -- `:242-245`. No extra state is
//! needed to detect a transition: "stopped matching" already *is* the removed
//! row.

use nmos_registry_core::body::Body;
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::event::ResourceEvent;
use nmos_registry_core::query_filter::matches;
use nmos_registry_core::resource::ResourceId;
use nmos_registry_core::resource_type::ResourceType;

/// One entry in `/subscriptions`.
///
/// Fields mirror `queryapi-subscription-response.json`. `resource_type` is
/// derived from `resource_path` at construction so that every event does not
/// have to re-parse it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Subscription {
    /// The subscription's id.
    pub id: String,
    /// The WebSocket URL a client connects to.
    pub ws_href: String,
    /// The Query API path this subscribes to, e.g. `/senders`.
    pub resource_path: String,
    /// The type `resource_path` names.
    pub resource_type: ResourceType,
    /// The basic-query filters, as `(path, expected)` pairs.
    ///
    /// `:214` -- "Query parameters are specified in a params attribute rather
    /// than the query string", but they mean the same thing, so the same
    /// matcher serves both.
    pub params: Vec<(String, String)>,
    /// The client's requested grain rate limit.
    pub max_update_rate_ms: u32,
    /// Whether the subscription outlives its last connection.
    pub persist: bool,
    /// Whether `ws_href` is `wss`.
    pub secure: bool,
    /// Whether the Query API requires authorization.
    pub authorization: bool,
    /// When it was created.
    pub created: TaiCursor,
    /// The `Host` header it was created for.
    ///
    /// Part of the match key, because `ws_href` is host-derived: two clients
    /// reaching the registry by different names must not be handed each
    /// other's WebSocket URL.
    pub host: String,
}

impl Subscription {
    /// Whether a resource representation satisfies this subscription.
    ///
    /// `None` -- an absent side of an event -- never matches, which is what
    /// makes the table above fall out of two calls.
    #[must_use]
    pub fn matches(&self, body: Option<&Body>) -> bool {
        body.is_some_and(|body| matches(body.data(), &self.params))
    }

    /// Render as `queryapi-subscription-response.json`.
    ///
    /// Written by hand rather than derived, for two reasons that are both about
    /// bytes. Member order is the Python dict literal's order, not alphabetical
    /// and not the struct's -- `params` is last and `id` is first. And the
    /// separators are `dump_any`'s, inherited from `json.dumps`: `", "` and
    /// `": "`, where `serde` would emit neither.
    ///
    /// `params` keeps the order the creating client sent, which is why it is a
    /// `Vec` rather than a map. Two clients asking for the same filters in
    /// different orders get the same subscription (see
    /// `SubscriptionRequest::matches`), and the one that created it decides how
    /// the response spells them.
    #[must_use]
    pub fn to_json(&self) -> String {
        let quote = |text: &str| {
            nmos_json::engine::dump_any(&text).unwrap_or_else(|_| format!("\"{text}\""))
        };
        let params = self
            .params
            .iter()
            .map(|(key, value)| format!("{}: {}", quote(key), quote(value)))
            .collect::<Vec<_>>()
            .join(", ");
        format!(
            "{{\"id\": {}, \"ws_href\": {}, \"max_update_rate_ms\": {}, \
             \"persist\": {}, \"secure\": {}, \"authorization\": {}, \
             \"resource_path\": {}, \"params\": {{{params}}}}}",
            quote(&self.id),
            quote(&self.ws_href),
            self.max_update_rate_ms,
            self.persist,
            self.secure,
            self.authorization,
            quote(&self.resource_path),
        )
    }

    /// Whether this subscription filters at all.
    ///
    /// An unfiltered subscription never needs a parsed body, which is what
    /// keeps the common case free under [`Body`]'s lazy parse.
    #[must_use]
    pub fn is_unfiltered(&self) -> bool {
        self.params.is_empty()
    }
}

/// One coalesced change awaiting delivery to a connection.
///
/// Coalescing keeps the **first** `pre` and the **latest** `post` for a given
/// resource, so a client rate-limited to one grain per window sees the net
/// change over that window rather than a replay of every intermediate state.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PendingEvent {
    /// The resource id, which is the grain entry's `path`.
    pub path: ResourceId,
    /// The state before the first change in this window.
    pub pre: Option<Body>,
    /// The state after the last change in this window.
    pub post: Option<Body>,
}

impl PendingEvent {
    /// Fold a newer change for the same resource into this one.
    ///
    /// `pre` is the state before the **first** change in the window, so it is
    /// never overwritten; `post` is the state after the **last**.
    pub fn merge(&mut self, newer: Self) {
        self.post = newer.post;
    }
}

/// What one subscription should see for one change.
///
/// Returns `None` when the subscription's view did not change -- the fourth row
/// of the table, where the resource matched neither before nor after.
#[must_use]
pub fn classify(subscription: &Subscription, event: &ResourceEvent) -> Option<PendingEvent> {
    // `Body::data` parses on first use, and an unfiltered subscription never
    // gets here, so it never pays for a parse at all.
    if subscription.is_unfiltered() {
        // Unfiltered: the subscription's view is the resource, so the event
        // passes through with its own shape.
        return match (&event.pre, &event.post) {
            (None, None) => None,
            (pre, post) => Some(PendingEvent {
                path: event.resource_id.clone(),
                pre: pre.clone(),
                post: post.clone(),
            }),
        };
    }

    let pre_matches = subscription.matches(event.pre.as_ref());
    let post_matches = subscription.matches(event.post.as_ref());

    match (pre_matches, post_matches) {
        (true, true) => Some(PendingEvent {
            path: event.resource_id.clone(),
            pre: event.pre.clone(),
            post: event.post.clone(),
        }),
        // A genuine add, or a resource that has just begun to match. Both are
        // a Resource Added Event.
        (false, true) => Some(PendingEvent {
            path: event.resource_id.clone(),
            pre: None,
            post: event.post.clone(),
        }),
        // A genuine delete, or a resource that has stopped matching. Both are
        // a Resource Removed Event.
        (true, false) => Some(PendingEvent {
            path: event.resource_id.clone(),
            pre: event.pre.clone(),
            post: None,
        }),
        (false, false) => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn subscription(params: &[(&str, &str)]) -> Subscription {
        Subscription {
            id: "sub-1".to_owned(),
            ws_href: "ws://example.test/x-nmos/query/v1.3/ws/?uid=sub-1".to_owned(),
            resource_path: "/senders".to_owned(),
            resource_type: ResourceType::Sender,
            params: params
                .iter()
                .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
                .collect(),
            max_update_rate_ms: 100,
            persist: true,
            secure: false,
            authorization: false,
            created: TaiCursor::new(100, 0),
            host: "example.test".to_owned(),
        }
    }

    fn body(label: &str) -> Body {
        Body::from_value(json!({"id": "s1", "label": label}))
    }

    fn event(pre: Option<Body>, post: Option<Body>) -> ResourceEvent {
        use nmos_registry_core::event::EventKind;
        ResourceEvent {
            kind: match (&pre, &post) {
                (None, Some(_)) => EventKind::Added,
                (Some(_), None) => EventKind::Removed,
                _ => EventKind::Modified,
            },
            resource_type: ResourceType::Sender,
            resource_id: "s1".to_owned(),
            pre,
            post,
        }
    }

    #[test]
    fn to_json_matches_the_bytes_python_renders() {
        // Captured from `JsonEngine.dump_any`, not composed here. Member order
        // is the dict literal's, and the separators are `", "` / `": "`.
        let mut sub = subscription(&[("label", "a"), ("format", "b")]);
        sub.id = "s-1".to_owned();
        sub.ws_href = "ws://h:1/x".to_owned();
        sub.resource_path = "/senders".to_owned();
        assert_eq!(
            sub.to_json(),
            r#"{"id": "s-1", "ws_href": "ws://h:1/x", "max_update_rate_ms": 100, "persist": true, "secure": false, "authorization": false, "resource_path": "/senders", "params": {"label": "a", "format": "b"}}"#,
        );
    }

    #[test]
    fn to_json_renders_an_empty_filter_set_as_an_empty_object() {
        let mut sub = subscription(&[]);
        sub.id = "s-1".to_owned();
        sub.ws_href = "ws://h:1/x".to_owned();
        sub.resource_path = "/senders".to_owned();
        assert!(
            sub.to_json().ends_with(r#""params": {}}"#),
            "{}",
            sub.to_json()
        );
    }

    #[test]
    fn to_json_keeps_the_filter_order_the_creating_client_sent() {
        let mut sub = subscription(&[("zulu", "1"), ("alpha", "2")]);
        sub.id = "s".to_owned();
        let json = sub.to_json();
        let zulu = json.find("zulu").expect("present");
        let alpha = json.find("alpha").expect("present");
        assert!(zulu < alpha, "the filters were reordered: {json}");
    }

    #[test]
    fn to_json_escapes_what_a_client_can_put_in_a_filter() {
        let mut sub = subscription(&[("label", r#"a "quoted" value"#)]);
        sub.id = "s".to_owned();
        let json = sub.to_json();
        assert!(json.contains(r#"\"quoted\""#), "{json}");
        let parsed: serde_json::Value = serde_json::from_str(&json).expect("valid JSON");
        assert_eq!(parsed["params"]["label"], r#"a "quoted" value"#);
    }

    #[test]
    fn an_unfiltered_subscription_sees_the_event_unchanged() {
        let sub = subscription(&[]);

        let added = classify(&sub, &event(None, Some(body("a")))).expect("an add is seen");
        assert!(added.pre.is_none() && added.post.is_some());

        let removed = classify(&sub, &event(Some(body("a")), None)).expect("a remove is seen");
        assert!(removed.pre.is_some() && removed.post.is_none());

        let modified =
            classify(&sub, &event(Some(body("a")), Some(body("b")))).expect("a change is seen");
        assert!(modified.pre.is_some() && modified.post.is_some());
    }

    #[test]
    fn an_unfiltered_subscription_never_parses_a_body() {
        // The laziness that makes the common case free. A filter would force a
        // parse; the absence of one must not.
        let sub = subscription(&[]);
        let pre = body("a");
        let post = body("b");
        // `from_value` arrives parsed, so build unparsed ones for this.
        let pre = Body::new(pre.text());
        let post = Body::new(post.text());
        assert!(!pre.is_parsed() && !post.is_parsed());

        let _ = classify(&sub, &event(Some(pre.clone()), Some(post.clone())));
        assert!(
            !pre.is_parsed() && !post.is_parsed(),
            "an unfiltered subscription parsed a body",
        );
    }

    #[test]
    fn a_resource_that_starts_matching_is_reported_as_added() {
        // `:242-245`. Not a modification -- the subscriber has never seen it,
        // so a `pre` would describe a state it has no record of.
        let sub = subscription(&[("label", "wanted")]);
        let pending = classify(&sub, &event(Some(body("other")), Some(body("wanted"))))
            .expect("the transition is visible");

        assert!(
            pending.pre.is_none(),
            "a resource that began matching was reported with a pre",
        );
        assert!(pending.post.is_some());
    }

    #[test]
    fn a_resource_that_stops_matching_is_reported_as_removed() {
        // The mirror, and the one that would otherwise leave a subscriber
        // believing a resource it can no longer see is still there.
        let sub = subscription(&[("label", "wanted")]);
        let pending = classify(&sub, &event(Some(body("wanted")), Some(body("other"))))
            .expect("the transition is visible");

        assert!(pending.pre.is_some());
        assert!(
            pending.post.is_none(),
            "a resource that stopped matching was reported with a post",
        );
    }

    #[test]
    fn a_change_between_two_non_matching_states_is_invisible() {
        let sub = subscription(&[("label", "wanted")]);
        assert!(classify(&sub, &event(Some(body("a")), Some(body("b")))).is_none());
        assert!(classify(&sub, &event(None, Some(body("a")))).is_none());
        assert!(classify(&sub, &event(Some(body("a")), None)).is_none());
    }

    #[test]
    fn a_change_between_two_matching_states_is_a_modification() {
        let sub = subscription(&[("label", "wanted")]);
        let pending = classify(&sub, &event(Some(body("wanted")), Some(body("wanted"))))
            .expect("both sides match");
        assert!(pending.pre.is_some() && pending.post.is_some());
    }

    #[test]
    fn coalescing_keeps_the_first_pre_and_the_latest_post() {
        // What a rate-limited client is owed: the net change over the window,
        // not a replay of every intermediate state.
        let mut pending = PendingEvent {
            path: "s1".to_owned(),
            pre: Some(body("first")),
            post: Some(body("second")),
        };
        pending.merge(PendingEvent {
            path: "s1".to_owned(),
            pre: Some(body("second")),
            post: Some(body("third")),
        });

        assert_eq!(
            pending.pre.as_ref().map(Body::text),
            Some(body("first").text()),
            "the pre moved off the first state in the window",
        );
        assert_eq!(
            pending.post.as_ref().map(Body::text),
            Some(body("third").text()),
            "the post did not follow the last state in the window",
        );
    }

    #[test]
    fn coalescing_an_add_then_a_delete_leaves_both_sides_of_the_window() {
        // Deliberately NOT collapsed to nothing. The subscriber was told the
        // resource existed, so it is owed the removal -- and a client that
        // connected mid-window has its own sync burst rather than this.
        let mut pending = PendingEvent {
            path: "s1".to_owned(),
            pre: None,
            post: Some(body("a")),
        };
        pending.merge(PendingEvent {
            path: "s1".to_owned(),
            pre: Some(body("a")),
            post: None,
        });

        assert!(pending.pre.is_none(), "the window's opening state changed");
        assert!(
            pending.post.is_none(),
            "the window's closing state is not the delete"
        );
    }
}
