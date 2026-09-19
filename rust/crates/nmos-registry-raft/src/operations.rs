// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The replicated unit: a registry operation, not a key-value pair.
//!
//! Port of `nmos/raft/operations.py`.
//!
//! This is the design decision the whole package turns on. The etcd backend
//! replicates *writes to a key-value store* and rebuilds a registry view from
//! them, which is why it needs an envelope format, a key layout, a watch, and a
//! fence to tell it when its view has caught up to a write it just made. Here
//! the log carries the operation itself, so applying a committed entry **is**
//! the store mutation plus its grain publication, and none of that machinery
//! exists.
//!
//! # Determinism is the whole contract
//!
//! Every member applies the same entries in the same order and must reach the
//! same state. That makes the choice of what travels in an operation a
//! correctness question rather than an efficiency one, and it produces one
//! rule:
//!
//! > **Anything a member would otherwise read from its local environment must
//! > be decided once, by the proposer, and carried.**
//!
//! Concretely, `created`/`updated` cursors and `health` are fields here rather
//! than defaults filled in at apply time, because the store's apply would
//! otherwise read the clock and the local cursor allocator -- both of which
//! give a different answer on every member. Those two defaults are precisely
//! the ones the state machine must always override.
//!
//! # What is *not* carried, and why
//!
//! The proposer's prepared registration is not on the wire. It is tempting --
//! validation has already happened, so why do it twice -- but it would be
//! wrong: `store.prepare` decides five things, and four of them are scoped to
//! the Node's own subtree while one, the id-uniqueness check, is global across
//! every Node. An owner cannot decide that one, because a concurrent
//! registration under a different Node on a different member might be claiming
//! the same id.
//!
//! The log is what serialises those, so apply re-runs `prepare` against the
//! replicated store and *that* answer is authoritative. What travels instead is
//! `expect_created`: the proposer's belief about 201-vs-200, carried purely as
//! a tripwire. If apply disagrees, the two members have diverged, and saying so
//! loudly beats serving two different answers quietly.
//!
//! # Bodies travel verbatim
//!
//! `body_text` is the request body exactly as the client sent it. The
//! registry's guarantee is that what was registered is what every member
//! serves, byte for byte, including vendor extensions the generated types do
//! not model -- so a re-encode here, however lossless it looked, would break
//! that guarantee at the one point nobody would think to check.
//!
//! # Shape: one struct, one kind
//!
//! The Python gives each operation its own frozen dataclass, each carrying its
//! own `proposal`. Here the proposal is lifted out into [`Operation`] and the
//! rest is an [`OperationKind`] enum, which is what the encoding already does
//! -- field 1 of the body is the proposal, uniformly, for every kind. Lifting
//! it makes that structural rather than repeated, and makes "every operation
//! answers a proposal" impossible to forget when a kind is added.

use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource_type::ResourceType;

use crate::errors::RaftProtocolError;
use crate::wire::{Reader, Writer};

/// What an entry does. Numbers are permanent, like every wire number.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum OpKind {
    /// A leader's term-establishing entry.
    ///
    /// Raft §8: a new leader cannot know what is committed from previous terms
    /// until it commits an entry of its own, so it appends one that does
    /// nothing.
    Noop = 0,
    /// Register or update one resource.
    Register = 1,
    /// Remove one resource and its subtree.
    Unregister = 2,
    /// A Node whose lease lapsed, and its whole subtree.
    ///
    /// Decided by the owner against its own clock and then replicated, rather
    /// than each member expiring on its own clock -- which is how members end
    /// up disagreeing about which Nodes are alive.
    Expire = 3,
    /// Drop tombstones whose forget interval has elapsed.
    ///
    /// Replicated for the same reason and with the same shape as
    /// [`Self::Expire`]: the decision reads a clock, so it is made once and the
    /// *result* is what every member applies.
    Forget = 4,
    /// Take responsibility for a Node's subtree.
    ClaimOwnership = 5,
    /// Give it up.
    ReleaseOwnership = 6,
    /// One member is gone; release every Node it owned, atomically.
    ///
    /// A single entry rather than one per Node, so a member holding a thousand
    /// Nodes does not put a thousand entries through consensus at the moment
    /// the cluster is already one member down.
    MemberDown = 7,
}

impl OpKind {
    const fn from_byte(value: u64) -> Option<Self> {
        match value {
            0 => Some(Self::Noop),
            1 => Some(Self::Register),
            2 => Some(Self::Unregister),
            3 => Some(Self::Expire),
            4 => Some(Self::Forget),
            5 => Some(Self::ClaimOwnership),
            6 => Some(Self::ReleaseOwnership),
            7 => Some(Self::MemberDown),
            _ => None,
        }
    }
}

/// Identifies a proposal so its originator can be answered.
///
/// `(member, sequence)` rather than a UUID: it is two varints instead of
/// sixteen bytes on every entry, and it is ordered.
///
/// The sequence must be unique over the member's whole **history**, not just
/// over one run of it. A restarted member has no outstanding waiters of its
/// own -- but its *entries* survive it, sitting in the cluster's log and
/// applying after it returns. If the new incarnation mints the same ids, an old
/// entry's outcome resolves a new caller's future: a registration answered with
/// an unregistration's result. The node therefore seeds the sequence from the
/// incarnation, giving each run of the member its own range.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ProposalId {
    /// Which member proposed it.
    pub member: u64,
    /// Unique within that member's whole history. See the struct docs.
    pub sequence: u64,
}

/// Register or update one resource.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Register {
    /// Which collection it belongs to.
    pub resource_type: ResourceType,
    /// The resource's own id.
    pub resource_id: String,
    /// The Node at the root of its subtree, for ownership.
    pub node_id: String,
    /// The request body, verbatim. See the module docs.
    pub body_text: String,
    /// The creation cursor, decided by the proposer.
    pub created: TaiCursor,
    /// The update cursor, decided by the proposer.
    pub updated: TaiCursor,
    /// The health instant, decided by the proposer.
    pub health: u64,
    /// The proposer's belief about 201-versus-200, carried as a tripwire.
    pub expect_created: bool,
    /// Fused ownership claim.
    ///
    /// When the proposer is taking ownership of a previously unowned Node, the
    /// claim rides along on the registration instead of being a separate entry.
    /// That keeps a Node's first registration to one round trip rather than
    /// two, which matters because a facility powering up is entirely first
    /// registrations.
    ///
    /// `None` rather than zero: member 0 is a real member, so a sentinel would
    /// claim ownership for the first member on every registration that was not
    /// claiming anything.
    pub claim_owner: Option<u64>,
}

/// What an operation does, beyond answering its proposal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OperationKind {
    /// See [`OpKind::Noop`].
    Noop,
    /// See [`Register`].
    Register(Register),
    /// Remove one resource and its subtree.
    Unregister {
        /// Which collection it belongs to.
        resource_type: ResourceType,
        /// The resource's own id.
        resource_id: String,
    },
    /// A Node whose lease lapsed.
    Expire {
        /// The Node to remove, with its subtree.
        node_id: String,
    },
    /// Drop tombstones whose forget interval has elapsed.
    Forget {
        /// Exactly which records to drop, decided once by the proposer.
        victims: Vec<(ResourceType, String)>,
    },
    /// Take responsibility for a Node's subtree.
    ClaimOwnership {
        /// The Node being claimed.
        node_id: String,
        /// The member taking it.
        owner: u64,
    },
    /// Give it up.
    ReleaseOwnership {
        /// The Node being released.
        node_id: String,
    },
    /// One member is gone.
    MemberDown {
        /// Which member.
        member: u64,
    },
}

impl OperationKind {
    /// The kind byte this travels under.
    #[must_use]
    pub const fn kind(&self) -> OpKind {
        match *self {
            Self::Noop => OpKind::Noop,
            Self::Register(_) => OpKind::Register,
            Self::Unregister { .. } => OpKind::Unregister,
            Self::Expire { .. } => OpKind::Expire,
            Self::Forget { .. } => OpKind::Forget,
            Self::ClaimOwnership { .. } => OpKind::ClaimOwnership,
            Self::ReleaseOwnership { .. } => OpKind::ReleaseOwnership,
            Self::MemberDown { .. } => OpKind::MemberDown,
        }
    }
}

/// One replicated operation: who proposed it, and what it does.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Operation {
    /// Who proposed it, so the outcome can be returned to them.
    pub proposal: ProposalId,
    /// What it does.
    pub kind: OperationKind,
}

impl Operation {
    /// Serialise one operation. Field 1 is always the kind.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        let mut body = Writer::new().bytes(
            1,
            &Writer::new()
                .uint(1, self.proposal.member)
                .uint(2, self.proposal.sequence)
                .take(),
        );

        match self.kind {
            OperationKind::Noop => {}
            OperationKind::Register(ref op) => {
                body = body
                    .string(2, op.resource_type.singular())
                    .string(3, &op.resource_id)
                    .string(4, &op.node_id)
                    .string(5, &op.body_text);
                body = write_cursor(body, 6, op.created);
                body = write_cursor(body, 7, op.updated);
                body = body.uint(8, op.health).bool(9, op.expect_created);
                if let Some(owner) = op.claim_owner {
                    body = body.uint(10, owner);
                }
            }
            OperationKind::Unregister {
                resource_type,
                ref resource_id,
            } => {
                body = body
                    .string(2, resource_type.singular())
                    .string(3, resource_id);
            }
            OperationKind::Expire { ref node_id }
            | OperationKind::ReleaseOwnership { ref node_id } => {
                body = body.string(2, node_id);
            }
            OperationKind::Forget { ref victims } => {
                for &(resource_type, ref resource_id) in victims {
                    let victim = Writer::new()
                        .string(1, resource_type.singular())
                        .string(2, resource_id)
                        .take();
                    body = body.bytes(2, &victim);
                }
            }
            OperationKind::ClaimOwnership { ref node_id, owner } => {
                body = body.string(2, node_id).uint(3, owner);
            }
            OperationKind::MemberDown { member } => {
                body = body.uint(2, member);
            }
        }

        Writer::new()
            .uint(1, self.kind.kind() as u64)
            .bytes(2, &body.take())
            .take()
    }

    /// Parse one operation.
    ///
    /// Called on receipt rather than at apply time, deliberately. A malformed
    /// entry discovered here can still drop the link; discovered inside apply
    /// it would be a synchronous mutation that has nowhere to fail.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed, names no kind, or
    /// names a kind or resource type this build does not know.
    pub fn decode(data: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut kind_value: Option<u64> = None;
        let mut body: &[u8] = &[];
        let mut reader = Reader::new(data);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => kind_value = Some(reader.uint()?),
                2 => body = reader.bytes()?,
                _ => reader.skip(wire)?,
            }
        }

        let Some(kind_value) = kind_value else {
            return Err(RaftProtocolError("operation has no kind".to_owned()));
        };
        let Some(kind) = OpKind::from_byte(kind_value) else {
            return Err(RaftProtocolError(format!(
                "unknown operation kind {kind_value}"
            )));
        };

        let mut fields = Fields::default();
        let mut inner = Reader::new(body);
        while let Some((number, wire)) = inner.next_field()? {
            if number == 1 {
                fields.proposal = read_proposal(inner.bytes()?)?;
                continue;
            }
            fields.read(kind, number, wire, &mut inner)?;
        }

        Ok(Self {
            proposal: fields.proposal,
            kind: fields.into_kind(kind)?,
        })
    }
}

/// Everything a body can carry, before it is narrowed to one kind.
///
/// Flat, like the Python's local variables, because the fields a kind uses are
/// decided by the kind byte and the reader cannot know them until it has one.
struct Fields {
    proposal: ProposalId,
    resource_type_name: String,
    resource_id: String,
    node_id: String,
    body_text: String,
    created: TaiCursor,
    updated: TaiCursor,
    health: u64,
    expect_created: bool,
    claim_owner: Option<u64>,
    member: u64,
    victims: Vec<(ResourceType, String)>,
}

impl Default for Fields {
    fn default() -> Self {
        Self {
            proposal: ProposalId {
                member: 0,
                sequence: 0,
            },
            resource_type_name: String::new(),
            resource_id: String::new(),
            node_id: String::new(),
            body_text: String::new(),
            created: TaiCursor::MIN,
            updated: TaiCursor::MIN,
            health: 0,
            expect_created: false,
            claim_owner: None,
            member: 0,
            victims: Vec::new(),
        }
    }
}

impl Fields {
    fn read(
        &mut self,
        kind: OpKind,
        number: u64,
        wire: crate::wire::WireType,
        inner: &mut Reader<'_>,
    ) -> Result<(), RaftProtocolError> {
        match (kind, number) {
            (OpKind::Register, 2) => self.resource_type_name = inner.string()?,
            (OpKind::Register, 3) => self.resource_id = inner.string()?,
            (OpKind::Register, 4) => self.node_id = inner.string()?,
            (OpKind::Register, 5) => self.body_text = inner.string()?,
            (OpKind::Register, 6) => self.created = read_cursor(inner.bytes()?)?,
            (OpKind::Register, 7) => self.updated = read_cursor(inner.bytes()?)?,
            (OpKind::Register, 8) => self.health = inner.uint()?,
            (OpKind::Register, 9) => self.expect_created = inner.bool()?,
            (OpKind::Register, 10) => self.claim_owner = Some(inner.uint()?),

            (OpKind::Unregister, 2) => self.resource_type_name = inner.string()?,
            (OpKind::Unregister, 3) => self.resource_id = inner.string()?,

            (OpKind::Expire | OpKind::ReleaseOwnership | OpKind::ClaimOwnership, 2) => {
                self.node_id = inner.string()?;
            }
            (OpKind::ClaimOwnership, 3) => self.claim_owner = Some(inner.uint()?),

            (OpKind::Forget, 2) => {
                let victim = read_victim(inner.bytes()?)?;
                self.victims.push(victim);
            }

            (OpKind::MemberDown, 2) => self.member = inner.uint()?,

            _ => inner.skip(wire)?,
        }
        Ok(())
    }

    fn into_kind(self, kind: OpKind) -> Result<OperationKind, RaftProtocolError> {
        Ok(match kind {
            OpKind::Noop => OperationKind::Noop,
            OpKind::Register => OperationKind::Register(Register {
                resource_type: resource_type(&self.resource_type_name)?,
                resource_id: self.resource_id,
                node_id: self.node_id,
                body_text: self.body_text,
                created: self.created,
                updated: self.updated,
                health: self.health,
                expect_created: self.expect_created,
                claim_owner: self.claim_owner,
            }),
            OpKind::Unregister => OperationKind::Unregister {
                resource_type: resource_type(&self.resource_type_name)?,
                resource_id: self.resource_id,
            },
            OpKind::Expire => OperationKind::Expire {
                node_id: self.node_id,
            },
            OpKind::Forget => OperationKind::Forget {
                victims: self.victims,
            },
            OpKind::ClaimOwnership => {
                let Some(owner) = self.claim_owner else {
                    return Err(RaftProtocolError(
                        "ownership claim names no owner".to_owned(),
                    ));
                };
                OperationKind::ClaimOwnership {
                    node_id: self.node_id,
                    owner,
                }
            }
            OpKind::ReleaseOwnership => OperationKind::ReleaseOwnership {
                node_id: self.node_id,
            },
            OpKind::MemberDown => OperationKind::MemberDown {
                member: self.member,
            },
        })
    }
}

fn write_cursor(writer: Writer, field: u64, cursor: TaiCursor) -> Writer {
    let encoded = Writer::new()
        .uint(1, cursor.seconds)
        .uint(2, cursor.nanoseconds)
        .take();
    writer.bytes(field, &encoded)
}

fn read_cursor(payload: &[u8]) -> Result<TaiCursor, RaftProtocolError> {
    let mut seconds = 0;
    let mut nanoseconds = 0;
    let mut reader = Reader::new(payload);
    while let Some((number, wire)) = reader.next_field()? {
        match number {
            1 => seconds = reader.uint()?,
            2 => nanoseconds = reader.uint()?,
            _ => reader.skip(wire)?,
        }
    }
    // The struct literal, not `TaiCursor::new`: `new` normalises a nanosecond
    // field at or above one second into the seconds, and the Python's
    // `TaiCursor(seconds, nanoseconds)` does not. A cursor that arrived as
    // `0:5000000000` must stay `0:5000000000`, or two members would hold
    // different cursors for the same resource and page differently.
    Ok(TaiCursor {
        seconds,
        nanoseconds,
    })
}

fn read_proposal(payload: &[u8]) -> Result<ProposalId, RaftProtocolError> {
    let mut member = 0;
    let mut sequence = 0;
    let mut reader = Reader::new(payload);
    while let Some((number, wire)) = reader.next_field()? {
        match number {
            1 => member = reader.uint()?,
            2 => sequence = reader.uint()?,
            _ => reader.skip(wire)?,
        }
    }
    Ok(ProposalId { member, sequence })
}

fn read_victim(payload: &[u8]) -> Result<(ResourceType, String), RaftProtocolError> {
    let mut name = String::new();
    let mut resource_id = String::new();
    let mut reader = Reader::new(payload);
    while let Some((number, wire)) = reader.next_field()? {
        match number {
            1 => name = reader.string()?,
            2 => resource_id = reader.string()?,
            _ => reader.skip(wire)?,
        }
    }
    Ok((resource_type(&name)?, resource_id))
}

fn resource_type(value: &str) -> Result<ResourceType, RaftProtocolError> {
    ResourceType::from_singular(value)
        .ok_or_else(|| RaftProtocolError(format!("unknown resource type '{value}'")))
}
