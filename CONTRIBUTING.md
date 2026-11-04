# Contributing

Use focused feature branches and pull requests. GPL-3.0-or-later. Keep credentials,
real boards/sessions, host-specific policies, logs and runtime state out of Git.
Do not patch Hermes core or replace native lifecycle/approval behavior.

## Verification

Requirements: Docker, Git, Bash, Python 3 for the Docker-boundary regression, and
an owned scratch directory. Product tests execute only in disposable non-root,
network-denied containers. Online image bootstrap uses unmodified official Hermes
tags and the release's frozen PM environment builder with its default pinned
interpreter, preserving lock overrides;
plugin test dependencies are pinned in `requirements-test.txt`. The same online
build installs the selected plugin bytes into an empty image-owned home and runs
real native enable/list, including the independent PM tool and dependency closure.
Offline fixtures reuse that native configuration and verify every installed plugin
file against the mounted candidate; changed plugin code requires a rebuild.
No live Hermes
home, credentials, board state or Docker socket is mounted.

```sh
export TMPDIR="$HOME/.cache/kanban-delivery-tests"
mkdir -p "$TMPDIR"
export DELIVERY_TEST_IMAGE=hermes-kanban-delivery:v0.22.2
docker build --build-arg HERMES_REF=v0.22.2 -t "$DELIVERY_TEST_IMAGE" -f docker/Dockerfile .
bash scripts/verify-docker.sh
python3 scripts/test-docker-inputs.py

export DELIVERY_TEST_IMAGE=hermes-kanban-delivery:v0.22.5
docker build --build-arg HERMES_REF=v0.22.5 -t "$DELIVERY_TEST_IMAGE" -f docker/Dockerfile .
bash scripts/verify-docker.sh
python3 scripts/test-docker-inputs.py
```

The canonical wrapper selects regular, nonlinked runtime/test inputs through Git
and ignore rules, mounts that snapshot read-only and removes its owned staging
and container on success/failure. Label-scoped container/volume/network queries
must succeed and show absence; cleanup failure remains nonzero. Reusable images
may remain. There is no global prune or automatic live installation.

Inside the container, `scripts/verify.sh` checks Bash syntax separately per script,
enforces Ruff C901 <=10 over every production Python module, runs native plugin
validate and Doctor, then discovers the real pytest suite. Exit failures propagate;
no tests discovered is an error. Native fixtures produce lifecycle states through
stock APIs, exercise registered tools and actual CLI adapters, and contrast
privacy/fences/incomplete or invalid review evidence with valid positives. The
application-effects check observes attempted process/network/filesystem/SQLite
effects at the Python audit boundary; SQLite-managed reader WAL/SHM bookkeeping
is permitted. First-call and partial-sidecar fixtures compare complete committed
logical history/schema and exact non-database settings, with real foreign history.
Native WAL preconditions and the actual non-root Python/SQLite runtime are checked
without forcing WAL around native safety. Lock regressions contrast tracked and
entirely plain same-process holders in WAL/DELETE modes with independent valid
writers; raw byte observers run in separate processes. `test-docker-inputs.py` exercises the actual wrapper with
an ignored supported-format canary inside `tests`, verifies forced-failure cleanup,
and proves that removing ignore filtering makes the regression fail.

## CI and design

The `verify` workflow runs both stock release targets on PRs and on pushes to
main, without duplicate feature-branch push suites. The plugin is one shared
component: all current product tests are affected by its runtime or verification
changes, so full PR testing is the conservative impact selection. Hosted results
are separate from local verification; do not claim a pass before the jobs run.

Keep one application path behind native adapters. Admission/storage, policy and
phase/artifact projections are separate responsibilities, not a second state
machine. Extend the bounded existing-store adapter only when public native reads
cannot preserve the application-read-only/bookkeeping and disclosure contract. Unknown evidence must
remain unknown. A recorded exact-source review is never effect authority.
