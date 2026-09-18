// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Every subscription, and every connection on it.
//!
//! Port of `SubscriptionManager` (`nmos/registry/subscriptions.py:225-398`).
//! The Python class needs no lock because asyncio gave it one implicitly; here
//! it owns a `parking_lot::RwLock` of its own, and the interesting part is what
//! that lock is allowed to touch.
//!
//! # Two locks, and the order between them
//!
//! There are exactly two locks in this crate: the registry's, and this one.
//! They nest in **one** direction -- manager first, registry second -- and only
//! [`SubscriptionManager::connect`] nests them at all. Everything else takes one
//! or the other, never both.
//!
//! `connect` holds the manager's write lock across the registry read that builds
//! the sync burst, and that is deliberate rather than careless. It is what buys
//! back Python's atomicity at `subscriptions.py:345-366`: the connection is
//! registered and its burst is in the buffer before any matcher can see the
//! connection, so a grain can never be queued ahead of the burst that is
//! supposed to precede it. The alternatives were measured against the protocol
//! and both lose:
//!
//! * register first, then burst -- a change above the anchor can be enqueued
//!   into an empty buffer and the burst then lands *behind* it, so the client's
//!   first grain reports a stale `post` for that resource;
//! * burst first, then register -- events committed in the gap are drained by
//!   the matcher while the buffer is invisible, and are simply lost.
//!
//! The cost is that concurrent connects serialise against each other and
//! against the matcher's lookup for as long as it takes to walk one resource
//! type. Connect is a once-per-socket operation, so this is the right trade at
//! this scale; if connect storms ever show up in the M7 profile, the escape
//! hatch is a per-subscription lock rather than a global one, and the ordering
//! rule above is what makes that a contained change.
//!
//! # What the manager does not do
//!
//! It does not classify and it does not build grains. It is the registry of who
//! is listening; [`crate::matcher`] is what feeds them.

use std::collections::HashMap;
use std::fmt;
use std::sync::Arc;

use parking_lot::RwLock;

use nmos_json::error::python_repr;
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource_type::ResourceType;

use crate::connection::ConnectionBuffer;
use crate::registry::Registry;
use crate::subscription::Subscription;

/// A subscription request the Query API must refuse.
///
/// Carries the message rather than a code, because the caller puts it straight
/// into the 400 body -- `handlers_query.py:345-346` is `error_response(400,
/// str(exc))`, which makes the text an observable part of the API exactly as
/// decode errors are. It is reproduced here character for character, down to
/// Python's `repr` quoting of the offending path.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SubscriptionError(String);

impl SubscriptionError {
    /// The message, as the 400 body will carry it.
    #[must_use]
    pub fn message(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for SubscriptionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for SubscriptionError {}

/// Map a subscription's `resource_path` to a resource type.
///
/// `QueryAPI.raml:432` covers a request that is "incorrectly formatted [or] an
/// attribute is invalid given the API's configuration", which is what an
/// unsubscribable path is.
fn resource_type_of(resource_path: &str) -> Result<ResourceType, SubscriptionError> {
    // `strip("/")` in Python removes leading *and* trailing slashes, and any
    // number of them -- `trim_matches` is the same operation, not `strip_prefix`.
    ResourceType::from_plural(resource_path.trim_matches('/')).ok_or_else(|| {
        let permitted = ResourceType::ALL
            .iter()
            .map(|rt| format!("/{}", rt.plural()))
            .collect::<Vec<_>>()
            .join(", ");
        SubscriptionError(format!(
            "resource_path {} is not subscribable; expected one of: {permitted}",
            python_repr(resource_path),
        ))
    })
}

/// What a client asked for when it POSTed to `/subscriptions`.
///
/// A struct rather than nine arguments, and every field is part of the match
/// key in [`SubscriptionManager::create_or_match`] -- which is the reason it is
/// one type: adding a field to the request without adding it to the key is the
/// way this goes wrong, and here the two are the same list.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SubscriptionRequest {
    /// The Query API path to subscribe to, e.g. `/senders`.
    pub resource_path: String,
    /// The basic-query filters, as `(path, expected)` pairs.
    pub params: Vec<(String, String)>,
    /// The client's requested grain rate limit.
    pub max_update_rate_ms: u32,
    /// Whether the subscription outlives its last connection.
    pub persist: bool,
    /// Whether the WebSocket URL should be `wss`.
    pub secure: bool,
    /// Whether the Query API requires authorization.
    pub authorization: bool,
    /// The `Host` header the request arrived with.
    pub host: String,
    /// The scheme for `ws_href` -- `ws` or `wss`.
    pub ws_scheme: String,
    /// The authority for `ws_href`.
    pub ws_host: String,
}

impl SubscriptionRequest {
    /// Whether an existing subscription satisfies this request.
    ///
    /// `:25` -- "the Query API MAY return an existing Subscription to the user,
    /// if it matches the requested attributes". Reuse is what stops a Controller
    /// that opens one subscription per resource kind from accumulating
    /// duplicates across reconnects.
    fn matches(&self, existing: &Subscription) -> bool {
        existing.resource_path == self.resource_path
            && existing.max_update_rate_ms == self.max_update_rate_ms
            && existing.persist == self.persist
            && existing.secure == self.secure
            && existing.authorization == self.authorization
            && existing.host == self.host
            && params_equal(&existing.params, &self.params)
    }
}

/// Whether two filter sets are the same set, ignoring order.
///
/// Python compares `dict == dict`, which is order-independent, while `params`
/// is a `Vec` here so that `to_json` can echo the order the client sent. A
/// client POSTing `{"label":"a","format":"b"}` and one POSTing
/// `{"format":"b","label":"a"}` ask for the same subscription and must be given
/// the same one -- comparing the vectors positionally would mint a second.
///
/// Linear, not sorted: JSON object keys are unique and filter sets are a handful
/// of entries, so the quadratic scan is cheaper than the allocation a sort
/// would need.
fn params_equal(left: &[(String, String)], right: &[(String, String)]) -> bool {
    left.len() == right.len()
        && left
            .iter()
            .all(|(key, value)| right.iter().any(|(k, v)| k == key && v == value))
}

/// Owns every subscription and every connection.
#[derive(Debug, Default)]
pub struct SubscriptionManager {
    state: RwLock<ManagerState>,
}

#[derive(Debug, Default)]
struct ManagerState {
    subscriptions: HashMap<String, Subscription>,
    connections: HashMap<String, Vec<Arc<ConnectionBuffer>>>,
}

impl SubscriptionManager {
    /// An empty manager.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    // -- introspection -----------------------------------------------------

    /// How many subscriptions exist.
    #[must_use]
    pub fn count(&self) -> usize {
        self.state.read().subscriptions.len()
    }

    /// Live grains -- one per connected WebSocket, as in nmos-cpp.
    #[must_use]
    pub fn grain_count(&self) -> usize {
        self.state
            .read()
            .connections
            .values()
            .map(Vec::len)
            .sum::<usize>()
    }

    /// One subscription by id.
    #[must_use]
    pub fn get(&self, subscription_id: &str) -> Option<Subscription> {
        self.state
            .read()
            .subscriptions
            .get(subscription_id)
            .cloned()
    }

    /// Every subscription, for `GET /subscriptions`.
    ///
    /// Sorted by id. A `HashMap` has no order, and two cluster members handed
    /// the same subscriptions would otherwise render the collection
    /// differently -- the same reason the store sorts a cascade.
    #[must_use]
    pub fn all(&self) -> Vec<Subscription> {
        let mut all: Vec<Subscription> =
            self.state.read().subscriptions.values().cloned().collect();
        all.sort_by(|left, right| left.id.cmp(&right.id));
        all
    }

    // -- lifecycle ---------------------------------------------------------

    /// Return an existing matching subscription, or create a new one.
    ///
    /// The `bool` is `created`, and selects 201 over 200.
    ///
    /// # Errors
    ///
    /// [`SubscriptionError`] when `resource_path` is not one of the six
    /// collections, which the caller answers 400.
    pub fn create_or_match(
        &self,
        request: &SubscriptionRequest,
    ) -> Result<(Subscription, bool), SubscriptionError> {
        let resource_type = resource_type_of(&request.resource_path)?;
        let mut state = self.state.write();

        // Sorted, so that when several existing subscriptions match the request
        // the same one is returned every time. Python iterates a dict in
        // insertion order and is deterministic for free; a `HashMap` is not,
        // and "which of the two equivalent subscriptions did you get" would
        // otherwise vary between runs of the same client.
        let mut candidates: Vec<&Subscription> = state
            .subscriptions
            .values()
            .filter(|existing| request.matches(existing))
            .collect();
        candidates.sort_by(|left, right| left.id.cmp(&right.id));
        if let Some(existing) = candidates.first() {
            return Ok(((*existing).clone(), false));
        }

        let id = uuid::Uuid::new_v4().to_string();
        let subscription = Subscription {
            ws_href: format!(
                "{}://{}/x-nmos/query/v1.3/subscriptions/{id}",
                request.ws_scheme, request.ws_host,
            ),
            id: id.clone(),
            resource_path: request.resource_path.clone(),
            resource_type,
            params: request.params.clone(),
            max_update_rate_ms: request.max_update_rate_ms,
            persist: request.persist,
            secure: request.secure,
            authorization: request.authorization,
            created: TaiCursor::now(),
            host: request.host.clone(),
        };
        state.subscriptions.insert(id.clone(), subscription.clone());
        state.connections.insert(id, Vec::new());
        drop(state);
        Ok((subscription, true))
    }

    /// Remove a subscription and forcibly close its clients.
    ///
    /// `Behaviour - Querying.md:19` -- "If an HTTP DELETE is issued prior to all
    /// WebSocket connections being closed, they SHOULD be forcibly closed by
    /// the server."
    ///
    /// Does not re-check `persist`. The caller rejects a non-persistent
    /// subscription with 403 first (`:18`), but reaping goes through this same
    /// path legitimately.
    pub fn delete(&self, subscription_id: &str) -> bool {
        // The buffers are closed **after** the guard drops. `close` takes the
        // buffer's own mutex, and taking it under the manager's write lock
        // would nest a third lock inside the two this module is allowed to
        // reason about.
        let (existed, closing) = {
            let mut state = self.state.write();
            let existed = state.subscriptions.remove(subscription_id).is_some();
            let closing = state
                .connections
                .remove(subscription_id)
                .unwrap_or_default();
            drop(state);
            (existed, closing)
        };
        for connection in &closing {
            connection.close();
        }
        existed
    }

    // -- connections -------------------------------------------------------

    /// Attach a WebSocket, queueing its synchronisation burst.
    ///
    /// `:166` -- the sync events carry identical `pre` and `post` and exist so
    /// "the client has received all data for a given topic".
    ///
    /// If nothing currently matches, nothing is queued: an empty grain would
    /// violate `queryapi-subscriptions-websocket.json`, whose `data` array has
    /// `minItems: 1`. The AMWA mock sends the empty grain anyway, and broadcasts
    /// it to every client rather than to the new one.
    ///
    /// Returns `None` if the subscription has been deleted, which a client can
    /// race by connecting to an id that was valid when it read it.
    pub fn connect(
        &self,
        registry: &Registry,
        subscription_id: &str,
    ) -> Option<Arc<ConnectionBuffer>> {
        // The whole point of the write lock here -- see the module docs. The
        // burst must be in the buffer before the buffer is reachable.
        let mut state = self.state.write();
        let subscription = state.subscriptions.get(subscription_id)?.clone();

        let (burst, anchor) = registry.connect(&subscription);
        let connection = Arc::new(ConnectionBuffer::new(subscription, anchor));
        connection.enqueue_all(burst);

        state
            .connections
            .entry(subscription_id.to_owned())
            .or_default()
            .push(Arc::clone(&connection));
        // After the push, never before it. The guard has to outlive
        // `enqueue_all` above -- that is the invariant this whole method is
        // arranged around -- and it does, because the push is its last use.
        drop(state);
        Some(connection)
    }

    /// Detach a WebSocket, reaping the subscription if it was transient.
    ///
    /// `:18` -- "The Query API MAY remove any Subscriptions with persist set to
    /// false that no longer have WebSocket connections."
    ///
    /// Returns whether the subscription was reaped.
    pub fn disconnect(&self, connection: &Arc<ConnectionBuffer>) -> bool {
        connection.close();
        let subscription = connection.subscription();

        let mut state = self.state.write();
        let Some(connections) = state.connections.get_mut(&subscription.id) else {
            return false;
        };
        // By identity, not by value. Two connections to one subscription are
        // equal in every field they carry, so `==` would detach whichever the
        // scan reached first rather than the one that actually closed.
        connections.retain(|held| !Arc::ptr_eq(held, connection));

        let reap = connections.is_empty() && !subscription.persist;
        if reap {
            state.subscriptions.remove(&subscription.id);
            state.connections.remove(&subscription.id);
        }
        drop(state);
        reap
    }

    /// Every subscription with its live connections, for the matcher.
    ///
    /// Returned by value so the matcher classifies and enqueues with no lock
    /// held -- which is the divergence this crate exists to make safe.
    #[must_use]
    pub fn routes(&self) -> Vec<(Subscription, Vec<Arc<ConnectionBuffer>>)> {
        let state = self.state.read();
        let mut routes: Vec<(Subscription, Vec<Arc<ConnectionBuffer>>)> = state
            .subscriptions
            .values()
            .map(|subscription| {
                let connections = state
                    .connections
                    .get(&subscription.id)
                    .cloned()
                    .unwrap_or_default();
                (subscription.clone(), connections)
            })
            .collect();
        drop(state);
        routes.sort_by(|left, right| left.0.id.cmp(&right.0.id));
        routes
    }

    /// The live connections on one subscription.
    #[must_use]
    pub fn connections(&self, subscription_id: &str) -> Vec<Arc<ConnectionBuffer>> {
        self.state
            .read()
            .connections
            .get(subscription_id)
            .cloned()
            .unwrap_or_default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use nmos_registry_core::body::Body;
    use nmos_registry_core::store::RegistryStore;

    fn request(resource_path: &str) -> SubscriptionRequest {
        SubscriptionRequest {
            resource_path: resource_path.to_owned(),
            params: Vec::new(),
            max_update_rate_ms: 100,
            persist: true,
            secure: false,
            authorization: false,
            host: "example.test".to_owned(),
            ws_scheme: "ws".to_owned(),
            ws_host: "example.test".to_owned(),
        }
    }

    fn registry_with(bodies: &[(&str, &str)]) -> Registry {
        let registry = Registry::new(RegistryStore::new());
        for (index, (id, label)) in bodies.iter().enumerate() {
            let body = Body::new(format!(
                r#"{{"id":"{id}","version":"{}:0","label":"{label}"}}"#,
                index + 1,
            ));
            registry
                .register(ResourceType::Node, body)
                .expect("the fixture body registers");
        }
        registry
    }

    /// `create_or_match` on a path the six collections cover.
    fn created(
        manager: &SubscriptionManager,
        request: &SubscriptionRequest,
    ) -> (Subscription, bool) {
        manager
            .create_or_match(request)
            .expect("a subscribable resource_path")
    }

    // -- resource_path -----------------------------------------------------

    #[test]
    fn an_unsubscribable_path_is_refused_with_the_message_the_400_carries() {
        // `handlers_query.py:345` puts this text straight into the body, so it
        // is an API contract. Captured from the Python, not composed here.
        let manager = SubscriptionManager::new();
        let error = manager
            .create_or_match(&request("/bogus"))
            .expect_err("/bogus is not a collection");

        assert_eq!(
            error.message(),
            "resource_path '/bogus' is not subscribable; expected one of: \
             /nodes, /devices, /sources, /flows, /senders, /receivers",
        );
        assert_eq!(manager.count(), 0, "a refused request still minted state");
    }

    #[test]
    fn the_refusal_quotes_the_path_the_way_python_repr_does() {
        // `{resource_path!r}` switches to double quotes when the value contains
        // an apostrophe -- the same quirk the decode errors reproduce.
        let manager = SubscriptionManager::new();
        let error = manager
            .create_or_match(&request("/it's"))
            .expect_err("not a collection");
        assert!(
            error.message().starts_with("resource_path \"/it's\" "),
            "{}",
            error.message(),
        );

        let empty = manager
            .create_or_match(&request(""))
            .expect_err("not a collection");
        assert!(
            empty.message().starts_with("resource_path '' "),
            "{}",
            empty.message()
        );
    }

    #[test]
    fn surrounding_slashes_are_stripped_the_way_python_strips_them() {
        // `strip("/")` removes any number, on both ends. `strip_prefix` would
        // accept `/nodes` and refuse `//nodes//`, which Python accepts.
        let manager = SubscriptionManager::new();
        for path in ["/nodes", "nodes", "//nodes//", "nodes/"] {
            let (subscription, _) = manager
                .create_or_match(&request(path))
                .unwrap_or_else(|error| panic!("{path}: {error}"));
            assert_eq!(subscription.resource_type, ResourceType::Node, "{path}");
        }
    }

    #[test]
    fn every_collection_is_subscribable() {
        let manager = SubscriptionManager::new();
        for resource_type in ResourceType::ALL {
            let path = format!("/{}", resource_type.plural());
            let (subscription, _) = manager
                .create_or_match(&request(&path))
                .unwrap_or_else(|error| panic!("{path}: {error}"));
            assert_eq!(subscription.resource_type, resource_type);
        }
    }

    // -- create_or_match ---------------------------------------------------

    #[test]
    fn a_first_request_creates_and_reports_created() {
        let manager = SubscriptionManager::new();
        let (subscription, created) = created(&manager, &request("/nodes"));

        assert!(created, "the first request must mint a subscription");
        assert_eq!(manager.count(), 1);
        assert_eq!(subscription.resource_type, ResourceType::Node);
        assert!(
            subscription.ws_href.ends_with(&subscription.id),
            "ws_href must address the subscription it belongs to: {}",
            subscription.ws_href,
        );
    }

    #[test]
    fn an_identical_request_is_matched_rather_than_duplicated() {
        // `:25` -- what keeps a reconnecting Controller from accumulating
        // duplicates.
        let manager = SubscriptionManager::new();
        let (first, created_first) = created(&manager, &request("/nodes"));
        let (second, created_second) = created(&manager, &request("/nodes"));

        assert!(created_first);
        assert!(!created_second, "the second request must reuse the first");
        assert_eq!(first.id, second.id);
        assert_eq!(manager.count(), 1);
    }

    #[test]
    fn each_attribute_of_the_match_key_is_load_bearing() {
        // One case per field, so a field dropped from the key fails here rather
        // than by silently handing two clients one subscription.
        /// One field of the match key, and how to change it.
        type Mutation = (&'static str, fn(&mut SubscriptionRequest));

        let mutations: Vec<Mutation> = vec![
            ("resource_path", |r| r.resource_path = "/senders".to_owned()),
            ("max_update_rate_ms", |r| r.max_update_rate_ms = 200),
            ("persist", |r| r.persist = false),
            ("secure", |r| r.secure = true),
            ("authorization", |r| r.authorization = true),
            ("host", |r| r.host = "other.test".to_owned()),
            ("params", |r| {
                r.params = vec![("label".to_owned(), "x".to_owned())];
            }),
        ];

        for (field, mutate) in mutations {
            let manager = SubscriptionManager::new();
            let base = request("/nodes");
            let (_first, _) = created(&manager, &base);

            let mut altered = base.clone();
            mutate(&mut altered);
            let (_second, created) = created(&manager, &altered);

            assert!(
                created,
                "{field} is not part of the match key, so two different \
                 requests were handed the same subscription",
            );
            assert_eq!(manager.count(), 2, "{field}");
        }
    }

    #[test]
    fn filters_match_as_a_set_not_as_a_sequence() {
        // Python compares dicts, which ignores order. `params` is a Vec here so
        // that the response can echo what the client sent, and comparing it
        // positionally would mint a second subscription for the same request.
        let manager = SubscriptionManager::new();
        let mut first = request("/nodes");
        first.params = vec![
            ("label".to_owned(), "a".to_owned()),
            ("format".to_owned(), "b".to_owned()),
        ];
        let mut reordered = first.clone();
        reordered.params.reverse();

        let (original, _) = created(&manager, &first);
        let (matched, created) = created(&manager, &reordered);

        assert!(
            !created,
            "the same filters in another order are the same filters"
        );
        assert_eq!(original.id, matched.id);
        assert_eq!(
            matched.params,
            vec![
                ("label".to_owned(), "a".to_owned()),
                ("format".to_owned(), "b".to_owned()),
            ],
            "the stored order must stay as the creating client sent it",
        );
    }

    #[test]
    fn a_differing_filter_value_is_a_different_subscription() {
        let manager = SubscriptionManager::new();
        let mut first = request("/nodes");
        first.params = vec![("label".to_owned(), "a".to_owned())];
        let mut second = first.clone();
        second.params = vec![("label".to_owned(), "b".to_owned())];

        created(&manager, &first);
        let (_, created) = created(&manager, &second);
        assert!(created);
    }

    #[test]
    fn matching_is_stable_when_several_subscriptions_would_serve() {
        // Two subscriptions can become equivalent only through `delete` and
        // re-creation races, but a HashMap iteration order would then make
        // "which one do I get" vary run to run. Sorting by id fixes it.
        let manager = SubscriptionManager::new();
        let base = request("/nodes");
        let (first, _) = created(&manager, &base);
        // Force a second equivalent one past create_or_match's own reuse.
        {
            let mut state = manager.state.write();
            let mut twin = first.clone();
            twin.id = format!("{}-twin", first.id);
            state.subscriptions.insert(twin.id.clone(), twin);
        }

        let expected = created(&manager, &base).0.id;
        for _ in 0..20 {
            assert_eq!(created(&manager, &base).0.id, expected);
        }
    }

    // -- delete ------------------------------------------------------------

    #[test]
    fn deleting_removes_the_subscription_and_closes_its_clients() {
        // `:19` -- connected clients "SHOULD be forcibly closed by the server".
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[]);
        let (subscription, _) = created(&manager, &request("/nodes"));
        let connection = manager.connect(&registry, &subscription.id).unwrap();

        assert!(manager.delete(&subscription.id));
        assert_eq!(manager.count(), 0);
        assert_eq!(manager.grain_count(), 0);
        assert!(
            connection.is_closed(),
            "a deleted subscription kept serving its socket",
        );
    }

    #[test]
    fn deleting_an_unknown_subscription_is_not_an_error() {
        let manager = SubscriptionManager::new();
        assert!(!manager.delete("nope"));
    }

    // -- connect -----------------------------------------------------------

    #[test]
    fn connecting_queues_the_sync_burst() {
        // `:166` -- sync events carry identical pre and post.
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[("n1", "one"), ("n2", "two")]);
        let (subscription, _) = created(&manager, &request("/nodes"));

        let connection = manager.connect(&registry, &subscription.id).unwrap();
        let burst = connection.drain();

        assert_eq!(burst.len(), 2);
        for event in &burst {
            assert_eq!(
                event.pre.as_ref().map(Body::text),
                event.post.as_ref().map(Body::text),
                "a sync event must carry the same body on both sides",
            );
        }
    }

    #[test]
    fn a_sync_burst_goes_to_the_new_client_only() {
        // `:166` says the burst exists so that *the* client has received all
        // data for a topic. The AMWA mock broadcasts it to every connected
        // client instead, which re-sends every existing client the whole
        // collection each time anyone new connects.
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[("n1", "one")]);
        let (subscription, _) = created(&manager, &request("/nodes"));

        let existing = manager.connect(&registry, &subscription.id).unwrap();
        assert_eq!(existing.drain().len(), 1, "its own burst");

        let arriving = manager.connect(&registry, &subscription.id).unwrap();

        assert_eq!(arriving.drain().len(), 1, "the new client's burst");
        assert!(
            existing.is_empty(),
            "the new client's sync burst was broadcast to an existing client",
        );
    }

    #[test]
    fn a_sync_burst_respects_the_subscription_s_filter() {
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[("n1", "keep"), ("n2", "drop"), ("n3", "keep")]);
        let mut filtered = request("/nodes");
        filtered.params = vec![("label".to_owned(), "keep".to_owned())];
        let (subscription, _) = created(&manager, &filtered);

        let connection = manager.connect(&registry, &subscription.id).unwrap();

        let mut paths: Vec<String> = connection.drain().into_iter().map(|e| e.path).collect();
        paths.sort();
        assert_eq!(
            paths,
            ["n1", "n3"],
            "the burst ignored the subscription's filter",
        );
    }

    #[test]
    fn grain_count_tracks_connections_not_subscriptions() {
        // One grain per connected WebSocket, as in nmos-cpp -- it is what the
        // status line reports, so a subscription with no client must not count.
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[]);
        let (first, _) = created(&manager, &request("/nodes"));
        let (second, _) = created(&manager, &request("/senders"));

        assert_eq!(manager.count(), 2);
        assert_eq!(manager.grain_count(), 0, "no client is connected yet");

        let a = manager.connect(&registry, &first.id).unwrap();
        let b = manager.connect(&registry, &first.id).unwrap();
        let c = manager.connect(&registry, &second.id).unwrap();
        assert_eq!(manager.grain_count(), 3);

        manager.disconnect(&b);
        assert_eq!(manager.grain_count(), 2);
        manager.disconnect(&a);
        manager.disconnect(&c);
        assert_eq!(manager.grain_count(), 0);
        assert_eq!(manager.count(), 2, "both subscriptions are persistent");
    }

    #[test]
    fn connecting_to_an_empty_registry_queues_nothing() {
        // An empty grain would violate minItems: 1.
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[]);
        let (subscription, _) = created(&manager, &request("/nodes"));

        let connection = manager.connect(&registry, &subscription.id).unwrap();
        assert!(connection.is_empty());
    }

    #[test]
    fn connecting_to_a_deleted_subscription_yields_nothing() {
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[]);
        assert!(manager.connect(&registry, "never-existed").is_none());
    }

    #[test]
    fn a_connection_anchors_at_the_sequence_its_burst_describes() {
        // What the matcher uses to skip what the burst already carried.
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[("n1", "one")]);
        let (subscription, _) = created(&manager, &request("/nodes"));

        let connection = manager.connect(&registry, &subscription.id).unwrap();
        assert_eq!(connection.anchor(), registry.latest_sequence());
    }

    #[test]
    fn two_clients_on_one_subscription_get_separate_buffers() {
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[("n1", "one")]);
        let (subscription, _) = created(&manager, &request("/nodes"));

        let first = manager.connect(&registry, &subscription.id).unwrap();
        let second = manager.connect(&registry, &subscription.id).unwrap();

        assert_eq!(manager.grain_count(), 2);
        assert_eq!(first.drain().len(), 1);
        assert_eq!(
            second.drain().len(),
            1,
            "draining one client emptied the other's buffer",
        );
    }

    // -- disconnect --------------------------------------------------------

    #[test]
    fn a_non_persistent_subscription_is_reaped_when_its_last_client_leaves() {
        // `:18`.
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[]);
        let mut transient = request("/nodes");
        transient.persist = false;
        let (subscription, _) = created(&manager, &transient);

        let first = manager.connect(&registry, &subscription.id).unwrap();
        let second = manager.connect(&registry, &subscription.id).unwrap();

        assert!(!manager.disconnect(&first), "one client still remained");
        assert_eq!(manager.count(), 1);
        assert!(manager.disconnect(&second), "the last client left");
        assert_eq!(manager.count(), 0);
    }

    #[test]
    fn a_persistent_subscription_survives_its_last_client() {
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[]);
        let (subscription, _) = created(&manager, &request("/nodes"));
        let connection = manager.connect(&registry, &subscription.id).unwrap();

        assert!(!manager.disconnect(&connection));
        assert_eq!(manager.count(), 1);
        assert_eq!(manager.grain_count(), 0);
    }

    #[test]
    fn disconnecting_detaches_the_connection_that_closed_not_a_twin() {
        // Two connections on one subscription are equal in every field they
        // carry, so this must compare by identity.
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[]);
        let (subscription, _) = created(&manager, &request("/nodes"));
        let first = manager.connect(&registry, &subscription.id).unwrap();
        let second = manager.connect(&registry, &subscription.id).unwrap();

        // The SECOND one, deliberately. Detaching the first would also be the
        // answer a "remove the first equal element" implementation gives, so
        // closing the first proves nothing.
        manager.disconnect(&second);

        let remaining = manager.connections(&subscription.id);
        assert_eq!(remaining.len(), 1);
        assert!(
            Arc::ptr_eq(&remaining[0], &first),
            "the wrong connection was detached -- this compares by value, and \
             two connections on one subscription are equal in every field",
        );
        assert!(second.is_closed());
        assert!(!first.is_closed(), "the surviving connection was closed");
    }

    #[test]
    fn disconnecting_twice_is_harmless() {
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[]);
        let mut transient = request("/nodes");
        transient.persist = false;
        let (subscription, _) = created(&manager, &transient);
        let connection = manager.connect(&registry, &subscription.id).unwrap();

        assert!(manager.disconnect(&connection));
        assert!(
            !manager.disconnect(&connection),
            "reaping ran a second time for one socket",
        );
    }

    // -- routes ------------------------------------------------------------

    #[test]
    fn routes_carry_every_subscription_with_its_connections() {
        let manager = SubscriptionManager::new();
        let registry = registry_with(&[]);
        let (nodes, _) = created(&manager, &request("/nodes"));
        let (senders, _) = created(&manager, &request("/senders"));
        manager.connect(&registry, &nodes.id).unwrap();

        let routes = manager.routes();
        assert_eq!(routes.len(), 2);
        let by_id: HashMap<&str, usize> = routes
            .iter()
            .map(|(subscription, connections)| (subscription.id.as_str(), connections.len()))
            .collect();
        assert_eq!(by_id[nodes.id.as_str()], 1);
        assert_eq!(by_id[senders.id.as_str()], 0);
    }

    #[test]
    fn routes_and_all_are_ordered_so_two_members_agree() {
        let manager = SubscriptionManager::new();
        for path in ["/nodes", "/senders", "/devices", "/flows", "/sources"] {
            created(&manager, &request(path));
        }

        let ids: Vec<String> = manager.all().into_iter().map(|s| s.id).collect();
        let mut sorted = ids.clone();
        sorted.sort();
        assert_eq!(ids, sorted, "all() emitted in hash order");

        let route_ids: Vec<String> = manager.routes().into_iter().map(|(s, _)| s.id).collect();
        assert_eq!(route_ids, sorted, "routes() emitted in hash order");
    }

    // -- concurrency -------------------------------------------------------

    #[test]
    fn connects_and_disconnects_from_many_threads_leave_no_residue() {
        use std::thread;

        let manager = Arc::new(SubscriptionManager::new());
        let registry = Arc::new(registry_with(&[("n1", "one")]));
        let (subscription, _) = created(&manager, &request("/nodes"));

        let mut handles = Vec::new();
        for _ in 0..8 {
            let manager = Arc::clone(&manager);
            let registry = Arc::clone(&registry);
            let id = subscription.id.clone();
            handles.push(thread::spawn(move || {
                for _ in 0..100 {
                    let connection = manager.connect(&registry, &id).expect("subscription");
                    assert_eq!(connection.drain().len(), 1, "the burst went missing");
                    manager.disconnect(&connection);
                }
            }));
        }
        for handle in handles {
            handle.join().expect("no thread panicked");
        }

        assert_eq!(manager.count(), 1, "the persistent subscription survived");
        assert_eq!(
            manager.grain_count(),
            0,
            "connections were left attached after their sockets closed",
        );
    }
}
