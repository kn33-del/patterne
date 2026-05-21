#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: Apache 2.0

"""

Wraps DCPOptimizer with per-iteration history tracking:
  - Prompt and API call records for every Grok call
  - Vivado-sourced timing metrics (WNS, TNS, fmax, failing endpoints)
    captured after each optimization iteration
  - A structured history context injected into every subsequent prompt
    so the model never revisits a failed strategy or circles back

Drop-in usage:
    optimizer = HistoryAwareDCPOptimizer(api_key=..., model=..., debug=..., run_dir=...)
    await optimizer.start_servers()
    success = await optimizer.optimize(input_dcp, output_dcp)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from optimizer import (
    DCPOptimizer,
    parse_timing_summary_static,
    terminal_log,
    DEFAULT_MODEL,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class IterationMetrics:
    """Timing metrics captured from Vivado after a single optimization iteration."""
    iteration: int
    timestamp: float = field(default_factory=time.time)

    # Timing values – populated from vivado_report_timing_summary / vivado_get_wns
    wns: Optional[float] = None          # Worst Negative Slack (ns); positive = timing met
    tns: Optional[float] = None          # Total Negative Slack (ns)
    failing_endpoints: Optional[int] = None
    fmax_mhz: Optional[float] = None     # Achievable clock rate (MHz)

    # Delta versus the previous iteration (filled in by the tracker)
    wns_delta: Optional[float] = None
    fmax_delta_mhz: Optional[float] = None

    # Whether this iteration improved on the all-time best WNS
    is_new_best: bool = False

    # Strategies / tool calls the LLM attempted this iteration
    tools_called: list[str] = field(default_factory=list)

    # Free-form notes (e.g. "reverted – regression detected")
    notes: str = ""


@dataclass
class PromptRecord:
    """Records the prompt sent and the response received for a single Grok API call."""
    call_number: int
    iteration: int
    timestamp: float = field(default_factory=time.time)

    # Summarised prompt context (first N chars of the user message)
    prompt_summary: str = ""

    # What the model decided to do (text portion of response, truncated)
    response_summary: str = ""

    # Token accounting
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    # Tool calls requested by the model in this completion
    tool_calls_requested: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# History tracker
# ---------------------------------------------------------------------------

class OptimizationHistory:
    """
    Maintains the full history of prompts and metrics across all iterations.

    The tracker generates a compact *history context* string that can be
    prepended to the next prompt, giving the model:
      1. What strategies have already been tried (and their outcome)
      2. The metric trajectory (WNS / fmax) so it can see trends
      3. Explicit warnings when it is about to circle back
    """

    MAX_SUMMARY_CHARS = 300   # per-record truncation for prompt context

    def __init__(self, clock_period_ns: Optional[float] = None):
        self.clock_period_ns = clock_period_ns
        self.iterations: list[IterationMetrics] = []
        self.prompt_records: list[PromptRecord] = []
        self._all_time_best_wns: Optional[float] = None

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_metrics(
        self,
        iteration: int,
        wns: Optional[float],
        tns: Optional[float] = None,
        failing_endpoints: Optional[int] = None,
        fmax_mhz: Optional[float] = None,
        tools_called: Optional[list[str]] = None,
        notes: str = "",
    ) -> IterationMetrics:
        """Add a metrics snapshot for the given iteration."""
        prev = self.iterations[-1] if self.iterations else None

        wns_delta = None
        fmax_delta = None
        is_new_best = False

        if wns is not None:
            if prev is not None and prev.wns is not None:
                wns_delta = wns - prev.wns
                if fmax_mhz is not None and prev.fmax_mhz is not None:
                    fmax_delta = fmax_mhz - prev.fmax_mhz

            if self._all_time_best_wns is None or wns > self._all_time_best_wns:
                self._all_time_best_wns = wns
                is_new_best = True

        m = IterationMetrics(
            iteration=iteration,
            wns=wns,
            tns=tns,
            failing_endpoints=failing_endpoints,
            fmax_mhz=fmax_mhz,
            wns_delta=wns_delta,
            fmax_delta_mhz=fmax_delta,
            is_new_best=is_new_best,
            tools_called=tools_called or [],
            notes=notes,
        )
        self.iterations.append(m)

        tag = "★ NEW BEST" if is_new_best else ("▲ improved" if (wns_delta or 0) > 0 else ("▼ regressed" if (wns_delta or 0) < 0 else "─ no change"))
        logger.info(
            f"[History] iter={iteration}  WNS={wns}  fmax={fmax_mhz}  {tag}"
        )
        return m

    def record_prompt(
        self,
        call_number: int,
        iteration: int,
        prompt_summary: str,
        response_summary: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cost_usd: float = 0.0,
        tool_calls_requested: Optional[list[str]] = None,
    ) -> PromptRecord:
        """Record a single Grok API call."""
        p = PromptRecord(
            call_number=call_number,
            iteration=iteration,
            prompt_summary=prompt_summary[: self.MAX_SUMMARY_CHARS],
            response_summary=response_summary[: self.MAX_SUMMARY_CHARS],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            tool_calls_requested=tool_calls_requested or [],
        )
        self.prompt_records.append(p)
        return p

    # ------------------------------------------------------------------
    # Context generation (injected into the next prompt)
    # ------------------------------------------------------------------

    def build_history_context(self) -> str:
        """
        Return a concise history block for the model to read before deciding
        its next action.  Keeps the token footprint small while giving the
        model everything it needs to avoid circular behaviour.
        """
        if not self.iterations and not self.prompt_records:
            return ""

        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("OPTIMIZATION HISTORY  (read before deciding next action)")
        lines.append("=" * 60)

        # the Metric trajectory
        if self.iterations:
            lines.append("\n[Metric trajectory]")
            lines.append(
                f"{'Iter':>4}  {'WNS(ns)':>9}  {'fmax(MHz)':>10}  {'ΔWNS':>8}  {'Δfmax':>8}  Status"
            )
            lines.append("-" * 62)
            for m in self.iterations:
                wns_s = f"{m.wns:+.3f}" if m.wns is not None else "  N/A "
                fmax_s = f"{m.fmax_mhz:.2f}" if m.fmax_mhz is not None else "   N/A"
                dwns_s = f"{m.wns_delta:+.3f}" if m.wns_delta is not None else "    — "
                dfmax_s = f"{m.fmax_delta_mhz:+.2f}" if m.fmax_delta_mhz is not None else "    —"
                flag = "★ BEST" if m.is_new_best else ("▲" if (m.wns_delta or 0) > 0 else ("▼" if (m.wns_delta or 0) < 0 else "─"))
                extra = f"  [{m.notes}]" if m.notes else ""
                lines.append(
                    f"{m.iteration:>4}  {wns_s:>9}  {fmax_s:>10}  {dwns_s:>8}  {dfmax_s:>8}  {flag}{extra}"
                )

        # Strategies already attempted
        attempted_strategies: dict[str, list[int]] = {}
        for m in self.iterations:
            for t in m.tools_called:
                attempted_strategies.setdefault(t, []).append(m.iteration)

        if attempted_strategies:
            lines.append("\n[Tools / strategies already attempted]")
            for tool, iters in attempted_strategies.items():
                lines.append(f"  {tool}: iterations {iters}")

        #Regression warnings
        regressions = [m for m in self.iterations if (m.wns_delta or 0) < 0]
        if regressions:
            lines.append("\n[Iterations that caused regressions – do NOT repeat these]")
            for m in regressions:
                lines.append(
                    f"  Iter {m.iteration}: WNS went {m.wns_delta:+.3f} ns  tools={m.tools_called}"
                )

        #All time best (ask brindha for more info)
        if self._all_time_best_wns is not None:
            lines.append(f"\n[All-time best WNS so far: {self._all_time_best_wns:+.3f} ns]")

        # recent prompt summary (last 3 calls)
        if self.prompt_records:
            recent = self.prompt_records[-3:]
            lines.append("\n[Recent API calls]")
            for p in recent:
                lines.append(
                    f"  Call #{p.call_number} (iter {p.iteration}): "
                    f"tokens={p.total_tokens}  cost=${p.cost_usd:.4f}"
                )
                if p.tool_calls_requested:
                    lines.append(f"    → requested tools: {p.tool_calls_requested}")

        lines.append("=" * 60)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path):
        """Persist history to a JSON file in the run directory."""
        data = {
            "clock_period_ns": self.clock_period_ns,
            "all_time_best_wns": self._all_time_best_wns,
            "iterations": [asdict(m) for m in self.iterations],
            "prompt_records": [asdict(p) for p in self.prompt_records],
        }
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)
        logger.info(f"[History] Saved history to {path}")

    @classmethod
    def load(cls, path: Path) -> "OptimizationHistory":
        """Restore history from a JSON file (e.g. to resume a run)."""
        with open(path) as fh:
            data = json.load(fh)
        h = cls(clock_period_ns=data.get("clock_period_ns"))
        h._all_time_best_wns = data.get("all_time_best_wns")
        for raw in data.get("iterations", []):
            h.iterations.append(IterationMetrics(**raw))
        for raw in data.get("prompt_records", []):
            h.prompt_records.append(PromptRecord(**raw))
        logger.info(f"[History] Loaded {len(h.iterations)} iterations from {path}")
        return h


# ---------------------------------------------------------------------------
# History-aware optimizer (extends DCPOptimizer)
# ---------------------------------------------------------------------------

class HistoryAwareDCPOptimizer(DCPOptimizer):
    """
    DCPOptimizer subclass that tracks optimization history and injects it into
    every subsequent Grok prompt.

    Key additions vs. the base class:

    1.  After each iteration, captures Vivado timing metrics via
        ``vivado_report_timing_summary`` (already called by the base tool
        machinery) and stores them in ``OptimizationHistory``.

    2.  Overrides ``get_completion`` to:
        a. Inject the history context block at the top of the latest user message.
        b. Record the prompt/response pair in the history.

    3.  Overrides ``process_response`` to collect which tool calls were made
        during that iteration.

    4.  Saves a ``history.json`` alongside ``token_usage.json`` in the run dir.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        debug: bool = False,
        run_dir: Optional[Path] = None,
    ):
        super().__init__(api_key=api_key, model=model, debug=debug, run_dir=run_dir)
        self.history = OptimizationHistory()

        # Tools called during the *current* iteration (reset each iteration)
        self._current_iter_tools: list[str] = []

        # Snapshot WNS from the previous measurement so we can detect changes
        self._last_known_wns: Optional[float] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_metrics_from_vivado(self) -> tuple[Optional[float], Optional[float], Optional[int]]:
        """
        Pull fresh WNS / TNS / failing-endpoint data from Vivado.
        Uses the existing call_tool infrastructure so all logging is preserved.

        Returns (wns, tns, failing_endpoints).
        """
        try:
            timing_report = await self.call_tool("vivado_report_timing_summary", {})
            info = parse_timing_summary_static(timing_report)
            return info["wns"], info["tns"], info["failing_endpoints"]
        except Exception as exc:
            logger.warning(f"[History] Could not fetch timing metrics: {exc}")
            return None, None, None

    def _snapshot_metrics(self, wns: Optional[float], tns: Optional[float], failing_endpoints: Optional[int]):
        """Record a metrics snapshot for the current iteration."""
        fmax = self.calculate_fmax(wns, self.clock_period)
        m = self.history.record_metrics(
            iteration=self.iteration,
            wns=wns,
            tns=tns,
            failing_endpoints=failing_endpoints,
            fmax_mhz=fmax,
            tools_called=list(self._current_iter_tools),
        )

        # Surface a regression warning to the log so it is visible in run logs
        if m.wns_delta is not None and m.wns_delta < 0:
            terminal_log(
                "WARN",
                f"Iteration {self.iteration} caused a regression: "
                f"WNS {m.wns_delta:+.3f} ns — history will warn the model.",
                iteration=self.iteration,
                wns=wns,
                fmax=fmax,
            )
        elif m.is_new_best:
            terminal_log(
                "RESULT",
                f"New best WNS: {wns:+.3f} ns",
                iteration=self.iteration,
                wns=wns,
                fmax=fmax,
            )

        self._last_known_wns = wns
        self._current_iter_tools = []   # reset for next iteration

    def _inject_history_into_messages(self):
        """
        Prepend the history context to the *last* user message so the model
        sees it immediately before generating its next action.
        """
        ctx = self.history.build_history_context()
        if not ctx:
            return

        # Find the last user message and prepend the context block
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].get("role") == "user":
                original = self.messages[i].get("content", "")
                if isinstance(original, str):
                    self.messages[i] = {
                        **self.messages[i],
                        "content": ctx + "\n\n" + original,
                    }
                # If content is a list of blocks (vision / tool-result messages),
                # prepend a text block
                elif isinstance(original, list):
                    self.messages[i] = {
                        **self.messages[i],
                        "content": [{"type": "text", "text": ctx}] + original,
                    }
                break

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    async def process_response(self, response) -> tuple[str, bool]:
        """
        Intercept tool calls to record which tools the model used this iter,
        then delegate to the base implementation.
        """
        # Peek at tool calls requested in this completion
        try:
            message = response.choices[0].message
            if message.tool_calls:
                for tc in message.tool_calls:
                    if tc and hasattr(tc, "function") and tc.function:
                        self._current_iter_tools.append(tc.function.name)
        except Exception:
            pass  # non-fatal; base class will validate properly

        return await super().process_response(response)

    async def get_completion(self) -> tuple[str, bool]:
        """
        Inject history context, call Grok, record the prompt/response pair,
        then return results as normal.
        """
        # 1. Inject history so the model sees it
        self._inject_history_into_messages()

        # 2. Capture the user message we are about to send (for the record)
        prompt_summary = ""
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    prompt_summary = content
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            prompt_summary = block.get("text", "")
                            break
                break

        # 3. Call the base get_completion (handles the actual API request)
        pre_call_llm_count = self.llm_call_count
        response_text, is_done = await super().get_completion()

        # 4. Record this API call in the history
        #    api_call_details is appended by the base get_completion above
        latest_call = (
            self.api_call_details[-1] if self.api_call_details else {}
        )
        self.history.record_prompt(
            call_number=self.llm_call_count,
            iteration=self.iteration,
            prompt_summary=prompt_summary,
            response_summary=str(response_text),
            prompt_tokens=latest_call.get("prompt_tokens", 0),
            completion_tokens=latest_call.get("completion_tokens", 0),
            total_tokens=latest_call.get("total_tokens", 0),
            cost_usd=latest_call.get("cost", 0.0),
            tool_calls_requested=list(self._current_iter_tools),
        )

        return response_text, is_done

    async def optimize(self, input_dcp: Path, output_dcp: Path) -> bool:
        """
        Extended optimization loop that:
          - Syncs clock_period into the history tracker after initial analysis
          - After every iteration, fetches fresh Vivado metrics and records them
          - Saves history.json on completion / error
        """
        # Run the initial analysis first (sets self.clock_period, self.initial_wns, etc.)
        # We do this via the base class but need to hook in afterwards.
        self._history_path = self.run_dir / "history.json"

        # ------------------------------------------------------------------
        # wrap the base optimize() loop so we can inject
        # per-iteration metric snapshots.
        #
        # Strategy is that we reproduce the outer loop logic from DCPOptimizer.optimize()
        # but add metric capture after each iteration.
        # ------------------------------------------------------------------
        from pathlib import Path as _Path

        self.start_time = time.time()

        #  Initial analysis (same as base class) 
        try:
            initial_analysis = await self.perform_initial_analysis(input_dcp)
        except Exception as exc:
            logger.exception(f"Initial analysis failed: {exc}")
            terminal_log("ERROR", f"Initial analysis failed: {exc}")
            self.end_time = time.time()
            return False

        # Sync clock period into the history tracker now that we have it
        self.history.clock_period_ns = self.clock_period

        # Record baseline metrics (iteration 0 = pre-optimisation state)
        self.history.record_metrics(
            iteration=0,
            wns=self.initial_wns,
            tns=self.initial_tns,
            failing_endpoints=self.initial_failing_endpoints,
            fmax_mhz=self.calculate_fmax(self.initial_wns, self.clock_period),
            notes="baseline (pre-optimisation)",
        )
        self._last_known_wns = self.initial_wns

        # if Timing already met?
        if self.initial_wns is not None and self.initial_wns >= 0:
            logger.info("Design already meets timing")
            await self.call_tool("vivado_write_checkpoint", {
                "dcp_path": str(output_dcp.resolve()),
                "force": True,
            })
            self.end_time = time.time()
            total_runtime = self.end_time - self.start_time
            initial_fmax = self.calculate_fmax(self.initial_wns, self.clock_period)
            terminal_log("RESULT", "Design meets timing.", wns=self.initial_wns, fmax=initial_fmax)
            terminal_log("RESULT", f"Saved design: {output_dcp}")
            terminal_log("RESULT", f"Total runtime: {total_runtime:.2f}s; LLM calls: 0; Estimated cost: $0.00")
            self.history.save(self._history_path)
            return True

        #Build initial prompt (same as base class)
        from optimizer import load_system_prompt
        system_prompt_template = load_system_prompt()
        system_prompt = (
            system_prompt_template
            .replace("{temp_dir}", str(self.temp_dir))
            .replace("{input_dcp}", str(input_dcp.resolve()))
        )

        self.messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Optimize this FPGA design for timing.\n\n"
                    f"PATHS:\n"
                    f"- Input DCP: {input_dcp.resolve()}\n"
                    f"- Output DCP (save final result here): {output_dcp.resolve()}\n"
                    f"- Run directory (for intermediate files): {self.temp_dir}\n\n"
                    f"CURRENT STATE:\n"
                    f"- Vivado has the input design ALREADY OPEN and analyzed\n"
                    f"- RapidWright has the input design ALREADY LOADED (from initial analysis)\n\n"
                    f"INITIAL ANALYSIS RESULTS:\n"
                    f"{initial_analysis}\n\n"
                    f"Proceed with optimization strategy based on the analysis above. "
                    f"Do NOT reload the design in either Vivado or RapidWright - both already have it loaded."
                ),
            },
        ]

        max_iterations = 50
        terminal_log("INFO", "Starting LLM-driven optimization with history tracking")

        #  Main loop
        while self.iteration < max_iterations:
            self.iteration += 1
            logger.info(f"=== Iteration {self.iteration} ===")
            self._current_iter_tools = []  # reset tool tracker

            try:
                response_text, is_done = await self.get_completion()

                # Capture Vivado metrics after the iteration 
                wns, tns, failing_eps = await self._fetch_metrics_from_vivado()
                self._snapshot_metrics(wns, tns, failing_eps)

                # Persist history after every iteration
                try:
                    self.history.save(self._history_path)
                except Exception as save_exc:
                    logger.warning(f"[History] Could not save history: {save_exc}")

                # Log iteration result 
                best_fmax = (
                    self.calculate_fmax(self.best_wns, self.clock_period)
                    if self.best_wns not in (None, float("-inf"))
                    else None
                )
                terminal_log(
                    "ITER",
                    "Iteration completed",
                    iteration=self.iteration,
                    wns=self.best_wns,
                    fmax=best_fmax,
                )

                if self.debug:
                    logger.debug(f"LLM response (truncated): {str(response_text)[:500]}")

                if is_done:
                    logger.info("Optimization workflow completed")
                    self.end_time = time.time()
                    self._print_optimization_summary()
                    self.history.save(self._history_path)
                    terminal_log("INFO", f"History saved: {self._history_path}")
                    return True

            except Exception as exc:
                logger.exception(f"Error during optimization iteration {self.iteration}: {exc}")
                # Add error context so the model can recover
                self.messages.append({
                    "role": "user",
                    "content": (
                        f"An error occurred during iteration {self.iteration}: {exc}. "
                        "Please verify your approach and continue, or report if unrecoverable."
                    ),
                })

        #  Max iterations hit 
        logger.warning("Reached maximum iterations")
        self.end_time = time.time()
        self._print_optimization_summary(max_iterations_reached=True)
        self.history.save(self._history_path)
        terminal_log("INFO", f"History saved: {self._history_path}")
        return False
