#!/usr/bin/env python3

import argparse
import csv
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from RapidWrightMCP.rapidwright_tools import (
    initialize_rapidwright,
    read_checkpoint as rw_read_checkpoint,
    analyze_fabric_for_pblock,
    convert_fabric_region_to_pblock_ranges,
)

from VivadoMCP.vivado_mcp_server import run_tcl_command

def fail(msg: str) -> None:
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


def expect_success(result: Any, step_name: str) -> Any:
    if result is None:
        fail(step_name + " returned None")

    if isinstance(result, dict):
        if "error" in result and result["error"]:
            fail(step_name + " failed: " + str(result["error"]))
        status = result.get("status")
        if status not in (None, "success", "already_initialized"):
            fail(step_name + " failed: " + json.dumps(result, indent=2))
        return result

    if isinstance(result, str):
        lower = result.lower()
        if lower.startswith("error") or "\nerror:" in lower or " error:" in lower:
            fail(step_name + " failed:\n" + result)
        return result

    return result


def tcl(command: str, timeout: float = 300.0) -> str:
    return expect_success(run_tcl_command(command, timeout=timeout), command)


def open_checkpoint(dcp_path: str, timeout: float = 300.0) -> str:
    return tcl(f"open_checkpoint {{{dcp_path}}}", timeout=timeout)


def close_design(timeout: float = 60.0) -> str:
    return tcl("catch {close_design}", timeout=timeout)


def report_timing_summary(timeout: float = 300.0) -> str:
    return tcl("report_timing_summary -delay_type max -max_paths 10", timeout=timeout)


def report_route_status(timeout: float = 300.0) -> str:
    return tcl("report_route_status", timeout=timeout)


def place_design_cmd(directive: Optional[str] = None, timeout: float = 3600.0) -> str:
    cmd = "place_design"
    if directive:
        cmd += f" -directive {directive}"
    return tcl(cmd, timeout=timeout)


def route_design_cmd(directive: Optional[str] = None, timeout: float = 3600.0) -> str:
    cmd = "route_design"
    if directive:
        cmd += f" -directive {directive}"
    return tcl(cmd, timeout=timeout)


def write_checkpoint_cmd(dcp_path: str, force: bool = False, timeout: float = 300.0) -> str:
    force_flag = " -force" if force else ""
    return tcl(f"write_checkpoint{force_flag} {{{dcp_path}}}", timeout=timeout)


def report_utilization_for_pblock(timeout: float = 300.0) -> str:
    return tcl("report_utilization -return_string", timeout=timeout)


def maybe_get_clock_period_ns(timeout: float = 30.0) -> Optional[float]:
    try:
        output = tcl("get_property PERIOD [lindex [get_clocks] 0]", timeout=timeout)
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                return float(line)
            except ValueError:
                continue
    except Exception:
        return None
    return None


def pblock_exists(pblock_name: str, timeout: float = 30.0) -> bool:
    output = tcl(f"llength [get_pblocks {pblock_name}]", timeout=timeout)
    for line in output.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line) > 0
    return False


def delete_pblock_if_exists(pblock_name: str, timeout: float = 60.0) -> str:
    if pblock_exists(pblock_name, timeout=timeout):
        return tcl(f"delete_pblocks [get_pblocks {pblock_name}]", timeout=timeout)
    return f"pblock {pblock_name} did not exist"


def get_cell_query(apply_to: str) -> str:
    if apply_to == "current_design":
        return "[get_cells -hierarchical]"
    return f"[get_cells {apply_to}]"


def create_and_apply_pblock(
    pblock_name: str,
    ranges: str,
    apply_to: str = "current_design",
    is_soft: bool = False,
    timeout: float = 300.0,
) -> str:
    outputs = []

    outputs.append(delete_pblock_if_exists(pblock_name, timeout=timeout))
    outputs.append(tcl(f"create_pblock {pblock_name}", timeout=timeout))
    outputs.append(tcl(f"resize_pblock [get_pblocks {pblock_name}] -add {{{ranges}}}", timeout=timeout))
    outputs.append(
        tcl(
            f"set_property IS_SOFT {'1' if is_soft else '0'} [get_pblocks {pblock_name}]",
            timeout=timeout,
        )
    )

    cell_query = get_cell_query(apply_to)
    outputs.append(
        tcl(
            f"add_cells_to_pblock [get_pblocks {pblock_name}] {cell_query}",
            timeout=timeout,
        )
    )

    outputs.append(tcl(f"report_property [get_pblocks {pblock_name}]", timeout=timeout))
    return "\n".join(str(x) for x in outputs if x is not None)


def unplace_cells(apply_to: str = "current_design", timeout: float = 300.0) -> str:
    cell_query = get_cell_query(apply_to)
    return tcl(f"unplace_cell {cell_query}", timeout=timeout)


def unroute_design(timeout: float = 300.0) -> str:
    return tcl("route_design -unroute", timeout=timeout)


def parse_timing_summary_static(timing_report: str) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {
        "wns": None,
        "tns": None,
        "failing_endpoints": None,
    }

    lines = timing_report.splitlines()
    header_idx = -1

    for i, line in enumerate(lines):
        if "WNS(ns)" in line and "TNS(ns)" in line:
            header_idx = i
            break

    if header_idx < 0:
        return result

    data_idx = header_idx + 2
    if data_idx >= len(lines):
        return result

    parts = lines[data_idx].strip().split()
    if len(parts) >= 3:
        try:
            result["wns"] = float(parts[0])
            result["tns"] = float(parts[1])
            result["failing_endpoints"] = float(parts[2])
        except ValueError:
            pass

    return result


def _first_int_from_patterns(text: str, patterns: List[str]) -> int:
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                pass
    return 0


def parse_utilization_report(report_text: str) -> Dict[str, int]:
    resources = {
        "LUT": 0,
        "FF": 0,
        "DSP": 0,
        "BRAM": 0,
        "URAM": 0,
    }

    lut_patterns = [
        r"^\|\s*Slice LUTs\s*\|\s*([0-9,]+)\s*\|",
        r"^\|\s*CLB LUTs\s*\|\s*([0-9,]+)\s*\|",
        r"^\|\s*LUT as Logic\s*\|\s*([0-9,]+)\s*\|",
    ]
    ff_patterns = [
        r"^\|\s*Slice Registers\s*\|\s*([0-9,]+)\s*\|",
        r"^\|\s*CLB Registers\s*\|\s*([0-9,]+)\s*\|",
        r"^\|\s*Register as Flip Flop\s*\|\s*([0-9,]+)\s*\|",
    ]
    dsp_patterns = [
        r"^\|\s*DSPs\s*\|\s*([0-9,]+)\s*\|",
        r"^\|\s*DSP48E1\s*\|\s*([0-9,]+)\s*\|",
        r"^\|\s*DSP48E2\s*\|\s*([0-9,]+)\s*\|",
    ]
    bram_patterns = [
        r"^\|\s*Block RAM Tile\s*\|\s*([0-9,]+(?:\.[0-9]+)?)\s*\|",
        r"^\|\s*RAMB36(?:/FIFO\*)?\s*\|\s*([0-9,]+)\s*\|",
    ]
    uram_patterns = [
        r"^\|\s*URAM\s*\|\s*([0-9,]+)\s*\|",
        r"^\|\s*URAM288\s*\|\s*([0-9,]+)\s*\|",
    ]

    resources["LUT"] = _first_int_from_patterns(report_text, lut_patterns)
    resources["FF"] = _first_int_from_patterns(report_text, ff_patterns)
    resources["DSP"] = _first_int_from_patterns(report_text, dsp_patterns)
    resources["URAM"] = _first_int_from_patterns(report_text, uram_patterns)

    bram_tile_match = re.search(bram_patterns[0], report_text, flags=re.IGNORECASE | re.MULTILINE)
    if bram_tile_match:
        try:
            resources["BRAM"] = int(float(bram_tile_match.group(1).replace(",", "")))
        except ValueError:
            resources["BRAM"] = 0
    else:
        resources["BRAM"] = _first_int_from_patterns(report_text, bram_patterns[1:])

    return resources


def calculate_fmax_mhz(wns_ns: Optional[float], clock_period_ns: Optional[float]) -> Optional[float]:
    if wns_ns is None or clock_period_ns is None or clock_period_ns <= 0.0:
        return None

    achievable_period_ns = clock_period_ns - wns_ns
    if achievable_period_ns <= 0.0:
        return None

    return 1000.0 / achievable_period_ns


def print_timing(label: str, timing_text: str, clock_period_ns: Optional[float]) -> Dict[str, Optional[float]]:
    parsed = parse_timing_summary_static(timing_text)
    wns = parsed["wns"]
    tns = parsed["tns"]
    failing = parsed["failing_endpoints"]
    fmax = calculate_fmax_mhz(wns, clock_period_ns)

    print("")
    print(label)
    print("-" * len(label))
    if wns is not None:
        print("WNS (ns):", wns)
    if tns is not None:
        print("TNS (ns):", tns)
    if failing is not None:
        print("Failing endpoints:", int(failing))
    if clock_period_ns is not None:
        print("Clock period (ns):", clock_period_ns)
    if fmax is not None:
        print("Estimated achievable fmax (MHz): %.3f" % fmax)

    return parsed


def choose_targets(util: Dict[str, int], args: argparse.Namespace) -> Dict[str, int]:
    parsed_any = any(util[k] > 0 for k in ["LUT", "FF", "DSP", "BRAM", "URAM"])

    if parsed_any:
        target_lut = args.target_lut if args.target_lut is not None else max(64, int(util["LUT"] * 1.5))
        target_ff = args.target_ff if args.target_ff is not None else max(64, int(util["FF"] * 1.5))
        target_dsp = args.target_dsp if args.target_dsp is not None else int(util["DSP"] * 1.5)
        target_bram = args.target_bram if args.target_bram is not None else int(util["BRAM"] * 1.5)
    else:
        target_lut = args.target_lut if args.target_lut is not None else 256
        target_ff = args.target_ff if args.target_ff is not None else 256
        target_dsp = args.target_dsp if args.target_dsp is not None else 0
        target_bram = args.target_bram if args.target_bram is not None else 0

    return {
        "LUT": target_lut,
        "FF": target_ff,
        "DSP": target_dsp,
        "BRAM": target_bram,
    }


def make_output_dir(base_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = base_dir / f"outputrun_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=False)
    return out_dir


def clamp_region(
    col_min: int,
    row_min: int,
    width: int,
    height: int,
    fabric_bounds: Dict[str, int],
) -> Dict[str, int]:
    min_col = int(fabric_bounds["min_col"])
    max_col = int(fabric_bounds["max_col"])
    min_row = int(fabric_bounds["min_row"])
    max_row = int(fabric_bounds["max_row"])

    max_start_col = max_col - width + 1
    max_start_row = max_row - height + 1

    c0 = max(min_col, min(col_min, max_start_col))
    r0 = max(min_row, min(row_min, max_start_row))

    return {
        "col_min": c0,
        "col_max": c0 + width - 1,
        "row_min": r0,
        "row_max": r0 + height - 1,
    }


def mutate_region(
    region: Dict[str, int],
    fabric_bounds: Dict[str, int],
    rng: random.Random,
    jump_bias: float = 0.30,
) -> Dict[str, int]:
    width = int(region["col_max"]) - int(region["col_min"]) + 1
    height = int(region["row_max"]) - int(region["row_min"]) + 1

    if rng.random() < jump_bias:
        dx = rng.randint(-80, 80)
        dy = rng.randint(-80, 80)
    else:
        dx = rng.randint(-20, 20)
        dy = rng.randint(-20, 20)

    new_col_min = int(region["col_min"]) + dx
    new_row_min = int(region["row_min"]) + dy
    return clamp_region(new_col_min, new_row_min, width, height, fabric_bounds)


def generate_seed_regions(
    recommended_region: Dict[str, int],
    fabric_bounds: Dict[str, int],
    count: int,
    rng: random.Random,
) -> List[Dict[str, int]]:
    width = int(recommended_region["col_max"]) - int(recommended_region["col_min"]) + 1
    height = int(recommended_region["row_max"]) - int(recommended_region["row_min"]) + 1

    min_col = int(fabric_bounds["min_col"])
    max_col = int(fabric_bounds["max_col"])
    min_row = int(fabric_bounds["min_row"])
    max_row = int(fabric_bounds["max_row"])

    max_start_col = max_col - width + 1
    max_start_row = max_row - height + 1

    rec = clamp_region(
        int(recommended_region["col_min"]),
        int(recommended_region["row_min"]),
        width,
        height,
        fabric_bounds,
    )

    center = clamp_region(
        (min_col + max_col) // 2 - width // 2,
        (min_row + max_row) // 2 - height // 2,
        width,
        height,
        fabric_bounds,
    )

    anchors = [
        rec,
        center,
        clamp_region(min_col, min_row, width, height, fabric_bounds),
        clamp_region(max_start_col, min_row, width, height, fabric_bounds),
        clamp_region(min_col, max_start_row, width, height, fabric_bounds),
        clamp_region(max_start_col, max_start_row, width, height, fabric_bounds),
        clamp_region(max_start_col - 10, max_start_row - 10, width, height, fabric_bounds),
        clamp_region(min_col + 10, max_start_row - 10, width, height, fabric_bounds),
        clamp_region(max_start_col - 10, min_row + 10, width, height, fabric_bounds),
    ]

    regions: List[Dict[str, int]] = []
    seen = set()

    for region in anchors:
        key = (region["col_min"], region["col_max"], region["row_min"], region["row_max"])
        if key not in seen:
            regions.append(region)
            seen.add(key)

    while len(regions) < count:
        start_col = rng.randint(min_col, max_start_col)
        start_row = rng.randint(min_row, max_start_row)
        region = clamp_region(start_col, start_row, width, height, fabric_bounds)
        key = (region["col_min"], region["col_max"], region["row_min"], region["row_max"])
        if key in seen:
            continue
        seen.add(key)
        regions.append(region)

    return regions


def timing_score(parsed: Dict[str, Optional[float]]) -> Tuple[float, float]:
    wns = parsed["wns"]
    tns = parsed["tns"]

    if wns is None:
        wns_score = float("-inf")
    else:
        wns_score = float(wns)

    if tns is None:
        tns_score = float("-inf")
    else:
        tns_score = float(tns)

    return (wns_score, tns_score)


def is_better_timing(
    new_parsed: Dict[str, Optional[float]],
    ref_parsed: Dict[str, Optional[float]],
) -> bool:
    return timing_score(new_parsed) > timing_score(ref_parsed)


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def attempt_one_candidate(
    input_dcp: Path,
    output_dcp: Path,
    pblock_name: str,
    pblock_ranges: str,
    apply_to: str,
    hard_pblock: bool,
    keep_placement: bool,
    keep_routing: bool,
    place_directive: str,
    route_directive: str,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": "unknown",
        "output_dcp": str(output_dcp),
        "pblock_ranges": pblock_ranges,
    }

    close_design()
    open_checkpoint(str(input_dcp))

    create_log = create_and_apply_pblock(
        pblock_name=pblock_name,
        ranges=pblock_ranges,
        apply_to=apply_to,
        is_soft=(not hard_pblock),
    )
    result["create_and_apply_log"] = create_log

    if not keep_placement:
        result["unplace_log"] = unplace_cells(apply_to=apply_to)

    if not keep_routing:
        result["unroute_log"] = unroute_design()

    place_log = place_design_cmd(directive=place_directive)
    route_log = route_design_cmd(directive=route_directive)
    route_status = report_route_status()
    timing_text = report_timing_summary()
    timing_parsed = parse_timing_summary_static(timing_text)
    write_checkpoint_log = write_checkpoint_cmd(str(output_dcp), force=True)

    result["place_log"] = place_log
    result["route_log"] = route_log
    result["route_status"] = route_status
    result["timing_text"] = timing_text
    result["timing_parsed"] = timing_parsed
    result["write_checkpoint_log"] = write_checkpoint_log
    result["status"] = "success"

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Try aggressive pblock search until timing improves.")
    parser.add_argument("--input-dcp", required=True, help="Input DCP path")
    parser.add_argument("--output-root", default=".", help="Directory where outputrun_* is created")
    parser.add_argument("--pblock-name", default="pblock_auto_0", help="Pblock name")
    parser.add_argument(
        "--apply-to",
        default="current_design",
        help='Cell pattern to apply pblock to, or "current_design"',
    )
    parser.add_argument(
        "--use-clock-regions",
        action="store_true",
        help="Use CLOCKREGION ranges instead of site ranges",
    )
    parser.add_argument(
        "--hard-pblock",
        action="store_true",
        help="Set IS_SOFT=0",
    )
    parser.add_argument(
        "--place-directive",
        default="Default",
        help="Vivado place_design directive",
    )
    parser.add_argument(
        "--route-directive",
        default="Default",
        help="Vivado route_design directive",
    )
    parser.add_argument(
        "--target-lut",
        type=int,
        default=None,
        help="Override LUT target instead of using utilization report",
    )
    parser.add_argument(
        "--target-ff",
        type=int,
        default=None,
        help="Override FF target instead of using utilization report",
    )
    parser.add_argument(
        "--target-dsp",
        type=int,
        default=None,
        help="Override DSP target instead of using utilization report",
    )
    parser.add_argument(
        "--target-bram",
        type=int,
        default=None,
        help="Override BRAM target instead of using utilization report",
    )
    parser.add_argument(
        "--keep-placement",
        action="store_true",
        help="Do not unplace cells before place_design",
    )
    parser.add_argument(
        "--keep-routing",
        action="store_true",
        help="Do not unroute before route_design",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=12,
        help="Maximum successful implementation attempts",
    )
    parser.add_argument(
        "--continue-after-improvement",
        action="store_true",
        help="Keep searching even after timing improves over baseline",
    )
    parser.add_argument(
        "--seed-count",
        type=int,
        default=10,
        help="Initial number of seed regions",
    )
    parser.add_argument(
        "--elite-count",
        type=int,
        default=4,
        help="How many best successful candidates to mutate from",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=7,
        help="Random seed",
    )
    args = parser.parse_args()

    rng = random.Random(args.random_seed)

    input_dcp = Path(args.input_dcp).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()

    if not input_dcp.exists():
        fail("Input DCP not found: " + str(input_dcp))

    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = make_output_dir(output_root)

    print("Input DCP: ", input_dcp)
    print("Run dir:   ", run_dir)

    rw_init = expect_success(initialize_rapidwright(jvm_max_memory="8G"), "initialize_rapidwright")
    rw_dcp = expect_success(rw_read_checkpoint(str(input_dcp)), "RapidWright read_checkpoint")

    baseline_meta: Dict[str, Any] = {
        "input_dcp": str(input_dcp),
        "rapidwright_init": rw_init,
        "rapidwright_design": rw_dcp,
        "random_seed": args.random_seed,
    }

    close_design()
    open_checkpoint(str(input_dcp))

    baseline_timing_text = expect_success(report_timing_summary(), "baseline report_timing_summary")
    baseline_clock_period = maybe_get_clock_period_ns()
    baseline_timing_parsed = parse_timing_summary_static(baseline_timing_text)
    baseline_util_text = expect_success(report_utilization_for_pblock(), "baseline report_utilization_for_pblock")
    baseline_util = parse_utilization_report(baseline_util_text)
    targets = choose_targets(baseline_util, args)

    analyze_result = expect_success(
        analyze_fabric_for_pblock(
            target_lut_count=targets["LUT"],
            target_ff_count=targets["FF"],
            target_dsp_count=targets["DSP"],
            target_bram_count=targets["BRAM"],
        ),
        "analyze_fabric_for_pblock",
    )

    recommended_region = analyze_result.get("recommended_region")
    fabric_bounds = analyze_result.get("fabric_bounds")

    if not recommended_region or not fabric_bounds:
        fail("analyze_fabric_for_pblock() did not return recommended_region/fabric_bounds")

    baseline_meta["baseline_timing_parsed"] = baseline_timing_parsed
    baseline_meta["baseline_clock_period_ns"] = baseline_clock_period
    baseline_meta["baseline_utilization"] = baseline_util
    baseline_meta["targets"] = targets
    baseline_meta["analyze_result"] = analyze_result
    baseline_meta["start_region"] = recommended_region

    write_json(run_dir / "baseline_meta.json", baseline_meta)
    write_text(run_dir / "baseline_timing_summary.txt", baseline_timing_text)
    write_text(run_dir / "baseline_utilization.txt", baseline_util_text)

    print_timing("Baseline Timing", baseline_timing_text, baseline_clock_period)

    candidate_queue = generate_seed_regions(
        recommended_region=recommended_region,
        fabric_bounds=fabric_bounds,
        count=max(args.seed_count, args.max_attempts),
        rng=rng,
    )

    attempt_rows: List[Dict[str, Any]] = []
    seen_ranges = set()
    seen_regions = set()

    best_parsed = baseline_timing_parsed
    best_attempt_num: Optional[int] = None
    best_output_dcp: Optional[str] = None

    successful_regions: List[Tuple[Dict[str, int], Dict[str, Optional[float]]]] = []

    input_stem = input_dcp.stem
    attempt_index = 0
    skipped_index = 0

    queue_index = 0
    while attempt_index < args.max_attempts and queue_index < len(candidate_queue):
        candidate = candidate_queue[queue_index]
        queue_index += 1

        region_key = (candidate["col_min"], candidate["col_max"], candidate["row_min"], candidate["row_max"])
        if region_key in seen_regions:
            continue
        seen_regions.add(region_key)

        convert_result = convert_fabric_region_to_pblock_ranges(
            col_min=int(candidate["col_min"]),
            col_max=int(candidate["col_max"]),
            row_min=int(candidate["row_min"]),
            row_max=int(candidate["row_max"]),
            device_name=None,
            use_clock_regions=args.use_clock_regions,
        )

        if isinstance(convert_result, dict) and convert_result.get("error"):
            skipped_index += 1
            skip_summary = {
                "skip_num": skipped_index,
                "candidate_region": candidate,
                "status": "skipped",
                "error": convert_result["error"],
            }
            write_json(run_dir / f"skipped_{skipped_index:03d}_summary.json", skip_summary)
            attempt_rows.append(skip_summary)
            continue

        pblock_ranges = convert_result.get("pblock_ranges")
        if not pblock_ranges:
            skipped_index += 1
            skip_summary = {
                "skip_num": skipped_index,
                "candidate_region": candidate,
                "status": "skipped",
                "error": "No pblock_ranges returned",
            }
            write_json(run_dir / f"skipped_{skipped_index:03d}_summary.json", skip_summary)
            attempt_rows.append(skip_summary)
            continue

        if pblock_ranges in seen_ranges:
            skipped_index += 1
            skip_summary = {
                "skip_num": skipped_index,
                "candidate_region": candidate,
                "status": "skipped",
                "error": "Duplicate pblock range",
                "pblock_ranges": pblock_ranges,
            }
            write_json(run_dir / f"skipped_{skipped_index:03d}_summary.json", skip_summary)
            attempt_rows.append(skip_summary)
            continue

        seen_ranges.add(pblock_ranges)

        attempt_index += 1
        attempt_num = attempt_index

        attempt_prefix = f"attempt_{attempt_num:03d}"
        output_dcp = run_dir / f"{input_stem}_{attempt_num}.dcp"

        print("")
        print(f"Attempt {attempt_num}")
        print("----------")
        print("Region:", candidate)
        print("Ranges:", pblock_ranges)

        attempt_summary: Dict[str, Any] = {
            "attempt_num": attempt_num,
            "candidate_region": candidate,
            "convert_result": convert_result,
            "pblock_ranges": pblock_ranges,
            "output_dcp": str(output_dcp),
            "status": "unknown",
        }

        try:
            result = attempt_one_candidate(
                input_dcp=input_dcp,
                output_dcp=output_dcp,
                pblock_name=args.pblock_name,
                pblock_ranges=pblock_ranges,
                apply_to=args.apply_to,
                hard_pblock=args.hard_pblock,
                keep_placement=args.keep_placement,
                keep_routing=args.keep_routing,
                place_directive=args.place_directive,
                route_directive=args.route_directive,
            )

            attempt_summary.update(result)
            parsed = result["timing_parsed"]
            improved_vs_baseline = is_better_timing(parsed, baseline_timing_parsed)
            improved_vs_best_before = is_better_timing(parsed, best_parsed)

            attempt_summary["improved_vs_baseline"] = improved_vs_baseline
            attempt_summary["improved_vs_best_before"] = improved_vs_best_before

            write_text(run_dir / f"{attempt_prefix}_create_and_apply.log", result["create_and_apply_log"])
            if "unplace_log" in result:
                write_text(run_dir / f"{attempt_prefix}_unplace.log", result["unplace_log"])
            if "unroute_log" in result:
                write_text(run_dir / f"{attempt_prefix}_unroute.log", result["unroute_log"])
            write_text(run_dir / f"{attempt_prefix}_place.log", result["place_log"])
            write_text(run_dir / f"{attempt_prefix}_route.log", result["route_log"])
            write_text(run_dir / f"{attempt_prefix}_route_status.log", result["route_status"])
            write_text(run_dir / f"{attempt_prefix}_timing_summary.txt", result["timing_text"])
            write_text(run_dir / f"{attempt_prefix}_write_checkpoint.log", result["write_checkpoint_log"])

            print_timing(f"Attempt {attempt_num} Timing", result["timing_text"], baseline_clock_period)

            successful_regions.append((candidate, parsed))
            successful_regions.sort(key=lambda item: timing_score(item[1]), reverse=True)
            successful_regions = successful_regions[:max(1, args.elite_count)]

            if improved_vs_best_before:
                best_parsed = parsed
                best_attempt_num = attempt_num
                best_output_dcp = str(output_dcp)

            new_children: List[Dict[str, int]] = []

            for elite_region, _elite_parsed in successful_regions:
                for _ in range(4):
                    new_children.append(mutate_region(elite_region, fabric_bounds, rng, jump_bias=0.35))

            for _ in range(3):
                random_seed_region = generate_seed_regions(recommended_region, fabric_bounds, 1, rng)[0]
                new_children.append(random_seed_region)

            candidate_queue.extend(new_children)

            if improved_vs_baseline and not args.continue_after_improvement:
                attempt_summary["stop_reason"] = "timing_improved_over_baseline"
                write_json(run_dir / f"{attempt_prefix}_summary.json", attempt_summary)
                attempt_rows.append(attempt_summary)
                print("")
                print(f"Stopping after attempt {attempt_num}: timing improved over baseline.")
                break

        except Exception as exc:
            attempt_summary["status"] = "failed"
            attempt_summary["error"] = str(exc)
            write_text(run_dir / f"{attempt_prefix}_error.log", str(exc))

        write_json(run_dir / f"{attempt_prefix}_summary.json", attempt_summary)
        attempt_rows.append(attempt_summary)

    summary_json = {
        "input_dcp": str(input_dcp),
        "run_dir": str(run_dir),
        "baseline_timing_parsed": baseline_timing_parsed,
        "baseline_clock_period_ns": baseline_clock_period,
        "best_attempt_num": best_attempt_num,
        "best_output_dcp": best_output_dcp,
        "best_timing_parsed": best_parsed,
        "attempt_count": len([r for r in attempt_rows if r.get("status") != "skipped"]),
        "skip_count": len([r for r in attempt_rows if r.get("status") == "skipped"]),
        "attempts": attempt_rows,
    }
    write_json(run_dir / "run_summary.json", summary_json)

    csv_path = run_dir / "run_summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "attempt_num",
                "status",
                "pblock_ranges",
                "col_min",
                "col_max",
                "row_min",
                "row_max",
                "wns",
                "tns",
                "failing_endpoints",
                "improved_vs_baseline",
                "output_dcp",
                "error",
            ]
        )
        for row in attempt_rows:
            parsed = row.get("timing_parsed", {})
            region = row.get("candidate_region", {})
            writer.writerow(
                [
                    row.get("attempt_num", row.get("skip_num")),
                    row.get("status"),
                    row.get("pblock_ranges"),
                    region.get("col_min"),
                    region.get("col_max"),
                    region.get("row_min"),
                    region.get("row_max"),
                    parsed.get("wns"),
                    parsed.get("tns"),
                    parsed.get("failing_endpoints"),
                    row.get("improved_vs_baseline"),
                    row.get("output_dcp"),
                    row.get("error"),
                ]
            )

    print("")
    print("Run complete")
    print("------------")
    print("Run dir:", run_dir)
    print("Successful attempts:", len([r for r in attempt_rows if r.get("status") != "skipped"]))
    print("Skipped candidates:", len([r for r in attempt_rows if r.get("status") == "skipped"]))
    print("Best attempt:", best_attempt_num)
    print("Best output DCP:", best_output_dcp)

    if baseline_timing_parsed["wns"] is not None:
        print("Baseline WNS:", baseline_timing_parsed["wns"])
    if best_parsed["wns"] is not None:
        print("Best WNS:", best_parsed["wns"])
    if baseline_timing_parsed["tns"] is not None:
        print("Baseline TNS:", baseline_timing_parsed["tns"])
    if best_parsed["tns"] is not None:
        print("Best TNS:", best_parsed["tns"])


if __name__ == "__main__":
    main()