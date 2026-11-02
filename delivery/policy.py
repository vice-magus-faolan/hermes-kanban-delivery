# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate only the selected v2 board; never infer or persist defaults."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

from .values import PROFILE, QueryError, literal, path, text

MAX_POLICY_BYTES = 1024 * 1024
EFFECT_FIELDS = ("push", "deployment", "restart", "live_evaluation")
EFFECT_VALUES = {"operator-required", "forbidden", "disabled", "manual", "allowed"}


def review_config(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"required", "allowed_profiles"}:
        raise QueryError("review_policy_invalid")
    required, profiles = value["required"], value["allowed_profiles"]
    if type(required) is not bool or not isinstance(profiles, list) or len(profiles) > 32:
        raise QueryError("review_policy_invalid")
    if any(not literal(p, PROFILE) for p in profiles) or len(set(profiles)) != len(profiles):
        raise QueryError("review_policy_invalid")
    if bool(profiles) != required:
        raise QueryError("review_policy_invalid")
    return {"required": required, "allowed_profiles": list(profiles)}


def full_ref(value: object) -> str:
    value = text(value, 256)
    forbidden = ("..", "@{", "//", "\\", " ", "~", "^", ":", "?", "*", "[")
    if not value or not value.startswith("refs/") or any(p in value for p in forbidden):
        raise QueryError("integration_target_ref_invalid")
    if any(not p or p.startswith(".") or p.endswith((".", ".lock")) for p in value.split("/")):
        raise QueryError("integration_target_ref_invalid")
    return value


def ssh_config(value: object) -> dict:
    if not isinstance(value, dict):
        raise QueryError("ssh_invalid")
    host, user, port = text(value.get("host"), 253), text(value.get("user"), 64), value.get("port", 22)
    if not host or not user or any(c.isspace() for c in host + user):
        raise QueryError("ssh_invalid")
    if type(port) is not int or not 1 <= port <= 65535:
        raise QueryError("ssh_invalid")
    return {"host": host, "user": user, "port": port}


def board_config(config: object) -> dict:
    if not isinstance(config, dict) or config.get("mode") not in ("git", "none", "unconfigured"):
        raise QueryError("board_policy_invalid")
    integration = config.get("local_target_integration", True)
    if type(integration) is not bool:
        raise QueryError("local_target_integration_invalid")
    result = {"mode": config["mode"]}
    if config["mode"] != "git":
        return result
    if config.get("transport") not in ("local", "ssh") or not path(config.get("repository")):
        raise QueryError("source_policy_invalid")
    result.update(transport=config["transport"], repository=config["repository"],
                  integration_target_ref=full_ref(config.get("integration_target_ref")),
                  local_target_integration=config.get("local_target_integration"), review=review_config(config.get("review")))
    if config["transport"] == "ssh":
        result["ssh"] = ssh_config(config.get("ssh"))
    return result


def effect_view(config: dict, defaults: object) -> dict:
    """Only known categorical restrictions; commands/free prose never escape."""
    defaults = defaults if isinstance(defaults, dict) else {}
    result = {}
    for key in EFFECT_FIELDS:
        value = config.get(key, defaults.get(key))
        result[key] = value if isinstance(value, str) and value in EFFECT_VALUES else "unknown"
    return result


def load(home: Path, board: str) -> dict:
    filename = home / "kanban" / "delivery-policy.json"
    try:
        # Nonblocking open lets us reject a FIFO without waiting for a writer.
        descriptor = os.open(filename, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return {"state": "missing", "diagnostics": ["policy_missing"]}
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise QueryError("policy_not_regular")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_POLICY_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_POLICY_BYTES:
        raise QueryError("policy_size_limit")
    try:
        policy = json.loads(raw)
    except (ValueError, UnicodeError):
        raise QueryError("policy_malformed") from None
    if not isinstance(policy, dict) or type(policy.get("policy_version")) is not int or policy["policy_version"] != 2:
        raise QueryError("policy_version_invalid")
    boards = policy.get("boards")
    if not isinstance(boards, dict):
        raise QueryError("policy_boards_invalid")
    if board not in boards:
        return {"state": "unconfigured", "diagnostics": ["board_policy_missing"]}
    selected = board_config(boards[board])
    selected["effects"] = effect_view(boards[board], policy.get("defaults"))
    state = {"git": "valid", "none": "not_applicable", "unconfigured": "unconfigured"}[selected["mode"]]
    return {"state": state, "policy_version": 2, "config": selected, "diagnostics": []}
