from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


LOG_SUFFIXES: Dict[str, str] = {
    "create_and_apply": "_create_and_apply.log",
    "unplace": "_unplace.log",
    "unroute": "_unroute.log",
    "place": "_place.log",
    "route": "_route.log",
    "route_status": "_route_status.log",
    "timing_summary": "_timing_summary.txt",
    "write_checkpoint": "_write_checkpoint.log",
    "error": "_error.log",
}

HEAVY_ATTEMPT_FIELDS = {
    "create_and_apply_log",
    "unplace_log",
    "unroute_log",
    "place_log",
    "route_log",
    "route_status",
    "timing_text",
    "write_checkpoint_log",
}

STRUCTURED_LOG_RE = re.compile(
    r"\[(?P<kind>[A-Z]+)\]\s*-\s*(?P<message>.*?)(?:\s*-\s*iter=(?P<iter>\d+))?"
    r"(?:\s*-\s*WNS=(?P<wns>-?\d+(?:\.\d+)?)ns)?(?:\s*-\s*fmax=(?P<fmax>-?\d+(?:\.\d+)?)MHz)?\s*$"
)
API_TOKENS_RE = re.compile(
    r"Call #(?P<calls>\d+) Tokens: (?P<total>[\d,]+) \(P:(?P<prompt>[\d,]+), C:(?P<completion>[\d,]+)\)"
    r"(?: \| Cost: \$(?P<cost>\d+(?:\.\d+)?))?"
)
RESULT_COUNTS_RE = re.compile(r"Iterations: (?P<iterations>\d+); LLM calls: (?P<calls>\d+)")
RESULT_COST_RE = re.compile(r"Estimated cost: \$(?P<cost>\d+(?:\.\d+)?)")
OPEN_CHECKPOINT_RE = re.compile(r"open_checkpoint\s+\{(?P<path>[^}]+)\}")

RECIPE_LABELS: Dict[Optional[str], str] = {
    None: "Analyze",
    "quick_timing_rescue": "Quick Rescue",
    "pblock_explorer": "Pblock Explorer",
    "high_fanout_optimization": "Fanout Focus",
    "ai_recommended_plan": "AI Plan",
    "ai_autopilot": "Autopilot",
}
FEATURED_DEMO_PRIMARY_RUN_NAME = "dcp_optimizer_run-20260502_110423"
FEATURED_DEMO_NETS = [
    "u_bench/sel_a[0]",
    "u_bench/sel_a[1]",
    "u_bench/sel_b[0]",
]
SETUP_SUMMARY_RE = re.compile(
    r"Setup\s*:\s*(?P<failing>\d+)\s+Failing Endpoint[s]?\s*,\s*Worst Slack\s+(?P<wns>-?\d+\.\d+)ns,\s*"
    r"Total Violation\s+(?P<tns>-?\d+\.\d+)ns"
)
PHYSOPT_POST_SUMMARY_RE = re.compile(
    r"Post Physical Optimization Timing Summary \| WNS=(?P<wns>-?\d+\.\d+) \| TNS=(?P<tns>-?\d+\.\d+)"
)
TIMING_REPORT_DESIGN_RE = re.compile(r"^\|\s*Design\s*:\s*(?P<design>.+?)\s*$", re.MULTILINE)
TIMING_REPORT_DEVICE_RE = re.compile(r"^\|\s*Device\s*:\s*(?P<device>.+?)\s*$", re.MULTILINE)
EDIF_PART_RE = re.compile(r'\(property PART \(string "(?P<part>[^"]+)"\)\)')
EDIF_DESIGN_RE = re.compile(r"\(design\s+(?P<design>[^\s)]+)")


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def display_recipe_name(recipe: Optional[str]) -> str:
    return RECIPE_LABELS.get(recipe, RECIPE_LABELS[None])


def parse_token_report_timestamp(raw: Optional[str], *, fallback_path: Path) -> str:
    if raw:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            return parsed.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback_path.stat().st_mtime, tz=timezone.utc).isoformat()


def _delta(best: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if best is None or baseline is None:
        return None
    return best - baseline


def _percent_delta(best: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if best is None or baseline in (None, 0):
        return None
    return ((best - baseline) / abs(baseline)) * 100.0


def parse_timing_summary_text(timing_report: str) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {
        "wns": None,
        "tns": None,
        "failing_endpoints": None,
    }
    lines = timing_report.splitlines()
    header_idx = -1
    for index, line in enumerate(lines):
        if "WNS(ns)" in line and "TNS(ns)" in line:
            header_idx = index
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
            return result
    return result


def calculate_fmax_mhz(wns_ns: Optional[float], clock_period_ns: Optional[float]) -> Optional[float]:
    if wns_ns is None or clock_period_ns is None or clock_period_ns <= 0:
        return None
    achievable_period = clock_period_ns - wns_ns
    if achievable_period <= 0:
        return None
    return 1000.0 / achievable_period


def normalize_timing(raw: Optional[Dict[str, Any]], clock_period_ns: Optional[float]) -> Dict[str, Optional[float]]:
    payload = raw or {}
    wns = payload.get("wns")
    tns = payload.get("tns")
    failing_endpoints = payload.get("failing_endpoints")
    return {
        "wns": wns,
        "tns": tns,
        "failing_endpoints": failing_endpoints,
        "estimated_fmax_mhz": calculate_fmax_mhz(wns, clock_period_ns),
    }


def ai_timing_payload(
    *,
    wns: Optional[float] = None,
    estimated_fmax_mhz: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    return {
        "wns": wns,
        "tns": None,
        "failing_endpoints": None,
        "estimated_fmax_mhz": estimated_fmax_mhz,
    }


def summarize_impact(
    baseline: Optional[Dict[str, Any]],
    best_timing: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    baseline_timing = (baseline or {}).get("timing") or {}
    best = best_timing or {}

    baseline_wns = baseline_timing.get("wns")
    best_wns = best.get("wns")
    baseline_tns = baseline_timing.get("tns")
    best_tns = best.get("tns")
    baseline_endpoints = baseline_timing.get("failing_endpoints")
    best_endpoints = best.get("failing_endpoints")
    baseline_fmax = baseline_timing.get("estimated_fmax_mhz")
    best_fmax = best.get("estimated_fmax_mhz")

    timing_margin_gain = _delta(best_wns, baseline_wns)
    tns_gain = _delta(best_tns, baseline_tns)
    endpoint_reduction = None
    if baseline_endpoints is not None and best_endpoints is not None:
        endpoint_reduction = baseline_endpoints - best_endpoints
    performance_uplift = _delta(best_fmax, baseline_fmax)

    improved = None
    if timing_margin_gain is not None:
        improved = timing_margin_gain > 0
    elif performance_uplift is not None:
        improved = performance_uplift > 0

    return {
        "timing_margin_gain_ns": timing_margin_gain,
        "tns_gain_ns": tns_gain,
        "failing_endpoint_reduction": endpoint_reduction,
        "performance_uplift_mhz": performance_uplift,
        "performance_uplift_pct": _percent_delta(best_fmax, baseline_fmax),
        "timing_closed": bool(baseline_wns is not None and best_wns is not None and baseline_wns < 0 <= best_wns),
        "improved": improved,
    }


def demo_metric_snapshot(
    *,
    wns: Optional[float],
    tns: Optional[float],
    failing_endpoints: Optional[float],
    clock_period_ns: Optional[float],
) -> Dict[str, Optional[float]]:
    return {
        "wns": wns,
        "tns": tns,
        "failing_endpoints": failing_endpoints,
        "estimated_fmax_mhz": calculate_fmax_mhz(wns, clock_period_ns),
    }


def _merge_demo_metrics(
    base: Dict[str, Optional[float]],
    overrides: Dict[str, Optional[float]],
    *,
    clock_period_ns: Optional[float],
) -> Dict[str, Optional[float]]:
    merged = dict(base)
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    merged["estimated_fmax_mhz"] = calculate_fmax_mhz(merged.get("wns"), clock_period_ns)
    return merged


def _parse_setup_timing_summaries(
    log_text: str,
    *,
    clock_period_ns: Optional[float],
) -> List[Dict[str, Optional[float]]]:
    summaries: List[Dict[str, Optional[float]]] = []
    for match in SETUP_SUMMARY_RE.finditer(log_text):
        summaries.append(
            demo_metric_snapshot(
                wns=float(match.group("wns")),
                tns=float(match.group("tns")),
                failing_endpoints=float(match.group("failing")),
                clock_period_ns=clock_period_ns,
            )
        )
    return summaries


def _parse_physopt_post_summary(
    log_text: str,
    *,
    clock_period_ns: Optional[float],
) -> Optional[Dict[str, Optional[float]]]:
    match = PHYSOPT_POST_SUMMARY_RE.search(log_text)
    if match is None:
        return None
    return demo_metric_snapshot(
        wns=float(match.group("wns")),
        tns=float(match.group("tns")),
        failing_endpoints=None,
        clock_period_ns=clock_period_ns,
    )


def _parse_design_info_from_timing_report(path: Path) -> Tuple[Optional[str], Optional[str]]:
    if not path.exists():
        return None, None
    text = path.read_text(encoding="utf-8", errors="replace")
    design_match = TIMING_REPORT_DESIGN_RE.search(text)
    device_match = TIMING_REPORT_DEVICE_RE.search(text)
    design_name = design_match.group("design").strip() if design_match is not None else None
    device_name = device_match.group("device").strip() if device_match is not None else None
    return design_name, device_name


def _parse_design_info_from_edif(path: Path) -> Tuple[Optional[str], Optional[str]]:
    if not path.exists():
        return None, None
    text = path.read_text(encoding="utf-8", errors="replace")
    design_match = EDIF_DESIGN_RE.search(text)
    part_match = EDIF_PART_RE.search(text)
    design_name = design_match.group("design").strip() if design_match is not None else None
    part_name = part_match.group("part").strip() if part_match is not None else None
    return design_name, part_name


def _infer_featured_design_identity(
    run_dir: Path,
    *,
    replay_summary: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    if replay_summary:
        read_checkpoint = ((replay_summary.get("rapidwright") or {}).get("read_checkpoint") or {})
        design_name = read_checkpoint.get("design_name")
        part_name = read_checkpoint.get("part_name")
        if design_name and part_name:
            return str(design_name), str(part_name)

    design_name, part_name = _parse_design_info_from_edif(run_dir / "after_fanout_route.edf")
    if design_name and part_name:
        return design_name, part_name

    for report_name in (
        "final_timing_summary.rpt",
        "baseline_timing_summary.rpt",
        "fanout_route_timing_summary.rpt",
    ):
        report_design, report_device = _parse_design_info_from_timing_report(run_dir / report_name)
        if report_design or report_device:
            return report_design or run_dir.name, report_device or "Not available"

    return run_dir.name, "Not available"


def _featured_demo_source(repo_root: Path) -> Tuple[str, Path]:
    primary_run_dir = repo_root / FEATURED_DEMO_PRIMARY_RUN_NAME
    if primary_run_dir.is_dir() and (primary_run_dir / "token_usage.json").exists():
        return "historical_ai", primary_run_dir.resolve()

    replay_dirs = sorted(
        repo_root.glob("replay_run_route_cluster_bench_*"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for replay_dir in replay_dirs:
        summary = read_json(replay_dir / "replay_summary.json")
        if summary and ((summary.get("improvement") or {}).get("timing_closed") is True):
            return "replay", replay_dir.resolve()

    raise FileNotFoundError("No featured visitor optimization scenario artifacts were found.")


def _demo_step(
    *,
    step_id: str,
    order: int,
    phase: str,
    title: str,
    status_label: str,
    duration_ms: int,
    assistant_text: str,
    tool_text: str,
    metrics: Dict[str, Optional[float]],
    highlighted_nets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "id": step_id,
        "order": order,
        "phase": phase,
        "title": title,
        "status_label": status_label,
        "duration_ms": duration_ms,
        "assistant_text": assistant_text,
        "tool_text": tool_text,
        "metrics": metrics,
        "highlighted_nets": highlighted_nets or [],
    }


def _build_featured_demo_steps(
    *,
    initial_metrics: Dict[str, Optional[float]],
    route_metrics: Dict[str, Optional[float]],
    physopt_metrics: Dict[str, Optional[float]],
    final_metrics: Dict[str, Optional[float]],
) -> List[Dict[str, Any]]:
    return [
        _demo_step(
            step_id="load-checkpoint",
            order=1,
            phase="setup",
            title="Load checkpoint",
            status_label="Checkpoint opened",
            duration_ms=700,
            assistant_text="Starting from the loaded design and preserving the original checkpoint.",
            tool_text="open_checkpoint /home/vik/route_cluster_bench.dcp",
            metrics=initial_metrics,
        ),
        _demo_step(
            step_id="baseline-analysis",
            order=2,
            phase="analysis",
            title="Analyze baseline timing",
            status_label="Baseline captured",
            duration_ms=900,
            assistant_text="Baseline timing is failing, so the first pass focuses on the paths with the highest leverage.",
            tool_text="report_timing_summary, report_timing",
            metrics=initial_metrics,
        ),
        _demo_step(
            step_id="identify-fanout",
            order=3,
            phase="analysis",
            title="Identify critical high-fanout selector nets",
            status_label="Targets selected",
            duration_ms=950,
            assistant_text="The violation is concentrated around selector fanout, so the optimization stays focused before widening scope.",
            tool_text="get_critical_high_fanout_nets, analyze_critical_path_spread",
            metrics=initial_metrics,
            highlighted_nets=list(FEATURED_DEMO_NETS),
        ),
        _demo_step(
            step_id="split-sel-a0",
            order=4,
            phase="fanout",
            title="Split u_bench/sel_a[0]",
            status_label="Fanout reduced",
            duration_ms=700,
            assistant_text="Split the first selector net into replicated branches so routing can stop paying the full fanout penalty on one source.",
            tool_text="rapidwright_optimize_fanout u_bench/sel_a[0] split=8",
            metrics=initial_metrics,
            highlighted_nets=["u_bench/sel_a[0]"],
        ),
        _demo_step(
            step_id="split-sel-a1",
            order=5,
            phase="fanout",
            title="Split u_bench/sel_a[1]",
            status_label="Fanout reduced",
            duration_ms=700,
            assistant_text="Apply the same replication pattern to the companion selector so the lane-level pressure drops together.",
            tool_text="rapidwright_optimize_fanout u_bench/sel_a[1] split=8",
            metrics=initial_metrics,
            highlighted_nets=["u_bench/sel_a[1]"],
        ),
        _demo_step(
            step_id="split-sel-b0",
            order=6,
            phase="fanout",
            title="Split u_bench/sel_b[0]",
            status_label="Fanout reduced",
            duration_ms=700,
            assistant_text="Finish the targeted fanout pass on the third selector before re-implementing the checkpoint.",
            tool_text="rapidwright_optimize_fanout u_bench/sel_b[0] split=8",
            metrics=initial_metrics,
            highlighted_nets=["u_bench/sel_b[0]"],
        ),
        _demo_step(
            step_id="route-fanout",
            order=7,
            phase="implementation",
            title="Route fanout-optimized checkpoint",
            status_label="Implementation converging",
            duration_ms=1500,
            assistant_text="Route first to see the real post-edit timing picture before spending effort on physical optimization.",
            tool_text="write fanout_optimized.dcp, route_design",
            metrics=route_metrics,
            highlighted_nets=list(FEATURED_DEMO_NETS),
        ),
        _demo_step(
            step_id="physopt-explore",
            order=8,
            phase="implementation",
            title="Run phys_opt_design -directive Explore",
            status_label="Critical paths refined",
            duration_ms=1250,
            assistant_text="Use Explore physopt to recover timing around the replicated selectors and stabilize the best routing opportunities.",
            tool_text="phys_opt_design -directive Explore",
            metrics=physopt_metrics,
            highlighted_nets=list(FEATURED_DEMO_NETS),
        ),
        _demo_step(
            step_id="reopen-stable",
            order=9,
            phase="implementation",
            title="Reopen stabilized checkpoint",
            status_label="Best branch locked",
            duration_ms=650,
            assistant_text="Reopen the stabilized checkpoint instead of carrying forward weaker branches.",
            tool_text="open_checkpoint after_fanout_route.dcp",
            metrics=physopt_metrics,
        ),
        _demo_step(
            step_id="explore-route",
            order=10,
            phase="signoff",
            title="Run route_design -directive Explore",
            status_label="Signoff route running",
            duration_ms=1650,
            assistant_text="Re-route the stabilized checkpoint with the broader Explore directive to push the last failing path across the line.",
            tool_text="route_design -directive Explore",
            metrics=final_metrics,
            highlighted_nets=list(FEATURED_DEMO_NETS),
        ),
        _demo_step(
            step_id="timing-closed",
            order=11,
            phase="signoff",
            title="Report timing closed",
            status_label="Timing closed",
            duration_ms=850,
            assistant_text="The design is now timing clean, with the original failing endpoints removed and positive margin restored.",
            tool_text="report_timing_summary, write_checkpoint route_c_optim.dcp",
            metrics=final_metrics,
        ),
    ]


def _historical_featured_demo_scenario(run_dir: Path) -> Dict[str, Any]:
    token_report = read_json(run_dir / "token_usage.json") or {}
    report_summary = token_report.get("summary", {})
    clock_period_ns = report_summary.get("clock_period_ns")
    if clock_period_ns is not None:
        clock_period_ns = float(clock_period_ns)

    vivado_log_path = run_dir / "vivado.log"
    vivado_log_text = vivado_log_path.read_text(encoding="utf-8", errors="replace") if vivado_log_path.exists() else ""
    setup_summaries = _parse_setup_timing_summaries(vivado_log_text, clock_period_ns=clock_period_ns)
    if len(setup_summaries) < 4:
        raise ValueError(f"Expected at least four timing summaries in {vivado_log_path}.")

    initial_metrics = setup_summaries[0]
    route_metrics = setup_summaries[2]
    final_metrics = setup_summaries[-1]
    physopt_post = _parse_physopt_post_summary(vivado_log_text, clock_period_ns=clock_period_ns)
    physopt_metrics = (
        _merge_demo_metrics(route_metrics, physopt_post, clock_period_ns=clock_period_ns)
        if physopt_post is not None
        else route_metrics
    )

    design_name, design_part = _infer_featured_design_identity(run_dir)
    input_dcp = infer_ai_input_dcp(run_dir)
    impact = summarize_impact({"timing": initial_metrics}, final_metrics)
    steps = _build_featured_demo_steps(
        initial_metrics=initial_metrics,
        route_metrics=route_metrics,
        physopt_metrics=physopt_metrics,
        final_metrics=final_metrics,
    )

    return {
        "scenario_id": "featured-route-cluster-bench",
        "title": "route_cluster_bench timing recovery",
        "duration_ms": sum(step["duration_ms"] for step in steps),
        "featured_run_id": run_dir.name,
        "design_name": design_name,
        "design_part": design_part,
        "input_dcp_label": Path(input_dcp).name if input_dcp else "route_cluster_bench.dcp",
        "initial_metrics": initial_metrics,
        "final_metrics": final_metrics,
        "impact": impact,
        "steps": steps,
    }


def _replay_featured_demo_scenario(run_dir: Path) -> Dict[str, Any]:
    replay_summary = read_json(run_dir / "replay_summary.json") or {}
    if not replay_summary:
        raise ValueError(f"Replay summary was not found in {run_dir}.")

    initial_raw = replay_summary.get("baseline") or {}
    route_raw = replay_summary.get("after_fanout_route") or {}
    final_raw = replay_summary.get("final") or {}
    clock_period_ns = initial_raw.get("clock_period_ns") or route_raw.get("clock_period_ns") or final_raw.get("clock_period_ns")

    initial_metrics = demo_metric_snapshot(
        wns=initial_raw.get("wns_ns"),
        tns=initial_raw.get("tns_ns"),
        failing_endpoints=initial_raw.get("failing_endpoints"),
        clock_period_ns=clock_period_ns,
    )
    route_metrics = demo_metric_snapshot(
        wns=route_raw.get("wns_ns"),
        tns=route_raw.get("tns_ns"),
        failing_endpoints=route_raw.get("failing_endpoints"),
        clock_period_ns=clock_period_ns,
    )
    final_metrics = demo_metric_snapshot(
        wns=final_raw.get("wns_ns"),
        tns=final_raw.get("tns_ns"),
        failing_endpoints=final_raw.get("failing_endpoints"),
        clock_period_ns=clock_period_ns,
    )

    route_physopt_log = run_dir / "01_route_physopt.vivado.log"
    route_physopt_text = route_physopt_log.read_text(encoding="utf-8", errors="replace") if route_physopt_log.exists() else ""
    physopt_post = _parse_physopt_post_summary(route_physopt_text, clock_period_ns=clock_period_ns)
    physopt_metrics = (
        _merge_demo_metrics(route_metrics, physopt_post, clock_period_ns=clock_period_ns)
        if physopt_post is not None
        else route_metrics
    )

    design_name, design_part = _infer_featured_design_identity(run_dir, replay_summary=replay_summary)
    impact = summarize_impact({"timing": initial_metrics}, final_metrics)
    steps = _build_featured_demo_steps(
        initial_metrics=initial_metrics,
        route_metrics=route_metrics,
        physopt_metrics=physopt_metrics,
        final_metrics=final_metrics,
    )

    return {
        "scenario_id": "featured-route-cluster-bench",
        "title": "route_cluster_bench timing recovery",
        "duration_ms": sum(step["duration_ms"] for step in steps),
        "featured_run_id": run_dir.name,
        "design_name": design_name,
        "design_part": design_part,
        "input_dcp_label": Path(replay_summary.get("input_dcp") or "/home/vik/route_cluster_bench.dcp").name,
        "initial_metrics": initial_metrics,
        "final_metrics": final_metrics,
        "impact": impact,
        "steps": steps,
    }


def parse_featured_demo_scenario(repo_root: Path) -> Dict[str, Any]:
    source_type, run_dir = _featured_demo_source(repo_root)
    if source_type == "historical_ai":
        return _historical_featured_demo_scenario(run_dir)
    return _replay_featured_demo_scenario(run_dir)


def timing_score(parsed: Dict[str, Optional[float]]) -> Tuple[float, float]:
    wns = parsed.get("wns")
    tns = parsed.get("tns")
    return (
        float("-inf") if wns is None else float(wns),
        float("-inf") if tns is None else float(tns),
    )


def relative_if_within(path_value: Optional[str], root: Path) -> Optional[str]:
    if not path_value:
        return None
    path = Path(path_value)
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return None


def infer_ai_input_dcp(run_dir: Path) -> Optional[str]:
    candidate_logs = [
        run_dir / "vivado-mcp.log",
        run_dir / "vivado.log",
        run_dir / "rapidwright.log",
    ]
    for log_path in candidate_logs:
        if not log_path.exists():
            continue
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = OPEN_CHECKPOINT_RE.search(line)
            if match is not None:
                candidate = Path(match.group("path"))
                try:
                    resolved = candidate.resolve()
                except FileNotFoundError:
                    resolved = candidate
                if resolved.parent.resolve() != run_dir.resolve():
                    return str(resolved)
    return None


def infer_ai_output_dcp(run_dir: Path) -> Optional[Path]:
    preferred_patterns = [
        "after_fanout_route.dcp",
        "*_optimized*.dcp",
        "fanout_optimized.dcp",
        "after_selc_fanout.dcp",
    ]
    for pattern in preferred_patterns:
        matches = sorted(run_dir.glob(pattern))
        if matches:
            return matches[-1].resolve()

    dcp_files = sorted(
        (path.resolve() for path in run_dir.glob("*.dcp")),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    return dcp_files[-1] if dcp_files else None


def detect_outputrun_dir(output_root: Path, existing: Optional[str] = None) -> Optional[Path]:
    if existing:
        existing_path = Path(existing)
        if existing_path.exists():
            return existing_path.resolve()
    if not output_root.exists():
        return None
    candidates = [path for path in output_root.glob("outputrun_*") if path.is_dir()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)[0].resolve()


def stage_has_attempt_activity(run_dir: Optional[Path]) -> bool:
    if run_dir is None or not run_dir.exists():
        return False
    return any(run_dir.glob("attempt_*_*"))


def parse_baseline_summary(run_dir: Path) -> Dict[str, Any]:
    baseline_meta = read_json(run_dir / "baseline_meta.json") or {}
    run_summary = read_json(run_dir / "run_summary.json") or {}
    timing_text_path = run_dir / "baseline_timing_summary.txt"
    clock_period = baseline_meta.get("baseline_clock_period_ns", run_summary.get("baseline_clock_period_ns"))
    baseline_timing = baseline_meta.get("baseline_timing_parsed") or run_summary.get("baseline_timing_parsed")
    if baseline_timing is None and timing_text_path.exists():
        baseline_timing = parse_timing_summary_text(timing_text_path.read_text(encoding="utf-8"))

    return {
        "artifact_dir": str(run_dir.resolve()),
        "input_dcp": baseline_meta.get("input_dcp") or run_summary.get("input_dcp"),
        "baseline_clock_period_ns": clock_period,
        "clock_period_ns": clock_period,
        "timing": normalize_timing(baseline_timing, clock_period),
        "utilization": baseline_meta.get("baseline_utilization", {}),
        "targets": baseline_meta.get("targets", {}),
        "analyze_result": baseline_meta.get("analyze_result", {}),
        "start_region": baseline_meta.get("start_region", {}),
        "report_files": {
            "baseline_meta": "baseline_meta.json" if (run_dir / "baseline_meta.json").exists() else "",
            "baseline_timing_summary": "baseline_timing_summary.txt" if timing_text_path.exists() else "",
            "baseline_utilization": "baseline_utilization.txt" if (run_dir / "baseline_utilization.txt").exists() else "",
        },
    }


def _summary_files_in_write_order(run_dir: Path) -> List[Path]:
    candidates = list(run_dir.glob("attempt_*_summary.json")) + list(run_dir.glob("skipped_*_summary.json"))
    return sorted(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def _raw_attempt_rows(run_dir: Path) -> List[Dict[str, Any]]:
    summary_files = _summary_files_in_write_order(run_dir)
    if summary_files:
        rows: List[Dict[str, Any]] = []
        for path in summary_files:
            payload = read_json(path)
            if payload is not None:
                rows.append(payload)
        return rows
    run_summary = read_json(run_dir / "run_summary.json") or {}
    return list(run_summary.get("attempts", []))


def available_logs_for_attempt(run_dir: Path, attempt_num: Optional[int]) -> List[str]:
    if attempt_num is None:
        return []
    attempt_prefix = f"attempt_{attempt_num:03d}"
    available: List[str] = []
    for log_type, suffix in LOG_SUFFIXES.items():
        if (run_dir / f"{attempt_prefix}{suffix}").exists():
            available.append(log_type)
    return available


def normalize_attempts(run_dir: Path, clock_period_ns: Optional[float]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for row in _raw_attempt_rows(run_dir):
        sanitized = {key: value for key, value in row.items() if key not in HEAVY_ATTEMPT_FIELDS}
        attempt_num = sanitized.get("attempt_num")
        sanitized["candidate_region"] = sanitized.get("candidate_region") or {}
        sanitized["timing"] = normalize_timing(sanitized.get("timing_parsed"), clock_period_ns)
        sanitized.pop("timing_parsed", None)
        sanitized["relative_output_dcp"] = relative_if_within(sanitized.get("output_dcp"), run_dir)
        sanitized["available_logs"] = available_logs_for_attempt(run_dir, attempt_num)
        sanitized["leaderboard_rank"] = None
        normalized.append(sanitized)

    successful = [
        entry
        for entry in normalized
        if entry.get("status") == "success" and entry.get("attempt_num") is not None
    ]
    ranked = sorted(successful, key=lambda entry: timing_score(entry["timing"]), reverse=True)
    for index, entry in enumerate(ranked, start=1):
        entry["leaderboard_rank"] = index
    return normalized


def compute_progress(run_dir: Path, attempts: List[Dict[str, Any]], max_attempts: Optional[int]) -> Dict[str, Any]:
    attempt_numbers = []
    for path in run_dir.glob("attempt_*"):
        match = re.match(r"attempt_(\d+)", path.name)
        if match:
            attempt_numbers.append(int(match.group(1)))
    return {
        "max_attempts": max_attempts,
        "current_attempt": max(attempt_numbers) if attempt_numbers else None,
        "completed_attempts": len([entry for entry in attempts if entry.get("attempt_num") is not None]),
        "skipped_candidates": len([entry for entry in attempts if entry.get("status") == "skipped"]),
        "successful_attempts": len([entry for entry in attempts if entry.get("status") == "success"]),
    }


def parse_run_artifacts(run_dir: Path) -> Dict[str, Any]:
    baseline = parse_baseline_summary(run_dir)
    run_summary = read_json(run_dir / "run_summary.json") or {}
    clock_period = baseline.get("baseline_clock_period_ns")
    attempts = normalize_attempts(run_dir, clock_period)

    successful_attempts = [
        entry for entry in attempts if entry.get("status") == "success" and entry.get("attempt_num") is not None
    ]
    best_attempt = max(
        successful_attempts,
        key=lambda entry: timing_score(entry["timing"]),
        default=None,
    )
    best_attempt_num = run_summary.get("best_attempt_num") or (best_attempt or {}).get("attempt_num")
    best_output_dcp = run_summary.get("best_output_dcp") or (best_attempt or {}).get("output_dcp")
    best_timing_raw = run_summary.get("best_timing_parsed") or (best_attempt or {}).get("timing") or baseline.get("timing")
    best_timing = normalize_timing(best_timing_raw, clock_period)

    max_attempts = None
    if (run_dir / "baseline_meta.json").exists():
        max_attempts = len([path for path in run_dir.glob("attempt_*_summary.json")]) or None

    return {
        "artifact_dir": str(run_dir.resolve()),
        "baseline": baseline,
        "best_attempt_num": best_attempt_num,
        "best_output_dcp": best_output_dcp,
        "best_output_artifact": relative_if_within(best_output_dcp, run_dir),
        "best_timing": best_timing,
        "attempt_count": len([entry for entry in attempts if entry.get("status") != "skipped"]),
        "skip_count": len([entry for entry in attempts if entry.get("status") == "skipped"]),
        "attempts": attempts,
        "progress": compute_progress(run_dir, attempts, max_attempts),
    }


def _parse_int_with_commas(raw: str) -> int:
    return int(raw.replace(",", ""))


def _structured_log_match(line: str) -> Optional[re.Match[str]]:
    marker = line.find("[")
    if marker < 0:
        return None
    return STRUCTURED_LOG_RE.search(line[marker:])


def _update_ai_timing(
    target: Dict[str, Optional[float]],
    *,
    wns: Optional[float] = None,
    estimated_fmax_mhz: Optional[float] = None,
) -> None:
    if wns is not None:
        target["wns"] = wns
    if estimated_fmax_mhz is not None:
        target["estimated_fmax_mhz"] = estimated_fmax_mhz


def parse_ai_run_artifacts(
    run_dir: Path,
    *,
    stderr_log_path: Optional[Path],
    output_dcp_path: Optional[Path],
    model: Optional[str],
) -> Dict[str, Any]:
    initial_timing = ai_timing_payload()
    best_timing = ai_timing_payload()
    baseline_clock_period_ns: Optional[float] = None
    phase: Optional[str] = None
    iteration: Optional[int] = None
    llm_call_count = 0
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    estimated_cost_usd: Optional[float] = None
    last_message: Optional[str] = None
    error: Optional[str] = None

    if stderr_log_path is not None and stderr_log_path.exists():
        for raw_line in stderr_log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = _structured_log_match(raw_line)
            if match is None:
                continue
            kind = match.group("kind")
            message = (match.group("message") or "").strip()
            parsed_iteration = match.group("iter")
            parsed_wns = match.group("wns")
            parsed_fmax = match.group("fmax")
            wns_value = float(parsed_wns) if parsed_wns is not None else None
            fmax_value = float(parsed_fmax) if parsed_fmax is not None else None

            if message:
                last_message = message

            if kind == "INIT":
                phase = "initial_analysis"
                if "Timing analyzed" in message:
                    _update_ai_timing(initial_timing, wns=wns_value, estimated_fmax_mhz=fmax_value)
            elif kind == "INFO":
                if "Starting LLM-driven optimization" in message:
                    phase = "llm_optimization"
            elif kind == "ITER":
                phase = "iterating"
                if parsed_iteration is not None:
                    iteration = int(parsed_iteration)
                _update_ai_timing(best_timing, wns=wns_value, estimated_fmax_mhz=fmax_value)
            elif kind == "API":
                api_match = API_TOKENS_RE.search(message)
                if api_match is not None:
                    llm_call_count = max(llm_call_count, int(api_match.group("calls")))
                    total_tokens = max(total_tokens, _parse_int_with_commas(api_match.group("total")))
                    prompt_tokens = max(prompt_tokens, _parse_int_with_commas(api_match.group("prompt")))
                    completion_tokens = max(completion_tokens, _parse_int_with_commas(api_match.group("completion")))
                    cost_group = api_match.group("cost")
                    if cost_group is not None:
                        estimated_cost_usd = float(cost_group)
            elif kind == "RESULT":
                if "Design meets timing." in message or "Optimization completed successfully" in message:
                    phase = "completed"
                    _update_ai_timing(best_timing, wns=wns_value, estimated_fmax_mhz=fmax_value)
                elif message.startswith("Initial WNS:"):
                    _update_ai_timing(initial_timing, wns=wns_value, estimated_fmax_mhz=fmax_value)
                elif message.startswith("Best WNS:"):
                    _update_ai_timing(best_timing, wns=wns_value, estimated_fmax_mhz=fmax_value)
                else:
                    counts_match = RESULT_COUNTS_RE.search(message)
                    if counts_match is not None:
                        iteration = int(counts_match.group("iterations"))
                        llm_call_count = max(llm_call_count, int(counts_match.group("calls")))
                    cost_match = RESULT_COST_RE.search(message)
                    if cost_match is not None:
                        estimated_cost_usd = float(cost_match.group("cost"))
            elif kind == "ERROR":
                phase = "failed"
                if message:
                    error = message

    token_report = read_json(run_dir / "token_usage.json") or {}
    token_report_present = bool(token_report)
    if token_report_present:
        report_summary = token_report.get("summary", {})
        raw_clock_period = report_summary.get("clock_period_ns")
        if raw_clock_period is not None:
            baseline_clock_period_ns = float(raw_clock_period)
        llm_call_count = int(report_summary.get("total_llm_calls", llm_call_count) or 0)
        iteration = int(report_summary.get("total_iterations", iteration or 0) or 0) or iteration
        total_tokens = int(report_summary.get("total_tokens", total_tokens) or 0)
        prompt_tokens = int(report_summary.get("total_prompt_tokens", prompt_tokens) or 0)
        completion_tokens = int(report_summary.get("total_completion_tokens", completion_tokens) or 0)
        total_cost = report_summary.get("total_cost")
        if total_cost is not None:
            estimated_cost_usd = float(total_cost)
        initial_wns = report_summary.get("initial_wns")
        best_wns = report_summary.get("best_wns")
        initial_fmax = report_summary.get("initial_fmax_mhz")
        best_fmax = report_summary.get("best_fmax_mhz")
        _update_ai_timing(
            initial_timing,
            wns=None if initial_wns in (None, float("-inf")) else float(initial_wns),
            estimated_fmax_mhz=None if initial_fmax is None else float(initial_fmax),
        )
        _update_ai_timing(
            best_timing,
            wns=None if best_wns in (None, float("-inf")) else float(best_wns),
            estimated_fmax_mhz=None if best_fmax is None else float(best_fmax),
        )
        if phase != "failed":
            phase = "completed"

    if output_dcp_path is None:
        output_dcp_path = infer_ai_output_dcp(run_dir)

    input_dcp = infer_ai_input_dcp(run_dir)
    planned_output_dcp = str(output_dcp_path.resolve()) if output_dcp_path is not None else None
    output_ready = output_dcp_path is not None and output_dcp_path.exists()
    output_dcp = str(output_dcp_path.resolve()) if output_ready and output_dcp_path is not None else None
    relative_output_dcp = relative_if_within(output_dcp, run_dir) if output_dcp else None

    if best_timing["wns"] is None and initial_timing["wns"] is not None:
        best_timing = dict(initial_timing)
    if phase is None:
        if error is not None:
            phase = "failed"
        elif output_ready:
            phase = "completed"
        elif iteration:
            phase = "iterating"
        elif stderr_log_path is not None and stderr_log_path.exists():
            phase = "starting"

    baseline = {
        "artifact_dir": str(run_dir.resolve()),
        "input_dcp": input_dcp,
        "baseline_clock_period_ns": baseline_clock_period_ns,
        "clock_period_ns": baseline_clock_period_ns,
        "timing": initial_timing,
        "utilization": {},
        "targets": {},
        "analyze_result": {},
        "start_region": {},
        "report_files": {},
    }

    ai = {
        "phase": phase,
        "model": model or token_report.get("model"),
        "iteration": iteration,
        "llm_call_count": llm_call_count,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "initial_timing": initial_timing,
        "best_timing": best_timing,
        "planned_output_dcp": planned_output_dcp,
        "output_dcp": output_dcp,
        "relative_output_dcp": relative_output_dcp,
        "output_ready": output_ready,
        "run_dir": str(run_dir.resolve()),
        "token_report_present": token_report_present,
        "last_message": last_message,
        "error": error,
    }

    return {
        "artifact_dir": str(run_dir.resolve()),
        "baseline": baseline,
        "best_attempt_num": None,
        "best_output_dcp": output_dcp,
        "best_output_artifact": relative_output_dcp,
        "best_timing": best_timing,
        "attempt_count": 0,
        "skip_count": 0,
        "attempts": [],
        "progress": {
            "max_attempts": None,
            "current_attempt": iteration,
            "completed_attempts": iteration or 0,
            "skipped_candidates": 0,
            "successful_attempts": 1 if output_ready else 0,
        },
        "ai": ai,
    }


def get_attempt_log_path(run_dir: Path, attempt_num: int, log_type: str) -> Path:
    if log_type not in LOG_SUFFIXES:
        raise KeyError(log_type)
    return run_dir / f"attempt_{attempt_num:03d}{LOG_SUFFIXES[log_type]}"


def preview_text_file(path: Path, *, full: bool, max_bytes: int) -> Dict[str, Any]:
    raw = path.read_bytes()
    truncated = False
    preview_bytes = raw
    if not full and len(raw) > max_bytes:
        preview_bytes = raw[:max_bytes]
        truncated = True
    return {
        "path": str(path),
        "size_bytes": len(raw),
        "truncated": truncated,
        "preview_text": preview_bytes.decode("utf-8", errors="replace"),
    }


def list_artifacts(run_dir: Path) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for path in sorted(run_dir.rglob("*")):
        rel = path.relative_to(run_dir)
        entries.append(
            {
                "path": str(rel),
                "kind": "directory" if path.is_dir() else "file",
                "size_bytes": 0 if path.is_dir() else path.stat().st_size,
            }
        )
    return entries
