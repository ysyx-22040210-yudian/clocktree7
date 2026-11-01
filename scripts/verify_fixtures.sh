#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
source scripts/eda_env.sh
stamp="$(date +%Y%m%d_%H%M%S)"
for kind in small generate stress; do
    fixture="validation/fixtures/recheck_${stamp}_${kind}"
    run="validation/runs/recheck_${stamp}_${kind}"
    options=()
    [[ "$kind" != generate ]] || options+=(--generate-scopes)
    [[ "$kind" != stress ]] || options+=(--copies 256)
    python3 scripts/make_fixture.py "$fixture" ${options[@]+"${options[@]}"}
    python3 -m clocktree extract --config "$fixture/config.json" --out "$run"
    python3 scripts/check_golden.py "$run/clocktree.json" "$fixture/golden.json" | tee "$run/golden_check.json"
done
