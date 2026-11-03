# SPDX-License-Identifier: GPL-3.0-or-later
"""Real stock producers and registered public query ingress, not canned snapshots."""
from __future__ import annotations

import contextvars
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

import pytest
from hermes_cli.kanban_db_connect import connect
from hermes_cli.sqlite_safe_read import connect_tracked

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
OTHER = "b" * 40
AUDIT = contextvars.ContextVar("query_audit", default=None)


def audit(event, args):
    attempted = AUDIT.get()
    if attempted is None:
        return
    effect = event in {"subprocess.Popen", "os.system", "socket.connect", "socket.bind", "os.mkdir",
                       "os.remove", "os.rename", "os.rmdir", "os.chmod", "os.symlink"}
    if event == "open":
        effect = bool(args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    if event == "sqlite3.connect":
        effect = "mode=ro" not in str(args[0])
    if effect:
        attempted.append(event)
        raise AssertionError("query attempted effect: " + event)


sys.addaudithook(audit)


def cli(*args, check=True):
    completed = subprocess.run([sys.executable, "-m", "hermes_cli.main", *args],
                               text=True, capture_output=True, timeout=45)
    if check:
        assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed


@pytest.fixture(scope="session")
def installed(tmp_path_factory):
    home = tmp_path_factory.mktemp("installed")
    admitted = Path("/opt/fixture/home")
    source = admitted / "plugins" / "kanban-delivery"
    # A stale image must not test new code using an earlier admission result.
    expected = {"plugin.yaml", "__init__.py", *(str(p.relative_to(ROOT)) for p in (ROOT / "delivery").glob("*.py"))}
    actual = {str(p.relative_to(source)) for p in source.rglob("*") if p.is_file()}
    assert actual == expected
    for filename in expected:
        assert (source / filename).read_bytes() == (ROOT / filename).read_bytes(), filename
    previous = dict(os.environ)
    try:
        os.environ.update(HERMES_HOME=str(home), HERMES_KANBAN_HOME=str(home))
        shutil.copyfile(admitted / "config.yaml", home / "config.yaml")
        shutil.copytree(source, home / "plugins" / "kanban-delivery")
        status = cli("plugins", "list")
        assert "kanban-delivery" in status.stdout
        yield home
    finally:
        os.environ.clear()
        os.environ.update(previous)


def git_policy(transport="local"):
    value = {"mode": "git", "transport": transport, "repository": "/synthetic/repository",
             "integration_target_ref": "refs/heads/main", "local_target_integration": False,
             "review": {"required": True, "allowed_profiles": ["reviewer"]},
             "verify_command": "PRIVATE_COMMAND_CANARY", "push": "operator-required"}
    if transport == "ssh":
        value["ssh"] = {"host": "example.invalid", "user": "synthetic", "port": 2222}
    return value


class Native:
    def __init__(self, home, manager):
        from hermes_cli import kanban_db as kb
        self.home, self.manager, self.kb = home, manager, kb
        self.board = "project"
        kb.create_board(self.board)
        self.conn = connect(board=self.board)
        self.task = kb.create_task(self.conn, title="PRIVATE_TITLE_CANARY", body="PRIVATE_BODY_CANARY",
                                   assignee="builder", workspace_kind="dir", workspace_path="/synthetic/lane",
                                   board=self.board)
        kb.add_comment(self.conn, self.task, author="builder", body="PRIVATE_COMMENT_CANARY")
        self.write_policy(git_policy())

    def write_policy(self, config, extra=None):
        boards = {self.board: config, "foreign": {"PRIVATE_FOREIGN_CANARY": "PRIVATE_FOREIGN_CANARY"}}
        if extra:
            boards.update(extra)
        self.policy = self.home / "kanban" / "delivery-policy.json"
        self.policy.write_text(json.dumps({"policy_version": 2, "boards": boards}))

    def claim(self, review=False):
        fn = self.kb.claim_review_task if review else self.kb.claim_task
        task = fn(self.conn, self.task)
        assert task is not None
        self.run = task.current_run_id
        return self.run

    def handoff(self, metadata=None, reviewer="reviewer"):
        metadata = {"commit": SHA, "private": "PRIVATE_METADATA_CANARY"} if metadata is None else metadata
        assert self.kb.request_review(self.conn, self.task, summary="Synthetic handoff", metadata=metadata,
                                      reviewer=reviewer, expected_run_id=self.run)

    def complete(self, metadata=None):
        metadata = {"approved": True, "review_outcome": "approved", "reviewed_commit": SHA} if metadata is None else metadata
        assert self.kb.complete_task(self.conn, self.task, summary="Synthetic completion", metadata=metadata,
                                     expected_run_id=self.run)

    def query(self, name="delivery_context", **args):
        from tools.registry import registry
        params = {"board": self.board}
        if name == "delivery_context":
            params["task_id"] = self.task
        params.update(args)
        raw = registry.dispatch(name, params, scope=self.manager.scope_key)
        result = json.loads(raw)
        assert "PRIVATE_" not in raw, raw
        assert len(raw.encode()) < 32768
        return result

    def public_cli(self):
        result = cli("delivery", "context", "--board", self.board, "--task-id", self.task)
        return json.loads(result.stdout)


@pytest.fixture
def native(tmp_path, monkeypatch, installed):
    from hermes_cli.plugins import PluginManager
    home = tmp_path / "home"
    shutil.copytree(installed, home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    for key in ("HERMES_PROFILE", "HERMES_PROFILE_NAME", "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID",
                "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(key, raising=False)
    manager = PluginManager()
    manager.discover_and_load()
    instance = Native(home, manager)
    yield instance
    instance.conn.close()
    manager.unload()


def pin(native, monkeypatch, actor):
    monkeypatch.setenv("HERMES_KANBAN_TASK", native.task)
    monkeypatch.setenv("HERMES_KANBAN_BOARD", native.board)
    monkeypatch.setenv("HERMES_KANBAN_DB", str(native.home / "kanban" / "boards" / native.board / "kanban.db"))
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(native.run))
    monkeypatch.setenv("HERMES_PROFILE_NAME", actor)


@pytest.mark.parametrize("stage", ["builder", "reviewer", "returned_builder", "completed"])
def test_native_phase_and_cli_share_handler(native, stage):
    native.claim()
    if stage != "builder":
        native.handoff()
        native.claim(review=True)
    if stage == "returned_builder":
        assert native.kb.request_changes(native.conn, native.task, reason="Synthetic rework", expected_run_id=native.run)[0]
        native.claim()
    if stage == "completed":
        native.complete()
    native.conn.close()
    result = native.query()
    assert result["ok"], result
    view = result["context"]
    assert view["phase"] == stage
    assert view["source"]["repository"] == "/synthetic/repository"
    assert view["actor"] == ("reviewer" if stage in {"reviewer", "completed"} else "builder")
    assert native.public_cli() == result
    assert result["effect_clearance"] == "not_established"
    assert view["review"]["state"] == ("recorded" if stage == "completed" else "not_recorded")
    if stage in {"reviewer", "completed"}:
        assert view["artifact"] == {"kind": "git", "commit": SHA}
    else:
        assert view["artifact"] is None


def test_pending_and_returned_owner(native):
    native.claim()
    native.handoff()
    native.conn.close()
    view = native.query()["context"]
    assert view["phase"] == "awaiting_reviewer"
    assert view["next_owner"] == "reviewer"
    native.conn = connect(board=native.board)
    native.claim(review=True)
    assert native.kb.request_changes(native.conn, native.task, reason="Synthetic rework", expected_run_id=native.run)[0]
    native.conn.close()
    view = native.query()["context"]
    assert view["phase"] == "returned_builder"
    assert view["next_owner"] == "builder"
    assert view["artifact"] is None


@pytest.mark.parametrize("transport", ["local", "ssh"])
def test_policy_source_and_pr_only(native, transport):
    native.write_policy(git_policy(transport))
    result = native.query("delivery_policy_check")
    config = result["policy"]["config"]
    assert config["transport"] == transport
    assert config["local_target_integration"] is False
    assert config["review"]["allowed_profiles"] == ["reviewer"]
    assert "verify_command" not in config
    assert json.loads(cli("delivery", "policy-check", "--board", native.board).stdout) == result


@pytest.mark.parametrize("config,state", [({"mode": "none"}, "not_applicable"),
                                           ({"mode": "unconfigured"}, "unconfigured"), ([], "invalid")])
def test_policy_states(native, config, state):
    native.write_policy(config)
    assert native.query("delivery_policy_check")["policy"]["state"] == state


@pytest.mark.parametrize("change", [
    {"integration_target_ref": "main"}, {"integration_target_ref": "refs/heads/../main"},
    {"review": {"required": True, "allowed_profiles": []}},
    {"review": {"required": False, "allowed_profiles": ["reviewer"]}},
    {"review": {"required": True, "allowed_profiles": ["reviewer", "reviewer"]}},
    {"local_target_integration": "false"}, {"repository": "relative"},
    {"transport": "ssh", "ssh": {"host": "example.invalid", "user": "synthetic", "port": 0}},
])
def test_policy_invariants(native, change):
    config = git_policy()
    config.update(change)
    native.write_policy(config)
    assert native.query("delivery_policy_check")["policy"]["state"] == "invalid"


def test_missing_malformed_and_unselected_policy(native):
    native.policy.unlink()
    assert native.query("delivery_policy_check")["policy"]["state"] == "missing"
    native.policy.write_text("[]")
    assert native.query("delivery_policy_check")["policy"]["state"] == "invalid"
    native.policy.write_text("{")
    assert native.query("delivery_policy_check")["policy"]["state"] == "invalid"
    native.policy.write_text(json.dumps({"policy_version": 2, "boards": {"foreign": "PRIVATE_FOREIGN_CANARY"}}))
    assert native.query("delivery_policy_check")["policy"]["state"] == "unconfigured"


@pytest.mark.parametrize("reviewer,metadata,diagnostic", [
    ("reviewer", {"approved": True, "review_outcome": "approved", "reviewed_commit": OTHER}, "review_artifact_mismatch"),
    ("builder", {"approved": True, "review_outcome": "approved", "reviewed_commit": SHA}, "review_not_independent"),
    ("outsider", {"approved": True, "review_outcome": "approved", "reviewed_commit": SHA}, "reviewer_unauthorized"),
    ("reviewer", {"approved": True, "review_outcome": "approved", "reviewed_commit": SHA, "blocking_findings": ["finding"]}, "review_contradictory"),
])
def test_untrusted_review_is_not_approval(native, reviewer, metadata, diagnostic):
    native.claim()
    native.handoff(reviewer=reviewer)
    native.claim(review=True)
    native.complete(metadata)
    native.conn.close()
    review = native.query()["context"]["review"]
    assert review["state"] == "invalid"
    assert diagnostic in review["diagnostics"]


def test_completion_status_or_prose_is_not_review(native):
    native.claim()
    native.complete({"commit": SHA, "private": "PRIVATE_METADATA_CANARY"})
    native.conn.close()
    result = native.query()["context"]
    assert result["phase"] == "completed"
    assert result["artifact"] == {"kind": "git", "commit": SHA}
    assert result["review"]["state"] == "unknown"


def test_report_identity_and_no_file_read(native):
    report = [{"path": "/nonexistent/report.json", "sha256": "c" * 64}]
    native.claim()
    native.handoff({"artifact_kind": "report", "artifacts": report})
    native.claim(review=True)
    native.complete({"artifact_kind": "report", "reviewed_artifacts": report,
                     "approved": True, "review_outcome": "approved"})
    native.conn.close()
    view = native.query()["context"]
    assert view["artifact"] == {"kind": "report", "artifacts": report}
    assert view["review"]["state"] == "recorded"


@pytest.mark.parametrize("metadata", [{}, {"commit": "A" * 40}, {"commit": SHA + " "},
                                       {"artifact_kind": "report", "commit": SHA, "artifacts": []},
                                       {"artifact_kind": "report", "artifacts": []}])
def test_no_identity_repair_or_guessed_fallback(native, metadata):
    native.claim()
    native.handoff(metadata)
    native.claim(review=True)
    native.conn.close()
    view = native.query()["context"]
    assert view["artifact"] is None
    assert view["diagnostics"]


def test_changed_successor_does_not_inherit_approval(native):
    native.claim()
    native.handoff()
    native.claim(review=True)
    assert native.kb.request_changes(native.conn, native.task, reason="Synthetic rework", expected_run_id=native.run)[0]
    native.claim()
    native.handoff({"commit": OTHER})
    native.claim(review=True)
    native.complete({"approved": True, "review_outcome": "approved", "reviewed_commit": SHA})
    native.conn.close()
    view = native.query()["context"]
    assert view["artifact"]["commit"] == OTHER
    assert view["review"]["state"] == "invalid"


def test_worker_pin_defaults_and_fences(native, monkeypatch):
    native.claim()
    pin(native, monkeypatch, "builder")
    native.conn.close()
    from tools.registry import registry
    result = json.loads(registry.dispatch("delivery_context", {}, scope=native.manager.scope_key))
    assert result["context"]["phase"] == "builder"
    assert native.query(board="foreign")["error"] == "board_fence"
    assert native.query(task_id="foreign-task")["error"] == "task_fence"
    monkeypatch.setenv("HERMES_PROFILE_NAME", "reviewer")
    assert native.query()["error"] == "worker_actor_mismatch"
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "999999")
    assert native.query()["error"] == "worker_run_stale"


def test_unpinned_explicit_board_and_literal_ids(native, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_BOARD", native.board)
    assert native.query(board=None)["error"] == "board_required"
    for slug in (" project", "Project", "project ", "../project"):
        assert native.query(board=slug)["error"] == "board_required"
    assert native.query(task_id=native.task + " ")["error"] == "task_invalid"
    assert native.query(database="/foreign.db")["error"] == "invalid_arguments"
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "1")
    assert native.query()["error"] == "worker_identity_incomplete"


def test_default_and_named_boards_no_cross_board_fallback(native):
    default = connect(board="default")
    try:
        task = native.kb.create_task(default, title="Synthetic default", assignee="builder", workspace_kind="dir",
                                      workspace_path="/synthetic/default", board="default")
        native.kb.claim_task(default, task)
        default.close()
        result = native.query(board="default", task_id=task)
        assert result["context"]["phase"] == "builder"
        assert result["policy"]["state"] == "unconfigured"
        assert native.query(board="default")["error"] == "task_missing"
    finally:
        default.close()


def test_missing_store_does_not_initialize(native):
    assert native.query(board="missing")["error"] == "store_missing"
    assert not (native.home / "kanban" / "boards" / "missing").exists()


def test_foreign_store_pins_are_refused(native, monkeypatch, tmp_path):
    native.claim()
    pin(native, monkeypatch, "builder")
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "foreign.db"))
    assert native.query()["error"] == "store_fence"
    assert not (tmp_path / "foreign.db").exists()


def test_registered_query_has_no_effect_attempts(native):
    native.claim()
    native.handoff()
    native.claim(review=True)
    native.complete()
    native.conn.close()
    # Prime lazy native registry/config imports before observing the query itself.
    expected = native.query()
    before = file_bytes(native.home)
    attempted = []
    token = AUDIT.set(attempted)
    try:
        result = native.query()
        policy = native.query("delivery_policy_check")
    finally:
        AUDIT.reset(token)
    assert result == expected and policy["ok"]
    assert attempted == []
    assert file_bytes(native.home) == before


def file_bytes(root):
    # Closing a raw DB/sidecar fd in the holder process would cancel its locks.
    completed = subprocess.run([sys.executable, "-c", """
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
print(json.dumps({str(p.relative_to(root)): p.read_bytes().hex()
                  for p in root.rglob("*") if p.is_file()}))
""", str(root)], text=True, capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


def probe_write_lock(filename, task_id):
    # A real independent writer must be valid both before and after the query.
    completed = subprocess.run([sys.executable, "-c", """
import json
import os
import sqlite3
import sys
from hermes_cli.sqlite_safe_read import connect_tracked

conn = connect_tracked(sys.argv[1], timeout=0.1, isolation_level=None)
try:
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
            raise
        outcome = "locked"
    else:
        assert conn.execute("UPDATE tasks SET title=? WHERE id=?",
                            ("Synthetic rolled-back probe", sys.argv[2])).rowcount == 1
        outcome = "acquired"
finally:
    conn.rollback()
    conn.close()
print(json.dumps({"pid": os.getpid(), "outcome": outcome}))
""", str(filename), task_id], text=True, capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["pid"] != os.getpid()
    return result["outcome"]


@pytest.mark.parametrize("name", ["delivery_context", "delivery_policy_check"])
def test_query_preserves_same_process_native_write_lock(native, monkeypatch, name):
    assert native.conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    native.claim()
    pin(native, monkeypatch, "builder")
    filename = Path(native.conn.execute("PRAGMA database_list").fetchone()[2])
    committed = tuple(native.conn.iterdump())
    native.conn.execute("BEGIN IMMEDIATE")
    try:
        native.conn.execute("UPDATE tasks SET title=? WHERE id=?", ("Synthetic pending write", native.task))
        before = probe_write_lock(filename, native.task)
        assert before == "locked", "the original transaction did not exclude a valid writer"
        result = native.query(name)
        assert native.conn.in_transaction, "the query ended the original holder's transaction"
        after = probe_write_lock(filename, native.task)
    finally:
        native.conn.rollback()
    released = probe_write_lock(filename, native.task)
    assert (before, after, released) == ("locked", "locked", "acquired")
    assert tuple(native.conn.iterdump()) == committed
    assert result == {"ok": False, "error": "store_header_unavailable"}


@pytest.mark.parametrize("mode", ["wal", "delete"])
def test_first_query_preserves_closed_store(native, mode):
    assert native.conn.execute("PRAGMA journal_mode=" + mode).fetchone()[0].lower() == mode
    native.claim()
    filename = Path(native.conn.execute("PRAGMA database_list").fetchone()[2])
    native.conn.close()
    assert filename.read_bytes()[18:20] == (b"\x02\x02" if mode == "wal" else b"\x01\x01")
    assert not filename.with_name(filename.name + "-wal").exists()
    assert not filename.with_name(filename.name + "-shm").exists()
    # No priming query: both membership and bytes precede the first public read.
    paths, before = set(native.home.rglob("*")), file_bytes(native.home)
    result = native.query()
    assert set(native.home.rglob("*")) == paths
    assert file_bytes(native.home) == before
    if mode == "wal":
        assert result == {"ok": False, "error": "store_sidecars_unavailable"}
    else:
        assert result["ok"] and result["context"]["phase"] == "builder"


@pytest.mark.parametrize("kind", ["unreadable", "short"])
def test_unavailable_header_does_not_guess_or_repair(native, kind):
    assert native.conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    native.claim()
    filename = Path(native.conn.execute("PRAGMA database_list").fetchone()[2])
    native.conn.close()
    if kind == "short":
        filename.write_bytes(filename.read_bytes()[:19])
    paths, before = set(native.home.rglob("*")), file_bytes(native.home)
    mode = filename.stat().st_mode
    if kind == "unreadable":
        filename.chmod(0)
    try:
        result = native.query()
    finally:
        filename.chmod(mode)
    assert result == {"ok": False, "error": "store_header_unavailable"}
    assert set(native.home.rglob("*")) == paths
    assert file_bytes(native.home) == before


def test_live_wal_reads_committed_wal_only_state(native):
    assert native.conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    assert native.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    filename = Path(native.conn.execute("PRAGMA database_list").fetchone()[2])
    main_before = file_bytes(filename.parent)[filename.name]
    # An actual reader pins the pre-claim snapshot while the native writer commits.
    reader = connect_tracked(filename)
    try:
        reader.execute("BEGIN")
        old_status = reader.execute("SELECT status FROM tasks WHERE id=?", (native.task,)).fetchone()[0]
        assert old_status != "running"
        native.claim()
        wal = filename.with_name(filename.name + "-wal")
        shm = filename.with_name(filename.name + "-shm")
        assert wal.is_file() and shm.is_file() and wal.stat().st_size > 32
        before = file_bytes(filename.parent)
        assert before[filename.name] == main_before
        committed = tuple(native.conn.iterdump())
        paths = set(filename.parent.iterdir())
        result = native.query()
        assert result["ok"] and result["context"]["phase"] == "builder", result
        assert result["context"]["run_id"] == native.run
        assert reader.execute("SELECT status FROM tasks WHERE id=?", (native.task,)).fetchone()[0] == old_status
        after = file_bytes(filename.parent)
        assert after[filename.name] == main_before and after[wal.name] == before[wal.name]
        assert tuple(native.conn.iterdump()) == committed
        assert set(filename.parent.iterdir()) == paths
    finally:
        reader.close()


@pytest.mark.parametrize("remaining", ["wal", "shm"])
def test_partial_wal_sidecars_are_not_repaired(native, remaining):
    assert native.conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    native.claim()
    filename = Path(native.conn.execute("PRAGMA database_list").fetchone()[2])
    sidecars = {kind: filename.with_name(filename.name + "-" + kind) for kind in ("wal", "shm")}
    retained = bytes.fromhex(file_bytes(filename.parent)[sidecars[remaining].name])
    native.conn.close()
    assert all(not path.exists() for path in sidecars.values())
    # Re-create one native-produced sidecar only after the owned writer closes.
    sidecars[remaining].write_bytes(retained)
    paths, before = set(native.home.rglob("*")), file_bytes(native.home)
    result = native.query()
    assert set(native.home.rglob("*")) == paths
    assert file_bytes(native.home) == before
    assert result == {"ok": False, "error": "store_sidecars_unavailable"}


@pytest.mark.parametrize("name", ["delivery_policy_check", "delivery_context"])
def test_symlink_policy_is_refused_with_valid_native_state(native, name):
    native.claim()
    native.conn.close()
    assert native.query(name)["ok"]
    target = native.policy.with_name("retained-policy.json")
    native.policy.rename(target)
    native.policy.symlink_to(target)
    before = file_bytes(native.home)
    assert native.query(name) == {"ok": False, "error": "policy_path_fence"}
    assert native.policy.is_symlink() and file_bytes(native.home) == before


@pytest.mark.parametrize("linked", ["database", "board"])
def test_symlink_board_is_refused_with_valid_native_state(native, linked):
    native.claim()
    filename = Path(native.conn.execute("PRAGMA database_list").fetchone()[2])
    native.conn.close()
    assert native.query()["ok"]
    original = filename if linked == "database" else filename.parent
    target = original.with_name("retained-" + original.name)
    original.rename(target)
    original.symlink_to(target, target_is_directory=linked == "board")
    before = file_bytes(native.home)
    assert native.query() == {"ok": False, "error": "store_fence"}
    assert original.is_symlink() and file_bytes(native.home) == before


@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_nonregular_policy_is_refused_promptly(native, kind):
    assert native.query("delivery_policy_check")["policy"]["state"] == "valid"
    native.policy.unlink()
    if kind == "fifo":
        os.mkfifo(native.policy)
    else:
        native.policy.mkdir()
    mode = native.policy.stat().st_mode
    expired = []

    def deadline(signum, frame):
        expired.append(True)
        raise TimeoutError("policy query exceeded deadline")

    # Bound the registered handler, excluding unrelated native CLI startup time.
    previous = signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 3)
    try:
        result = native.query("delivery_policy_check")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert not expired, "policy query blocked until its deadline"
    assert result["ok"]
    assert result["policy"] == {"state": "invalid", "diagnostics": ["policy_not_regular"]}
    assert native.policy.stat().st_mode == mode


def test_profile_allowlist_and_bound_home(native, monkeypatch, tmp_path):
    import hermes_yaml as yaml
    config_path = native.home / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    entry = config["plugins"].setdefault("entries", {}).setdefault("kanban-delivery", {})
    entry["settings"] = {"allowed_boards": ["default"]}
    config_path.write_text(yaml.safe_dump(config))
    assert native.query()["error"] == "profile_board_fence"
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    token = set_hermes_home_override(tmp_path / "foreign-profile")
    try:
        result = native.query()
        assert result.get("error") in {"profile_fence", "Unknown tool: delivery_context"}, result
    finally:
        reset_hermes_home_override(token)


def test_event_tail_does_not_hide_claim(native):
    native.claim()
    # Genuine native events exceed kanban_show's event tail, without hiding claim.
    for index in range(60):
        native.kb.add_comment(native.conn, native.task, author="builder", body="PRIVATE_COMMENT_CANARY")
    native.conn.close()
    assert native.query()["context"]["phase"] == "builder"


def test_native_manual_completion_has_incomplete_claim_history(native):
    assert native.kb.complete_task(native.conn, native.task, summary="Synthetic operator completion",
                                    metadata={"commit": SHA})
    native.conn.close()
    view = native.query()["context"]
    assert view["artifact"] is None
    assert "completion_provenance_missing" in view["diagnostics"]
    assert view["review"]["state"] == "unknown"


def test_bounded_native_history(native):
    native.claim()
    for index in range(64):
        native.handoff()
        native.claim(review=True)
        assert native.kb.request_changes(native.conn, native.task, reason="Synthetic rework", expected_run_id=native.run)[0]
        native.claim()
    native.conn.close()
    view = native.query()["context"]
    assert view["phase"] == "unknown"
    assert view["artifact"] is None
    assert view["diagnostics"] == ["history_limit"]
