# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the registry benchmark harness.

The harness is committed and held to the same standard as the rest of the
repository for the reason ``pyproject.toml`` gives: its numbers are cited, and
a performance claim nobody can re-run is not a claim. A benchmark that reports
a wrong number confidently is worse than no benchmark, so the arithmetic that
turns samples into rates is tested rather than eyeballed.
"""
