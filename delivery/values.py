# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded literal values at the observation boundary; never normalize identity."""
from __future__ import annotations

import json
import re
from typing import TypeGuard

BOARD = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\Z")
PROFILE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
TASK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class QueryError(ValueError):
    """Code-authored diagnostics only; no exception text crosses the boundary."""


def literal(value: object, pattern: re.Pattern) -> TypeGuard[str]:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def text(value: object, limit: int = 512) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= limit and not CONTROL.search(value):
        return value
    return None


def path(value: object) -> str | None:
    location = text(value, 1024)
    if location and location.startswith("/") and all(p not in {"", ".", ".."} for p in location[1:].split("/")):
        return location
    return None


def object_json(raw: object) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, str) or len(raw) > 65536:
        raise QueryError("native_metadata_unavailable")
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise QueryError("native_metadata_unavailable") from None
    if not isinstance(value, dict):
        raise QueryError("native_metadata_unavailable")
    return value


def profile(value: object) -> str | None:
    return value if literal(value, PROFILE) else None
