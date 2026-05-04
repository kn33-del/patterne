from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from dashboard_backend.preflight import PreflightError, apply_java_environment, run_dashboard_preflight
from dashboard_backend.settings import Settings


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        java_home = root / "java"
        (java_home / "bin").mkdir(parents=True)
        (java_home / "lib" / "server").mkdir(parents=True)
        (java_home / "lib" / "server" / "libjvm.so").write_text("stub", encoding="utf-8")
        self.settings = Settings(
            repo_root=root,
            data_root=root / "dashboard_data",
            uploads_dir=root / "dashboard_data" / "uploads",
            runs_dir=root / "dashboard_data" / "runs",
            dcp_registry_path=root / "dashboard_data" / "dcp_registry.json",
            frontend_dist_dir=root / "dashboard_frontend" / "dist",
            pblock_script=root / "Optimizer" / "Pblock_optimizers" / "pblock.py",
            optimizer_script=root / "Optimizer" / "optimizer.py",
            python_executable="/tmp/fake-venv/bin/python",
            java_home=java_home,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_apply_java_environment_sets_expected_paths(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            resolved = apply_java_environment(self.settings)
            self.assertEqual(resolved, self.settings.java_home.resolve())
            self.assertEqual(os.environ["JAVA_HOME"], str(self.settings.java_home.resolve()))
            self.assertEqual(os.environ["PATH"], str(self.settings.java_home.resolve() / "bin"))
            self.assertEqual(
                os.environ["LD_LIBRARY_PATH"],
                str(self.settings.java_home.resolve() / "lib" / "server"),
            )

    def test_run_dashboard_preflight_returns_versions(self) -> None:
        responses = [
            CompletedProcess(args=["java", "-version"], returncode=0, stdout="", stderr="openjdk version \"21.0.1\"\n"),
            CompletedProcess(
                args=[self.settings.python_executable, "-c", "..."],
                returncode=0,
                stdout="2025.2.1\n",
                stderr="",
            ),
        ]

        with patch("dashboard_backend.preflight.subprocess.run", side_effect=responses) as mocked_run:
            with patch.dict("os.environ", {}, clear=True):
                result = run_dashboard_preflight(self.settings)

        self.assertTrue(result["ok"])
        self.assertEqual(result["rapidwright_version"], "2025.2.1")
        self.assertIn("openjdk version", result["java_version_output"])
        self.assertEqual(mocked_run.call_count, 2)

    def test_run_dashboard_preflight_requires_libjvm(self) -> None:
        missing = self.settings.java_home / "lib" / "server" / "libjvm.so"
        missing.unlink()
        with self.assertRaises(PreflightError):
            run_dashboard_preflight(self.settings)


if __name__ == "__main__":
    unittest.main()
