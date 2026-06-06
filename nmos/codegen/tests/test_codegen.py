# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.codegen -- code generator."""

from __future__ import annotations

import importlib
import json as stdlib_json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from nmos.codegen.descriptors import MemberDesc, TypeDesc
from nmos.codegen.generator import generate_type
from nmos.enums import EnumRegistry
from nmos.json.engine import JsonEngine


@pytest.fixture(autouse=True)
def _clean_registry() -> None:  # type: ignore[misc]
    """Save and restore enum registry around each test."""
    saved = dict(EnumRegistry._entries)
    yield  # type: ignore[misc]
    EnumRegistry._entries.clear()
    EnumRegistry._entries.update(saved)


def _load_generated_module(filepath: str, module_name: str) -> Any:
    """Dynamically import a generated Python file."""
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestDescriptorValidation:
    """TypeDesc.validate() catches invalid configurations."""

    def test_value_type_must_have_one_member_named_value(self) -> None:
        desc = TypeDesc(
            package="test",
            name="BadValue",
            is_value=True,
            members=[MemberDesc(name="x", type_name="int")],
        )
        with pytest.raises(ValueError, match="one member named 'value'"):
            desc.validate()

    def test_value_type_cannot_be_embedded(self) -> None:
        desc = TypeDesc(
            package="test",
            name="BadValue",
            is_value=True,
            is_embedded=True,
            members=[MemberDesc(name="value", type_name="int")],
        )
        with pytest.raises(ValueError, match="cannot be embedded"):
            desc.validate()

    def test_valid_object_type_passes(self) -> None:
        desc = TypeDesc(
            package="test",
            name="GoodObject",
            members=[
                MemberDesc(name="Name", type_name="NString", json_key="name"),
            ],
        )
        desc.validate()  # should not raise


class TestGenerateObjectType:
    """Generate a simple object type and verify it works end-to-end."""

    def test_generate_and_use_simple_object(self) -> None:
        desc = TypeDesc(
            package="nmos",
            name="NError",
            members=[
                MemberDesc(name="Code", type_name="NInt", json_key="code"),
                MemberDesc(
                    name="Error", type_name="NString", json_key="error",
                ),
                MemberDesc(
                    name="Debug", type_name="NNullString", json_key="debug",
                    optional=True,
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = generate_type(tmpdir, desc)
            assert Path(filepath).exists()

            # Read the generated file to verify it's valid Python
            source = Path(filepath).read_text()
            assert "class NErrorValue:" in source
            assert "class NError:" in source
            assert "class NErrorEnums:" in source

            # Load and use the generated module
            mod = _load_generated_module(filepath, "test_n_error")

            # Create an instance and set values
            obj = mod.NError()
            obj.set_to_default()
            assert obj.defined

            obj.value.Code.value = 404
            obj.value.Error.value = "Not Found"

            # Encode to JSON
            engine = JsonEngine()
            engine.reset()
            obj.encode(engine, None)
            output = engine.get_output()
            result = stdlib_json.loads(output)

            assert result["code"] == 404
            assert result["error"] == "Not Found"

    def test_generate_and_decode(self) -> None:
        desc = TypeDesc(
            package="nmos",
            name="NRational",
            members=[
                MemberDesc(name="Numerator", type_name="NInt", json_key="numerator"),
                MemberDesc(name="Denominator", type_name="NInt", json_key="denominator"),
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = generate_type(tmpdir, desc)
            mod = _load_generated_module(filepath, "test_n_rational")

            # Decode from JSON
            engine = JsonEngine()
            obj = mod.NRational()
            obj.decode(engine, {"numerator": 30000, "denominator": 1001})

            assert obj.defined
            assert obj.value.Numerator.value == 30000
            assert obj.value.Denominator.value == 1001

    def test_generate_with_optional_and_default(self) -> None:
        desc = TypeDesc(
            package="nmos",
            name="NSimple",
            members=[
                MemberDesc(name="Label", type_name="NString", json_key="label"),
                MemberDesc(
                    name="Description", type_name="NString", json_key="description",
                    optional=True, default='"default desc"',
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = generate_type(tmpdir, desc)
            mod = _load_generated_module(filepath, "test_n_simple")

            # Decode with missing optional field
            engine = JsonEngine()
            obj = mod.NSimple()
            obj.decode(engine, {"label": "test"})

            assert obj.value.Label.value == "test"
            # Optional with default should be set
            assert obj.value.Description.value == "default desc"

    def test_roundtrip_encode_decode(self) -> None:
        desc = TypeDesc(
            package="nmos",
            name="NPoint",
            members=[
                MemberDesc(name="X", type_name="NInt", json_key="x"),
                MemberDesc(name="Y", type_name="NInt", json_key="y"),
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = generate_type(tmpdir, desc)
            mod = _load_generated_module(filepath, "test_n_point")

            # Create and encode
            obj1 = mod.NPoint()
            obj1.set_to_default()
            obj1.value.X.value = 100
            obj1.value.Y.value = 200

            engine = JsonEngine()
            engine.reset()
            obj1.encode(engine, None)
            json_str = engine.get_output()

            # Decode into a new object
            parsed = stdlib_json.loads(json_str)
            obj2 = mod.NPoint()
            obj2.decode(JsonEngine(), parsed)

            assert obj2.value.X.value == 100
            assert obj2.value.Y.value == 200

    def test_clone(self) -> None:
        desc = TypeDesc(
            package="nmos",
            name="NTag",
            members=[
                MemberDesc(name="Key", type_name="NString", json_key="key"),
                MemberDesc(name="Value", type_name="NString", json_key="value"),
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = generate_type(tmpdir, desc)
            mod = _load_generated_module(filepath, "test_n_tag")

            obj = mod.NTag()
            obj.set_to_default()
            obj.value.Key.value = "genre"
            obj.value.Value.value = "drama"

            cloned = obj.clone()
            cloned.value.Key.value = "changed"

            assert obj.value.Key.value == "genre"  # original unchanged
            assert cloned.value.Key.value == "changed"

    def test_sealed_type_rejects_unknown_keys(self) -> None:
        desc = TypeDesc(
            package="nmos",
            name="NStrict",
            is_sealed=True,
            members=[
                MemberDesc(name="Id", type_name="NString", json_key="id"),
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = generate_type(tmpdir, desc)
            mod = _load_generated_module(filepath, "test_n_strict")

            obj = mod.NStrict()
            with pytest.raises(Exception, match="unknown property"):
                obj.decode(JsonEngine(), {"id": "abc", "extra": "bad"})

    def test_convenience_accessors(self) -> None:
        desc = TypeDesc(
            package="nmos",
            name="NItem",
            members=[
                MemberDesc(name="Name", type_name="NString", json_key="name"),
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = generate_type(tmpdir, desc)
            mod = _load_generated_module(filepath, "test_n_item")

            obj = mod.NItem()
            obj.set_to_default()
            obj.set_Name("hello")
            assert obj.get_Name().value == "hello"

    def test_generated_factory_functions(self) -> None:
        desc = TypeDesc(
            package="nmos",
            name="NCoord",
            members=[
                MemberDesc(name="X", type_name="NInt", json_key="x"),
                MemberDesc(name="Y", type_name="NInt", json_key="y"),
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = generate_type(tmpdir, desc)
            mod = _load_generated_module(filepath, "test_n_coord")

            # Create via make_ncoord_value
            val = mod.NCoordValue()
            val.X.value = 10
            val.Y.value = 20
            val = mod.make_ncoord_value(val)

            # Create via make_ncoord
            obj = mod.make_ncoord(val)
            assert obj.defined
            assert obj.value.X.value == 10
            assert obj.value.Y.value == 20
