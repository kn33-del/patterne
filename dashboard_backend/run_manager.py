from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .artifacts import detect_outputrun_dir, stage_has_attempt_activity
from .launcher import build_ai_command, build_high_fanout_command, build_pblock_command
from .models import AIConfig, HighFanoutConfig, PblockConfig
from .settings import Settings
from .storage import DashboardStore, iso_now


@dataclass
class ActiveProcess:
    run_id: str
    stage: str
    process: subprocess.Popen[str]
    output_root: Path


class RunManager:
    def __init__(self, settings: Settings, store: DashboardStore) -> None:
        self.settings = settings
        self.store = store
        self._active: Dict[str, ActiveProcess] = {}
        self._lock = threading.RLock()

    def launch_analysis(self, run_id: str) -> Dict[str, Any]:
        metadata = self.store.read_run_metadata(run_id)
        if metadata is None:
            raise FileNotFoundError(run_id)
        output_root = Path(metadata["analysis"]["output_root"])
        output_root.mkdir(parents=True, exist_ok=True)
        command = build_pblock_command(
            self.settings,
            input_dcp=Path(metadata["dcp_path"]),
            output_root=output_root,
            max_attempts_override=0,
        )
        return self._launch_stage(run_id, "analysis", command)

    def launch_optimization(self, run_id: str, config: PblockConfig, recipe: str) -> Dict[str, Any]:
        metadata = self.store.read_run_metadata(run_id)
        if metadata is None:
            raise FileNotFoundError(run_id)
        output_root = Path(metadata["optimization"]["output_root"])
        output_root.mkdir(parents=True, exist_ok=True)
        command = build_pblock_command(
            self.settings,
            input_dcp=Path(metadata["dcp_path"]),
            output_root=output_root,
            config=config,
        )

        def mutator(current: Dict[str, Any]) -> Dict[str, Any]:
            current["recipe"] = recipe
            current["current_stage"] = "optimization"
            current["status"] = "queued"
            current["optimization"]["status"] = "queued"
            current["optimization"]["requested_at"] = iso_now()
            current["optimization"]["config"] = config.model_dump()
            current["optimization"]["runner"] = "pblock"
            return current

        self.store.update_run_metadata(run_id, mutator)
        return self._launch_stage(run_id, "optimization", command)

    def launch_quick_timing_rescue(self, run_id: str, config: PblockConfig, recipe: str) -> Dict[str, Any]:
        rescue_config = config.model_copy(
            update={
                "max_attempts": min(config.max_attempts, 4),
                "seed_count": min(config.seed_count, 4),
                "elite_count": min(config.elite_count, 2),
                "continue_after_improvement": False,
                "keep_placement": False,
                "keep_routing": False,
            }
        )
        return self.launch_optimization(run_id, rescue_config, recipe)

    def launch_ai_optimization(self, run_id: str, config: AIConfig, recipe: str) -> Dict[str, Any]:
        metadata = self.store.read_run_metadata(run_id)
        if metadata is None:
            raise FileNotFoundError(run_id)
        output_root = Path(metadata["optimization"]["output_root"])
        output_root.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        artifact_dir = output_root / f"ai_run_{timestamp}"
        output_dcp = artifact_dir / f"{Path(metadata['dcp_name']).stem}_ai_optimized.dcp"
        command = build_ai_command(
            self.settings,
            input_dcp=Path(metadata["dcp_path"]),
            output_dcp=output_dcp,
            run_dir=artifact_dir,
            config=config,
        )

        def mutator(current: Dict[str, Any]) -> Dict[str, Any]:
            current["recipe"] = recipe
            current["current_stage"] = "optimization"
            current["status"] = "queued"
            current["optimization"]["status"] = "queued"
            current["optimization"]["requested_at"] = iso_now()
            current["optimization"]["config"] = config.model_dump()
            current["optimization"]["artifact_dir"] = str(artifact_dir)
            current["optimization"]["output_dcp"] = str(output_dcp)
            current["optimization"]["runner"] = "ai"
            return current

        self.store.update_run_metadata(run_id, mutator)
        return self._launch_stage(run_id, "optimization", command)

    def launch_high_fanout_optimization(self, run_id: str, config: HighFanoutConfig, recipe: str) -> Dict[str, Any]:
        metadata = self.store.read_run_metadata(run_id)
        if metadata is None:
            raise FileNotFoundError(run_id)
        output_root = Path(metadata["optimization"]["output_root"])
        output_root.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        artifact_dir = output_root / f"ai_run_{timestamp}"
        output_dcp = artifact_dir / f"{Path(metadata['dcp_name']).stem}_fanout_focus.dcp"
        use_test_mode = self._supports_corundum_test(Path(metadata["dcp_path"]))
        command = build_high_fanout_command(
            self.settings,
            input_dcp=Path(metadata["dcp_path"]),
            output_dcp=output_dcp,
            run_dir=artifact_dir,
            config=config,
            use_test_mode=use_test_mode,
        )

        def mutator(current: Dict[str, Any]) -> Dict[str, Any]:
            current["recipe"] = recipe
            current["current_stage"] = "optimization"
            current["status"] = "queued"
            current["optimization"]["status"] = "queued"
            current["optimization"]["requested_at"] = iso_now()
            current["optimization"]["config"] = {
                **config.model_dump(),
                "use_test_mode": use_test_mode,
            }
            current["optimization"]["artifact_dir"] = str(artifact_dir)
            current["optimization"]["output_dcp"] = str(output_dcp)
            current["optimization"]["runner"] = "ai"
            return current

        self.store.update_run_metadata(run_id, mutator)
        return self._launch_stage(run_id, "optimization", command)

    def _launch_stage(self, run_id: str, stage: str, command: list[str]) -> Dict[str, Any]:
        metadata = self.store.read_run_metadata(run_id)
        if metadata is None:
            raise FileNotFoundError(run_id)
        stage_meta = metadata[stage]
        output_root = Path(stage_meta["output_root"])
        output_root.mkdir(parents=True, exist_ok=True)
        stdout_path = Path(stage_meta["stdout_log"])
        stderr_path = Path(stage_meta["stderr_log"])

        stdout_handle = stdout_path.open("w", encoding="utf-8")
        stderr_handle = stderr_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            command,
            cwd=self.settings.repo_root,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            start_new_session=True,
        )

        def mutator(current: Dict[str, Any]) -> Dict[str, Any]:
            current["status"] = "starting"
            current["current_stage"] = stage
            current[stage]["status"] = "starting"
            current[stage]["started_at"] = iso_now()
            current[stage]["command"] = command
            current[stage]["pid"] = process.pid
            current[stage]["process_group"] = process.pid
            current[stage]["exit_code"] = None
            return current

        updated = self.store.update_run_metadata(run_id, mutator)
        active = ActiveProcess(run_id=run_id, stage=stage, process=process, output_root=output_root)
        with self._lock:
            self._active[run_id] = active

        thread = threading.Thread(
            target=self._monitor_process,
            args=(active, stdout_handle, stderr_handle),
            daemon=True,
        )
        thread.start()
        return updated

    def _monitor_process(
        self,
        active: ActiveProcess,
        stdout_handle: Any,
        stderr_handle: Any,
    ) -> None:
        exit_code = active.process.wait()
        stdout_handle.close()
        stderr_handle.close()
        artifact_dir = detect_outputrun_dir(active.output_root)

        def mutator(current: Dict[str, Any]) -> Dict[str, Any]:
            stage_meta = current[active.stage]
            stage_meta["artifact_dir"] = str(artifact_dir) if artifact_dir else stage_meta.get("artifact_dir")
            stage_meta["finished_at"] = iso_now()
            stage_meta["exit_code"] = exit_code
            cancel_requested = current.get("cancel_requested_at") is not None or stage_meta.get("status") == "cancelled"
            if cancel_requested:
                stage_meta["status"] = "cancelled"
                current["status"] = "cancelled"
            elif exit_code == 0:
                stage_meta["status"] = "completed"
                current["status"] = "completed"
            else:
                stage_meta["status"] = "failed"
                current["status"] = "failed"
            current["current_stage"] = None
            return current

        self.store.update_run_metadata(active.run_id, mutator)
        with self._lock:
            self._active.pop(active.run_id, None)

    def cancel_run(self, run_id: str) -> Dict[str, Any]:
        metadata = self.store.read_run_metadata(run_id)
        if metadata is None:
            raise FileNotFoundError(run_id)
        active = self.get_active_process(run_id)

        def mutator(current: Dict[str, Any]) -> Dict[str, Any]:
            current["cancel_requested_at"] = iso_now()
            current["status"] = "cancelled"
            stage_name = current.get("current_stage")
            if stage_name:
                current[stage_name]["status"] = "cancelled"
            return current

        updated = self.store.update_run_metadata(run_id, mutator)
        if active is not None:
            try:
                os.killpg(active.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            def killer(process: subprocess.Popen[str]) -> None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

            threading.Thread(target=killer, args=(active.process,), daemon=True).start()
        return updated

    def get_active_process(self, run_id: str) -> Optional[ActiveProcess]:
        with self._lock:
            active = self._active.get(run_id)
        if active is None:
            return None
        if active.process.poll() is not None:
            with self._lock:
                self._active.pop(run_id, None)
            return None
        return active

    def resolve_artifact_dir(self, metadata: Dict[str, Any], stage: str) -> Optional[Path]:
        stage_meta = metadata.get(stage, {})
        output_root = Path(stage_meta["output_root"])
        artifact_dir = detect_outputrun_dir(output_root, stage_meta.get("artifact_dir"))
        if artifact_dir and stage_meta.get("artifact_dir") != str(artifact_dir):
            self.store.update_run_metadata(
                metadata["run_id"],
                lambda current: self._set_artifact_dir(current, stage, artifact_dir),
            )
        return artifact_dir

    @staticmethod
    def _set_artifact_dir(current: Dict[str, Any], stage: str, artifact_dir: Path) -> Dict[str, Any]:
        current[stage]["artifact_dir"] = str(artifact_dir)
        return current

    def infer_status(self, metadata: Dict[str, Any]) -> str:
        run_id = metadata["run_id"]
        if metadata.get("read_only"):
            return metadata.get("status", "completed")
        runner = (metadata.get("optimization") or {}).get("runner")
        if metadata.get("cancel_requested_at") or metadata.get("status") == "cancelled":
            return "cancelled"
        active = self.get_active_process(run_id)
        analysis_dir = self.resolve_artifact_dir(metadata, "analysis")
        optimization_dir = self.resolve_artifact_dir(metadata, "optimization")
        if active is not None:
            if active.stage == "analysis":
                return "analyzing" if analysis_dir is not None else "starting"
            if runner == "ai":
                return "running"
            return "running" if stage_has_attempt_activity(optimization_dir) else "starting"

        if metadata.get("optimization", {}).get("status") == "failed" or metadata.get("analysis", {}).get("status") == "failed":
            return "failed"
        if metadata.get("optimization", {}).get("status") == "completed" and optimization_dir is not None:
            return "completed"
        if metadata.get("analysis", {}).get("status") == "completed" and analysis_dir is not None:
            return "completed"
        if metadata.get("status") == "queued":
            return "queued"
        if metadata.get("optimization", {}).get("status") == "starting" or metadata.get("analysis", {}).get("status") == "starting":
            return "failed"
        return metadata.get("status", "queued")

    @staticmethod
    def _supports_corundum_test(input_dcp: Path) -> bool:
        name = input_dcp.name.lower()
        return "corundum" in name or name == "demo_corundum_25g_misses_timing.dcp"
