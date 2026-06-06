# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NMOS error hierarchy.

Each error type maps to a distinct exception class, enabling isinstance()-based
dispatch.

Errors are split into two categories:
- Recoverable: expected conditions that callers should handle (NotAvailable,
  Skip, Timeout, etc.)
- Fatal: programming errors or unexpected failures (InvalidParameter,
  InvalidObject, UnexpectedError, etc.)
"""

from __future__ import annotations


class NmosError(Exception):
    """Base class for all NMOS errors. Supports isinstance() dispatch."""

    recoverable: bool = False

    def __init__(self, msg: str = "") -> None:
        super().__init__(msg)
        self.msg = msg


# ---------------------------------------------------------------------------
# Recoverable errors -- expected conditions that callers should handle
# ---------------------------------------------------------------------------

class NotSuccessful(NmosError):
    """Operation completed but was not successful."""
    recoverable = True


class NotRecognized(NmosError):
    """Item or format not recognized."""
    recoverable = True


class NotMatching(NmosError):
    """Items do not match."""
    recoverable = True


class NotAvailable(NmosError):
    """Resource or value is not available (e.g., undefined field access)."""
    recoverable = True


class NotAllowed(NmosError):
    """Operation is not allowed."""
    recoverable = True


class NotUnique(NmosError):
    """Item is not unique when uniqueness is required."""
    recoverable = True


class NotFound(NmosError):
    """Item was not found."""
    recoverable = True


class Done(NmosError):
    """Context is done (cancellation)."""
    recoverable = True


class Skip(NmosError):
    """Control flow signal -- terminate current parsing scope."""
    recoverable = True


class Lost(NmosError):
    """Connection or resource was lost."""
    recoverable = True


class Idle(NmosError):
    """Resource is idle."""
    recoverable = True


class Busy(NmosError):
    """Resource is busy."""
    recoverable = True


class Empty(NmosError):
    """Container is empty."""
    recoverable = True


class Full(NmosError):
    """Container is full."""
    recoverable = True


class Timeout(NmosError):
    """Operation timed out."""
    recoverable = True


class Expired(NmosError):
    """Context deadline exceeded."""
    recoverable = True


# ---------------------------------------------------------------------------
# Fatal errors -- programming errors or unexpected failures
# ---------------------------------------------------------------------------

class Fail(NmosError):
    """General failure."""
    recoverable = False


class InvalidOperation(NmosError):
    """Operation is not valid in current state."""
    recoverable = False


class InvalidParameter(NmosError):
    """Invalid parameter was provided."""
    recoverable = False


class InvalidObject(NmosError):
    """Object validation failed (e.g., missing required field after decode)."""
    recoverable = False


class InvalidAddress(NmosError):
    """Invalid network address."""
    recoverable = False


class InvalidPointer(NmosError):
    """Invalid pointer/reference."""
    recoverable = False


class InvalidIndex(NmosError):
    """Index out of range."""
    recoverable = False


class InvalidHandle(NmosError):
    """Invalid handle."""
    recoverable = False


class InvalidData(NmosError):
    """Data is malformed or invalid (e.g., bad JSON structure)."""
    recoverable = False


class InvalidSize(NmosError):
    """Invalid size."""
    recoverable = False


class InvalidCount(NmosError):
    """Invalid count."""
    recoverable = False


class InvalidValue(NmosError):
    """Invalid value."""
    recoverable = False


class InvalidAlignment(NmosError):
    """Invalid alignment."""
    recoverable = False


class InvalidType(NmosError):
    """Type mismatch."""
    recoverable = False


class UnexpectedStatus(NmosError):
    """Unexpected status code."""
    recoverable = False


class UnexpectedState(NmosError):
    """Unexpected internal state."""
    recoverable = False


class UnexpectedError(NmosError):
    """Unexpected error."""
    recoverable = False


def is_recoverable(err: BaseException) -> bool:
    """Check if an error is recoverable (expected condition)."""
    if isinstance(err, NmosError):
        return err.recoverable
    return False
