"""Generated NMOS type: NRtpSenderActivation. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NBool
from nmos.types.generated.nactivation import NActivation, NActivationValue
from nmos.types.generated.narray_of_rtp_sender_transport_params import NArrayOfRtpSenderTransportParams, NArrayOfRtpSenderTransportParamsValue
from nmos.validators import CheckResourceIdNullableString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NRtpSenderActivationEnums:
    """JSON property name enums for NRtpSenderActivation."""
    ReceiverId = EnumRegistry.get("receiver_id")
    MasterEnable = EnumRegistry.get("master_enable")
    Activation = EnumRegistry.get("activation")
    TransportParams = EnumRegistry.get("transport_params")
    pass


class NRtpSenderActivationValue:
    """Inner value struct for NRtpSenderActivation."""

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
        self.TransportParams: NArrayOfRtpSenderTransportParams = NArrayOfRtpSenderTransportParams()

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
        self.ReceiverId.encode(engine, NRtpSenderActivationEnums.ReceiverId)
        self.MasterEnable.encode(engine, NRtpSenderActivationEnums.MasterEnable)
        self.Activation.encode(engine, NRtpSenderActivationEnums.Activation)
        self.TransportParams.encode(engine, NRtpSenderActivationEnums.TransportParams)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NRtpSenderActivation")

        if NRtpSenderActivationEnums.ReceiverId.s in data:
            self.ReceiverId.decode_value(data[NRtpSenderActivationEnums.ReceiverId.s])
        if NRtpSenderActivationEnums.MasterEnable.s in data:
            self.MasterEnable.decode_value(data[NRtpSenderActivationEnums.MasterEnable.s])
        if NRtpSenderActivationEnums.Activation.s in data:
            self.Activation.decode_value(data[NRtpSenderActivationEnums.Activation.s])
        if NRtpSenderActivationEnums.TransportParams.s in data:
            self.TransportParams.decode_value(data[NRtpSenderActivationEnums.TransportParams.s])

        # Sealed type: reject unknown keys
        _known_keys = {
            NRtpSenderActivationEnums.ReceiverId.s,
            NRtpSenderActivationEnums.MasterEnable.s,
            NRtpSenderActivationEnums.Activation.s,
            NRtpSenderActivationEnums.TransportParams.s,
        }
        for key in data:
            if key not in _known_keys:
                raise InvalidData(f"unknown property '{key}' in sealed type NRtpSenderActivation")

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NRtpSenderActivationValue:
        o = NRtpSenderActivationValue()
        o.ReceiverId = self.ReceiverId.clone()
        o.MasterEnable = self.MasterEnable.clone()
        o.Activation = self.Activation.clone()
        o.TransportParams = self.TransportParams.clone()
        return o


class NRtpSenderActivation:
    """Optional object type: NRtpSenderActivation."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NRtpSenderActivationValue = NRtpSenderActivationValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NRtpSenderActivationValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NRtpSenderActivationValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NRtpSenderActivationValue | None = None) -> NRtpSenderActivationValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ReceiverId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ReceiverId

    def set_ReceiverId(self, v: Any) -> None:
        assert self._defined, "NRtpSenderActivation must be defined before setting ReceiverId"
        _assign_value(self._value.ReceiverId, v)

    def get_MasterEnable(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MasterEnable

    def set_MasterEnable(self, v: Any) -> None:
        assert self._defined, "NRtpSenderActivation must be defined before setting MasterEnable"
        _assign_value(self._value.MasterEnable, v)

    def get_Activation(self) -> NActivation:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Activation

    def set_Activation(self, v: Any) -> None:
        assert self._defined, "NRtpSenderActivation must be defined before setting Activation"
        _assign_value(self._value.Activation, v)

    def get_TransportParams(self) -> NArrayOfRtpSenderTransportParams:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransportParams

    def set_TransportParams(self, v: Any) -> None:
        assert self._defined, "NRtpSenderActivation must be defined before setting TransportParams"
        _assign_value(self._value.TransportParams, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NRtpSenderActivationValue()

    def clone(self) -> NRtpSenderActivation:
        o = NRtpSenderActivation()
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
            return f"NRtpSenderActivation(defined)"
        return "NRtpSenderActivation(<undefined>)"


def make_nrtpsenderactivation_value(v: NRtpSenderActivationValue) -> NRtpSenderActivationValue:
    """Factory: create a NRtpSenderActivationValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nrtpsenderactivation(v: NRtpSenderActivationValue) -> NRtpSenderActivation:
    """Factory: create a defined NRtpSenderActivation from a NRtpSenderActivationValue."""
    o = NRtpSenderActivation()
    o.set_value(v)
    return o

