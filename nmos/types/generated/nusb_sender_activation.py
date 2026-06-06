"""Generated NMOS type: NUsbSenderActivation. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NBool
from nmos.types.generated.nactivation import NActivation, NActivationValue
from nmos.types.generated.narray_of_usb_sender_transport_params import NArrayOfUsbSenderTransportParams, NArrayOfUsbSenderTransportParamsValue
from nmos.validators import CheckResourceIdNullableString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NUsbSenderActivationEnums:
    """JSON property name enums for NUsbSenderActivation."""
    ReceiverId = EnumRegistry.get("receiver_id")
    MasterEnable = EnumRegistry.get("master_enable")
    Activation = EnumRegistry.get("activation")
    TransportParams = EnumRegistry.get("transport_params")
    pass


class NUsbSenderActivationValue:
    """Inner value struct for NUsbSenderActivation."""

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
        self.TransportParams: NArrayOfUsbSenderTransportParams = NArrayOfUsbSenderTransportParams()

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
        self.ReceiverId.encode(engine, NUsbSenderActivationEnums.ReceiverId)
        self.MasterEnable.encode(engine, NUsbSenderActivationEnums.MasterEnable)
        self.Activation.encode(engine, NUsbSenderActivationEnums.Activation)
        self.TransportParams.encode(engine, NUsbSenderActivationEnums.TransportParams)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NUsbSenderActivation")

        if NUsbSenderActivationEnums.ReceiverId.s in data:
            self.ReceiverId.decode_value(data[NUsbSenderActivationEnums.ReceiverId.s])
        if NUsbSenderActivationEnums.MasterEnable.s in data:
            self.MasterEnable.decode_value(data[NUsbSenderActivationEnums.MasterEnable.s])
        if NUsbSenderActivationEnums.Activation.s in data:
            self.Activation.decode_value(data[NUsbSenderActivationEnums.Activation.s])
        if NUsbSenderActivationEnums.TransportParams.s in data:
            self.TransportParams.decode_value(data[NUsbSenderActivationEnums.TransportParams.s])

        # Sealed type: reject unknown keys
        _known_keys = {
            NUsbSenderActivationEnums.ReceiverId.s,
            NUsbSenderActivationEnums.MasterEnable.s,
            NUsbSenderActivationEnums.Activation.s,
            NUsbSenderActivationEnums.TransportParams.s,
        }
        for key in data:
            if key not in _known_keys:
                raise InvalidData(f"unknown property '{key}' in sealed type NUsbSenderActivation")

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NUsbSenderActivationValue:
        o = NUsbSenderActivationValue()
        o.ReceiverId = self.ReceiverId.clone()
        o.MasterEnable = self.MasterEnable.clone()
        o.Activation = self.Activation.clone()
        o.TransportParams = self.TransportParams.clone()
        return o


class NUsbSenderActivation:
    """Optional object type: NUsbSenderActivation."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NUsbSenderActivationValue = NUsbSenderActivationValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NUsbSenderActivationValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NUsbSenderActivationValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NUsbSenderActivationValue | None = None) -> NUsbSenderActivationValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ReceiverId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ReceiverId

    def set_ReceiverId(self, v: Any) -> None:
        assert self._defined, "NUsbSenderActivation must be defined before setting ReceiverId"
        _assign_value(self._value.ReceiverId, v)

    def get_MasterEnable(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MasterEnable

    def set_MasterEnable(self, v: Any) -> None:
        assert self._defined, "NUsbSenderActivation must be defined before setting MasterEnable"
        _assign_value(self._value.MasterEnable, v)

    def get_Activation(self) -> NActivation:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Activation

    def set_Activation(self, v: Any) -> None:
        assert self._defined, "NUsbSenderActivation must be defined before setting Activation"
        _assign_value(self._value.Activation, v)

    def get_TransportParams(self) -> NArrayOfUsbSenderTransportParams:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransportParams

    def set_TransportParams(self, v: Any) -> None:
        assert self._defined, "NUsbSenderActivation must be defined before setting TransportParams"
        _assign_value(self._value.TransportParams, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NUsbSenderActivationValue()

    def clone(self) -> NUsbSenderActivation:
        o = NUsbSenderActivation()
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
            return f"NUsbSenderActivation(defined)"
        return "NUsbSenderActivation(<undefined>)"


def make_nusbsenderactivation_value(v: NUsbSenderActivationValue) -> NUsbSenderActivationValue:
    """Factory: create a NUsbSenderActivationValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nusbsenderactivation(v: NUsbSenderActivationValue) -> NUsbSenderActivation:
    """Factory: create a defined NUsbSenderActivation from a NUsbSenderActivationValue."""
    o = NUsbSenderActivation()
    o.set_value(v)
    return o

