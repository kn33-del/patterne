#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <input.dcp> [optimizer args...]" >&2
  exit 2
fi

DCP="$1"
shift

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${DEMO_LOG_DIR:-$ROOT_DIR/demo_logs}"

mkdir -p "$LOG_DIR"

next_run=1
while [[ -e "$LOG_DIR/run${next_run}" || -e "$LOG_DIR/run${next_run}_terminal.log" ]]; do
  next_run=$((next_run + 1))
done

run_name="run${next_run}"
run_dir="$LOG_DIR/$run_name"
output_dcp="$LOG_DIR/output_${run_name}.dcp"
terminal_log="$LOG_DIR/${run_name}_terminal.log"

echo "Demo run: $run_name"
echo "Input DCP: $DCP"
echo "Output DCP: $output_dcp"
echo "Run dir: $run_dir"
echo "Terminal log: $terminal_log"

cd "$ROOT_DIR"

python Optimizer/optimizer.py "$DCP" \
  --output "$output_dcp" \
  --run-dir "$run_dir" \
  --continue-when-timing-met \
  "$@" \
  2>&1 | tee "$terminal_log"

if [[ -f history.json ]]; then
  cp history.json "$LOG_DIR/history_after_${run_name}.json"
  tee "$LOG_DIR/history_after_${run_name}.txt" < history.json
else
  echo "Warning: history.json was not created." >&2
fi

echo
echo "Structured RESULT lines:"
grep -R "RESULT:" "$terminal_log" "$run_dir" || true

echo
echo "Saved artifacts:"
echo "- $terminal_log"
echo "- $run_dir"
echo "- $LOG_DIR/history_after_${run_name}.json"
echo "- $LOG_DIR/history_after_${run_name}.txt"
