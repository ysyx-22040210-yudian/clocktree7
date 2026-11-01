#!/usr/bin/env bash
# Rebuild the prepared, pinned validation sources on the verified VM.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$root/scripts/eda_env.sh"
profile="${1:?Usage: bash scripts/build_soc_kdb.sh pulp_soc|openc910|opentitan_clkmgr [new-build-dir]}"
build="${2:-/home/clocktree7-work/kdb/$profile}"
[[ ! -e "$build/simv.daidir/kdb.elab++" ]] || { echo "KDB already exists; choose a new build directory." >&2; exit 1; }
mkdir -p "$build"
case "$profile" in
  pulp_soc)
    (cd "$root/validation/deps/pulp_soc" && "$root/validation/tools/bender" script verilator --local --no-default-target -t rtl -t rtl_sim -t simulation) > "$root/validation/configs/pulp_soc.f"
    top=pulp_soc
    filelist="$root/validation/configs/pulp_soc.f"
    extra=("+incdir+$root/validation/projects/pulpissimo/hw/includes" -pvalue+pulp_soc.SIM_STDOUT=0 "$root/validation/projects/pulpissimo/hw/asic_autogen_rom.sv")
    template="$root/validation/configs/pulp_soc_kdb_complete.json"
    ;;
  openc910)
    (cd "$root" && python3 scripts/prepare_c910.py)
    top=soc
    filelist="$root/validation/configs/openc910.f"
    extra=()
    template="$root/validation/configs/openc910_ordered7_kdb.json"
    ;;
  opentitan_clkmgr)
    top=clkmgr
    filelist="$root/validation/configs/opentitan_silver.f"
    extra=()
    template="$root/validation/configs/opentitan_clkmgr_kdb.json"
    ;;
  *) echo "Unknown profile: $profile" >&2; exit 1 ;;
esac
cd "$build"
vcs -full64 -sverilog -timescale=1ns/1ps -assert svaext -kdb=only -lca -debug_access+all +define+SYNTHESIS -top "$top" -f "$filelist" ${extra[@]+"${extra[@]}"} -o simv 2>&1 | tee build.log
test -d simv.daidir/kdb.elab++
python3 - "$template" "$build/simv.daidir/kdb.elab++" "$build/clocktree-config.json" <<'PY'
import json, sys
from pathlib import Path
config = json.loads(Path(sys.argv[1]).read_text())
config['design'].pop('filelist', None)
config['design']['elab'] = str(Path(sys.argv[2]).resolve())
Path(sys.argv[3]).write_text(json.dumps(config, indent=2) + '\n')
print('Extraction config:', sys.argv[3])
PY
