# SPDX-License-Identifier: GPL-3.0-or-later
"""Narrow compatibility reader for stock Hermes 0.22 native storage.

The public connect/show paths initialize stores and render unrelated prose.
This adapter opens only canonical existing stores, with no schema repair, and
selects bounded task-local columns in one snapshot. It is not a lifecycle API.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3

from .values import BOARD, TASK, QueryError, literal, object_json

PINS = ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_DB")
MAX_RUNS = 128
MAX_EVENTS = 512
EVENT_KINDS = ("claimed", "review_requested", "changes_requested", "completed")


def selection(params: dict, bound_home: Path, allowed: object) -> tuple[Path, str, str | None, int | None]:
    from hermes_constants import get_default_hermes_root, get_hermes_home

    active_home = get_hermes_home().resolve()
    if active_home != bound_home:
        raise QueryError("profile_fence")
    home = get_default_hermes_root(home=active_home).resolve()
    if os.environ.get("HERMES_KANBAN_HOME") and Path(os.environ["HERMES_KANBAN_HOME"]).resolve() != home:
        raise QueryError("store_fence")
    pinned = any(key in os.environ for key in PINS)
    board, task_id, run_id = params.get("board"), params.get("task_id"), None
    if pinned:
        board, task_id, run_id = worker_inputs(board, task_id)
    if not literal(board, BOARD):
        raise QueryError("board_required")
    if task_id is not None and not literal(task_id, TASK):
        raise QueryError("task_invalid")
    check_allowlist(board, allowed)
    return home, board, task_id, run_id


def worker_inputs(board: object, task_id: object) -> tuple[str, str, int]:
    from agent.delegation_context import is_dispatcher_owned_worker_context

    if not is_dispatcher_owned_worker_context() or not all(os.environ.get(k) for k in PINS):
        raise QueryError("worker_identity_incomplete")
    ambient = os.environ.get("HERMES_KANBAN_BOARD")
    if not literal(ambient, BOARD) or board not in (None, ambient):
        raise QueryError("board_fence")
    owner = os.environ["HERMES_KANBAN_TASK"]
    if not literal(owner, TASK):
        raise QueryError("worker_identity_incomplete")
    if task_id not in (None, owner):
        raise QueryError("task_fence")
    raw = os.environ["HERMES_KANBAN_RUN_ID"]
    if not raw.isascii() or not raw.isdecimal() or raw.startswith("0") or len(raw) > 18:
        raise QueryError("worker_identity_incomplete")
    return ambient, owner, int(raw)


def check_allowlist(board: str, allowed: object) -> None:
    if allowed is None:
        return
    if not isinstance(allowed, list) or len(allowed) > 128 or any(not literal(b, BOARD) for b in allowed):
        raise QueryError("profile_board_policy_invalid")
    if board not in allowed:
        raise QueryError("profile_board_fence")


def database_path(home: Path, board: str) -> Path:
    expected = home / "kanban.db" if board == "default" else home / "kanban" / "boards" / board / "kanban.db"
    if expected.resolve() != expected:
        raise QueryError("store_fence")
    pin = os.environ.get("HERMES_KANBAN_DB")
    if pin and Path(pin).resolve() != expected:
        raise QueryError("store_fence")
    return expected


@contextmanager
def snapshot(filename: Path):
    if not filename.is_file():
        raise QueryError("store_missing")
    # mode=ro can create BOTH sidecars for a cleanly closed WAL database.
    # SQLite's persisted read/write format bytes (18/19) identify WAL even
    # when neither sidecar exists; never open an incomplete WAL store for it.
    with filename.open("rb") as stream:
        wal_mode = 2 in stream.read(20)[18:20]
    wal = filename.with_name(filename.name + "-wal")
    shm = filename.with_name(filename.name + "-shm")
    if (wal_mode or wal.exists() or shm.exists()) and not (wal.is_file() and shm.is_file()):
        raise QueryError("store_sidecars_unavailable")
    with closing(sqlite3.connect(filename.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        # Bound corrupt/unindexed-store work; no retries or migrations.
        budget = iter(range(2000))
        conn.set_progress_handler(lambda: 0 if next(budget, None) is not None else 1, 1000)
        conn.execute("BEGIN")
        yield conn


def task_row(conn, task_id: str) -> dict:
    row = conn.execute(
        "SELECT id,status,assignee,current_run_id,workspace_kind,workspace_path,branch_name "
        "FROM tasks WHERE id=?", (task_id,),
    ).fetchone()
    if row is None:
        raise QueryError("task_missing")
    return dict(row)


def worker_check(conn, task_id: str | None, run_id: int | None) -> None:
    if run_id is None:
        return
    from hermes_cli.profiles import current_profile_name

    if task_id is None:
        raise QueryError("worker_identity_incomplete")
    task = task_row(conn, task_id)
    row = conn.execute("SELECT profile,status,outcome,ended_at FROM task_runs WHERE task_id=? AND id=?",
                       (task_id, run_id)).fetchone()
    if task["status"] != "running" or task["current_run_id"] != run_id or row is None:
        raise QueryError("worker_run_stale")
    if row["profile"] != current_profile_name() or row["status"] != "running" or row["outcome"] is not None or row["ended_at"] is not None:
        raise QueryError("worker_actor_mismatch")


def history(conn, task_id: str) -> tuple[list[dict], list[dict]]:
    runs = conn.execute(
        "SELECT id,profile,status,outcome,started_at,ended_at,"
        "CASE WHEN length(metadata)<=65536 THEN metadata ELSE '{}' END AS metadata,"
        "length(metadata) AS metadata_size FROM task_runs WHERE task_id=? ORDER BY id LIMIT ?",
        (task_id, MAX_RUNS + 1),
    ).fetchall()
    events = conn.execute(
        "SELECT id,run_id,kind,CASE WHEN length(payload)<=65536 THEN payload ELSE '{}' END AS payload,"
        "length(payload) AS payload_size FROM task_events WHERE task_id=? "
        "AND kind IN (?,?,?,?) ORDER BY id LIMIT ?", (task_id, *EVENT_KINDS, MAX_EVENTS + 1),
    ).fetchall()
    if len(runs) > MAX_RUNS or len(events) > MAX_EVENTS:
        raise QueryError("history_limit")
    return decode_rows(runs, "metadata"), decode_rows(events, "payload")


def decode_rows(rows: list, field: str) -> list[dict]:
    result = []
    for row in rows:
        row = dict(row)
        if (row.pop(field + "_size") or 0) > 65536:
            raise QueryError("native_metadata_unavailable")
        row[field] = object_json(row[field])
        result.append(row)
    return result
