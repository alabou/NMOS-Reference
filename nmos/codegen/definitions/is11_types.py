# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Type definitions auto-generated from Go source."""

from __future__ import annotations

from nmos.codegen.descriptors import MemberDesc, TypeDesc

ninput = TypeDesc(
    package="nmos",
    name="NInput",
    members=[
        MemberDesc(name="ResourceCore", type_name="NResourceCore", embedded=True),
        MemberDesc(name="Connected", type_name="NBool", json_key="connected"),
        MemberDesc(name="EdidSupport", type_name="NBool", json_key="edid_support"),
        MemberDesc(name="Status", type_name="NInputStatus", json_key="status"),
        MemberDesc(name="SourceId", type_name="NString", json_key="source_id", optional=True, assertion="CheckResourceIdString"),
        MemberDesc(name="DeviceId", type_name="NString", json_key="device_id", assertion="CheckResourceIdString"),
        MemberDesc(name="Device", type_name="NDevicePtr", json_key="-"),
    ],
)

narray_of_input = TypeDesc(
    package="nmos",
    name="NArrayOfInput",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NInputValue]", json_key="-"),
    ],
)

ninput_status = TypeDesc(
    package="nmos",
    name="NInputStatus",
    members=[
        MemberDesc(name="State", type_name="NEnum", json_key="state", assertion="CheckInputStatusState"),
        MemberDesc(name="Debug", type_name="NNullString", json_key="debug", optional=True),
    ],
)

noutput = TypeDesc(
    package="nmos",
    name="NOutput",
    members=[
        MemberDesc(name="ResourceCore", type_name="NResourceCore", embedded=True),
        MemberDesc(name="Connected", type_name="NBool", json_key="connected"),
        MemberDesc(name="EdidSupport", type_name="NBool", json_key="edid_support"),
        MemberDesc(name="Status", type_name="NOutputStatus", json_key="status"),
        MemberDesc(name="SourceId", type_name="NString", json_key="source_id", optional=True, assertion="CheckResourceIdString"),
        MemberDesc(name="DeviceId", type_name="NString", json_key="device_id", optional=True, assertion="CheckResourceIdString"),
        MemberDesc(name="Device", type_name="NDevicePtr", json_key="-"),
    ],
)

narray_of_output = TypeDesc(
    package="nmos",
    name="NArrayOfOutput",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NOutputValue]", json_key="-"),
    ],
)

noutput_status = TypeDesc(
    package="nmos",
    name="NOutputStatus",
    members=[
        MemberDesc(name="State", type_name="NEnum", json_key="state", assertion="CheckOutputStatusState"),
        MemberDesc(name="Debug", type_name="NNullString", json_key="debug", optional=True),
    ],
)

nsender_status = TypeDesc(
    package="nmos",
    name="NSenderStatus",
    members=[
        MemberDesc(name="State", type_name="NEnum", json_key="state", assertion="CheckSenderStatusState"),
        MemberDesc(name="Debug", type_name="NNullString", json_key="debug", optional=True),
    ],
)

nreceiver_status = TypeDesc(
    package="nmos",
    name="NReceiverStatus",
    members=[
        MemberDesc(name="State", type_name="NEnum", json_key="state", assertion="CheckReceiverStatusState"),
        MemberDesc(name="Debug", type_name="NNullString", json_key="debug", optional=True),
    ],
)

nsender_supported_constraints = TypeDesc(
    package="nmos",
    name="NSenderSupportedConstraints",
    members=[
        MemberDesc(name="ParameterConstraints", type_name="NArrayOfEnum", json_key="parameter_constraints"),
    ],
)

nsender_active_constraints = TypeDesc(
    package="nmos",
    name="NSenderActiveConstraints",
    members=[
        MemberDesc(name="ConstraintSets", type_name="NArrayOfConstraintSet", json_key="constraint_sets"),
    ],
)

ALL_TYPES = [
    ninput,
    narray_of_input,
    ninput_status,
    noutput,
    narray_of_output,
    noutput_status,
    nsender_status,
    nreceiver_status,
    nsender_supported_constraints,
    nsender_active_constraints,
]

