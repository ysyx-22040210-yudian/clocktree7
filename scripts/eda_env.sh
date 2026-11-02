#!/usr/bin/env bash
# Source this file. Preserve the device's own EDA paths and license settings.
# A site/user may opt in to an existing Bash EDA setup for desktop launches.
ct_setup_default="${XDG_CONFIG_HOME:-$HOME/.config}/clocktree7/eda.sh"
ct_setup="${CLOCKTREE_EDA_SETUP:-$ct_setup_default}"
if [[ -n "${CLOCKTREE_EDA_SETUP:-}" ]] || { ! command -v verdi >/dev/null 2>&1 && [[ -f "$ct_setup" ]]; }; then
  if [[ ! -r "$ct_setup" ]]; then
    echo "ClockTree7: cannot read EDA setup: $ct_setup" >&2
    return 1
  fi
  ct_restore_nounset=0
  case "$-" in *u*) ct_restore_nounset=1; set +u ;; esac
  if source "$ct_setup"; then ct_setup_status=0; else ct_setup_status=$?; fi
  if [[ "$ct_restore_nounset" == 1 ]]; then set -u; fi
  if [[ "$ct_setup_status" != 0 ]]; then
    echo "ClockTree7: EDA setup returned an error: $ct_setup" >&2
    return "$ct_setup_status"
  fi
fi
if ! command -v verdi >/dev/null 2>&1 && [[ -n "${VERDI_HOME:-}" && -x "$VERDI_HOME/bin/verdi" ]]; then
  export PATH="$VERDI_HOME/bin:$PATH"
fi
unset ct_setup ct_setup_default ct_restore_nounset ct_setup_status
