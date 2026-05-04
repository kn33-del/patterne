from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from dashboard_backend.artifacts import (
    parse_ai_run_artifacts,
    parse_baseline_summary,
    parse_featured_demo_scenario,
    parse_run_artifacts,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class ArtifactParserTests(unittest.TestCase):
    def test_baseline_summary_extracts_fmax(self) -> None:
        run_dir = REPO_ROOT / "outputrun_20260407_134441"
        baseline = parse_baseline_summary(run_dir)
        self.assertEqual(baseline["timing"]["wns"], -0.492)
        self.assertAlmostEqual(baseline["timing"]["estimated_fmax_mhz"], 1000.0 / (1.333 - (-0.492)), places=6)

    def test_run_summary_strips_embedded_logs_and_finds_best_attempt(self) -> None:
        run_dir = REPO_ROOT / "outputrun_20260407_134441"
        parsed = parse_run_artifacts(run_dir)
        self.assertEqual(parsed["best_attempt_num"], 10)
        self.assertEqual(parsed["best_output_artifact"], "reduction_or_routed_10.dcp")
        attempt = next(entry for entry in parsed["attempts"] if entry.get("attempt_num") == 1)
        self.assertNotIn("create_and_apply_log", attempt)
        self.assertIn("route", attempt["available_logs"])

    def test_skipped_attempts_and_leaderboard_ranking(self) -> None:
        run_dir = REPO_ROOT / "outputrun_20260406_130231"
        parsed = parse_run_artifacts(run_dir)
        self.assertEqual(parsed["skip_count"], 2)
        skipped = [entry for entry in parsed["attempts"] if entry["status"] == "skipped"]
        self.assertEqual(len(skipped), 2)
        best = next(entry for entry in parsed["attempts"] if entry.get("attempt_num") == 10)
        self.assertEqual(best["leaderboard_rank"], 1)

    def test_ai_log_parser_extracts_iteration_tokens_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            run_dir = Path(temp_dir_name)
            stderr_log = run_dir / "optimization.stderr.log"
            stderr_log.write_text(
                "\n".join(
                    [
                        "2026-05-01 12:00:01,000 - INFO - [INIT] - Timing analyzed - WNS=-0.492ns - fmax=547.95MHz",
                        "2026-05-01 12:01:01,000 - INFO - [INFO] - Starting LLM-driven optimization",
                        "2026-05-01 12:02:01,000 - INFO - [API] - Call #1 Tokens: 1,234 (P:1,000, C:234) | Cost: $0.0123",
                        "2026-05-01 12:03:01,000 - INFO - [ITER] - Iteration completed - iter=2 - WNS=-0.123ns - fmax=686.81MHz",
                    ]
                ),
                encoding="utf-8",
            )
            output_dcp = run_dir / "demo_ai_optimized.dcp"
            output_dcp.write_text("stub", encoding="utf-8")

            parsed = parse_ai_run_artifacts(
                run_dir,
                stderr_log_path=stderr_log,
                output_dcp_path=output_dcp,
                model="x-ai/grok-4.1-fast",
            )

        self.assertEqual(parsed["ai"]["phase"], "iterating")
        self.assertEqual(parsed["ai"]["iteration"], 2)
        self.assertEqual(parsed["ai"]["llm_call_count"], 1)
        self.assertEqual(parsed["ai"]["total_tokens"], 1234)
        self.assertEqual(parsed["ai"]["best_timing"]["estimated_fmax_mhz"], 686.81)
        self.assertEqual(parsed["baseline"]["timing"]["wns"], -0.492)
        self.assertEqual(parsed["best_output_artifact"], "demo_ai_optimized.dcp")

    def test_imported_ai_run_uses_token_report_and_infers_input_checkpoint(self) -> None:
        run_dir = REPO_ROOT / "dcp_optimizer_run-20260502_110423"
        parsed = parse_ai_run_artifacts(
            run_dir,
            stderr_log_path=None,
            output_dcp_path=None,
            model=None,
        )

        self.assertEqual(parsed["baseline"]["input_dcp"], "/home/vik/route_cluster_bench.dcp")
        self.assertEqual(parsed["baseline"]["timing"]["wns"], -0.282)
        self.assertEqual(parsed["best_timing"]["wns"], 0.109)
        self.assertEqual(parsed["best_output_artifact"], "after_fanout_route.dcp")

    def test_featured_demo_prefers_historical_route_cluster_bench_run(self) -> None:
        parsed = parse_featured_demo_scenario(REPO_ROOT)

        self.assertEqual(parsed["featured_run_id"], "dcp_optimizer_run-20260502_110423")
        self.assertEqual(parsed["input_dcp_label"], "route_cluster_bench.dcp")
        self.assertEqual(parsed["design_part"], "xc7z020clg484-1")
        self.assertEqual(len(parsed["steps"]), 11)
        self.assertTrue(parsed["impact"]["timing_closed"])
        self.assertEqual(parsed["steps"][6]["metrics"]["wns"], -0.037)
        self.assertEqual(parsed["steps"][-1]["metrics"]["wns"], 0.109)

    def test_featured_demo_falls_back_to_latest_successful_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            repo_root = Path(temp_dir_name)
            replay_dir = repo_root / "replay_run_route_cluster_bench_20260502_172904"
            replay_dir.mkdir(parents=True)
            (replay_dir / "01_route_physopt.vivado.log").write_text(
                "INFO: [Physopt 32-669] Post Physical Optimization Timing Summary | WNS=-0.066 | TNS=-0.401 | WHS=0.013 | THS=0.000 |",
                encoding="utf-8",
            )
            (replay_dir / "replay_summary.json").write_text(
                """
                {
                  "input_dcp": "/home/vik/route_cluster_bench.dcp",
                  "baseline": {
                    "wns_ns": -0.282,
                    "tns_ns": -6.732,
                    "failing_endpoints": 105,
                    "clock_period_ns": 5.0
                  },
                  "after_fanout_route": {
                    "wns_ns": -0.320,
                    "tns_ns": -1.629,
                    "failing_endpoints": 31,
                    "clock_period_ns": 5.0
                  },
                  "final": {
                    "wns_ns": 0.013,
                    "tns_ns": 0.0,
                    "failing_endpoints": 0,
                    "clock_period_ns": 5.0
                  },
                  "improvement": {
                    "timing_closed": true
                  },
                  "rapidwright": {
                    "read_checkpoint": {
                      "design_name": "route_cluster_bench_top",
                      "part_name": "xc7z020clg484-1"
                    }
                  }
                }
                """,
                encoding="utf-8",
            )

            parsed = parse_featured_demo_scenario(repo_root)

        self.assertEqual(parsed["featured_run_id"], replay_dir.name)
        self.assertEqual(parsed["design_name"], "route_cluster_bench_top")
        self.assertTrue(parsed["impact"]["timing_closed"])
        self.assertEqual(parsed["steps"][7]["metrics"]["wns"], -0.066)
        self.assertEqual(parsed["final_metrics"]["wns"], 0.013)


if __name__ == "__main__":
    unittest.main()
