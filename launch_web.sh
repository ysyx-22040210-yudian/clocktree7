#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"
source scripts/eda_env.sh
args=(serve --host "${CT7_HOST:-0.0.0.0}" --port "${CT7_PORT:-8767}" --runs validation/runs)
sample="examples/flexible_layers/clocktree.json"
if [[ -f "$sample" ]]; then args+=(--result "$sample"); fi
exec python3 -m clocktree "${args[@]}"
