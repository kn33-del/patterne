#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <input.dcp> <output.dcp>" >&2
    exit 1
fi

INPUT_DCP="$(realpath "$1")"
OUTPUT_DCP="$(realpath "$2")"

if [ ! -f "$INPUT_DCP" ]; then
    echo "ERROR: input DCP not found: $INPUT_DCP" >&2
    exit 1
fi

if [ ! -f "$OUTPUT_DCP" ]; then
    echo "ERROR: output DCP not found: $OUTPUT_DCP" >&2
    exit 1
fi

VIVADO_BIN="${VIVADO_BIN:-vivado}"

TMPDIR_GUI="$(mktemp -d)"
TMP1="$TMPDIR_GUI/input_dcp.tcl"
TMP2="$TMPDIR_GUI/output_dcp.tcl"

cleanup() {
    rm -rf "$TMPDIR_GUI"
}
trap cleanup EXIT

cat > "$TMP1" <<EOF
start_gui
open_checkpoint {$INPUT_DCP}
catch { set_property NAME {INPUT_DCP} [current_design] }
puts "Opened input DCP: $INPUT_DCP"
EOF

cat > "$TMP2" <<EOF
start_gui
open_checkpoint {$OUTPUT_DCP}
catch { set_property NAME {OUTPUT_DCP} [current_design] }
puts "Opened output DCP: $OUTPUT_DCP"
EOF

"$VIVADO_BIN" -mode gui -source "$TMP1" >/tmp/vivado_input.log 2>&1 &
PID1=$!

"$VIVADO_BIN" -mode gui -source "$TMP2" >/tmp/vivado_output.log 2>&1 &
PID2=$!

sleep 10

echo "Started Vivado PIDs: $PID1 $PID2"

if command -v wmctrl >/dev/null 2>&1; then
    mapfile -t WINDOWS < <(wmctrl -lx | awk '/Vivado/ {print $1}' | tail -n 2)

    if [ "${#WINDOWS[@]}" -eq 2 ]; then
        DESKTOP_WIDTH="$(xdpyinfo 2>/dev/null | awk '/dimensions:/ {print $2}' | cut -d'x' -f1)"
        DESKTOP_HEIGHT="$(xdpyinfo 2>/dev/null | awk '/dimensions:/ {print $2}' | cut -d'x' -f2)"

        if [ -n "${DESKTOP_WIDTH:-}" ] && [ -n "${DESKTOP_HEIGHT:-}" ]; then
            HALF_WIDTH=$((DESKTOP_WIDTH / 2))
            wmctrl -ir "${WINDOWS[0]}" -e "0,0,0,${HALF_WIDTH},${DESKTOP_HEIGHT}"
            wmctrl -ir "${WINDOWS[1]}" -e "0,${HALF_WIDTH},0,${HALF_WIDTH},${DESKTOP_HEIGHT}"
            wmctrl -ia "${WINDOWS[0]}"
            wmctrl -ia "${WINDOWS[1]}"
        fi
    else
        echo "WARNING: Could not find two Vivado windows with wmctrl." >&2
    fi
elif command -v xdotool >/dev/null 2>&1; then
    mapfile -t WINDOWS < <(xdotool search --name Vivado 2>/dev/null | tail -n 2)
    if [ "${#WINDOWS[@]}" -eq 2 ]; then
        xdotool windowraise "${WINDOWS[0]}"
        xdotool windowraise "${WINDOWS[1]}"
    else
        echo "WARNING: Could not find two Vivado windows with xdotool." >&2
    fi
else
    echo "Neither wmctrl nor xdotool is installed." >&2
    echo "Install one of them for automatic tiling/focus:" >&2
    echo "  sudo apt-get install wmctrl" >&2
    echo "or" >&2
    echo "  sudo apt-get install xdotool" >&2
fi

echo "Input log : /tmp/vivado_input.log"
echo "Output log: /tmp/vivado_output.log"

wait "$PID1" "$PID2"