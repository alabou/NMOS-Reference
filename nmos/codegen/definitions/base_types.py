# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Type definitions auto-generated from Go source."""

from __future__ import annotations

from nmos.codegen.descriptors import MemberDesc, TypeDesc

nbool = TypeDesc(
    package="nmos",
    name="NBool",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="bool", json_key="-"),
    ],
)

nstring = TypeDesc(
    package="nmos",
    name="NString",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="str", json_key="-"),
    ],
)

nhyperlink = TypeDesc(
    package="nmos",
    name="NHyperlink",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="tuple[str, str]", json_key="-"),
    ],
)

nint = TypeDesc(
    package="nmos",
    name="NInt",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="int", json_key="-"),
    ],
)

nfloat = TypeDesc(
    package="nmos",
    name="NFloat",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="float", json_key="-"),
    ],
)

nnull = TypeDesc(
    package="nmos",
    name="NNull",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="object", json_key="-"),
    ],
)

nnull_string = TypeDesc(
    package="nmos",
    name="NNullString",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="object", json_key="-"),
    ],
)

nenum = TypeDesc(
    package="nmos",
    name="NEnum",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="EnumId", json_key="-"),
    ],
)

nurl = TypeDesc(
    package="nmos",
    name="NUrl",
    is_value=True,
    members=[
        MemberDesc(name="value", type_name="str", json_key="-"),
    ],
)

ntime = TypeDesc(
    package="nmos",
    name="NTime",
    is_value=True,
    members=[
        MemberDesc(name="value", type_name="datetime", json_key="-"),
    ],
)

narray_of_bool = TypeDesc(
    package="nmos",
    name="NArrayOfBool",
    is_value=True,
    is_array_values=True,
    members=[
        MemberDesc(name="value", type_name="list[bool]", json_key="-"),
    ],
)

narray_of_string = TypeDesc(
    package="nmos",
    name="NArrayOfString",
    is_value=True,
    is_array_values=True,
    members=[
        MemberDesc(name="value", type_name="list[str]", json_key="-"),
    ],
)

narray_of_hyperlink = TypeDesc(
    package="nmos",
    name="NArrayOfHyperlink",
    is_value=True,
    is_array_values=True,
    members=[
        MemberDesc(name="value", type_name="list[tuple[str, str]]", json_key="-"),
    ],
)

narray_of_int = TypeDesc(
    package="nmos",
    name="NArrayOfInt",
    is_value=True,
    is_array_values=True,
    members=[
        MemberDesc(name="value", type_name="list[int]", json_key="-"),
    ],
)

narray_of_float = TypeDesc(
    package="nmos",
    name="NArrayOfFloat",
    is_value=True,
    is_array_values=True,
    members=[
        MemberDesc(name="value", type_name="list[float]", json_key="-"),
    ],
)

narray_of_null = TypeDesc(
    package="nmos",
    name="NArrayOfNull",
    is_value=True,
    members=[
        MemberDesc(name="value", type_name="list[object]", json_key="-"),
    ],
)

narray_of_null_string = TypeDesc(
    package="nmos",
    name="NArrayOfNullString",
    is_value=True,
    members=[
        MemberDesc(name="value", type_name="list[object]", json_key="-"),
    ],
)

narray_of_enum = TypeDesc(
    package="nmos",
    name="NArrayOfEnum",
    is_value=True,
    is_array_values=True,
    members=[
        MemberDesc(name="value", type_name="list[EnumId]", json_key="-"),
    ],
)

narray_of_url = TypeDesc(
    package="nmos",
    name="NArrayOfUrl",
    is_value=True,
    is_array_values=True,
    members=[
        MemberDesc(name="value", type_name="list[str]", json_key="-"),
    ],
)

narray_of_time = TypeDesc(
    package="nmos",
    name="NArrayOfTime",
    is_value=True,
    is_array_values=True,
    members=[
        MemberDesc(name="value", type_name="list[datetime]", json_key="-"),
    ],
)

ntags = TypeDesc(
    package="nmos",
    name="NTags",
    is_value=True,
    members=[
        MemberDesc(name="value", type_name="dict[str, list[str]]", json_key="-"),
    ],
)

ngeneric = TypeDesc(
    package="nmos",
    name="NGeneric",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="object", json_key="-"),
    ],
)

narray_of_generic = TypeDesc(
    package="nmos",
    name="NArrayOfGeneric",
    is_value=True,
    members=[
        MemberDesc(name="value", type_name="list[object]", json_key="-"),
    ],
)

ALL_TYPES = [
    nbool,
    nstring,
    nhyperlink,
    nint,
    nfloat,
    nnull,
    nnull_string,
    nenum,
    nurl,
    ntime,
    narray_of_bool,
    narray_of_string,
    narray_of_hyperlink,
    narray_of_int,
    narray_of_float,
    narray_of_null,
    narray_of_null_string,
    narray_of_enum,
    narray_of_url,
    narray_of_time,
    ntags,
    ngeneric,
    narray_of_generic,
]

