"""Generated NMOS type: NNdiSenderActivation. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NBool
from nmos.types.generated.nactivation import NActivation, NActivationValue
from nmos.types.generated.narray_of_ndi_sender_transport_params import NArrayOfNdiSenderTransportParams, NArrayOfNdiSenderTransportParamsValue
from nmos.validators import CheckResourceIdNullableString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NNdiSenderActivationEnums:
    """JSON property name enums for NNdiSenderActivation."""
    ReceiverId = EnumRegistry.get("receiver_id")
    MasterEnable = EnumRegistry.get("master_enable")
    Activation = EnumRegistry.get("activation")
    TransportParams = EnumRegistry.get("transport_params")
    pass


class NNdiSenderActivationValue:
    """Inner value struct for NNdiSenderActivation."""

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
        self.TransportParams: NArrayOfNdiSenderTransportParams = NArrayOfNdiSenderTransportParams()

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
        self.ReceiverId.encode(engine, NNdiSenderActivationEnums.ReceiverId)
        self.MasterEnable.encode(engine, NNdiSenderActivationEnums.MasterEnable)
        self.Activation.encode(engine, NNdiSenderActivationEnums.Activation)
        self.TransportParams.encode(engine, NNdiSenderActivationEnums.TransportParams)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NNdiSenderActivation")

        if NNdiSenderActivationEnums.ReceiverId.s in data:
            self.ReceiverId.decode_value(data[NNdiSenderActivationEnums.ReceiverId.s])
        if NNdiSenderActivationEnums.MasterEnable.s in data:
            self.MasterEnable.decode_value(data[NNdiSenderActivationEnums.MasterEnable.s])
        if NNdiSenderActivationEnums.Activation.s in data:
            self.Activation.decode_value(data[NNdiSenderActivationEnums.Activation.s])
        if NNdiSenderActivationEnums.TransportParams.s in data:
            self.TransportParams.decode_value(data[NNdiSenderActivationEnums.TransportParams.s])

        # Sealed type: reject unknown keys
        _known_keys = {
            NNdiSenderActivationEnums.ReceiverId.s,
            NNdiSenderActivationEnums.MasterEnable.s,
            NNdiSenderActivationEnums.Activation.s,
            NNdiSenderActivationEnums.TransportParams.s,
        }
        for key in data:
            if key not in _known_keys:
                raise InvalidData(f"unknown property '{key}' in sealed type NNdiSenderActivation")

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NNdiSenderActivationValue:
        o = NNdiSenderActivationValue()
        o.ReceiverId = self.ReceiverId.clone()
        o.MasterEnable = self.MasterEnable.clone()
        o.Activation = self.Activation.clone()
        o.TransportParams = self.TransportParams.clone()
        return o


class NNdiSenderActivation:
    """Optional object type: NNdiSenderActivation."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NNdiSenderActivationValue = NNdiSenderActivationValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NNdiSenderActivationValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NNdiSenderActivationValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NNdiSenderActivationValue | None = None) -> NNdiSenderActivationValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ReceiverId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ReceiverId

    def set_ReceiverId(self, v: Any) -> None:
        assert self._defined, "NNdiSenderActivation must be defined before setting ReceiverId"
        _assign_value(self._value.ReceiverId, v)

    def get_MasterEnable(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MasterEnable

    def set_MasterEnable(self, v: Any) -> None:
        assert self._defined, "NNdiSenderActivation must be defined before setting MasterEnable"
        _assign_value(self._value.MasterEnable, v)

    def get_Activation(self) -> NActivation:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Activation

    def set_Activation(self, v: Any) -> None:
        assert self._defined, "NNdiSenderActivation must be defined before setting Activation"
        _assign_value(self._value.Activation, v)

    def get_TransportParams(self) -> NArrayOfNdiSenderTransportParams:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransportParams

    def set_TransportParams(self, v: Any) -> None:
        assert self._defined, "NNdiSenderActivation must be defined before setting TransportParams"
        _assign_value(self._value.TransportParams, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NNdiSenderActivationValue()

    def clone(self) -> NNdiSenderActivation:
        o = NNdiSenderActivation()
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
            return f"NNdiSenderActivation(defined)"
        return "NNdiSenderActivation(<undefined>)"


def make_nndisenderactivation_value(v: NNdiSenderActivationValue) -> NNdiSenderActivationValue:
    """Factory: create a NNdiSenderActivationValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nndisenderactivation(v: NNdiSenderActivationValue) -> NNdiSenderActivation:
    """Factory: create a defined NNdiSenderActivation from a NNdiSenderActivationValue."""
    o = NNdiSenderActivation()
    o.set_value(v)
    return o

