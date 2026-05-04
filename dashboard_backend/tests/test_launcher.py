from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from dashboard_backend.launcher import build_ai_command, build_high_fanout_command, build_pblock_command
from dashboard_backend.models import AIConfig, HighFanoutConfig, PblockConfig
from dashboard_backend.settings import Settings


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            repo_root=root,
            data_root=root / "data",
            uploads_dir=root / "data" / "uploads",
            runs_dir=root / "data" / "runs",
            dcp_registry_path=root / "data" / "registry.json",
            frontend_dist_dir=root / "frontend" / "dist",
            pblock_script=root / "Optimizer" / "Pblock_optimizers" / "pblock.py",
            optimizer_script=root / "Optimizer" / "optimizer.py",
            python_executable=os.environ.get("DASHBOARD_PYTHON_EXECUTABLE", os.sys.executable),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_default_command_uses_sys_executable(self) -> None:
        cmd = build_pblock_command(
            self.settings,
            input_dcp=Path("/tmp/input.dcp"),
            output_root=Path("/tmp/out"),
            config=PblockConfig(),
        )
        self.assertEqual(cmd[0], self.settings.python_executable)
        self.assertNotEqual(cmd[0], "python")
        self.assertNotIn("--use-clock-regions", cmd)
        self.assertNotIn("--target-lut", cmd)

    def test_boolean_flags_and_target_overrides_are_conditional(self) -> None:
        cmd = build_pblock_command(
            self.settings,
            input_dcp=Path("/tmp/input.dcp"),
            output_root=Path("/tmp/out"),
            config=PblockConfig(
                use_clock_regions=True,
                hard_pblock=True,
                keep_placement=True,
                keep_routing=True,
                target_ff=123,
            ),
        )
        self.assertIn("--use-clock-regions", cmd)
        self.assertIn("--hard-pblock", cmd)
        self.assertIn("--keep-placement", cmd)
        self.assertIn("--keep-routing", cmd)
        self.assertIn("--target-ff", cmd)
        self.assertIn("123", cmd)

    def test_analysis_command_sets_max_attempts_zero(self) -> None:
        cmd = build_pblock_command(
            self.settings,
            input_dcp=Path("/tmp/input.dcp"),
            output_root=Path("/tmp/out"),
            max_attempts_override=0,
        )
        self.assertIn("--max-attempts", cmd)
        index = cmd.index("--max-attempts")
        self.assertEqual(cmd[index + 1], "0")

    def test_ai_command_uses_run_dir_and_model(self) -> None:
        cmd = build_ai_command(
            self.settings,
            input_dcp=Path("/tmp/input.dcp"),
            output_dcp=Path("/tmp/out/optimized.dcp"),
            run_dir=Path("/tmp/out/ai_run"),
            config=AIConfig(model="openai/gpt-5", continue_when_timing_met=True),
        )
        self.assertEqual(cmd[0], self.settings.python_executable)
        self.assertIn(str(self.settings.optimizer_script), cmd)
        self.assertIn("--run-dir", cmd)
        self.assertIn("/tmp/out/ai_run", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("openai/gpt-5", cmd)
        self.assertIn("--continue-when-timing-met", cmd)
        self.assertNotIn("python", cmd[0:1])

    def test_ai_command_omits_continue_flag_when_disabled(self) -> None:
        cmd = build_ai_command(
            self.settings,
            input_dcp=Path("/tmp/input.dcp"),
            output_dcp=Path("/tmp/out/optimized.dcp"),
            run_dir=Path("/tmp/out/ai_run"),
            config=AIConfig(model="openai/gpt-5", continue_when_timing_met=False),
        )
        self.assertNotIn("--continue-when-timing-met", cmd)

    def test_high_fanout_test_mode_adds_test_flags(self) -> None:
        cmd = build_high_fanout_command(
            self.settings,
            input_dcp=Path("/tmp/corundum.dcp"),
            output_dcp=Path("/tmp/out/fanout.dcp"),
            run_dir=Path("/tmp/out/high_fanout"),
            config=HighFanoutConfig(max_nets=3, continue_when_timing_met=True),
            use_test_mode=True,
        )
        self.assertIn("--test", cmd)
        self.assertIn("--max-nets", cmd)
        self.assertIn("3", cmd)

    def test_high_fanout_fallback_uses_existing_ai_flow(self) -> None:
        cmd = build_high_fanout_command(
            self.settings,
            input_dcp=Path("/tmp/custom.dcp"),
            output_dcp=Path("/tmp/out/fanout.dcp"),
            run_dir=Path("/tmp/out/high_fanout"),
            config=HighFanoutConfig(model="openai/gpt-5", continue_when_timing_met=False),
            use_test_mode=False,
        )
        self.assertNotIn("--test", cmd)
        self.assertNotIn("--max-nets", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("openai/gpt-5", cmd)


if __name__ == "__main__":
    unittest.main()
