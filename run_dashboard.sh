#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_ACTIVATE="$ROOT_DIR/.venv/bin/activate"

if [[ ! -f "$VENV_ACTIVATE" ]]; then
  echo "Expected virtualenv activate script at $VENV_ACTIVATE" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_ACTIVATE"

#export JAVA_HOME="${JAVA_HOME:-/tools/Xilinx/Vivado/2024.2/tps/lnx64/jre21.0.1_12}"
#for ews
export JAVA_HOME="/software/xilinx-2025.1/2025.1/Vivado/tps/lnx64/jre21.0.1_12"
export PATH="$JAVA_HOME/bin:$PATH"
if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then
  export LD_LIBRARY_PATH="$JAVA_HOME/lib/server:$LD_LIBRARY_PATH"
else
  export LD_LIBRARY_PATH="$JAVA_HOME/lib/server"
fi

export DCP_DASHBOARD_REPO_ROOT="${DCP_DASHBOARD_REPO_ROOT:-$ROOT_DIR}"

python -m dashboard_backend.preflight

exec python -m uvicorn dashboard_backend.main:app \
  --host "${DCP_DASHBOARD_HOST:-0.0.0.0}" \
  --port "${DCP_DASHBOARD_PORT:-8000}" \
  "$@"
