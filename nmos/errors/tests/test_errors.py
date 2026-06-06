# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.errors module."""

from __future__ import annotations

import pytest

from nmos.errors import (
    Busy,
    Done,
    Empty,
    Expired,
    Fail,
    Full,
    Idle,
    InvalidData,
    InvalidObject,
    InvalidParameter,
    InvalidType,
    Lost,
    NmosError,
    NotAvailable,
    NotFound,
    NotMatching,
    NotRecognized,
    NotSuccessful,
    NotUnique,
    Skip,
    Timeout,
    UnexpectedError,
    UnexpectedState,
    UnexpectedStatus,
    is_recoverable,
)


class TestErrorHierarchy:
    """All NMOS errors inherit from NmosError."""

    def test_all_inherit_from_nmos_error(self) -> None:
        recoverable = [
            NotSuccessful, NotRecognized, NotMatching, NotAvailable,
            NotFound, Done, Skip, Lost, Idle, Busy, Empty, Full,
            Timeout, Expired, NotUnique,
        ]
        fatal = [
            Fail, InvalidParameter, InvalidObject, InvalidData,
            InvalidType, UnexpectedError, UnexpectedStatus, UnexpectedState,
        ]
        for cls in recoverable + fatal:
            assert issubclass(cls, NmosError)
            assert issubclass(cls, Exception)

    def test_nmos_error_is_exception(self) -> None:
        with pytest.raises(NmosError):
            raise NotAvailable("test")


class TestRecoverableFlag:
    """Recoverable vs fatal distinction."""

    def test_recoverable_errors(self) -> None:
        recoverable_types = [
            NotSuccessful, NotRecognized, NotMatching, NotAvailable,
            NotFound, Done, Skip, Lost, Idle, Busy, Empty, Full,
            Timeout, Expired, NotUnique,
        ]
        for cls in recoverable_types:
            err = cls("test")
            assert err.recoverable is True, f"{cls.__name__} should be recoverable"
            assert is_recoverable(err) is True

    def test_fatal_errors(self) -> None:
        fatal_types = [
            Fail, InvalidParameter, InvalidObject, InvalidData,
            InvalidType, UnexpectedError, UnexpectedStatus, UnexpectedState,
        ]
        for cls in fatal_types:
            err = cls("test")
            assert err.recoverable is False, f"{cls.__name__} should be fatal"
            assert is_recoverable(err) is False

    def test_non_nmos_error(self) -> None:
        assert is_recoverable(ValueError("x")) is False


class TestErrorMessages:
    """Error messages are stored and accessible."""

    def test_message_stored(self) -> None:
        err = NotAvailable("undefined value")
        assert err.msg == "undefined value"
        assert str(err) == "undefined value"

    def test_empty_message(self) -> None:
        err = InvalidData()
        assert err.msg == ""
        assert str(err) == ""


class TestIsinstanceDispatch:
    """Errors can be dispatched with isinstance (Python's switch err.(type))."""

    def test_catch_specific_type(self) -> None:
        err: NmosError = NotAvailable("test")
        assert isinstance(err, NotAvailable)
        assert not isinstance(err, InvalidData)

    def test_catch_base_class(self) -> None:
        err: NmosError = InvalidData("bad json")
        assert isinstance(err, NmosError)

    def test_try_except_specific(self) -> None:
        with pytest.raises(Skip):
            raise Skip()

    def test_try_except_base(self) -> None:
        with pytest.raises(NmosError):
            raise InvalidObject("missing field")
