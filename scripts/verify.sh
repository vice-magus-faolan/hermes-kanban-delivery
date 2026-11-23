#!/usr/bin/env bash
set -euo pipefail
expected=${DELIVERY_EXPECTED_HERMES-}
if [[ ! $expected =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
    printf 'Set DELIVERY_EXPECTED_HERMES to the requested stock release (vX.Y.Z).\n' >&2
    exit 2
fi
mkdir -p "$HOME"
export HERMES_HOME="$HOME/.hermes"
export HERMES_KANBAN_HOME="$HERMES_HOME"
mkdir -p "$HERMES_HOME"
# Native identity under the actual test user, independent of the image name.
if ! version=$(python -c 'from hermes_cli.version_info import get_version_info; print(get_version_info().derived_version)'); then
    printf 'Native runtime version unavailable under the test user.\n' >&2
    exit 1
fi
printf 'Native Hermes runtime: v%s; requested: %s\n' "$version" "$expected"
if [[ ! $version =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
    printf 'Native runtime version unavailable under the test user.\n' >&2
    exit 1
fi
if [[ v$version != "$expected" ]]; then
    printf 'Native runtime mismatch: expected %s, got v%s.\n' "$expected" "$version" >&2
    exit 1
fi
printf 'Runtime identity verified; starting product checks.\n'
bash -n scripts/verify-docker.sh
bash -n scripts/verify.sh
ruff check --no-cache --isolated --select C901 --config 'lint.mccabe.max-complexity = 10' __init__.py delivery
python -m hermes_cli.main plugins validate /source
python -m hermes_cli.main plugins doctor /source --ci
python -m pytest -p no:cacheprovider -q tests
