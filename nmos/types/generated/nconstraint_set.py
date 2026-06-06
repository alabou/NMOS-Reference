"""Generated NMOS type: NConstraintSet. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NEnum, NInt, NBool, NArrayOfInt
from nmos.types.generated.nconstraints import NConstraints, NConstraintsValue
from nmos.validators import CheckConstraintSetPreference, CheckConstraintsLength

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NConstraintSetEnums:
    """JSON property name enums for NConstraintSet."""
    MetaLabel = EnumRegistry.get("urn:x-nmos:cap:meta:label")
    MetaFormat = EnumRegistry.get("urn:x-matrox:cap:meta:format")
    MetaLayer = EnumRegistry.get("urn:x-matrox:cap:meta:layer")
    MetaLayerEnabled = EnumRegistry.get("urn:x-matrox:cap:meta:layer_enabled")
    MetaLayerCompatibilityGroups = EnumRegistry.get("urn:x-matrox:cap:meta:layer_compatibility_groups")
    MetaEnabled = EnumRegistry.get("urn:x-nmos:cap:meta:enabled")
    MetaPreference = EnumRegistry.get("urn:x-nmos:cap:meta:preference")
    MetaInfoBlock = EnumRegistry.get("urn:x-matrox:cap:meta:info_block")
    pass


class NConstraintSetValue:
    """Inner value struct for NConstraintSet."""

    __slots__ = (
        "MetaLabel",
        "MetaFormat",
        "MetaLayer",
        "MetaLayerEnabled",
        "MetaLayerCompatibilityGroups",
        "MetaEnabled",
        "MetaPreference",
        "MetaInfoBlock",
        "Constraints",
    )

    def __init__(self) -> None:
        self.MetaLabel: NString = NString()
        self.MetaFormat: NEnum = NEnum()
        self.MetaLayer: NInt = NInt()
        self.MetaLayerEnabled: NBool = NBool()
        self.MetaLayerCompatibilityGroups: NArrayOfInt = NArrayOfInt()
        self.MetaEnabled: NBool = NBool()
        self.MetaPreference: NInt = NInt()
        self.MetaInfoBlock: NArrayOfInt = NArrayOfInt()
        self.Constraints: NConstraintsValue = NConstraintsValue()

    def set_to_default(self) -> None:
        _assign_value(self.MetaEnabled, True)
        _assign_value(self.MetaPreference, 0)
        self.Constraints = NConstraintsValue()
        self.Constraints.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        if not self.MetaEnabled.defined:
            _assign_value(self.MetaEnabled, True)
        if not self.MetaPreference.defined:
            _assign_value(self.MetaPreference, 0)
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.MetaPreference.defined:
            CheckConstraintSetPreference(self.MetaPreference)
        self.Constraints.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.MetaLabel.encode(engine, NConstraintSetEnums.MetaLabel)
        self.MetaFormat.encode(engine, NConstraintSetEnums.MetaFormat)
        self.MetaLayer.encode(engine, NConstraintSetEnums.MetaLayer)
        self.MetaLayerEnabled.encode(engine, NConstraintSetEnums.MetaLayerEnabled)
        self.MetaLayerCompatibilityGroups.encode(engine, NConstraintSetEnums.MetaLayerCompatibilityGroups)
        self.MetaEnabled.encode(engine, NConstraintSetEnums.MetaEnabled)
        self.MetaPreference.encode(engine, NConstraintSetEnums.MetaPreference)
        self.MetaInfoBlock.encode(engine, NConstraintSetEnums.MetaInfoBlock)
        self.Constraints.encode(engine, None)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NConstraintSet")

        if NConstraintSetEnums.MetaLabel.s in data:
            self.MetaLabel.decode_value(data[NConstraintSetEnums.MetaLabel.s])
        if NConstraintSetEnums.MetaFormat.s in data:
            self.MetaFormat.decode_value(data[NConstraintSetEnums.MetaFormat.s])
        if NConstraintSetEnums.MetaLayer.s in data:
            self.MetaLayer.decode_value(data[NConstraintSetEnums.MetaLayer.s])
        if NConstraintSetEnums.MetaLayerEnabled.s in data:
            self.MetaLayerEnabled.decode_value(data[NConstraintSetEnums.MetaLayerEnabled.s])
        if NConstraintSetEnums.MetaLayerCompatibilityGroups.s in data:
            self.MetaLayerCompatibilityGroups.decode_value(data[NConstraintSetEnums.MetaLayerCompatibilityGroups.s])
        if NConstraintSetEnums.MetaEnabled.s in data:
            self.MetaEnabled.decode_value(data[NConstraintSetEnums.MetaEnabled.s])
        if NConstraintSetEnums.MetaPreference.s in data:
            self.MetaPreference.decode_value(data[NConstraintSetEnums.MetaPreference.s])
        if NConstraintSetEnums.MetaInfoBlock.s in data:
            self.MetaInfoBlock.decode_value(data[NConstraintSetEnums.MetaInfoBlock.s])
        self.Constraints.decode(engine, data)

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NConstraintSetValue:
        o = NConstraintSetValue()
        o.MetaLabel = self.MetaLabel.clone()
        o.MetaFormat = self.MetaFormat.clone()
        o.MetaLayer = self.MetaLayer.clone()
        o.MetaLayerEnabled = self.MetaLayerEnabled.clone()
        o.MetaLayerCompatibilityGroups = self.MetaLayerCompatibilityGroups.clone()
        o.MetaEnabled = self.MetaEnabled.clone()
        o.MetaPreference = self.MetaPreference.clone()
        o.MetaInfoBlock = self.MetaInfoBlock.clone()
        o.Constraints = self.Constraints.clone()
        return o


class NConstraintSet:
    """Optional object type: NConstraintSet."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NConstraintSetValue = NConstraintSetValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NConstraintSetValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NConstraintSetValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NConstraintSetValue | None = None) -> NConstraintSetValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_MetaLabel(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MetaLabel

    def set_MetaLabel(self, v: Any) -> None:
        assert self._defined, "NConstraintSet must be defined before setting MetaLabel"
        _assign_value(self._value.MetaLabel, v)

    def get_MetaFormat(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MetaFormat

    def set_MetaFormat(self, v: Any) -> None:
        assert self._defined, "NConstraintSet must be defined before setting MetaFormat"
        _assign_value(self._value.MetaFormat, v)

    def get_MetaLayer(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MetaLayer

    def set_MetaLayer(self, v: Any) -> None:
        assert self._defined, "NConstraintSet must be defined before setting MetaLayer"
        _assign_value(self._value.MetaLayer, v)

    def get_MetaLayerEnabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MetaLayerEnabled

    def set_MetaLayerEnabled(self, v: Any) -> None:
        assert self._defined, "NConstraintSet must be defined before setting MetaLayerEnabled"
        _assign_value(self._value.MetaLayerEnabled, v)

    def get_MetaLayerCompatibilityGroups(self) -> NArrayOfInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MetaLayerCompatibilityGroups

    def set_MetaLayerCompatibilityGroups(self, v: Any) -> None:
        assert self._defined, "NConstraintSet must be defined before setting MetaLayerCompatibilityGroups"
        _assign_value(self._value.MetaLayerCompatibilityGroups, v)

    def get_MetaEnabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MetaEnabled

    def set_MetaEnabled(self, v: Any) -> None:
        assert self._defined, "NConstraintSet must be defined before setting MetaEnabled"
        _assign_value(self._value.MetaEnabled, v)

    def get_MetaPreference(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MetaPreference

    def set_MetaPreference(self, v: Any) -> None:
        assert self._defined, "NConstraintSet must be defined before setting MetaPreference"
        _assign_value(self._value.MetaPreference, v)

    def get_MetaInfoBlock(self) -> NArrayOfInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MetaInfoBlock

    def set_MetaInfoBlock(self, v: Any) -> None:
        assert self._defined, "NConstraintSet must be defined before setting MetaInfoBlock"
        _assign_value(self._value.MetaInfoBlock, v)

    def get_Constraints(self) -> NConstraintsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Constraints

    def set_Constraints(self, v: NConstraintsValue) -> None:
        assert self._defined, "NConstraintSet must be defined before setting Constraints"
        self._value.Constraints = v.clone()  # copy to match Go's value semantics


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NConstraintSetValue()

    def clone(self) -> NConstraintSet:
        o = NConstraintSet()
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
            return f"NConstraintSet(defined)"
        return "NConstraintSet(<undefined>)"


def make_nconstraintset_value(v: NConstraintSetValue) -> NConstraintSetValue:
    """Factory: create a NConstraintSetValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nconstraintset(v: NConstraintSetValue) -> NConstraintSet:
    """Factory: create a defined NConstraintSet from a NConstraintSetValue."""
    o = NConstraintSet()
    o.set_value(v)
    return o

