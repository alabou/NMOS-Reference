"""Generated NMOS type: NSrtReceiverActivation. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NBool
from nmos.types.generated.nactivation import NActivation, NActivationValue
from nmos.types.generated.ntransport_file import NTransportFile, NTransportFileValue
from nmos.types.generated.narray_of_srt_receiver_transport_params import NArrayOfSrtReceiverTransportParams, NArrayOfSrtReceiverTransportParamsValue
from nmos.validators import CheckResourceIdNullableString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NSrtReceiverActivationEnums:
    """JSON property name enums for NSrtReceiverActivation."""
    SenderId = EnumRegistry.get("sender_id")
    MasterEnable = EnumRegistry.get("master_enable")
    Activation = EnumRegistry.get("activation")
    TransportFile = EnumRegistry.get("transport_file")
    TransportParams = EnumRegistry.get("transport_params")
    pass


class NSrtReceiverActivationValue:
    """Inner value struct for NSrtReceiverActivation."""

    __slots__ = (
        "SenderId",
        "MasterEnable",
        "Activation",
        "TransportFile",
        "TransportParams",
    )

    def __init__(self) -> None:
        self.SenderId: NNullString = NNullString()
        self.MasterEnable: NBool = NBool()
        self.Activation: NActivation = NActivation()
        self.TransportFile: NTransportFile = NTransportFile()
        self.TransportParams: NArrayOfSrtReceiverTransportParams = NArrayOfSrtReceiverTransportParams()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.SenderId.defined:
            CheckResourceIdNullableString(self.SenderId)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SenderId.encode(engine, NSrtReceiverActivationEnums.SenderId)
        self.MasterEnable.encode(engine, NSrtReceiverActivationEnums.MasterEnable)
        self.Activation.encode(engine, NSrtReceiverActivationEnums.Activation)
        self.TransportFile.encode(engine, NSrtReceiverActivationEnums.TransportFile)
        self.TransportParams.encode(engine, NSrtReceiverActivationEnums.TransportParams)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NSrtReceiverActivation")

        if NSrtReceiverActivationEnums.SenderId.s in data:
            self.SenderId.decode_value(data[NSrtReceiverActivationEnums.SenderId.s])
        if NSrtReceiverActivationEnums.MasterEnable.s in data:
            self.MasterEnable.decode_value(data[NSrtReceiverActivationEnums.MasterEnable.s])
        if NSrtReceiverActivationEnums.Activation.s in data:
            self.Activation.decode_value(data[NSrtReceiverActivationEnums.Activation.s])
        if NSrtReceiverActivationEnums.TransportFile.s in data:
            self.TransportFile.decode_value(data[NSrtReceiverActivationEnums.TransportFile.s])
        if NSrtReceiverActivationEnums.TransportParams.s in data:
            self.TransportParams.decode_value(data[NSrtReceiverActivationEnums.TransportParams.s])

        # Sealed type: reject unknown keys
        _known_keys = {
            NSrtReceiverActivationEnums.SenderId.s,
            NSrtReceiverActivationEnums.MasterEnable.s,
            NSrtReceiverActivationEnums.Activation.s,
            NSrtReceiverActivationEnums.TransportFile.s,
            NSrtReceiverActivationEnums.TransportParams.s,
        }
        for key in data:
            if key not in _known_keys:
                raise InvalidData(f"unknown property '{key}' in sealed type NSrtReceiverActivation")

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NSrtReceiverActivationValue:
        o = NSrtReceiverActivationValue()
        o.SenderId = self.SenderId.clone()
        o.MasterEnable = self.MasterEnable.clone()
        o.Activation = self.Activation.clone()
        o.TransportFile = self.TransportFile.clone()
        o.TransportParams = self.TransportParams.clone()
        return o


class NSrtReceiverActivation:
    """Optional object type: NSrtReceiverActivation."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSrtReceiverActivationValue = NSrtReceiverActivationValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NSrtReceiverActivationValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NSrtReceiverActivationValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NSrtReceiverActivationValue | None = None) -> NSrtReceiverActivationValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SenderId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SenderId

    def set_SenderId(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverActivation must be defined before setting SenderId"
        _assign_value(self._value.SenderId, v)

    def get_MasterEnable(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MasterEnable

    def set_MasterEnable(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverActivation must be defined before setting MasterEnable"
        _assign_value(self._value.MasterEnable, v)

    def get_Activation(self) -> NActivation:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Activation

    def set_Activation(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverActivation must be defined before setting Activation"
        _assign_value(self._value.Activation, v)

    def get_TransportFile(self) -> NTransportFile:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransportFile

    def set_TransportFile(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverActivation must be defined before setting TransportFile"
        _assign_value(self._value.TransportFile, v)

    def get_TransportParams(self) -> NArrayOfSrtReceiverTransportParams:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransportParams

    def set_TransportParams(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverActivation must be defined before setting TransportParams"
        _assign_value(self._value.TransportParams, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSrtReceiverActivationValue()

    def clone(self) -> NSrtReceiverActivation:
        o = NSrtReceiverActivation()
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
            return f"NSrtReceiverActivation(defined)"
        return "NSrtReceiverActivation(<undefined>)"


def make_nsrtreceiveractivation_value(v: NSrtReceiverActivationValue) -> NSrtReceiverActivationValue:
    """Factory: create a NSrtReceiverActivationValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nsrtreceiveractivation(v: NSrtReceiverActivationValue) -> NSrtReceiverActivation:
    """Factory: create a defined NSrtReceiverActivation from a NSrtReceiverActivationValue."""
    o = NSrtReceiverActivation()
    o.set_value(v)
    return o

