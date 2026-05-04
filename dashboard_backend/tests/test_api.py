from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard_backend.main import create_app
from dashboard_backend.settings import Settings
from dashboard_backend.storage import DashboardStore


REPO_ROOT = Path(__file__).resolve().parents[2]


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            repo_root=REPO_ROOT,
            data_root=root / "dashboard_data",
            uploads_dir=root / "dashboard_data" / "uploads",
            runs_dir=root / "dashboard_data" / "runs",
            dcp_registry_path=root / "dashboard_data" / "dcp_registry.json",
            frontend_dist_dir=REPO_ROOT / "dashboard_frontend" / "dist",
            pblock_script=REPO_ROOT / "Optimizer" / "Pblock_optimizers" / "pblock.py",
            optimizer_script=REPO_ROOT / "Optimizer" / "optimizer.py",
            python_executable="python3",
            skip_startup_preflight=True,
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.store: DashboardStore = self.app.state.store
        self.manager = self.app.state.manager

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_upload_validation_rejects_non_dcp(self) -> None:
        response = self.client.post(
            "/api/dcps/upload",
            files={"file": ("bad.txt", b"not a dcp", "text/plain")},
        )
        self.assertEqual(response.status_code, 400)

    def test_local_path_validation_requires_absolute_existing_dcp(self) -> None:
        response = self.client.post("/api/dcps/register-local", json={"path": "relative.dcp"})
        self.assertEqual(response.status_code, 400)

        response = self.client.post("/api/dcps/register-local", json={"path": "/tmp/missing.txt"})
        self.assertEqual(response.status_code, 400)

        response = self.client.post("/api/dcps/register-local", json={"path": "/tmp/missing.dcp"})
        self.assertEqual(response.status_code, 404)

    def test_settings_exposes_product_name_and_recipe_lists(self) -> None:
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}, clear=True):
            response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["product_name"], "DCP Forge")
        self.assertIn("quick_timing_rescue", payload["supported_recipes"])
        self.assertIn("ai_recommended_plan", payload["enabled_recipes"])
        self.assertFalse(payload["startup_preflight_enabled"])
        self.assertTrue(payload["preflight"]["skipped"])

    def test_featured_demo_endpoint_returns_read_only_scenario_without_creating_runs(self) -> None:
        before = sorted(self.settings.runs_dir.glob("*/metadata.json"))

        response = self.client.get("/api/demo/featured")

        after = sorted(self.settings.runs_dir.glob("*/metadata.json"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(before, after)
        self.assertEqual(payload["featured_run_id"], "dcp_optimizer_run-20260502_110423")
        self.assertEqual(payload["input_dcp_label"], "route_cluster_bench.dcp")
        self.assertEqual(len(payload["steps"]), 11)
        self.assertTrue(payload["impact"]["timing_closed"])

    def test_list_runs_merges_imported_ai_and_pblock_runs(self) -> None:
        response = self.client.get("/api/runs")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        sources = {entry["source"] for entry in payload}
        self.assertIn("imported_ai", sources)
        self.assertIn("imported_pblock", sources)

    def test_imported_run_summary_is_read_only_and_has_impact(self) -> None:
        runs = self.client.get("/api/runs").json()
        imported = next(entry for entry in runs if entry["source"] == "imported_ai")

        response = self.client.get(f"/api/runs/{imported['run_id']}/summary")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["source"], "imported_ai")
        self.assertGreater(payload["impact"]["timing_margin_gain_ns"], 0)
        self.assertIsInstance(payload["impact"]["timing_closed"], bool)

    def test_quick_rescue_dispatches_to_pblock_launcher(self) -> None:
        run = self._seed_completed_analysis_run()

        with patch.object(self.manager, "launch_quick_timing_rescue") as launch:
            response = self.client.post(
                f"/api/runs/{run['run_id']}/optimize",
                json={"recipe": "quick_timing_rescue", "config": {"max_attempts": 6}},
            )

        self.assertEqual(response.status_code, 200)
        launch.assert_called_once()

    def test_high_fanout_corundum_flow_does_not_require_api_key(self) -> None:
        temp_dcp_dir = Path(self.temp_dir.name) / "dcps"
        temp_dcp_dir.mkdir(parents=True, exist_ok=True)
        corundum_path = temp_dcp_dir / "demo_corundum_25g_misses_timing.dcp"
        corundum_path.write_text("stub", encoding="utf-8")

        record = self.store.register_dcp(kind="local", filename=corundum_path.name, path=corundum_path)
        run = self.store.create_run_metadata(record)
        analysis_dir = REPO_ROOT / "outputrun_20260407_134441"
        self.store.update_run_metadata(run["run_id"], lambda current: _seed_completed_analysis(current, analysis_dir))

        with patch.dict("os.environ", {}, clear=True):
            with patch.object(self.manager, "launch_high_fanout_optimization") as launch:
                response = self.client.post(
                    f"/api/runs/{run['run_id']}/optimize",
                    json={"recipe": "high_fanout_optimization", "config": {"max_nets": 3}},
                )

        self.assertEqual(response.status_code, 200)
        launch.assert_called_once()

    def test_ai_optimize_requires_openrouter_api_key_for_standard_autopilot(self) -> None:
        run = self._seed_completed_analysis_run()

        with patch.dict("os.environ", {}, clear=True):
            response = self.client.post(f"/api/runs/{run['run_id']}/ai/optimize", json={"config": {}})

        self.assertEqual(response.status_code, 409)
        self.assertIn("OPENROUTER_API_KEY", response.json()["detail"])

    def test_ai_plan_returns_structured_recommendation(self) -> None:
        run = self._seed_completed_analysis_run()
        response = self.client.post(f"/api/runs/{run['run_id']}/ai/plan")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(payload["recommended_recipe"], {"quick_timing_rescue", "pblock_explorer", "ai_autopilot"})
        self.assertTrue(payload["stop_conditions"])

    def test_read_only_runs_reject_cancel_and_optimize(self) -> None:
        runs = self.client.get("/api/runs").json()
        imported = next(entry for entry in runs if entry["source"] == "imported_ai")

        cancel_response = self.client.post(f"/api/runs/{imported['run_id']}/cancel")
        optimize_response = self.client.post(
            f"/api/runs/{imported['run_id']}/optimize",
            json={"recipe": "pblock_explorer", "config": {}},
        )

        self.assertEqual(cancel_response.status_code, 409)
        self.assertEqual(optimize_response.status_code, 409)

    def test_status_mapping_and_safe_artifact_rejection(self) -> None:
        run = self._seed_completed_analysis_run()

        summary = self.client.get(f"/api/runs/{run['run_id']}/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["status"], "completed")

        traversal = self.client.get(f"/api/runs/{run['run_id']}/artifacts/%2E%2E%2FREADME.md")
        self.assertEqual(traversal.status_code, 400)

    def test_artifact_zip_requires_known_artifact_directory(self) -> None:
        dcp_path = REPO_ROOT / "Optimizer" / "Original DCPs" / "reduction_or_routed.dcp"
        record = self.store.register_dcp(kind="local", filename=dcp_path.name, path=dcp_path)
        run = self.store.create_run_metadata(record)

        response = self.client.get(f"/api/runs/{run['run_id']}/artifacts.zip")
        self.assertEqual(response.status_code, 404)

    def test_ai_status_endpoint_reads_parsed_log_summary(self) -> None:
        dcp_path = REPO_ROOT / "Optimizer" / "Original DCPs" / "reduction_or_routed.dcp"
        record = self.store.register_dcp(kind="local", filename=dcp_path.name, path=dcp_path)
        run = self.store.create_run_metadata(record)
        analysis_dir = REPO_ROOT / "outputrun_20260407_134441"
        ai_dir = self.settings.runs_dir / run["run_id"] / "optimization" / "ai_run_20260501_120000"
        ai_dir.mkdir(parents=True)
        optimized = ai_dir / "reduction_or_routed_ai_optimized.dcp"
        optimized.write_text("stub", encoding="utf-8")
        stderr_path = self.settings.runs_dir / run["run_id"] / "optimization.stderr.log"
        stderr_path.write_text(
            "\n".join(
                [
                    "2026-05-01 12:00:01,000 - INFO - [INIT] - Timing analyzed - WNS=-0.492ns - fmax=547.95MHz",
                    "2026-05-01 12:01:01,000 - INFO - [INFO] - Starting LLM-driven optimization",
                    "2026-05-01 12:02:01,000 - INFO - [API] - Call #1 Tokens: 1,234 (P:1,000, C:234) | Cost: $0.0123",
                    "2026-05-01 12:03:01,000 - INFO - [ITER] - Iteration completed - iter=1 - WNS=-0.123ns - fmax=686.81MHz",
                ]
            ),
            encoding="utf-8",
        )
        self.store.update_run_metadata(
            run["run_id"],
            lambda current: _seed_completed_ai(current, analysis_dir, ai_dir, optimized),
        )

        response = self.client.get(f"/api/runs/{run['run_id']}/ai/status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["phase"], "iterating")
        self.assertEqual(payload["iteration"], 1)
        self.assertEqual(payload["llm_call_count"], 1)
        self.assertEqual(payload["best_timing"]["wns"], -0.123)

    def _seed_completed_analysis_run(self) -> dict:
        dcp_path = REPO_ROOT / "Optimizer" / "Original DCPs" / "reduction_or_routed.dcp"
        record = self.store.register_dcp(kind="local", filename=dcp_path.name, path=dcp_path)
        run = self.store.create_run_metadata(record)
        analysis_dir = REPO_ROOT / "outputrun_20260407_134441"
        self.store.update_run_metadata(run["run_id"], lambda current: _seed_completed_analysis(current, analysis_dir))
        return run


def _seed_completed_analysis(current: dict, analysis_dir: Path) -> dict:
    current["status"] = "completed"
    current["current_stage"] = None
    current["analysis"]["status"] = "completed"
    current["analysis"]["artifact_dir"] = str(analysis_dir)
    return current


def _seed_completed_ai(current: dict, analysis_dir: Path, ai_dir: Path, output_dcp: Path) -> dict:
    current["recipe"] = "ai_autopilot"
    current["status"] = "completed"
    current["current_stage"] = None
    current["analysis"]["status"] = "completed"
    current["analysis"]["artifact_dir"] = str(analysis_dir)
    current["optimization"]["status"] = "completed"
    current["optimization"]["artifact_dir"] = str(ai_dir)
    current["optimization"]["output_dcp"] = str(output_dcp)
    current["optimization"]["runner"] = "ai"
    current["optimization"]["config"] = {
        "model": "x-ai/grok-4.1-fast",
        "debug": False,
        "continue_when_timing_met": False,
    }
    return current


if __name__ == "__main__":
    unittest.main()
