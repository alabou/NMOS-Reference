# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Type definitions auto-generated from Go source."""

from __future__ import annotations

from nmos.codegen.descriptors import MemberDesc, TypeDesc

nrational = TypeDesc(
    package="nmos",
    name="NRational",
    members=[
        MemberDesc(name="Numerator", type_name="NInt", json_key="numerator"),
        MemberDesc(name="Denominator", type_name="NInt", json_key="denominator", optional=True, default='1'),
    ],
)

narray_of_rational = TypeDesc(
    package="nmos",
    name="NArrayOfRational",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NRationalValue]", json_key="-"),
    ],
)

nconstraints = TypeDesc(
    package="nmos",
    name="NConstraints",
    is_value=True,
    members=[
        MemberDesc(name="value", type_name="dict[EnumId, NConstraint]", json_key="-"),
    ],
)

nconstraint_int = TypeDesc(
    package="nmos",
    name="NConstraintInt",
    members=[
        MemberDesc(name="Enum", type_name="NArrayOfInt", json_key="enum", optional=True),
        MemberDesc(name="Minimum", type_name="NInt", json_key="minimum", optional=True),
        MemberDesc(name="Maximum", type_name="NInt", json_key="maximum", optional=True),
        MemberDesc(name="Original", type_name="NBool", json_key="-", optional=True),
    ],
)

nconstraint_bool = TypeDesc(
    package="nmos",
    name="NConstraintBool",
    members=[
        MemberDesc(name="Enum", type_name="NArrayOfBool", json_key="enum", optional=True),
        MemberDesc(name="Original", type_name="NBool", json_key="-", optional=True),
    ],
)

nconstraint_string = TypeDesc(
    package="nmos",
    name="NConstraintString",
    members=[
        MemberDesc(name="Enum", type_name="NArrayOfEnum", json_key="enum", optional=True),
        MemberDesc(name="Original", type_name="NBool", json_key="-", optional=True),
    ],
)

nconstraint_float = TypeDesc(
    package="nmos",
    name="NConstraintFloat",
    members=[
        MemberDesc(name="Enum", type_name="NArrayOfFloat", json_key="enum", optional=True),
        MemberDesc(name="Minimum", type_name="NFloat", json_key="minimum", optional=True),
        MemberDesc(name="Maximum", type_name="NFloat", json_key="maximum", optional=True),
        MemberDesc(name="Original", type_name="NBool", json_key="-", optional=True),
    ],
)

nconstraint_rational = TypeDesc(
    package="nmos",
    name="NConstraintRational",
    members=[
        MemberDesc(name="Enum", type_name="NArrayOfRational", json_key="enum", optional=True),
        MemberDesc(name="Minimum", type_name="NRational", json_key="minimum", optional=True),
        MemberDesc(name="Maximum", type_name="NRational", json_key="maximum", optional=True),
        MemberDesc(name="Original", type_name="NBool", json_key="-", optional=True),
    ],
)

nconstraint = TypeDesc(
    package="nmos",
    name="NConstraint",
    is_value=True,
    is_base=True,
    poly_types=['NConstraintBool', 'NConstraintInt', 'NConstraintFloat', 'NConstraintString', 'NConstraintRational'],
    members=[
        MemberDesc(name="value", type_name="object", json_key="-"),
    ],
)

ntransport_constraints = TypeDesc(
    package="nmos",
    name="NTransportConstraints",
    is_value=True,
    members=[
        MemberDesc(name="value", type_name="dict[EnumId, NTransportConstraint]", json_key="-"),
    ],
)

ntransport_constraint = TypeDesc(
    package="nmos",
    name="NTransportConstraint",
    members=[
        MemberDesc(name="Minimum", type_name="NFloat", json_key="minimum", optional=True),
        MemberDesc(name="Maximum", type_name="NFloat", json_key="maximum", optional=True),
        MemberDesc(name="Enum", type_name="NArrayOfNull", json_key="enum", optional=True, assertion="CheckTransportConstraintEnumLength"),
        MemberDesc(name="Pattern", type_name="NString", json_key="pattern", optional=True),
        MemberDesc(name="Description", type_name="NString", json_key="description", optional=True),
    ],
)

nexclusive_acquire = TypeDesc(
    package="nmos",
    name="NExclusiveAcquire",
    members=[
        MemberDesc(name="Owner", type_name="NString", json_key="owner"),
        MemberDesc(name="ExclusiveKey", type_name="NString", json_key="exclusive_key"),
    ],
)

ALL_TYPES = [
    nrational,
    narray_of_rational,
    nconstraints,
    nconstraint_int,
    nconstraint_bool,
    nconstraint_string,
    nconstraint_float,
    nconstraint_rational,
    nconstraint,
    ntransport_constraints,
    ntransport_constraint,
    nexclusive_acquire,
]

