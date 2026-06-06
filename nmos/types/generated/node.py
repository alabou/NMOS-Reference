"""Generated NMOS type: Node. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NArrayOfString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NodeEnums:
    """JSON property name enums for Node."""
    Manufacturer = EnumRegistry.get("manufacturer")
    Product = EnumRegistry.get("product")
    SerialNumber = EnumRegistry.get("sn")
    AuthorizedUsers = EnumRegistry.get("authorized_users")
    pass


class NodeValue:
    """Inner value struct for Node."""

    __slots__ = (
        "Manufacturer",
        "Product",
        "SerialNumber",
        "AuthorizedUsers",
    )

    def __init__(self) -> None:
        self.Manufacturer: NString = NString()
        self.Product: NString = NString()
        self.SerialNumber: NString = NString()
        self.AuthorizedUsers: NArrayOfString = NArrayOfString()

    def set_to_default(self) -> None:
        self.Manufacturer.set_to_default()
        self.Product.set_to_default()
        self.SerialNumber.set_to_default()
        self.AuthorizedUsers.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Manufacturer.defined:
            raise InvalidObject("missing required member Manufacturer")
        if not self.Product.defined:
            raise InvalidObject("missing required member Product")
        if not self.SerialNumber.defined:
            raise InvalidObject("missing required member SerialNumber")
        if not self.AuthorizedUsers.defined:
            raise InvalidObject("missing required member AuthorizedUsers")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Manufacturer.encode(engine, NodeEnums.Manufacturer)
        self.Product.encode(engine, NodeEnums.Product)
        self.SerialNumber.encode(engine, NodeEnums.SerialNumber)
        self.AuthorizedUsers.encode(engine, NodeEnums.AuthorizedUsers)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for Node")

        if NodeEnums.Manufacturer.s in data:
            self.Manufacturer.decode_value(data[NodeEnums.Manufacturer.s])
        if NodeEnums.Product.s in data:
            self.Product.decode_value(data[NodeEnums.Product.s])
        if NodeEnums.SerialNumber.s in data:
            self.SerialNumber.decode_value(data[NodeEnums.SerialNumber.s])
        if NodeEnums.AuthorizedUsers.s in data:
            self.AuthorizedUsers.decode_value(data[NodeEnums.AuthorizedUsers.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NodeValue:
        o = NodeValue()
        o.Manufacturer = self.Manufacturer.clone()
        o.Product = self.Product.clone()
        o.SerialNumber = self.SerialNumber.clone()
        o.AuthorizedUsers = self.AuthorizedUsers.clone()
        return o


class Node:
    """Optional object type: Node."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NodeValue = NodeValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NodeValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NodeValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NodeValue | None = None) -> NodeValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Manufacturer(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Manufacturer

    def set_Manufacturer(self, v: Any) -> None:
        assert self._defined, "Node must be defined before setting Manufacturer"
        _assign_value(self._value.Manufacturer, v)

    def get_Product(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Product

    def set_Product(self, v: Any) -> None:
        assert self._defined, "Node must be defined before setting Product"
        _assign_value(self._value.Product, v)

    def get_SerialNumber(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SerialNumber

    def set_SerialNumber(self, v: Any) -> None:
        assert self._defined, "Node must be defined before setting SerialNumber"
        _assign_value(self._value.SerialNumber, v)

    def get_AuthorizedUsers(self) -> NArrayOfString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AuthorizedUsers

    def set_AuthorizedUsers(self, v: Any) -> None:
        assert self._defined, "Node must be defined before setting AuthorizedUsers"
        _assign_value(self._value.AuthorizedUsers, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NodeValue()

    def clone(self) -> Node:
        o = Node()
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
            return f"Node(defined)"
        return "Node(<undefined>)"


def make_node_value(v: NodeValue) -> NodeValue:
    """Factory: create a NodeValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_node(v: NodeValue) -> Node:
    """Factory: create a defined Node from a NodeValue."""
    o = Node()
    o.set_value(v)
    return o

