# SPDX-License-Identifier: GPL-3.0-or-later
"""Project native lifecycle evidence, without granting workflow authority."""
from __future__ import annotations

from .values import SHA, SHA256, QueryError, literal, path, profile, text


def artifact(metadata: dict, reviewed: bool = False) -> dict:
    kind = metadata.get("artifact_kind", "git")
    if kind == "report":
        if any(k in metadata for k in ("commit", "reviewed_commit")):
            raise QueryError("artifact_identity_mixed")
        entries = metadata.get("reviewed_artifacts" if reviewed else "artifacts")
        return {"kind": "report", "artifacts": report_entries(entries)}
    if kind != "git" or any(k in metadata for k in ("artifacts", "reviewed_artifacts")):
        raise QueryError("artifact_identity_mixed")
    commit = metadata.get("reviewed_commit" if reviewed else "commit")
    if not literal(commit, SHA):
        raise QueryError("artifact_identity_missing")
    if reviewed and "commit" in metadata and metadata["commit"] != commit:
        raise QueryError("artifact_identity_contradictory")
    return {"kind": "git", "commit": commit}


def report_entries(entries: object) -> list[dict]:
    if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
        raise QueryError("report_identity_invalid")
    seen, result = set(), []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise QueryError("report_identity_invalid")
        location = path(entry["path"])
        if not location or location in seen or not literal(entry["sha256"], SHA256):
            raise QueryError("report_identity_invalid")
        seen.add(location)
        result.append({"path": location, "sha256": entry["sha256"]})
    return result


def event_for(events: list[dict], run: dict, kind: str) -> dict | None:
    matching = [e for e in events if e["run_id"] == run["id"] and e["kind"] == kind]
    if len(matching) != 1:
        return None
    return matching[0]


def handoff_before(runs: list[dict], events: list[dict], before: int) -> dict:
    prior = [r for r in runs if r["id"] < before]
    handoffs = [r for r in prior if r["outcome"] == "review_requested"]
    if not handoffs:
        raise QueryError("handoff_missing")
    handoff = handoffs[-1]
    if handoff["status"] != "review" or handoff["ended_at"] is None:
        raise QueryError("handoff_incomplete")
    requested = event_for(events, handoff, "review_requested")
    if not requested or requested["payload"].get("implementer") != handoff["profile"] or not profile(handoff["profile"]):
        raise QueryError("handoff_provenance_missing")
    if any(r["id"] > handoff["id"] and r["outcome"] == "changes_requested" for r in prior):
        raise QueryError("handoff_stale")
    return handoff


def run_role(run: dict, events: list[dict]) -> str:
    claimed = event_for(events, run, "claimed")
    if not claimed or claimed["payload"].get("run_id") != run["id"]:
        raise QueryError("claim_provenance_missing")
    source = claimed["payload"].get("source_status")
    if source not in (None, "review"):
        raise QueryError("claim_provenance_invalid")
    return "reviewer" if source == "review" else "builder"


def phase(task: dict, runs: list[dict], events: list[dict]) -> tuple[str, dict | None]:
    active = [r for r in runs if r["ended_at"] is None]
    if task["status"] == "running":
        if len(active) != 1 or active[0]["id"] != task["current_run_id"]:
            raise QueryError("active_run_inconsistent")
        run = active[0]
        if run["status"] != "running" or run["outcome"] is not None or not profile(run["profile"]):
            raise QueryError("active_run_inconsistent")
        role = run_role(run, events)
        prior = [r for r in runs if r["id"] < run["id"]]
        if role == "builder" and prior and prior[-1]["outcome"] == "changes_requested":
            changes = event_for(events, prior[-1], "changes_requested")
            if not changes or changes["payload"].get("implementer") != run["profile"]:
                raise QueryError("return_provenance_missing")
            role = "returned_builder"
        return role, run
    if active:
        raise QueryError("active_run_inconsistent")
    return closed_phase(task, runs, events)


def closed_phase(task: dict, runs: list[dict], events: list[dict]) -> tuple[str, dict | None]:
    latest = runs[-1] if runs else None
    if latest and task["status"] == "done" and latest["outcome"] == "completed":
        if latest["status"] != "done" or not event_for(events, latest, "completed"):
            raise QueryError("completion_provenance_missing")
        return "completed", latest
    if latest and task["status"] == "review" and latest["outcome"] == "review_requested":
        handoff_before(runs, events, latest["id"] + 1)
        return "awaiting_reviewer", latest
    if latest and latest["outcome"] == "changes_requested" and task["status"] in {"ready", "todo"}:
        changes = event_for(events, latest, "changes_requested")
        if changes and changes["payload"].get("implementer") == task["assignee"]:
            return "returned_builder", None
    raise QueryError("phase_evidence_incomplete")


def candidate(role: str, run: dict | None, runs: list[dict], events: list[dict]) -> tuple[dict, dict]:
    if not run:
        raise QueryError("artifact_not_current")
    if role == "awaiting_reviewer":
        handoff = run
    elif role == "reviewer":
        handoff = handoff_before(runs, events, run["id"])
    elif role == "completed" and run_role(run, events) == "reviewer":
        handoff = handoff_before(runs, events, run["id"])
    elif role == "completed":
        return artifact(run["metadata"]), run
    else:
        raise QueryError("artifact_not_current")
    return artifact(handoff["metadata"]), handoff


def review_fact(run: dict | None, handoff: dict | None, identity: dict | None,
                events: list[dict], policy: dict) -> dict:
    if run is None or run["outcome"] != "completed":
        return {"state": "not_recorded"}
    metadata = run["metadata"]
    if metadata.get("approved") is not True or metadata.get("review_outcome") != "approved":
        return {"state": "unknown", "diagnostics": ["explicit_review_missing"]}
    try:
        validate_review(run, handoff, identity, events, policy)
    except QueryError as exc:
        return {"state": "invalid", "diagnostics": [str(exc)]}
    return {"state": "recorded", "run_id": run["id"], "reviewer": profile(run["profile"]),
            "artifact": identity, "effect_clearance": "not_established"}


def validate_review(run: dict, handoff: dict | None, identity: dict | None,
                    events: list[dict], policy: dict) -> None:
    if run_role(run, events) != "reviewer" or not event_for(events, run, "completed"):
        raise QueryError("review_provenance_missing")
    if not handoff or handoff["id"] >= run["id"] or identity is None:
        raise QueryError("review_handoff_missing")
    if run["profile"] == handoff["profile"]:
        raise QueryError("review_not_independent")
    allowed = policy.get("config", {}).get("review", {}).get("allowed_profiles", [])
    if policy.get("state") != "valid" or run["profile"] not in allowed:
        raise QueryError("reviewer_unauthorized")
    if run["metadata"].get("blocking_findings") not in (None, [], (), ""):
        raise QueryError("review_contradictory")
    if artifact(run["metadata"], reviewed=True) != identity:
        raise QueryError("review_artifact_mismatch")


def project(task: dict, runs: list[dict], events: list[dict], policy: dict) -> dict:
    result = {"phase": "unknown", "actor": None, "run_id": None, "artifact": None,
              "artifact_run_id": None, "review": {"state": "unknown"}, "next_owner": None,
              "source": {"workspace_kind": text(task["workspace_kind"], 32),
                         "workspace": path(task["workspace_path"]), "branch": text(task["branch_name"], 256),
                         "repository": path(policy.get("config", {}).get("repository"))}, "diagnostics": []}
    try:
        role, run = phase(task, runs, events)
    except QueryError as exc:
        result["diagnostics"].append(str(exc))
        return result
    result.update(phase=role, actor=profile(run["profile"]) if run else None,
                  run_id=run["id"] if run else None)
    result["next_owner"] = profile(task["assignee"]) if role != "completed" else None
    identity, handoff = None, None
    try:
        identity, handoff = candidate(role, run, runs, events)
        result.update(artifact=identity, artifact_run_id=handoff["id"])
    except QueryError as exc:
        result["diagnostics"].append(str(exc))
    result["review"] = review_fact(run, handoff, identity, events, policy)
    return result
