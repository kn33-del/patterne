#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard_backend.artifacts import calculate_fmax_mhz, parse_timing_summary_text

from Optimizer.RapidWrightMCP import rapidwright_tools


DEFAULT_NETS = [
    "u_bench/sel_a[0]",
    "u_bench/sel_a[1]",
    "u_bench/sel_b[0]",
]


@dataclass
class TimingSnapshot:
    label: str
    report: str
    route_status_report: Optional[str]
    wns_ns: Optional[float]
    tns_ns: Optional[float]
    failing_endpoints: Optional[float]
    clock_period_ns: Optional[float]
    estimated_fmax_mhz: Optional[float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay the strongest known fanout + implementation sequence on a DCP."
    )
    parser.add_argument(
        "--input-dcp",
        required=True,
        help="Absolute or relative path to the input .dcp.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Directory where replay artifacts should be written. Defaults to replay_run_<timestamp>.",
    )
    parser.add_argument(
        "--final-name",
        default="route_cluster_bench_replay_optimized.dcp",
        help="Filename for the final optimized DCP inside the output root.",
    )
    parser.add_argument(
        "--split-factor",
        type=int,
        default=8,
        help="RapidWright fanout split factor to apply to each selected net.",
    )
    parser.add_argument(
        "--net",
        action="append",
        dest="nets",
        default=None,
        help="Override the default fanout nets. Can be passed multiple times.",
    )
    return parser.parse_args()


def ensure_vivado() -> str:
    vivado = shutil.which("vivado")
    if vivado:
        return vivado
    raise RuntimeError("Vivado executable not found in PATH.")


def ensure_dcp(path_value: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Input DCP does not exist: {path}")
    if path.suffix.lower() != ".dcp":
        raise ValueError(f"Input file must end in .dcp: {path}")
    return path


def make_output_root(path_value: Optional[str]) -> Path:
    if path_value:
        root = Path(path_value).expanduser().resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = (Path.cwd() / f"replay_run_{stamp}").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_clock_period_ns(report_text: str) -> Optional[float]:
    patterns = [
        r"Period\(ns\):\s+([0-9]+(?:\.[0-9]+)?)",
        r"Clock Period:\s+([0-9]+(?:\.[0-9]+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, report_text)
        if match:
            return float(match.group(1))
    return None


def capture_timing(label: str, report_path: Path, route_status_path: Optional[Path]) -> TimingSnapshot:
    report_text = load_text(report_path)
    parsed = parse_timing_summary_text(report_text)
    clock_period_ns = parse_clock_period_ns(report_text)
    wns_ns = parsed.get("wns")
    return TimingSnapshot(
        label=label,
        report=str(report_path.resolve()),
        route_status_report=str(route_status_path.resolve()) if route_status_path and route_status_path.exists() else None,
        wns_ns=wns_ns,
        tns_ns=parsed.get("tns"),
        failing_endpoints=parsed.get("failing_endpoints"),
        clock_period_ns=clock_period_ns,
        estimated_fmax_mhz=calculate_fmax_mhz(wns_ns, clock_period_ns),
    )


def vivado_tcl_header() -> str:
    return "\n".join(
        [
            "proc log_marker {msg} { puts \"== ${msg} ==\" }",
            "set_msg_config -id {Common 17-55} -new_severity INFO",
        ]
    )


def run_vivado_batch(
    *,
    output_root: Path,
    step_name: str,
    commands: Iterable[str],
) -> Path:
    vivado = ensure_vivado()
    tcl_path = output_root / f"{step_name}.tcl"
    stdout_path = output_root / f"{step_name}.stdout.log"
    stderr_path = output_root / f"{step_name}.stderr.log"
    vivado_log = output_root / f"{step_name}.vivado.log"
    vivado_jou = output_root / f"{step_name}.vivado.jou"

    script = "\n".join([vivado_tcl_header(), *commands, "exit"])
    write_text(tcl_path, script)

    env = os.environ.copy()
    cmd = [
        vivado,
        "-mode",
        "batch",
        "-source",
        str(tcl_path),
        "-log",
        str(vivado_log),
        "-journal",
        str(vivado_jou),
    ]

    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
        result = subprocess.run(
            cmd,
            cwd=str(output_root),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            check=False,
            text=True,
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"Vivado step '{step_name}' failed with exit code {result.returncode}. "
            f"See {vivado_log} and {stderr_path}."
        )

    return vivado_log


def run_rapidwright_fanout(
    *,
    input_dcp: Path,
    output_dcp: Path,
    nets: List[str],
    split_factor: int,
) -> dict:
    init_result = rapidwright_tools.initialize_rapidwright()
    if init_result.get("status") not in {"success", "already_initialized"}:
        raise RuntimeError(f"RapidWright init failed: {json.dumps(init_result, indent=2)}")

    read_result = rapidwright_tools.read_checkpoint(str(input_dcp))
    if read_result.get("status") != "success":
        raise RuntimeError(f"RapidWright read failed: {json.dumps(read_result, indent=2)}")

    net_results = []
    for net_name in nets:
        result = rapidwright_tools.optimize_fanout(net_name=net_name, split_factor=split_factor)
        if result.get("status") != "success":
            raise RuntimeError(f"RapidWright optimize_fanout failed for {net_name}: {json.dumps(result, indent=2)}")
        net_results.append(result)

    write_result = rapidwright_tools.write_checkpoint(str(output_dcp), overwrite=True)
    if write_result.get("status") != "success":
        raise RuntimeError(f"RapidWright write failed: {json.dumps(write_result, indent=2)}")

    return {
        "initialize": init_result,
        "read_checkpoint": read_result,
        "net_results": net_results,
        "write_checkpoint": write_result,
    }


def main() -> int:
    args = parse_args()
    input_dcp = ensure_dcp(args.input_dcp)
    output_root = make_output_root(args.output_root)
    nets = args.nets or list(DEFAULT_NETS)

    baseline_report = output_root / "baseline_timing_summary.rpt"
    baseline_route_status = output_root / "baseline_route_status.rpt"
    fanout_dcp = output_root / "fanout_optimized.dcp"
    post_route_report = output_root / "fanout_route_timing_summary.rpt"
    post_route_status = output_root / "fanout_route_status.rpt"
    after_fanout_route_dcp = output_root / "after_fanout_route.dcp"
    after_fanout_route_edf = output_root / "after_fanout_route.edf"
    final_dcp = output_root / args.final_name
    final_report = output_root / "final_timing_summary.rpt"
    final_route_status = output_root / "final_route_status.rpt"

    print(f"Replay output root: {output_root}")
    print(f"Input DCP: {input_dcp}")
    print(f"Selected fanout nets: {', '.join(nets)}")
    print(f"Split factor: {args.split_factor}")

    run_vivado_batch(
        output_root=output_root,
        step_name="00_baseline",
        commands=[
            f"open_checkpoint {{{input_dcp}}}",
            f"report_timing_summary -file {{{baseline_report}}}",
            f"report_route_status -file {{{baseline_route_status}}}",
            "close_design",
        ],
    )

    rapidwright_result = run_rapidwright_fanout(
        input_dcp=input_dcp,
        output_dcp=fanout_dcp,
        nets=nets,
        split_factor=args.split_factor,
    )

    run_vivado_batch(
        output_root=output_root,
        step_name="01_route_physopt",
        commands=[
            f"open_checkpoint {{{fanout_dcp}}}",
            "route_design",
            f"report_timing_summary -file {{{post_route_report}}}",
            f"report_route_status -file {{{post_route_status}}}",
            "phys_opt_design -directive Explore",
            f"write_checkpoint -force {{{after_fanout_route_dcp}}}",
            f"write_edif -force {{{after_fanout_route_edf}}}",
            "close_design",
        ],
    )

    run_vivado_batch(
        output_root=output_root,
        step_name="02_final_explore_route",
        commands=[
            f"open_checkpoint {{{after_fanout_route_dcp}}}",
            "route_design -directive Explore",
            f"report_timing_summary -file {{{final_report}}}",
            f"report_route_status -file {{{final_route_status}}}",
            f"write_checkpoint -force {{{final_dcp}}}",
            "close_design",
        ],
    )

    baseline = capture_timing("baseline", baseline_report, baseline_route_status)
    post_route = capture_timing("after_fanout_route", post_route_report, post_route_status)
    final = capture_timing("final", final_report, final_route_status)

    summary = {
        "input_dcp": str(input_dcp),
        "output_root": str(output_root),
        "final_output_dcp": str(final_dcp),
        "sequence": {
            "fanout_nets": nets,
            "split_factor": args.split_factor,
            "phys_opt_directive": "Explore",
            "final_route_directive": "Explore",
        },
        "baseline": asdict(baseline),
        "after_fanout_route": asdict(post_route),
        "final": asdict(final),
        "improvement": {
            "wns_gain_ns": None if baseline.wns_ns is None or final.wns_ns is None else final.wns_ns - baseline.wns_ns,
            "tns_gain_ns": None if baseline.tns_ns is None or final.tns_ns is None else final.tns_ns - baseline.tns_ns,
            "failing_endpoint_reduction": None
            if baseline.failing_endpoints is None or final.failing_endpoints is None
            else baseline.failing_endpoints - final.failing_endpoints,
            "fmax_gain_mhz": None
            if baseline.estimated_fmax_mhz is None or final.estimated_fmax_mhz is None
            else final.estimated_fmax_mhz - baseline.estimated_fmax_mhz,
            "timing_closed": bool(
                baseline.wns_ns is not None
                and final.wns_ns is not None
                and baseline.wns_ns < 0 <= final.wns_ns
            ),
        },
        "rapidwright": rapidwright_result,
        "generated_files": {
            "fanout_optimized_dcp": str(fanout_dcp),
            "after_fanout_route_dcp": str(after_fanout_route_dcp),
            "after_fanout_route_edf": str(after_fanout_route_edf),
            "final_output_dcp": str(final_dcp),
            "baseline_report": str(baseline_report),
            "post_route_report": str(post_route_report),
            "final_report": str(final_report),
        },
    }

    summary_path = output_root / "replay_summary.json"
    write_text(summary_path, json.dumps(summary, indent=2))

    print("")
    print("Replay complete")
    print(f"Baseline WNS: {baseline.wns_ns}")
    print(f"Final WNS: {final.wns_ns}")
    print(f"WNS gain: {summary['improvement']['wns_gain_ns']}")
    print(f"Baseline fmax MHz: {baseline.estimated_fmax_mhz}")
    print(f"Final fmax MHz: {final.estimated_fmax_mhz}")
    print(f"Timing closed: {summary['improvement']['timing_closed']}")
    print(f"Final DCP: {final_dcp}")
    print(f"Summary: {summary_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Replay failed: {exc}", file=sys.stderr)
        raise
