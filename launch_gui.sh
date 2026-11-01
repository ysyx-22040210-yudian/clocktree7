#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"
source scripts/eda_env.sh
exec python3 -m clocktree gui "$@"
