from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
OPTIMIZER_PATH = REPO_ROOT / "Optimizer" / "optimizer.py"


def load_optimizer_module():
    module_name = f"optimizer_under_test_{uuid.uuid4().hex}"

    openai_module = types.ModuleType("openai")

    class DummyOpenAI:  # pragma: no cover - simple import stub
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    openai_module.OpenAI = DummyOpenAI

    mcp_module = types.ModuleType("mcp")
    mcp_module.ClientSession = object
    mcp_module.StdioServerParameters = object
    mcp_client_module = types.ModuleType("mcp.client")
    mcp_stdio_module = types.ModuleType("mcp.client.stdio")

    async def stdio_client(*args, **kwargs):  # pragma: no cover - import stub only
        raise RuntimeError("stdio_client stub should not be used in unit tests")

    mcp_stdio_module.stdio_client = stdio_client

    saved_modules = {
        "openai": sys.modules.get("openai"),
        "mcp": sys.modules.get("mcp"),
        "mcp.client": sys.modules.get("mcp.client"),
        "mcp.client.stdio": sys.modules.get("mcp.client.stdio"),
    }
    sys.modules["openai"] = openai_module
    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.client"] = mcp_client_module
    sys.modules["mcp.client.stdio"] = mcp_stdio_module

    try:
        spec = importlib.util.spec_from_file_location(module_name, OPTIMIZER_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load optimizer module from {OPTIMIZER_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in saved_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls = None

    def model_dump(self, exclude_none: bool = True):
        return {"role": "assistant", "content": self.content}


class FakeResponse:
    def __init__(self, content: str):
        self.choices = [types.SimpleNamespace(message=FakeMessage(content))]


class OptimizerHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.optimizer = load_optimizer_module()

    def test_is_better_wns_positive_vs_positive(self) -> None:
        self.assertTrue(self.optimizer.is_better_wns(0.25, 0.10))
        self.assertFalse(self.optimizer.is_better_wns(0.10, 0.25))

    def test_is_better_wns_negative_vs_negative(self) -> None:
        self.assertTrue(self.optimizer.is_better_wns(-0.05, -0.20))
        self.assertFalse(self.optimizer.is_better_wns(-0.20, -0.05))

    def test_is_better_wns_negative_vs_positive(self) -> None:
        self.assertTrue(self.optimizer.is_better_wns(0.05, -0.10))
        self.assertFalse(self.optimizer.is_better_wns(-0.10, 0.05))

    def test_is_better_wns_handles_none(self) -> None:
        self.assertTrue(self.optimizer.is_better_wns(0.10, None))
        self.assertFalse(self.optimizer.is_better_wns(None, 0.10))


class PromptLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.optimizer = load_optimizer_module()

    def test_load_system_prompt_prefers_optimizer_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            optimizer_dir = temp_dir / "Optimizer"
            optimizer_dir.mkdir()
            optimizer_prompt = optimizer_dir / "SYSTEM_PROMPT.TXT"
            repo_prompt = temp_dir / "SYSTEM_PROMPT.TXT"
            optimizer_prompt.write_text("optimizer prompt", encoding="utf-8")
            repo_prompt.write_text("repo prompt", encoding="utf-8")

            with patch.object(self.optimizer, "__file__", str(optimizer_dir / "optimizer.py")):
                prompt = self.optimizer.load_system_prompt()

        self.assertEqual(prompt, "optimizer prompt")

    def test_load_system_prompt_falls_back_to_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            optimizer_dir = temp_dir / "Optimizer"
            optimizer_dir.mkdir()
            repo_prompt = temp_dir / "SYSTEM_PROMPT.TXT"
            repo_prompt.write_text("repo prompt", encoding="utf-8")

            with patch.object(self.optimizer, "__file__", str(optimizer_dir / "optimizer.py")):
                prompt = self.optimizer.load_system_prompt()

        self.assertEqual(prompt, "repo prompt")

    def test_load_system_prompt_raises_clear_error_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            optimizer_dir = temp_dir / "Optimizer"
            optimizer_dir.mkdir()

            with patch.object(self.optimizer, "__file__", str(optimizer_dir / "optimizer.py")):
                with self.assertRaises(FileNotFoundError) as exc:
                    self.optimizer.load_system_prompt()

        self.assertIn("System prompt file not found", str(exc.exception))


class OptimizerBehaviorTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.optimizer_module = load_optimizer_module()

    def make_optimizer(self, *, continue_when_timing_met: bool):
        module = self.optimizer_module

        class TestOptimizer(module.DCPOptimizer):
            def __init__(self):
                self.temp_dir_ctx = tempfile.TemporaryDirectory()
                super().__init__(
                    api_key="test-key",
                    model="test-model",
                    run_dir=Path(self.temp_dir_ctx.name),
                    continue_when_timing_met=continue_when_timing_met,
                )
                self.call_tool_calls = []
                self.get_completion_called = 0

            async def perform_initial_analysis(self, input_dcp: Path) -> str:
                self.initial_wns = 0.11
                self.initial_tns = 0.0
                self.initial_failing_endpoints = 0
                self.best_wns = self.initial_wns
                self.clock_period = 6.4
                return "analysis summary"

            async def call_tool(self, tool_name: str, arguments: dict) -> str:
                self.call_tool_calls.append((tool_name, arguments))
                return "ok"

            async def get_completion(self):
                self.get_completion_called += 1
                return ("optimization complete", True)

            def cleanup_temp(self):
                self.temp_dir_ctx.cleanup()

        return TestOptimizer()

    async def test_positive_initial_wns_flag_off_uses_fast_path(self) -> None:
        optimizer = self.make_optimizer(continue_when_timing_met=False)
        try:
            result = await optimizer.optimize(Path("/tmp/input.dcp"), Path("/tmp/output.dcp"))
        finally:
            optimizer.cleanup_temp()

        self.assertTrue(result)
        self.assertEqual(optimizer.get_completion_called, 0)
        self.assertEqual(len(optimizer.call_tool_calls), 1)
        self.assertEqual(optimizer.call_tool_calls[0][0], "vivado_write_checkpoint")

    async def test_positive_initial_wns_flag_on_skips_fast_path_and_starts_loop(self) -> None:
        optimizer = self.make_optimizer(continue_when_timing_met=True)
        module = self.optimizer_module
        try:
            with patch.object(module, "load_system_prompt", return_value="Prompt {temp_dir} {input_dcp}"):
                result = await optimizer.optimize(Path("/tmp/input.dcp"), Path("/tmp/output.dcp"))
        finally:
            optimizer.cleanup_temp()

        self.assertTrue(result)
        self.assertEqual(optimizer.get_completion_called, 1)
        self.assertEqual(optimizer.call_tool_calls, [])

    async def test_completion_phrase_does_not_stop_when_flag_enabled(self) -> None:
        module = self.optimizer_module
        optimizer = module.DCPOptimizer(
            api_key="test-key",
            model="test-model",
            run_dir=Path(tempfile.mkdtemp(prefix="optimizer-test-")),
            continue_when_timing_met=True,
        )
        try:
            content, is_done = await optimizer.process_response(FakeResponse("Timing is met and WNS >= 0."))
        finally:
            optimizer.run_dir.rmdir()

        self.assertEqual(content, "Timing is met and WNS >= 0.")
        self.assertFalse(is_done)

    async def test_completion_phrase_still_stops_when_flag_disabled(self) -> None:
        module = self.optimizer_module
        optimizer = module.DCPOptimizer(
            api_key="test-key",
            model="test-model",
            run_dir=Path(tempfile.mkdtemp(prefix="optimizer-test-")),
            continue_when_timing_met=False,
        )
        try:
            content, is_done = await optimizer.process_response(FakeResponse("Timing is met and WNS >= 0."))
        finally:
            optimizer.run_dir.rmdir()

        self.assertEqual(content, "Timing is met and WNS >= 0.")
        self.assertTrue(is_done)


if __name__ == "__main__":
    unittest.main()
