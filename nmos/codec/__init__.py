# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Codec profile and level specification tables for NMOS codec validation.

Pure data modules — no NMOS type dependencies. Sub-modules:

- aac   — AAC audio codec profiles, levels, and object types
- h264  — H.264/AVC video codec profiles and levels
- h265  — H.265/HEVC video codec profiles and levels
- jxsv  — JPEG XS video codec profiles, levels, and sublevels

These tables are consumed by nmos.node.codec for profile/level validation
and automatic level selection.
"""
