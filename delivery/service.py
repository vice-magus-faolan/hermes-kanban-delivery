# SPDX-License-Identifier: GPL-3.0-or-later
"""One application path for CLI and model queries."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from . import context, policy, store
from .values import QueryError

OPERATIONS = {"delivery_policy_check": {"board"}, "delivery_context": {"board", "task_id"}}


class Queries:
    def __init__(self, native_context):
        from hermes_constants import get_hermes_home
        self.native_context = native_context
        self.profile_home = get_hermes_home().resolve()

    def query(self, name: str, params: object) -> dict:
        try:
            result = self._query(name, params)
            if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 32768:
                raise QueryError("response_limit")
            return result
        except QueryError as exc:
            return {"ok": False, "error": str(exc)}
        except (OSError, sqlite3.Error):
            return {"ok": False, "error": "native_store_unavailable"}
        except Exception:
            # A malformed/unexpected native shape is not approval or a traceback.
            return {"ok": False, "error": "query_unavailable"}

    def _query(self, name: str, params: object) -> dict:
        if name not in OPERATIONS or not isinstance(params, dict) or not set(params) <= OPERATIONS[name]:
            raise QueryError("invalid_arguments")
        allowed = self.native_context.get_config("allowed_boards", None)
        home, board, task_id, run_id = store.selection(params, self.profile_home, allowed)
        if name == "delivery_context" and task_id is None:
            raise QueryError("task_required")
        filename = store.database_path(home, board)
        # Validate worker pins before disclosing policy, even for policy-only calls.
        if run_id is not None:
            with store.snapshot(filename) as conn:
                store.worker_check(conn, task_id, run_id)
        selected_policy = read_policy(home, board)
        result = {"ok": True, "schema_version": 1, "board": board, "policy": selected_policy,
                  "authority": "observation_only", "effect_clearance": "not_established"}
        if name == "delivery_context":
            result["task_id"] = task_id
            result["context"] = read_context(filename, task_id, run_id, selected_policy)
        return result

    def tool(self, name: str):
        def handler(args: dict, **kwargs) -> str:
            return json.dumps(self.query(name, args), sort_keys=True, ensure_ascii=False)
        return handler


def read_policy(home: Path, board: str) -> dict:
    filename = home / "kanban" / "delivery-policy.json"
    if filename.resolve() != filename:
        raise QueryError("policy_path_fence")
    try:
        return policy.load(home, board)
    except QueryError as exc:
        return {"state": "invalid", "diagnostics": [str(exc)]}


def read_context(filename: Path, task_id: str, run_id: int | None, selected_policy: dict) -> dict:
    with store.snapshot(filename) as conn:
        store.worker_check(conn, task_id, run_id)
        task = store.task_row(conn, task_id)
        try:
            runs, events = store.history(conn, task_id)
        except QueryError as exc:
            return {"phase": "unknown", "artifact": None, "review": {"state": "unknown"},
                    "diagnostics": [str(exc)]}
        return context.project(task, runs, events, selected_policy)
