"""Generated NMOS type: NWebSocketSenderActivation. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NBool
from nmos.types.generated.nactivation import NActivation, NActivationValue
from nmos.types.generated.narray_of_web_socket_sender_transport_params import NArrayOfWebSocketSenderTransportParams, NArrayOfWebSocketSenderTransportParamsValue
from nmos.validators import CheckResourceIdNullableString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NWebSocketSenderActivationEnums:
    """JSON property name enums for NWebSocketSenderActivation."""
    ReceiverId = EnumRegistry.get("receiver_id")
    MasterEnable = EnumRegistry.get("master_enable")
    Activation = EnumRegistry.get("activation")
    TransportParams = EnumRegistry.get("transport_params")
    pass


class NWebSocketSenderActivationValue:
    """Inner value struct for NWebSocketSenderActivation."""

    __slots__ = (
        "ReceiverId",
        "MasterEnable",
        "Activation",
        "TransportParams",
    )

    def __init__(self) -> None:
        self.ReceiverId: NNullString = NNullString()
        self.MasterEnable: NBool = NBool()
        self.Activation: NActivation = NActivation()
        self.TransportParams: NArrayOfWebSocketSenderTransportParams = NArrayOfWebSocketSenderTransportParams()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.ReceiverId.defined:
            CheckResourceIdNullableString(self.ReceiverId)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.ReceiverId.encode(engine, NWebSocketSenderActivationEnums.ReceiverId)
        self.MasterEnable.encode(engine, NWebSocketSenderActivationEnums.MasterEnable)
        self.Activation.encode(engine, NWebSocketSenderActivationEnums.Activation)
        self.TransportParams.encode(engine, NWebSocketSenderActivationEnums.TransportParams)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NWebSocketSenderActivation")

        if NWebSocketSenderActivationEnums.ReceiverId.s in data:
            self.ReceiverId.decode_value(data[NWebSocketSenderActivationEnums.ReceiverId.s])
        if NWebSocketSenderActivationEnums.MasterEnable.s in data:
            self.MasterEnable.decode_value(data[NWebSocketSenderActivationEnums.MasterEnable.s])
        if NWebSocketSenderActivationEnums.Activation.s in data:
            self.Activation.decode_value(data[NWebSocketSenderActivationEnums.Activation.s])
        if NWebSocketSenderActivationEnums.TransportParams.s in data:
            self.TransportParams.decode_value(data[NWebSocketSenderActivationEnums.TransportParams.s])

        # Sealed type: reject unknown keys
        _known_keys = {
            NWebSocketSenderActivationEnums.ReceiverId.s,
            NWebSocketSenderActivationEnums.MasterEnable.s,
            NWebSocketSenderActivationEnums.Activation.s,
            NWebSocketSenderActivationEnums.TransportParams.s,
        }
        for key in data:
            if key not in _known_keys:
                raise InvalidData(f"unknown property '{key}' in sealed type NWebSocketSenderActivation")

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NWebSocketSenderActivationValue:
        o = NWebSocketSenderActivationValue()
        o.ReceiverId = self.ReceiverId.clone()
        o.MasterEnable = self.MasterEnable.clone()
        o.Activation = self.Activation.clone()
        o.TransportParams = self.TransportParams.clone()
        return o


class NWebSocketSenderActivation:
    """Optional object type: NWebSocketSenderActivation."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NWebSocketSenderActivationValue = NWebSocketSenderActivationValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NWebSocketSenderActivationValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NWebSocketSenderActivationValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NWebSocketSenderActivationValue | None = None) -> NWebSocketSenderActivationValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ReceiverId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ReceiverId

    def set_ReceiverId(self, v: Any) -> None:
        assert self._defined, "NWebSocketSenderActivation must be defined before setting ReceiverId"
        _assign_value(self._value.ReceiverId, v)

    def get_MasterEnable(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MasterEnable

    def set_MasterEnable(self, v: Any) -> None:
        assert self._defined, "NWebSocketSenderActivation must be defined before setting MasterEnable"
        _assign_value(self._value.MasterEnable, v)

    def get_Activation(self) -> NActivation:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Activation

    def set_Activation(self, v: Any) -> None:
        assert self._defined, "NWebSocketSenderActivation must be defined before setting Activation"
        _assign_value(self._value.Activation, v)

    def get_TransportParams(self) -> NArrayOfWebSocketSenderTransportParams:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransportParams

    def set_TransportParams(self, v: Any) -> None:
        assert self._defined, "NWebSocketSenderActivation must be defined before setting TransportParams"
        _assign_value(self._value.TransportParams, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NWebSocketSenderActivationValue()

    def clone(self) -> NWebSocketSenderActivation:
        o = NWebSocketSenderActivation()
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
            return f"NWebSocketSenderActivation(defined)"
        return "NWebSocketSenderActivation(<undefined>)"


def make_nwebsocketsenderactivation_value(v: NWebSocketSenderActivationValue) -> NWebSocketSenderActivationValue:
    """Factory: create a NWebSocketSenderActivationValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nwebsocketsenderactivation(v: NWebSocketSenderActivationValue) -> NWebSocketSenderActivation:
    """Factory: create a defined NWebSocketSenderActivation from a NWebSocketSenderActivationValue."""
    o = NWebSocketSenderActivation()
    o.set_value(v)
    return o

