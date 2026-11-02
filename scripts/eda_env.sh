#!/usr/bin/env bash
# Source this file; it contains installation paths, never license contents.
# Prefer the active verdi command; keep the existing VM deployment as a
# fallback for desktop sessions that do not inherit the EDA shell profile.
ct_verdi_command="$(command -v verdi || true)"
if [[ -n "$ct_verdi_command" ]]; then
  ct_verdi_path="$(readlink -f -- "$ct_verdi_command")"
  ct_verdi_candidate="$(dirname -- "$(dirname -- "$ct_verdi_path")")"
  if [[ -f "$ct_verdi_candidate/share/NPI/L1/TCL/npi_L1.tcl" ]]; then
    export VERDI_HOME="$ct_verdi_candidate"
  fi
fi
if [[ -z "${VERDI_HOME:-}" && -x /home/synopsys/verdi/Verdi_O-2018.09-SP2/bin/verdi ]]; then
  export VERDI_HOME=/home/synopsys/verdi/Verdi_O-2018.09-SP2
fi
export VCS_HOME="${VCS_HOME:-/home/synopsys/vcs/O-2018.09-SP2}"
export VCS_TARGET_ARCH="${VCS_TARGET_ARCH:-linux64}"
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-27000@IC_EDA}"
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-$LM_LICENSE_FILE}"
export PATH="$VCS_HOME/bin:${VERDI_HOME:+$VERDI_HOME/bin:}$PATH"
unset ct_verdi_command ct_verdi_path ct_verdi_candidate
