#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$HOME"
export HERMES_HOME="$HOME/.hermes"
export HERMES_KANBAN_HOME="$HERMES_HOME"
mkdir -p "$HERMES_HOME"
version=$(python -m hermes_cli.main --version)
printf '%s\n' "$version"
if [[ ! $version =~ Hermes.*[0-9]+\.[0-9]+\.[0-9]+ || $version == *unknown* ]]; then
    printf 'Native runtime version unavailable under the test user.\n' >&2
    exit 1
fi
bash -n scripts/verify-docker.sh
bash -n scripts/verify.sh
ruff check --no-cache --isolated --select C901 --config 'lint.mccabe.max-complexity = 10' __init__.py delivery
python -m hermes_cli.main plugins validate /source
python -m hermes_cli.main plugins doctor /source --ci
python -m pytest -p no:cacheprovider -q tests
