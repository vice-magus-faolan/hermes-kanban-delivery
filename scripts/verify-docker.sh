#!/usr/bin/env bash
# Canonical container boundary: run from the plugin Git root.
set -euo pipefail
image=${DELIVERY_TEST_IMAGE:-hermes-kanban-delivery:v0.22.5}
run="kanban-delivery-$(date +%s%N)-$$"
label="org.hermes-kanban-delivery.run=$run"
staging=''
cleanup() {
    result=$?
    trap - EXIT INT TERM
    cleanup_status=0
    docker rm -f "$run" >/dev/null 2>&1 || {
        remaining=$(docker ps -aq --filter "name=^/${run}$") || cleanup_status=1
        if [[ -n ${remaining:-} ]]; then cleanup_status=1; fi
    }
    for resource in container volume network; do
        case $resource in
            container) remaining=$(docker ps -aq --filter "label=$label") || cleanup_status=1 ;;
            volume) remaining=$(docker volume ls -q --filter "label=$label") || cleanup_status=1 ;;
            network) remaining=$(docker network ls -q --filter "label=$label") || cleanup_status=1 ;;
        esac
        printf 'Owned %s query: %s\n' "$resource" "${remaining:-empty}"
        if [[ -n ${remaining:-} ]]; then cleanup_status=1; fi
    done
    staging_status=0
    if [[ -n $staging ]]; then
        rm -rf -- "$staging" || staging_status=1
        if [[ -e $staging ]]; then staging_status=1; fi
    fi
    printf 'Test exit=%s; resource cleanup=%s; staging cleanup=%s; run=%s\n' "$result" "$cleanup_status" "$staging_status" "$run"
    if [[ $result -ne 0 ]]; then exit "$result"; fi
    if [[ $staging_status -ne 0 ]]; then exit "$staging_status"; fi
    exit "$cleanup_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
root=$(pwd -P)
if [[ $(git rev-parse --show-toplevel) != "$root" ]]; then
    printf 'Run from the plugin Git root.\n' >&2
    exit 2
fi
staging=$(mktemp -d "${TMPDIR:?Set TMPDIR to an owned scratch directory}/kanban-delivery-source.XXXXXX")
mkdir "$staging/source"
git ls-files --cached --others --exclude-standard --deduplicate -z -- \
    __init__.py plugin.yaml delivery scripts tests > "$staging/inputs"
while IFS= read -r -d '' input; do
    if git check-ignore --no-index -q -- "$input"; then continue; else ignored=$?; fi
    if [[ $ignored -ne 1 ]]; then exit "$ignored"; fi
    case $input in
        __init__.py|plugin.yaml|delivery/*.py|scripts/*.py|scripts/*.sh|tests/*.py) ;;
        *) printf 'Unsupported input: %s\n' "$input" >&2; exit 2 ;;
    esac
    if [[ ! -f $input || -L $input || $(realpath -e -- "$input") != "$root/$input" ]]; then
        printf 'Missing, linked or nonregular input: %s\n' "$input" >&2
        exit 2
    fi
    mkdir -p -- "$staging/source/$(dirname -- "$input")"
    cp -- "$input" "$staging/source/$input"
    chmod 644 "$staging/source/$input"
done < "$staging/inputs"
for required in __init__.py plugin.yaml delivery/service.py scripts/verify.sh tests/test_native.py; do
    test -f "$staging/source/$required"
done
chmod -R a+rX "$staging"
if [[ $# -eq 0 ]]; then set -- bash scripts/verify.sh; fi
docker run --rm --name "$run" --label "$label" --network none \
    --read-only --user 65532:65532 --cap-drop ALL --security-opt no-new-privileges \
    --cpus 2 --memory 2g --pids-limit 256 \
    --tmpfs /scratch:rw,nosuid,nodev,size=512m,mode=1777 \
    --mount "type=bind,src=$staging/source,dst=/source,readonly" "$image" "$@"
