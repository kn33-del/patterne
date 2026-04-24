#!/usr/bin/env python3
# Copyright (C) 2026
# SPDX-License-Identifier: Apache-2.0

"""
Deterministic local critical-path clustering optimizer with matplotlib visualization.

This script does not use an LLM. It reuses the existing RapidWright and Vivado
Python wrappers already present in your project and adds one new optimization
strategy:

    - identify the most spatially spread critical paths
    - create a small pblock around the cells on one path
    - constrain only those path cells into that local region
    - re-place, optionally phys-opt, re-route, and measure WNS
    - keep the best candidate checkpoint

Additionally, it generates step-by-step matplotlib figures so the entire process
can be visualized after a run.
"""


import argparse
import json
import logging
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

try:
    from PIL import Image, ImageOps, ImageDraw
except Exception:
    Image = None
    ImageOps = None
    ImageDraw = None
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import RapidWrightMCP.rapidwright_tools as rw
import VivadoMCP.vivado_mcp_server as vv


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("local_critical_path_cluster_visual")


@dataclass
class PathMetrics:
    path_index: int
    cell_names: List[str]
    placed_cells: List[Dict[str, object]]
    bbox_col_min: int
    bbox_col_max: int
    bbox_row_min: int
    bbox_row_max: int
    bbox_width: int
    bbox_height: int
    bbox_perimeter: int
    max_consecutive_distance: int
    avg_consecutive_distance: float
    score: float


@dataclass
class CandidateResult:
    candidate_id: int
    path_index: int
    pblock_ranges: str
    checkpoint_path: Path
    wns: Optional[float]
    success: bool
    note: str
    region: Tuple[int, int, int, int]
    figure_path: Optional[str] = None


@dataclass
class StepRecord:
    step_index: int
    title: str
    description: str
    figure_path: str


def parse_timing_summary_static(timing_report: str) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {
        "wns": None,
        "tns": None,
        "failing_endpoints": None,
    }

    lines = timing_report.split("\n")
    header_idx = -1
    for i, line in enumerate(lines):
        if "WNS(ns)" in line and "TNS(ns)" in line:
            header_idx = i
            break

    if header_idx == -1:
        return result

    data_idx = header_idx + 2
    if data_idx >= len(lines):
        return result

    data_line = lines[data_idx].strip()
    if not data_line:
        return result

    parts = data_line.split()
    if len(parts) >= 3:
        try:
            result["wns"] = float(parts[0])
            result["tns"] = float(parts[1])
            result["failing_endpoints"] = int(parts[2])
        except (ValueError, IndexError):
            pass

    return result


def calculate_fmax(wns: Optional[float], clock_period: Optional[float]) -> Optional[float]:
    if clock_period is None or clock_period <= 0 or wns is None:
        return None
    achievable_period = clock_period - wns
    if achievable_period <= 0:
        return None
    return 1000.0 / achievable_period


def run_tcl(command: str, timeout: float = 300.0) -> str:
    logger.debug("Tcl: %s", command)
    return vv.run_tcl_command(command, timeout=timeout)


def get_clock_period() -> Optional[float]:
    try:
        result = run_tcl("get_property PERIOD [get_clocks]", timeout=30.0)
        for line in result.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                return float(line)
            except ValueError:
                continue
    except Exception as exc:
        logger.warning("Failed to get clock period: %s", exc)
    return None


def open_checkpoint_in_vivado(dcp_path: Path, timeout: float = 600.0) -> None:
    try:
        run_tcl("close_design", timeout=30.0)
    except Exception:
        pass
    run_tcl(f"open_checkpoint {{{dcp_path}}}", timeout=timeout)


def report_timing_summary(timeout: float = 300.0) -> str:
    run_tcl("puts {timing_summary_start}", timeout=5.0)
    return run_tcl("report_timing_summary -return_string", timeout=timeout)


def extract_critical_paths_json(output_file: Path, num_paths: int, timeout: float = 600.0) -> List[List[str]]:
    response = vv.extract_critical_path_cells(
        num_paths=num_paths,
        output_file=str(output_file),
        timeout=timeout,
    )
    try:
        parsed = json.loads(response)
        if isinstance(parsed, dict) and parsed.get("error"):
            raise RuntimeError(parsed["error"])
    except json.JSONDecodeError:
        pass

    with open(output_file, "r", encoding="utf-8") as f:
        return json.load(f)


def initialize_rapidwright_and_load(dcp_path: Path) -> None:
    init_result = rw.initialize_rapidwright("8G")
    if init_result.get("status") not in ("success", "already_initialized"):
        raise RuntimeError(f"RapidWright initialization failed: {init_result}")

    load_result = rw.read_checkpoint(str(dcp_path))
    if load_result.get("status") != "success":
        raise RuntimeError(f"RapidWright read_checkpoint failed: {load_result}")


def collect_path_metrics(paths: List[List[str]]) -> List[PathMetrics]:
    design = rw._current_design
    if design is None:
        raise RuntimeError("RapidWright design is not loaded")

    results: List[PathMetrics] = []

    for idx, cell_names in enumerate(paths):
        placed_cells: List[Dict[str, object]] = []
        for cell_name in cell_names:
            try:
                cell = design.getCell(cell_name)
                if cell is None or not cell.isPlaced():
                    continue
                site = cell.getSite()
                if site is None:
                    continue
                tile = site.getTile()
                placed_cells.append(
                    {
                        "cell": str(cell.getName()),
                        "type": str(cell.getType()),
                        "site": str(site.getName()),
                        "tile": str(tile.getName()),
                        "col": int(tile.getColumn()),
                        "row": int(tile.getRow()),
                    }
                )
            except Exception:
                continue

        if len(placed_cells) < 2:
            continue

        cols = [int(c["col"]) for c in placed_cells]
        rows = [int(c["row"]) for c in placed_cells]
        bbox_col_min = min(cols)
        bbox_col_max = max(cols)
        bbox_row_min = min(rows)
        bbox_row_max = max(rows)
        bbox_width = bbox_col_max - bbox_col_min + 1
        bbox_height = bbox_row_max - bbox_row_min + 1
        bbox_perimeter = 2 * (bbox_width + bbox_height)

        consecutive_distances: List[int] = []
        for i in range(len(placed_cells) - 1):
            a = placed_cells[i]
            b = placed_cells[i + 1]
            dist = abs(int(a["col"]) - int(b["col"])) + abs(int(a["row"]) - int(b["row"]))
            consecutive_distances.append(dist)

        if not consecutive_distances:
            continue

        max_consecutive_distance = max(consecutive_distances)
        avg_consecutive_distance = sum(consecutive_distances) / len(consecutive_distances)
        score = 2.0 * max_consecutive_distance + 0.75 * avg_consecutive_distance + 0.10 * bbox_perimeter

        results.append(
            PathMetrics(
                path_index=idx,
                cell_names=[str(c["cell"]) for c in placed_cells],
                placed_cells=placed_cells,
                bbox_col_min=bbox_col_min,
                bbox_col_max=bbox_col_max,
                bbox_row_min=bbox_row_min,
                bbox_row_max=bbox_row_max,
                bbox_width=bbox_width,
                bbox_height=bbox_height,
                bbox_perimeter=bbox_perimeter,
                max_consecutive_distance=max_consecutive_distance,
                avg_consecutive_distance=avg_consecutive_distance,
                score=score,
            )
        )

    results.sort(key=lambda x: x.score, reverse=True)
    return results


def choose_region_for_path(
    metrics: PathMetrics,
    device_bounds: Dict[str, int],
    padding: int,
    min_width: int,
    min_height: int,
) -> Tuple[int, int, int, int]:
    col_min = metrics.bbox_col_min - padding
    col_max = metrics.bbox_col_max + padding
    row_min = metrics.bbox_row_min - padding
    row_max = metrics.bbox_row_max + padding

    width = col_max - col_min + 1
    height = row_max - row_min + 1

    if width < min_width:
        grow = min_width - width
        left = grow // 2
        right = grow - left
        col_min -= left
        col_max += right

    if height < min_height:
        grow = min_height - height
        down = grow // 2
        up = grow - down
        row_min -= down
        row_max += up

    col_min = max(device_bounds["min_col"], col_min)
    col_max = min(device_bounds["max_col"], col_max)
    row_min = max(device_bounds["min_row"], row_min)
    row_max = min(device_bounds["max_row"], row_max)

    return col_min, col_max, row_min, row_max


def get_device_bounds() -> Dict[str, int]:
    design = rw._current_design
    if design is None:
        raise RuntimeError("RapidWright design is not loaded")
    device = design.getDevice()
    min_col = 10**9
    max_col = -1
    min_row = 10**9
    max_row = -1
    for tile in device.getAllTiles():
        min_col = min(min_col, int(tile.getColumn()))
        max_col = max(max_col, int(tile.getColumn()))
        min_row = min(min_row, int(tile.getRow()))
        max_row = max(max_row, int(tile.getRow()))
    return {
        "min_col": min_col,
        "max_col": max_col,
        "min_row": min_row,
        "max_row": max_row,
    }


def make_site_pblock_ranges(col_min: int, col_max: int, row_min: int, row_max: int) -> str:
    result = rw.convert_fabric_region_to_pblock_ranges(
        col_min=col_min,
        col_max=col_max,
        row_min=row_min,
        row_max=row_max,
        use_clock_regions=False,
    )
    if result.get("status") != "success":
        raise RuntimeError(f"Could not convert region to pblock ranges: {result}")
    return str(result["pblock_ranges"])


def chunked(seq: List[str], size: int) -> List[List[str]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def build_add_cells_script(pblock_name: str, cell_names: List[str]) -> str:
    lines = []
    for group in chunked(cell_names, 64):
        listed = " ".join("{" + c + "}" for c in group)
        lines.append(f"add_cells_to_pblock {pblock_name} [get_cells -quiet [list {listed}]]")
    return "\n".join(lines)


def write_tcl_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def save_json(path: Path, payload: object) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def make_axis_equalish(ax, xs: List[int], ys: List[int], pad: int = 2) -> None:
    if not xs or not ys:
        return
    xmin = min(xs) - pad
    xmax = max(xs) + pad
    ymin = min(ys) - pad
    ymax = max(ys) + pad
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal", adjustable="box")


def save_top_paths_overview(metrics: List[PathMetrics], out_path: Path, top_k: int) -> None:
    fig, ax = plt.subplots(figsize=(10, 8))
    for rank, m in enumerate(metrics[:top_k], start=1):
        xs = [int(c["col"]) for c in m.placed_cells]
        ys = [int(c["row"]) for c in m.placed_cells]
        ax.plot(xs, ys, marker="o", linewidth=1.0, label=f"rank {rank}: path {m.path_index}")
        rect = Rectangle(
            (m.bbox_col_min, m.bbox_row_min),
            m.bbox_width,
            m.bbox_height,
            fill=False,
            linewidth=1.5,
        )
        ax.add_patch(rect)
        ax.text(m.bbox_col_min, m.bbox_row_max + 1, f"r{rank}", fontsize=9)
    ax.set_title(f"Step 1: Top {top_k} critical paths by spread score")
    ax.set_xlabel("tile column")
    ax.set_ylabel("tile row")
    ax.legend(loc="best", fontsize=8)
    all_xs = [int(c["col"]) for m in metrics[:top_k] for c in m.placed_cells]
    all_ys = [int(c["row"]) for m in metrics[:top_k] for c in m.placed_cells]
    make_axis_equalish(ax, all_xs, all_ys, pad=5)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def save_path_detail(metrics: PathMetrics, out_path: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(10, 8))
    xs = [int(c["col"]) for c in metrics.placed_cells]
    ys = [int(c["row"]) for c in metrics.placed_cells]
    ax.plot(xs, ys, marker="o", linewidth=1.5)
    for i, c in enumerate(metrics.placed_cells):
        if i in (0, len(metrics.placed_cells) - 1):
            ax.text(int(c["col"]) + 0.5, int(c["row"]) + 0.5, str(i), fontsize=8)
    rect = Rectangle(
        (metrics.bbox_col_min, metrics.bbox_row_min),
        metrics.bbox_width,
        metrics.bbox_height,
        fill=False,
        linewidth=1.5,
    )
    ax.add_patch(rect)
    ax.set_title(title)
    ax.set_xlabel("tile column")
    ax.set_ylabel("tile row")
    make_axis_equalish(ax, xs, ys, pad=4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def save_candidate_region_plot(metrics: PathMetrics, region: Tuple[int, int, int, int], out_path: Path, candidate_id: int) -> None:
    fig, ax = plt.subplots(figsize=(10, 8))
    xs = [int(c["col"]) for c in metrics.placed_cells]
    ys = [int(c["row"]) for c in metrics.placed_cells]
    ax.plot(xs, ys, marker="o", linewidth=1.5, label="critical path order")

    path_rect = Rectangle(
        (metrics.bbox_col_min, metrics.bbox_row_min),
        metrics.bbox_width,
        metrics.bbox_height,
        fill=False,
        linewidth=1.5,
        label="original path bbox",
    )
    ax.add_patch(path_rect)

    col_min, col_max, row_min, row_max = region
    region_rect = Rectangle(
        (col_min, row_min),
        col_max - col_min + 1,
        row_max - row_min + 1,
        fill=False,
        linestyle="--",
        linewidth=2.0,
        label="local pblock region",
    )
    ax.add_patch(region_rect)

    ax.set_title(f"Step 2: Candidate {candidate_id} local clustering region for path {metrics.path_index}")
    ax.set_xlabel("tile column")
    ax.set_ylabel("tile row")
    ax.legend(loc="best", fontsize=8)
    make_axis_equalish(ax, xs + [col_min, col_max], ys + [row_min, row_max], pad=4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def save_ranked_score_plot(metrics: List[PathMetrics], out_path: Path, top_k: int) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    chosen = metrics[:top_k]
    labels = [f"p{m.path_index}" for m in chosen]
    values = [m.score for m in chosen]
    ax.bar(labels, values)
    ax.set_title("Step 1b: Spread scores for selected critical paths")
    ax.set_xlabel("path")
    ax.set_ylabel("score")
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def save_candidate_results_plot(candidate_results: List[CandidateResult], initial_wns: Optional[float], out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    labels = [f"c{r.candidate_id}" for r in candidate_results]
    values = [r.wns if r.wns is not None else float("nan") for r in candidate_results]
    ax.bar(labels, values)
    if initial_wns is not None:
        ax.axhline(initial_wns, linestyle="--", linewidth=1.5)
    ax.set_title("Step 3: Candidate WNS comparison")
    ax.set_xlabel("candidate")
    ax.set_ylabel("WNS (ns)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def save_improvement_plot(candidate_results: List[CandidateResult], initial_wns: Optional[float], out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    labels = [f"c{r.candidate_id}" for r in candidate_results]
    improvements = []
    for r in candidate_results:
        if initial_wns is None or r.wns is None:
            improvements.append(float("nan"))
        else:
            improvements.append(r.wns - initial_wns)
    ax.bar(labels, improvements)
    ax.axhline(0.0, linestyle="--", linewidth=1.5)
    ax.set_title("Step 4: WNS improvement over baseline")
    ax.set_xlabel("candidate")
    ax.set_ylabel("delta WNS (ns)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def save_best_candidate_plot(metrics: PathMetrics, best: CandidateResult, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 8))
    xs = [int(c["col"]) for c in metrics.placed_cells]
    ys = [int(c["row"]) for c in metrics.placed_cells]
    ax.plot(xs, ys, marker="o", linewidth=1.5, label="best path")

    col_min, col_max, row_min, row_max = best.region
    region_rect = Rectangle(
        (col_min, row_min),
        col_max - col_min + 1,
        row_max - row_min + 1,
        fill=False,
        linestyle="--",
        linewidth=2.0,
        label="chosen pblock",
    )
    ax.add_patch(region_rect)
    ax.set_title(f"Step 5: Best candidate c{best.candidate_id} for path {best.path_index}")
    ax.set_xlabel("tile column")
    ax.set_ylabel("tile row")
    ax.legend(loc="best", fontsize=8)
    make_axis_equalish(ax, xs + [col_min, col_max], ys + [row_min, row_max], pad=4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def write_visualization_index(work_dir: Path, steps: List[StepRecord]) -> None:
    lines = ["# Visualization index", ""]
    for step in steps:
        lines.append(f"## Step {step.step_index}: {step.title}")
        lines.append(step.description)
        lines.append(f"Figure: `{step.figure_path}`")
        lines.append("")
    with open(work_dir / "visualization_index.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def create_step_animation(
    work_dir: Path,
    steps: List[StepRecord],
    output_name: str = "visualization_animation.gif",
    frame_duration_ms: int = 1200,
    add_captions: bool = True,
) -> Optional[Path]:
    if not steps or Image is None:
        return None

    frames = []
    max_w = 0
    max_h = 0

    for step in steps:
        img_path = Path(step.figure_path)
        if not img_path.is_absolute():
            img_path = (work_dir / img_path).resolve()
        if not img_path.exists():
            continue
        img = Image.open(img_path).convert("RGB")
        if add_captions and ImageOps is not None and ImageDraw is not None:
            caption_h = 90
            canvas = Image.new("RGB", (img.width, img.height + caption_h), "white")
            canvas.paste(img, (0, 0))
            draw = ImageDraw.Draw(canvas)
            draw.text((12, img.height + 10), f"Step {step.step_index}: {step.title}", fill="black")
            draw.text((12, img.height + 36), step.description[:140], fill="black")
            img = canvas
        max_w = max(max_w, img.width)
        max_h = max(max_h, img.height)
        frames.append(img)

    if not frames:
        return None

    padded = []
    for img in frames:
        canvas = Image.new("RGB", (max_w, max_h), "white")
        x = (max_w - img.width) // 2
        y = (max_h - img.height) // 2
        canvas.paste(img, (x, y))
        padded.append(canvas)

    out_path = work_dir / output_name
    padded[0].save(
        out_path,
        save_all=True,
        append_images=padded[1:],
        duration=frame_duration_ms,
        loop=0,
        optimize=False,
    )
    return out_path


def write_interactive_playback(work_dir: Path, steps: List[StepRecord], html_name: str = "interactive_playback.html") -> Optional[Path]:
    if not steps:
        return None

    payload = []
    for step in steps:
        fig_path = Path(step.figure_path)
        try:
            rel_fig = fig_path.relative_to(work_dir)
        except Exception:
            rel_fig = Path(step.figure_path)
        payload.append(
            {
                "step_index": step.step_index,
                "title": step.title,
                "description": step.description,
                "figure_path": str(rel_fig).replace("\\", "/"),
            }
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Critical Path Optimization Playback</title>
<style>
body {{
  font-family: Arial, sans-serif;
  margin: 0;
  background: #f5f5f5;
  color: #111;
}}
.wrap {{
  max-width: 1200px;
  margin: 0 auto;
  padding: 20px;
}}
h1 {{
  margin-top: 0;
}}
.controls {{
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 16px;
}}
button {{
  padding: 8px 14px;
  cursor: pointer;
}}
input[type=range] {{
  flex: 1 1 300px;
}}
.viewer {{
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 16px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}}
.meta {{
  margin-bottom: 12px;
}}
.meta .title {{
  font-size: 22px;
  font-weight: bold;
}}
.meta .desc {{
  margin-top: 8px;
  line-height: 1.4;
}}
img {{
  max-width: 100%;
  height: auto;
  display: block;
  margin: 0 auto;
  border: 1px solid #ccc;
}}
.footer {{
  margin-top: 14px;
  color: #444;
}}
</style>
</head>
<body>
<div class="wrap">
  <h1>Critical Path Optimization Playback</h1>
  <div class="controls">
    <button onclick="prevStep()">Prev</button>
    <button onclick="togglePlay()" id="playBtn">Play</button>
    <button onclick="nextStep()">Next</button>
    <label>Speed
      <select id="speedSel" onchange="updateSpeed()">
        <option value="2000">0.5x</option>
        <option value="1200" selected>1x</option>
        <option value="700">1.7x</option>
        <option value="400">3x</option>
      </select>
    </label>
    <input id="slider" type="range" min="0" max="{max(0, len(payload)-1)}" value="0" oninput="setStep(parseInt(this.value))">
  </div>
  <div class="viewer">
    <div class="meta">
      <div id="stepLabel"></div>
      <div class="title" id="title"></div>
      <div class="desc" id="desc"></div>
    </div>
    <img id="img" src="" alt="Playback step">
    <div class="footer">Use Left/Right arrow keys to navigate.</div>
  </div>
</div>
<script>
const steps = {json.dumps(payload, indent=2)};
let idx = 0;
let timer = null;
let delayMs = 1200;

function render() {{
  const s = steps[idx];
  document.getElementById("stepLabel").textContent = `Step ${'{'}s.step_index{'}'} of ${len(payload)}`;
  document.getElementById("title").textContent = s.title;
  document.getElementById("desc").textContent = s.description;
  document.getElementById("img").src = s.figure_path;
  document.getElementById("slider").value = idx;
}}

function setStep(i) {{
  idx = Math.max(0, Math.min(i, steps.length - 1));
  render();
}}

function nextStep() {{
  idx = (idx + 1) % steps.length;
  render();
}}

function prevStep() {{
  idx = (idx - 1 + steps.length) % steps.length;
  render();
}}

function tick() {{
  nextStep();
}}

function togglePlay() {{
  const btn = document.getElementById("playBtn");
  if (timer) {{
    clearInterval(timer);
    timer = null;
    btn.textContent = "Play";
  }} else {{
    timer = setInterval(tick, delayMs);
    btn.textContent = "Pause";
  }}
}}

function updateSpeed() {{
  delayMs = parseInt(document.getElementById("speedSel").value);
  if (timer) {{
    clearInterval(timer);
    timer = setInterval(tick, delayMs);
  }}
}}

document.addEventListener("keydown", (e) => {{
  if (e.key === "ArrowRight") nextStep();
  if (e.key === "ArrowLeft") prevStep();
  if (e.key === " ") {{
    e.preventDefault();
    togglePlay();
  }}
}});

render();
</script>
</body>
</html>
"""
    out_path = work_dir / html_name
    out_path.write_text(html, encoding="utf-8")
    return out_path


def evaluate_candidate(
    input_dcp: Path,
    work_dir: Path,
    candidate_id: int,
    metrics: PathMetrics,
    pblock_ranges: str,
    region: Tuple[int, int, int, int],
    place_directive: str,
    route_directive: str,
    run_phys_opt: bool,
) -> CandidateResult:
    pblock_name = f"cp_local_{candidate_id}"
    candidate_dcp = work_dir / f"candidate_{candidate_id}.dcp"
    script_path = work_dir / f"candidate_{candidate_id}.tcl"

    tcl_lines = [
        f"open_checkpoint {{{input_dcp}}}",
        f"if {{[llength [get_pblocks {pblock_name}]] > 0}} {{ delete_pblocks [get_pblocks {pblock_name}] }}",
        f"create_pblock {pblock_name}",
        f"resize_pblock {pblock_name} -add {{{pblock_ranges}}}",
        f"set_property IS_SOFT 0 [get_pblocks {pblock_name}]",
        build_add_cells_script(pblock_name, metrics.cell_names),
        "place_design -unplace",
        f"place_design -directive {place_directive}",
    ]

    if run_phys_opt:
        tcl_lines.append("phys_opt_design -placement_opt -critical_pin_opt")

    tcl_lines.extend(
        [
            f"route_design -directive {route_directive}",
            f"write_checkpoint -force {{{candidate_dcp}}}",
        ]
    )

    write_tcl_file(script_path, "\n".join(tcl_lines) + "\n")

    try:
        open_checkpoint_in_vivado(input_dcp)
        run_tcl(f"source {{{script_path}}}", timeout=7200.0)
        timing = report_timing_summary(timeout=600.0)
        timing_info = parse_timing_summary_static(timing)
        wns = timing_info["wns"]
        note = f"path={metrics.path_index} bbox={metrics.bbox_width}x{metrics.bbox_height} max_step={metrics.max_consecutive_distance}"
        return CandidateResult(
            candidate_id=candidate_id,
            path_index=metrics.path_index,
            pblock_ranges=pblock_ranges,
            checkpoint_path=candidate_dcp,
            wns=wns,
            success=wns is not None and candidate_dcp.exists(),
            note=note,
            region=region,
        )
    except Exception as exc:
        logger.exception("Candidate %d failed", candidate_id)
        return CandidateResult(
            candidate_id=candidate_id,
            path_index=metrics.path_index,
            pblock_ranges=pblock_ranges,
            checkpoint_path=candidate_dcp,
            wns=None,
            success=False,
            note=str(exc),
            region=region,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic local critical-path clustering optimizer with visualization")
    parser.add_argument("input_dcp", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--num-paths", type=int, default=40)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--padding", type=int, default=10)
    parser.add_argument("--min-width", type=int, default=16)
    parser.add_argument("--min-height", type=int, default=40)
    parser.add_argument("--place-directive", type=str, default="Default")
    parser.add_argument("--route-directive", type=str, default="Default")
    parser.add_argument("--phys-opt", action="store_true")
    parser.add_argument("--visualize", action="store_true", default=True)
    parser.add_argument("--no-visualize", dest="visualize", action="store_false")
    parser.add_argument("--vivado-log", type=Path, default=None)
    parser.add_argument("--vivado-journal", type=Path, default=None)
    parser.add_argument("--frame-duration-ms", type=int, default=1200)
    parser.add_argument("--make-gif", action="store_true", default=True)
    parser.add_argument("--no-gif", dest="make_gif", action="store_false")
    parser.add_argument("--make-html-playback", action="store_true", default=True)
    parser.add_argument("--no-html-playback", dest="make_html_playback", action="store_false")
    args = parser.parse_args()

    input_dcp = args.input_dcp.resolve()
    output_dcp = args.output.resolve()
    if not input_dcp.exists():
        logger.error("Input DCP not found: %s", input_dcp)
        return 1

    if args.work_dir is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        work_dir = Path.cwd() / f"local_cluster_run-{ts}"
    else:
        work_dir = args.work_dir.resolve()
    ensure_dir(work_dir)
    ensure_dir(output_dcp.parent)

    fig_dir = work_dir / "figures"
    ensure_dir(fig_dir)
    steps: List[StepRecord] = []

    vv._vivado_log_file = str(args.vivado_log) if args.vivado_log else str(work_dir / "vivado.log")
    vv._vivado_journal_file = str(args.vivado_journal) if args.vivado_journal else str(work_dir / "vivado.jou")
    vv.start_vivado(vv._vivado_log_file, vv._vivado_journal_file)

    logger.info("Work directory: %s", work_dir)
    logger.info("Opening checkpoint in Vivado: %s", input_dcp)
    open_checkpoint_in_vivado(input_dcp)

    initial_timing = report_timing_summary(timeout=600.0)
    initial_info = parse_timing_summary_static(initial_timing)
    initial_wns = initial_info["wns"]
    clock_period = get_clock_period()
    logger.info("Initial WNS: %s", initial_wns)
    if clock_period is not None:
        logger.info("Clock period: %.3f ns, initial fmax: %s", clock_period, calculate_fmax(initial_wns, clock_period))

    logger.info("Initializing RapidWright and loading checkpoint")
    initialize_rapidwright_and_load(input_dcp)

    paths_file = work_dir / "critical_paths.json"
    critical_paths = extract_critical_paths_json(paths_file, num_paths=args.num_paths)
    if not critical_paths:
        logger.error("No critical paths extracted")
        return 1

    metrics = collect_path_metrics(critical_paths)
    if not metrics:
        logger.error("Could not collect any placed path metrics")
        return 1

    device_bounds = get_device_bounds()

    ranked_summary = []
    for rank, m in enumerate(metrics[: args.top_k], start=1):
        ranked_summary.append(
            {
                "rank": rank,
                "path_index": m.path_index,
                "cell_count": len(m.cell_names),
                "bbox": [m.bbox_col_min, m.bbox_col_max, m.bbox_row_min, m.bbox_row_max],
                "bbox_width": m.bbox_width,
                "bbox_height": m.bbox_height,
                "max_consecutive_distance": m.max_consecutive_distance,
                "avg_consecutive_distance": m.avg_consecutive_distance,
                "score": m.score,
            }
        )
    save_json(work_dir / "ranked_paths.json", ranked_summary)

    if args.visualize:
        overview_path = fig_dir / "step_1_top_paths_overview.png"
        save_top_paths_overview(metrics, overview_path, args.top_k)
        steps.append(StepRecord(1, "Top critical path overview", "Top-ranked critical paths plotted in tile space with their original bounding boxes.", str(overview_path)))

        score_path = fig_dir / "step_1b_path_scores.png"
        save_ranked_score_plot(metrics, score_path, args.top_k)
        steps.append(StepRecord(2, "Spread score ranking", "Bar chart of the path selection score used to choose local-clustering candidates.", str(score_path)))

        for rank, m in enumerate(metrics[: args.top_k], start=1):
            detail_path = fig_dir / f"step_1c_path_{rank}_detail.png"
            save_path_detail(m, detail_path, f"Path rank {rank}: path {m.path_index}, score={m.score:.2f}")
            steps.append(StepRecord(2 + rank, f"Path detail rank {rank}", f"Ordered path geometry for selected path {m.path_index}.", str(detail_path)))

    candidate_results: List[CandidateResult] = []
    for candidate_id, m in enumerate(metrics[: args.top_k], start=1):
        region = choose_region_for_path(
            metrics=m,
            device_bounds=device_bounds,
            padding=args.padding,
            min_width=args.min_width,
            min_height=args.min_height,
        )
        pblock_ranges = make_site_pblock_ranges(*region)
        logger.info("Evaluating candidate %d on path %d with region %s", candidate_id, m.path_index, pblock_ranges)

        if args.visualize:
            candidate_fig = fig_dir / f"step_2_candidate_{candidate_id}_region.png"
            save_candidate_region_plot(m, region, candidate_fig, candidate_id)
        else:
            candidate_fig = None

        result = evaluate_candidate(
            input_dcp=input_dcp,
            work_dir=work_dir,
            candidate_id=candidate_id,
            metrics=m,
            pblock_ranges=pblock_ranges,
            region=region,
            place_directive=args.place_directive,
            route_directive=args.route_directive,
            run_phys_opt=args.phys_opt,
        )
        result.figure_path = str(candidate_fig) if candidate_fig else None
        candidate_results.append(result)
        logger.info("Candidate %d result: success=%s wns=%s note=%s", result.candidate_id, result.success, result.wns, result.note)

        if args.visualize and candidate_fig is not None:
            steps.append(StepRecord(100 + candidate_id, f"Candidate {candidate_id} region", f"Local pblock built around path {m.path_index}; result WNS={result.wns}.", str(candidate_fig)))

    serializable_candidates = []
    for r in candidate_results:
        serializable_candidates.append(
            {
                "candidate_id": r.candidate_id,
                "path_index": r.path_index,
                "pblock_ranges": r.pblock_ranges,
                "checkpoint_path": str(r.checkpoint_path),
                "wns": r.wns,
                "success": r.success,
                "note": r.note,
                "region": list(r.region),
                "figure_path": r.figure_path,
            }
        )
    save_json(work_dir / "candidate_results.json", serializable_candidates)

    successful = [r for r in candidate_results if r.success and r.wns is not None]
    if not successful:
        logger.error("No candidate completed successfully")
        return 1

    best = max(successful, key=lambda x: float(x.wns))
    logger.info("Best candidate: %d, WNS=%s", best.candidate_id, best.wns)

    shutil.copy2(best.checkpoint_path, output_dcp)

    final_fmax = calculate_fmax(best.wns, clock_period)
    improvement = None
    if initial_wns is not None and best.wns is not None:
        improvement = best.wns - initial_wns

    summary = {
        "input_dcp": str(input_dcp),
        "output_dcp": str(output_dcp),
        "work_dir": str(work_dir),
        "initial_wns": initial_wns,
        "best_wns": best.wns,
        "wns_improvement": improvement,
        "clock_period_ns": clock_period,
        "best_fmax_mhz": final_fmax,
        "best_candidate_id": best.candidate_id,
        "best_path_index": best.path_index,
        "best_pblock_ranges": best.pblock_ranges,
        "figures_dir": str(fig_dir),
    }
    save_json(work_dir / "summary.json", summary)

    if args.visualize:
        result_plot = fig_dir / "step_3_candidate_wns.png"
        save_candidate_results_plot(candidate_results, initial_wns, result_plot)
        steps.append(StepRecord(200, "Candidate WNS comparison", "WNS achieved by each candidate after place, optional phys-opt, and route.", str(result_plot)))

        improvement_plot = fig_dir / "step_4_candidate_improvement.png"
        save_improvement_plot(candidate_results, initial_wns, improvement_plot)
        steps.append(StepRecord(201, "Candidate delta WNS", "Improvement of each candidate relative to the original checkpoint.", str(improvement_plot)))

        best_metrics = next(m for m in metrics[: args.top_k] if m.path_index == best.path_index)
        best_plot = fig_dir / "step_5_best_candidate.png"
        save_best_candidate_plot(best_metrics, best, best_plot)
        steps.append(StepRecord(202, "Best candidate selection", "Final chosen path and local pblock region.", str(best_plot)))

        write_visualization_index(work_dir, steps)
        save_json(work_dir / "visualization_steps.json", [asdict(s) for s in steps])

        animation_path = None
        playback_html_path = None

        if args.make_gif:
            try:
                animation_path = create_step_animation(
                    work_dir=work_dir,
                    steps=steps,
                    output_name="visualization_animation.gif",
                    frame_duration_ms=args.frame_duration_ms,
                    add_captions=True,
                )
            except Exception as exc:
                logger.warning("Failed to create GIF animation: %s", exc)

        if args.make_html_playback:
            try:
                playback_html_path = write_interactive_playback(
                    work_dir=work_dir,
                    steps=steps,
                    html_name="interactive_playback.html",
                )
            except Exception as exc:
                logger.warning("Failed to create interactive playback HTML: %s", exc)

    print("Optimization summary")
    print(f"  Input DCP:         {input_dcp}")
    print(f"  Output DCP:        {output_dcp}")
    print(f"  Work dir:          {work_dir}")
    print(f"  Initial WNS:       {initial_wns}")
    print(f"  Best WNS:          {best.wns}")
    print(f"  WNS improvement:   {improvement}")
    if clock_period is not None:
        print(f"  Clock period:      {clock_period:.3f} ns")
    if final_fmax is not None:
        print(f"  Best fmax:         {final_fmax:.2f} MHz")
    print(f"  Best candidate:    {best.candidate_id}")
    print(f"  Best path index:   {best.path_index}")
    print(f"  Best pblock:       {best.pblock_ranges}")
    if args.visualize:
        print(f"  Figures dir:       {fig_dir}")
        print(f"  Visualization md:  {work_dir / 'visualization_index.md'}")
        try:
            if animation_path is not None:
                print(f"  GIF animation:     {animation_path}")
        except UnboundLocalError:
            pass
        try:
            if playback_html_path is not None:
                print(f"  HTML playback:     {playback_html_path}")
        except UnboundLocalError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
