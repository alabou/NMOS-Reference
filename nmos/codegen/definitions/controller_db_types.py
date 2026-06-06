# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Type definitions auto-generated from Go source."""

from __future__ import annotations

from nmos.codegen.descriptors import MemberDesc, TypeDesc

user = TypeDesc(
    package="controllerDB",
    name="User",
    members=[
        MemberDesc(name="FullName", type_name="NString", json_key="fullname"),
        MemberDesc(name="UserName", type_name="NString", json_key="username"),
        MemberDesc(name="Password", type_name="NString", json_key="password"),
        MemberDesc(name="Country", type_name="NString", json_key="country"),
        MemberDesc(name="Email", type_name="NString", json_key="email"),
        MemberDesc(name="Recovery", type_name="NString", json_key="recovery"),
        MemberDesc(name="Administrator", type_name="NBool", json_key="administrator"),
        MemberDesc(name="Key", type_name="NString", json_key="key"),
        MemberDesc(name="PasswordTime", type_name="NInt", json_key="-"),
        MemberDesc(name="RecoveryTime", type_name="NInt", json_key="-"),
        MemberDesc(name="PasswordErrors", type_name="NInt", json_key="-"),
    ],
)

node = TypeDesc(
    package="controllerDB",
    name="Node",
    members=[
        MemberDesc(name="Manufacturer", type_name="NString", json_key="manufacturer"),
        MemberDesc(name="Product", type_name="NString", json_key="product"),
        MemberDesc(name="SerialNumber", type_name="NString", json_key="sn"),
        MemberDesc(name="AuthorizedUsers", type_name="NArrayOfString", json_key="authorized_users"),
    ],
)

ALL_TYPES = [
    user,
    node,
]

