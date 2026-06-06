"""Generated NMOS type: User. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NBool, NInt

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class UserEnums:
    """JSON property name enums for User."""
    FullName = EnumRegistry.get("fullname")
    UserName = EnumRegistry.get("username")
    Password = EnumRegistry.get("password")
    Country = EnumRegistry.get("country")
    Email = EnumRegistry.get("email")
    Recovery = EnumRegistry.get("recovery")
    Administrator = EnumRegistry.get("administrator")
    Key = EnumRegistry.get("key")
    pass


class UserValue:
    """Inner value struct for User."""

    __slots__ = (
        "FullName",
        "UserName",
        "Password",
        "Country",
        "Email",
        "Recovery",
        "Administrator",
        "Key",
        "PasswordTime",
        "RecoveryTime",
        "PasswordErrors",
    )

    def __init__(self) -> None:
        self.FullName: NString = NString()
        self.UserName: NString = NString()
        self.Password: NString = NString()
        self.Country: NString = NString()
        self.Email: NString = NString()
        self.Recovery: NString = NString()
        self.Administrator: NBool = NBool()
        self.Key: NString = NString()
        self.PasswordTime: NInt = NInt()
        self.RecoveryTime: NInt = NInt()
        self.PasswordErrors: NInt = NInt()

    def set_to_default(self) -> None:
        self.FullName.set_to_default()
        self.UserName.set_to_default()
        self.Password.set_to_default()
        self.Country.set_to_default()
        self.Email.set_to_default()
        self.Recovery.set_to_default()
        self.Administrator.set_to_default()
        self.Key.set_to_default()
        self.PasswordTime.set_to_default()
        self.RecoveryTime.set_to_default()
        self.PasswordErrors.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.FullName.defined:
            raise InvalidObject("missing required member FullName")
        if not self.UserName.defined:
            raise InvalidObject("missing required member UserName")
        if not self.Password.defined:
            raise InvalidObject("missing required member Password")
        if not self.Country.defined:
            raise InvalidObject("missing required member Country")
        if not self.Email.defined:
            raise InvalidObject("missing required member Email")
        if not self.Recovery.defined:
            raise InvalidObject("missing required member Recovery")
        if not self.Administrator.defined:
            raise InvalidObject("missing required member Administrator")
        if not self.Key.defined:
            raise InvalidObject("missing required member Key")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.FullName.encode(engine, UserEnums.FullName)
        self.UserName.encode(engine, UserEnums.UserName)
        self.Password.encode(engine, UserEnums.Password)
        self.Country.encode(engine, UserEnums.Country)
        self.Email.encode(engine, UserEnums.Email)
        self.Recovery.encode(engine, UserEnums.Recovery)
        self.Administrator.encode(engine, UserEnums.Administrator)
        self.Key.encode(engine, UserEnums.Key)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for User")

        if UserEnums.FullName.s in data:
            self.FullName.decode_value(data[UserEnums.FullName.s])
        if UserEnums.UserName.s in data:
            self.UserName.decode_value(data[UserEnums.UserName.s])
        if UserEnums.Password.s in data:
            self.Password.decode_value(data[UserEnums.Password.s])
        if UserEnums.Country.s in data:
            self.Country.decode_value(data[UserEnums.Country.s])
        if UserEnums.Email.s in data:
            self.Email.decode_value(data[UserEnums.Email.s])
        if UserEnums.Recovery.s in data:
            self.Recovery.decode_value(data[UserEnums.Recovery.s])
        if UserEnums.Administrator.s in data:
            self.Administrator.decode_value(data[UserEnums.Administrator.s])
        if UserEnums.Key.s in data:
            self.Key.decode_value(data[UserEnums.Key.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> UserValue:
        o = UserValue()
        o.FullName = self.FullName.clone()
        o.UserName = self.UserName.clone()
        o.Password = self.Password.clone()
        o.Country = self.Country.clone()
        o.Email = self.Email.clone()
        o.Recovery = self.Recovery.clone()
        o.Administrator = self.Administrator.clone()
        o.Key = self.Key.clone()
        o.PasswordTime = self.PasswordTime.clone()
        o.RecoveryTime = self.RecoveryTime.clone()
        o.PasswordErrors = self.PasswordErrors.clone()
        return o


class User:
    """Optional object type: User."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: UserValue = UserValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> UserValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: UserValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: UserValue | None = None) -> UserValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_FullName(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FullName

    def set_FullName(self, v: Any) -> None:
        assert self._defined, "User must be defined before setting FullName"
        _assign_value(self._value.FullName, v)

    def get_UserName(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.UserName

    def set_UserName(self, v: Any) -> None:
        assert self._defined, "User must be defined before setting UserName"
        _assign_value(self._value.UserName, v)

    def get_Password(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Password

    def set_Password(self, v: Any) -> None:
        assert self._defined, "User must be defined before setting Password"
        _assign_value(self._value.Password, v)

    def get_Country(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Country

    def set_Country(self, v: Any) -> None:
        assert self._defined, "User must be defined before setting Country"
        _assign_value(self._value.Country, v)

    def get_Email(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Email

    def set_Email(self, v: Any) -> None:
        assert self._defined, "User must be defined before setting Email"
        _assign_value(self._value.Email, v)

    def get_Recovery(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Recovery

    def set_Recovery(self, v: Any) -> None:
        assert self._defined, "User must be defined before setting Recovery"
        _assign_value(self._value.Recovery, v)

    def get_Administrator(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Administrator

    def set_Administrator(self, v: Any) -> None:
        assert self._defined, "User must be defined before setting Administrator"
        _assign_value(self._value.Administrator, v)

    def get_Key(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Key

    def set_Key(self, v: Any) -> None:
        assert self._defined, "User must be defined before setting Key"
        _assign_value(self._value.Key, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = UserValue()

    def clone(self) -> User:
        o = User()
        o._defined = self._defined
        o._value = self._value.clone()
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._defined:
            self._value.encode(engine, name)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        self._value.decode(engine, data)
        self._defined = True

    def decode_value(self, data: Any) -> None:
        """Decode from a parent dict value. Creates minimal engine context."""
        self._value.decode(JsonEngine(), data)
        self._defined = True

    def __repr__(self) -> str:
        if self._defined:
            return f"User(defined)"
        return "User(<undefined>)"


def make_user_value(v: UserValue) -> UserValue:
    """Factory: create a UserValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_user(v: UserValue) -> User:
    """Factory: create a defined User from a UserValue."""
    o = User()
    o.set_value(v)
    return o

