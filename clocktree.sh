#!/usr/bin/env bash
set -euo pipefail
ct_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ct_root"
source "$ct_root/scripts/eda_env.sh"
cd "$ct_root"
ct_python="${CLOCKTREE_PYTHON:-python3}"
if ! command -v "$ct_python" >/dev/null 2>&1; then
  echo "ClockTree7: Python 3.6+ is required. Set CLOCKTREE_PYTHON to its executable path." >&2
  exit 1
fi
if ! "$ct_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 6) else 1)'; then
  echo "ClockTree7: Python 3.6+ is required; selected interpreter: $ct_python" >&2
  exit 1
fi
if [[ $# == 0 ]]; then set -- gui; fi
exec "$ct_python" -m clocktree "$@"
