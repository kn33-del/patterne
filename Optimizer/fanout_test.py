#!/usr/bin/env python3

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from optimizer import DCPOptimizer, load_system_prompt, parse_timing_summary_static, terminal_log


class MyOptimizer(DCPOptimizer):
    def __init__(self, debug: bool = False, run_dir: Optional[Path] = None):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")

        model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")

        super().__init__(
            api_key=api_key,
            model=model,
            debug=debug,
            run_dir=run_dir,
        )

        self.iteration_limit = 6
        self.no_improve_limit = 3
        self.no_improve_count = 0

        self.input_dcp: Optional[Path] = None
        self.output_dcp: Optional[Path] = None
        self.current_checkpoint: Optional[Path] = None
        self.best_checkpoint: Optional[Path] = None

        self.top_high_fanout_nets: List[Tuple[str, int]] = []
        self.modified_nets: Set[str] = set()
        self.last_actions: List[str] = []

    async def call_vivado(self, tool: str, args: Dict[str, Any], timeout: float = 300.0) -> str:
        return await self.call_tool(f"vivado_{tool}", args)

    async def call_rw(self, tool: str, args: Dict[str, Any], timeout: float = 300.0) -> str:
        return await self.call_tool(f"rapidwright_{tool}", args)

    def parse_high_fanout_report(self, report: str, threshold: int = 40) -> List[Tuple[str, int]]:
        nets: List[Tuple[str, int]] = []

        for raw_line in report.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                name = parts[1].strip() if len(parts) > 1 else ""
                fanout_str = parts[2].strip() if len(parts) > 2 else ""
                try:
                    fanout = int(fanout_str)
                    if name and fanout >= threshold:
                        nets.append((name, fanout))
                    continue
                except ValueError:
                    pass

            tokens = line.split()
            if len(tokens) >= 2:
                maybe_name = tokens[0].strip()
                maybe_fanout = tokens[1].strip()
                try:
                    fanout = int(maybe_fanout)
                    if maybe_name and fanout >= threshold:
                        nets.append((maybe_name, fanout))
                except ValueError:
                    pass

        deduped: List[Tuple[str, int]] = []
        seen: Set[str] = set()
        for name, fanout in sorted(nets, key=lambda x: x[1], reverse=True):
            if name in seen:
                continue
            seen.add(name)
            deduped.append((name, fanout))
        return deduped

    def trim_tool_result(self, tool_name: str, result: str) -> str:
        if not result:
            return f"[{tool_name} result]\n(no output)"
        if len(result) > 3000:
            return f"[{tool_name} result]\n{result[:3000]}\n... [truncated]"
        return f"[{tool_name} result]\n{result}"

    def compact_messages(self) -> None:
        if len(self.messages) <= 14:
            return
        system_msg = self.messages[0]
        recent = self.messages[-12:]
        self.messages = [system_msg] + recent

    def should_skip_tool_call(self, tool_name: str, args: Dict[str, Any]) -> Optional[str]:
        if tool_name == "rapidwright_optimize_fanout":
            net_name = str(args.get("net_name", "")).strip()
            if not net_name:
                return "Skipping rapidwright_optimize_fanout because net_name is empty."
            if net_name in self.modified_nets:
                return f"Skipping rapidwright_optimize_fanout because net {net_name} was already modified."
        return None

    async def gather_initial_context(self) -> str:
        timing_report = await self.call_vivado("report_timing_summary", {})
        timing_info = parse_timing_summary_static(timing_report)

        self.initial_wns = timing_info.get("wns")
        self.initial_tns = timing_info.get("tns")
        self.initial_failing_endpoints = timing_info.get("failing_endpoints")
        self.best_wns = self.initial_wns

        fanout_report = await self.call_vivado(
            "run_tcl",
            {
                "command": "report_high_fanout_nets -fanout_greater_than 40 -max_nets 20 -return_string"
            },
        )
        self.top_high_fanout_nets = self.parse_high_fanout_report(fanout_report, threshold=40)

        fanout_lines = []
        for name, fanout in self.top_high_fanout_nets[:8]:
            fanout_lines.append(f"- {name}: fanout={fanout}")

        if not fanout_lines:
            fanout_lines.append("- none reported above threshold")

        return (
            f"Current checkpoint: {self.current_checkpoint}\n"
            f"Initial WNS: {self.initial_wns}\n"
            f"Initial TNS: {self.initial_tns}\n"
            f"Initial failing endpoints: {self.initial_failing_endpoints}\n"
            f"Run directory: {self.run_dir}\n\n"
            f"High-fanout nets for context only:\n"
            + "\n".join(fanout_lines)
            + "\n\nDo not over-focus on fanout. Consider broader timing improvements first."
        )

    def build_iteration_prompt(self, iteration: int) -> str:
        high_fanout = [
            {"net_name": name, "fanout": fanout}
            for name, fanout in self.top_high_fanout_nets[:6]
        ]

        state = {
            "iteration": iteration,
            "current_checkpoint": str(self.current_checkpoint) if self.current_checkpoint else None,
            "best_checkpoint": str(self.best_checkpoint) if self.best_checkpoint else None,
            "best_wns": self.best_wns,
            "initial_wns": self.initial_wns,
            "modified_nets": sorted(self.modified_nets),
            "recent_actions": self.last_actions[-6:],
            "high_fanout_context": high_fanout,
        }

        return (
            "Choose one or two measured timing-improvement actions.\n"
            "Do not get stuck repeatedly splitting fanout nets.\n"
            "Prefer timing inspection, place_design, phys_opt_design, route_design, Tcl timing queries, "
            "and checkpoint flow hygiene before using rapidwright_optimize_fanout.\n"
            "Use rapidwright_optimize_fanout only when strongly justified and avoid nets already modified.\n"
            "If you modify the design in RapidWright, write a checkpoint and reopen it in Vivado before timing.\n"
            "State:\n"
            f"{json.dumps(state, indent=2)}"
        )

    def build_assistant_message(self, msg: Any) -> Dict[str, Any]:
        message: Dict[str, Any] = {
            "role": "assistant",
            "content": msg.content or "",
        }

        if getattr(msg, "tool_calls", None):
            tool_calls = []
            for call in msg.tool_calls:
                tool_calls.append(
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                )
            message["tool_calls"] = tool_calls

        return message

    async def evaluate_current_timing(self, iteration: int) -> Optional[float]:
        timing_report = await self.call_vivado("report_timing_summary", {})
        timing_info = parse_timing_summary_static(timing_report)
        current_wns = timing_info.get("wns")

        terminal_log("PERF", f"WNS after iteration {iteration}", wns=current_wns)

        if current_wns is None:
            self.no_improve_count += 1
            return None

        if self.best_wns is None or current_wns > self.best_wns:
            old_best = self.best_wns
            self.best_wns = current_wns
            self.no_improve_count = 0

            best_dcp = self.run_dir / "best.dcp"
            await self.call_vivado(
                "write_checkpoint",
                {
                    "dcp_path": str(best_dcp),
                    "force": True,
                },
            )
            self.best_checkpoint = best_dcp

            terminal_log("INFO", f"Improvement found: old_best={old_best}, new_best={current_wns}")
        else:
            self.no_improve_count += 1
            terminal_log("INFO", f"No improvement ({self.no_improve_count})")

        return current_wns

    async def llm_optimize_loop(self) -> None:
        self.no_improve_count = 0

        for iteration in range(self.iteration_limit):
            terminal_log("INFO", f"LLM iteration {iteration}")

            self.messages.append(
                {
                    "role": "user",
                    "content": self.build_iteration_prompt(iteration),
                }
            )

            response = self.openai.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=self.tools,
                tool_choice="auto",
            )

            msg = response.choices[0].message
            self.messages.append(self.build_assistant_message(msg))

            rw_modified = False
            timing_or_route_called = False
            changes_made = False
            last_rw_checkpoint: Optional[Path] = None

            if not getattr(msg, "tool_calls", None):
                self.messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You returned no tool calls. On the next turn, make concrete tool calls. "
                            "Prefer timing analysis, placement, phys_opt, routing, or checkpoint-safe RapidWright edits."
                        ),
                    }
                )
                self.no_improve_count += 1
                self.compact_messages()
                if self.no_improve_count >= self.no_improve_limit:
                    terminal_log("INFO", "Stopping: no improvement and no actionable tool calls")
                    break
                continue

            for call in msg.tool_calls:
                tool_name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                skip_reason = self.should_skip_tool_call(tool_name, args)
                if skip_reason is not None:
                    terminal_log("WARN", skip_reason)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": tool_name,
                            "content": skip_reason,
                        }
                    )
                    continue

                terminal_log("INFO", f"Calling {tool_name} with {args}")
                result = await self.call_tool(tool_name, args)
                trimmed = self.trim_tool_result(tool_name, result)

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": tool_name,
                        "content": trimmed,
                    }
                )

                self.last_actions.append(f"{tool_name} {args}")

                if tool_name == "rapidwright_optimize_fanout":
                    net_name = str(args.get("net_name", "")).strip()
                    if net_name:
                        self.modified_nets.add(net_name)
                    rw_modified = True
                    changes_made = True

                elif tool_name.startswith("rapidwright_"):
                    if tool_name == "rapidwright_write_checkpoint":
                        dcp_path = args.get("dcp_path")
                        if dcp_path:
                            last_rw_checkpoint = Path(dcp_path).resolve()
                    else:
                        rw_modified = True
                        changes_made = True

                elif tool_name.startswith("vivado_"):
                    changes_made = True
                    if tool_name in (
                        "vivado_route_design",
                        "vivado_report_timing_summary",
                        "vivado_place_design",
                        "vivado_phys_opt_design",
                    ):
                        timing_or_route_called = True

                    if tool_name == "vivado_open_checkpoint":
                        dcp_path = args.get("dcp_path")
                        if dcp_path:
                            self.current_checkpoint = Path(dcp_path).resolve()

            if rw_modified:
                if last_rw_checkpoint is None:
                    last_rw_checkpoint = self.run_dir / f"iter_{iteration}.dcp"
                    await self.call_rw(
                        "write_checkpoint",
                        {
                            "dcp_path": str(last_rw_checkpoint),
                            "overwrite": True,
                        },
                    )

                await self.call_vivado(
                    "open_checkpoint",
                    {
                        "dcp_path": str(last_rw_checkpoint),
                    },
                )
                self.current_checkpoint = last_rw_checkpoint

            if changes_made and not timing_or_route_called:
                await self.call_vivado("route_design", {})

            await self.evaluate_current_timing(iteration)
            self.compact_messages()

            if self.no_improve_count >= self.no_improve_limit:
                terminal_log("INFO", "Stopping: no improvement")
                break

    async def run(self, input_dcp: Path, output_dcp: Optional[Path] = None) -> bool:
        overall_start = time.time()
        terminal_log("INIT", "Starting optimizer")

        self.input_dcp = input_dcp.resolve()
        if output_dcp is None:
            output_dcp = self.run_dir / "final.dcp"
        self.output_dcp = output_dcp.resolve()

        if self.output_dcp.exists() and self.output_dcp.is_dir():
            raise RuntimeError(f"{self.output_dcp} is a directory, expected a file path")

        try:
            await self.start_servers()

            await self.call_rw(
                "initialize_rapidwright",
                {"jvm_max_memory": "8G"},
            )

            await self.call_vivado(
                "open_checkpoint",
                {"dcp_path": str(self.input_dcp)},
            )
            self.current_checkpoint = self.input_dcp

            await self.call_rw(
                "read_checkpoint",
                {"dcp_path": str(self.input_dcp)},
            )

            initial_context = await self.gather_initial_context()

            system_prompt = load_system_prompt() + "\n\n" + (
                "You are optimizing FPGA timing. Do not obsess over fanout splitting. "
                "Use a balanced strategy: inspect timing, try placement and phys_opt actions, "
                "route as needed, and only use RapidWright fanout optimization selectively. "
                "Avoid repeating the same failing action."
            )

            self.messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": initial_context},
            ]

            await self.llm_optimize_loop()

            final_source = self.best_checkpoint if self.best_checkpoint is not None else self.current_checkpoint
            if final_source is not None and final_source != self.output_dcp:
                await self.call_vivado(
                    "write_checkpoint",
                    {
                        "dcp_path": str(self.output_dcp),
                        "force": True,
                    },
                )

            elapsed = time.time() - overall_start
            terminal_log(
                "RESULT",
                f"Done in {elapsed:.2f}s, initial WNS={self.initial_wns}, best WNS={self.best_wns}",
            )
            terminal_log("RESULT", f"Final checkpoint: {self.output_dcp}")
            return True

        except Exception as e:
            terminal_log("ERROR", f"Run failed: {type(e).__name__}: {e}")
            return False


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 fanout_test.py <input.dcp> [output.dcp]")
        return

    input_dcp = Path(sys.argv[1])
    output_dcp = Path(sys.argv[2]) if len(sys.argv) >= 3 else None

    opt = MyOptimizer(debug=True)

    try:
        ok = await opt.run(input_dcp, output_dcp)
        if not ok:
            sys.exit(1)
    finally:
        await opt.cleanup()


if __name__ == "__main__":
    asyncio.run(main())