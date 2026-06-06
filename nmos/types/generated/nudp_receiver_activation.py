"""Generated NMOS type: NUdpReceiverActivation. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NBool
from nmos.types.generated.nactivation import NActivation, NActivationValue
from nmos.types.generated.ntransport_file import NTransportFile, NTransportFileValue
from nmos.types.generated.narray_of_udp_receiver_transport_params import NArrayOfUdpReceiverTransportParams, NArrayOfUdpReceiverTransportParamsValue
from nmos.validators import CheckResourceIdNullableString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NUdpReceiverActivationEnums:
    """JSON property name enums for NUdpReceiverActivation."""
    SenderId = EnumRegistry.get("sender_id")
    MasterEnable = EnumRegistry.get("master_enable")
    Activation = EnumRegistry.get("activation")
    TransportFile = EnumRegistry.get("transport_file")
    TransportParams = EnumRegistry.get("transport_params")
    pass


class NUdpReceiverActivationValue:
    """Inner value struct for NUdpReceiverActivation."""

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
        self.TransportParams: NArrayOfUdpReceiverTransportParams = NArrayOfUdpReceiverTransportParams()

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
        self.SenderId.encode(engine, NUdpReceiverActivationEnums.SenderId)
        self.MasterEnable.encode(engine, NUdpReceiverActivationEnums.MasterEnable)
        self.Activation.encode(engine, NUdpReceiverActivationEnums.Activation)
        self.TransportFile.encode(engine, NUdpReceiverActivationEnums.TransportFile)
        self.TransportParams.encode(engine, NUdpReceiverActivationEnums.TransportParams)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NUdpReceiverActivation")

        if NUdpReceiverActivationEnums.SenderId.s in data:
            self.SenderId.decode_value(data[NUdpReceiverActivationEnums.SenderId.s])
        if NUdpReceiverActivationEnums.MasterEnable.s in data:
            self.MasterEnable.decode_value(data[NUdpReceiverActivationEnums.MasterEnable.s])
        if NUdpReceiverActivationEnums.Activation.s in data:
            self.Activation.decode_value(data[NUdpReceiverActivationEnums.Activation.s])
        if NUdpReceiverActivationEnums.TransportFile.s in data:
            self.TransportFile.decode_value(data[NUdpReceiverActivationEnums.TransportFile.s])
        if NUdpReceiverActivationEnums.TransportParams.s in data:
            self.TransportParams.decode_value(data[NUdpReceiverActivationEnums.TransportParams.s])

        # Sealed type: reject unknown keys
        _known_keys = {
            NUdpReceiverActivationEnums.SenderId.s,
            NUdpReceiverActivationEnums.MasterEnable.s,
            NUdpReceiverActivationEnums.Activation.s,
            NUdpReceiverActivationEnums.TransportFile.s,
            NUdpReceiverActivationEnums.TransportParams.s,
        }
        for key in data:
            if key not in _known_keys:
                raise InvalidData(f"unknown property '{key}' in sealed type NUdpReceiverActivation")

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NUdpReceiverActivationValue:
        o = NUdpReceiverActivationValue()
        o.SenderId = self.SenderId.clone()
        o.MasterEnable = self.MasterEnable.clone()
        o.Activation = self.Activation.clone()
        o.TransportFile = self.TransportFile.clone()
        o.TransportParams = self.TransportParams.clone()
        return o


class NUdpReceiverActivation:
    """Optional object type: NUdpReceiverActivation."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NUdpReceiverActivationValue = NUdpReceiverActivationValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NUdpReceiverActivationValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NUdpReceiverActivationValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NUdpReceiverActivationValue | None = None) -> NUdpReceiverActivationValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SenderId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SenderId

    def set_SenderId(self, v: Any) -> None:
        assert self._defined, "NUdpReceiverActivation must be defined before setting SenderId"
        _assign_value(self._value.SenderId, v)

    def get_MasterEnable(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MasterEnable

    def set_MasterEnable(self, v: Any) -> None:
        assert self._defined, "NUdpReceiverActivation must be defined before setting MasterEnable"
        _assign_value(self._value.MasterEnable, v)

    def get_Activation(self) -> NActivation:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Activation

    def set_Activation(self, v: Any) -> None:
        assert self._defined, "NUdpReceiverActivation must be defined before setting Activation"
        _assign_value(self._value.Activation, v)

    def get_TransportFile(self) -> NTransportFile:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransportFile

    def set_TransportFile(self, v: Any) -> None:
        assert self._defined, "NUdpReceiverActivation must be defined before setting TransportFile"
        _assign_value(self._value.TransportFile, v)

    def get_TransportParams(self) -> NArrayOfUdpReceiverTransportParams:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransportParams

    def set_TransportParams(self, v: Any) -> None:
        assert self._defined, "NUdpReceiverActivation must be defined before setting TransportParams"
        _assign_value(self._value.TransportParams, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NUdpReceiverActivationValue()

    def clone(self) -> NUdpReceiverActivation:
        o = NUdpReceiverActivation()
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
            return f"NUdpReceiverActivation(defined)"
        return "NUdpReceiverActivation(<undefined>)"


def make_nudpreceiveractivation_value(v: NUdpReceiverActivationValue) -> NUdpReceiverActivationValue:
    """Factory: create a NUdpReceiverActivationValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nudpreceiveractivation(v: NUdpReceiverActivationValue) -> NUdpReceiverActivation:
    """Factory: create a defined NUdpReceiverActivation from a NUdpReceiverActivationValue."""
    o = NUdpReceiverActivation()
    o.set_value(v)
    return o

