# Hermes Kanban Delivery

Read-only delivery observations for native Hermes Kanban. GPL-3.0-or-later.

## Install and query

This is a native directory plugin with no third-party Python runtime dependencies.
The supported test targets are stock Hermes v0.22.2 and v0.22.5 on Linux x86-64.
Other releases and architectures are not verified.
Verification separates online native dependency/plugin admission during image
setup from network-denied product queries; it never activates a live profile.
Use native in-process plugin
loading for the CLI adapter; Hermes plugin-host isolation does not expose CLI
registrations. This plugin does not change that setting or bypass isolation.

```sh
hermes plugins install vice-magus-faolan/hermes-kanban-delivery --no-enable
hermes plugins enable kanban-delivery
hermes delivery policy-check --board project
hermes delivery context --board project --task-id t_example
```

Enable the `kanban_delivery` toolset in the selected profile to expose model tools
`delivery_policy_check` and `delivery_context`. They accept only `board` and,
for context, `task_id`; they use exactly the same query handlers as the CLI.
The no-write guarantee applies to these query handlers and their store
observation, not the complete Hermes CLI process: native CLI startup can write
logs and bootstrap files in the selected Hermes home before invoking a handler.
Outside a verified active worker, board selection is mandatory, even if a current
board is configured. A verified worker may omit its own board/task; cross-board
or cross-task requests, incomplete pins and stale/foreign actor pins are refused.
Identifiers are literal, never trimmed, normalized or repaired.

## Results and boundaries

JSON replies distinguish request success (`ok`) from policy validity and evidence
completeness. Policy states are `valid`, `missing`, `unconfigured`, `invalid` and
`not_applicable`. Only the selected board's v2 policy is validated. Applicable
Git invariants include explicit local/SSH source, full target ref, board-local
reviewer authorization and boolean local-target integration. Missing optional
integration/effect settings remain unknown; PR-only `false` is preserved. Commands
and free-form policy prose are not returned. No defaults or board registrations
are saved.

Context reports native builder, reviewer, returned-builder, awaiting-reviewer or
completed phase, actor/run, declared source, next assigned owner and available
exact artifact identity. Git commits must be full lowercase SHA-1 identifiers.
Report handoffs must explicitly declare `artifact_kind: report` with a nonempty
`artifacts` list of `{path, sha256}`; completed review uses `reviewed_artifacts`.
Report paths are identities only: files are never fetched or hashed by queries.
Mixed identities and missing provenance remain unknown/partial, not Git fallbacks.

A `review.state` of `recorded` requires native review-claim/handoff/completion
provenance, explicit affirmative metadata, matching exact identity and an
independent board-authorized reviewer. It is a recorded fact, not clearance to
publish, merge, deploy or continue after a stop. Every successful response says
`authority: observation_only` and `effect_clearance: not_established`. Recorded
categorical effect restrictions are in `policy.config.effects`; unstructured
restrictions and follow-up instructions in prose are intentionally not interpreted.

Reads use the native shared board root, not another profile's private stores.
For an additional profile-local narrowing, configure plugin setting
`allowed_boards` as a list of literal slugs; absent means the ordinary shared
native board scope, and an empty list denies every board. Runtime arguments cannot
select files, databases, profiles or policy paths. Canonical stores outside the
resolved shared root, redirected symlinks and mismatched environment pins fail
closed. Native Kanban remains the only lifecycle authority; the review recorder
and Outcome Loop retain their own responsibilities.

No task bodies, titles, comments, transcripts, arbitrary metadata or command
strings are exposed. No repository/Git/SSH/subprocess/network operations occur
inside queries. The plugin never initializes/migrates stores, records approvals,
changes configuration/current board or installs enforcement hooks.

## Limits and compatibility adapter

Policy input must be a regular file and is capped at 1 MiB; FIFOs and other
nonregular inputs are refused without waiting for a writer. Each task query reads
at most 128 runs and 512 relevant lifecycle events, with 64 KiB per
metadata/payload field and a bounded
SQLite work budget. Larger/incomplete history is explicitly unknown, never a
truncated history presented as complete. Encoded replies are capped at 32 KiB;
`response_limit` refuses an oversized result instead of silently truncating an
identity. Paths are limited to 1024 characters; reviewer lists to 32 entries;
report lists to 16 identities. Diagnostics are code-authored, never raw exceptions.

Stock native `connect` can initialize/migrate, and `kanban_show` includes prose,
unrelated role history and a capped event tail. `delivery/store.py` is therefore
a narrow read-only SQLite compatibility adapter for the two tested release
schemas. It uses task-local parameterized SELECTs in one snapshot, `mode=ro` and
`query_only`, without schema repair or lifecycle SQL writes. Native profile/root
and dispatcher-ownership helpers retain their existing scope semantics. Schema
changes require re-verification. WAL stores require both existing `-wal` and
`-shm` files, including on the first query after a clean writer close; missing
sidecars cause `store_sidecars_unavailable`, never creation or repair. Healthy
live WAL reads include committed WAL-only data; normal SQLite reader locking is
not application state mutation. DELETE-mode stores need no sidecars.
Unavailable stores are not created. Operator control of native
homes/pins is trusted; this is not an OS sandbox against arbitrary installed code.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the canonical container/CI checks.
