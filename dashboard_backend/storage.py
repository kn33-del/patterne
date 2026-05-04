from __future__ import annotations

import json
import re
import threading
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .artifacts import infer_ai_input_dcp, infer_ai_output_dcp, parse_token_report_timestamp, read_json as read_artifact_json
from .settings import Settings


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", filename.strip())
    if not cleaned:
        cleaned = "uploaded.dcp"
    if not cleaned.lower().endswith(".dcp"):
        cleaned = f"{cleaned}.dcp"
    return cleaned


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


class DashboardStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_layout()
        self._lock = threading.RLock()
        if not self.settings.dcp_registry_path.exists():
            write_json_atomic(self.settings.dcp_registry_path, {})

    def list_dcps(self) -> List[Dict[str, Any]]:
        with self._lock:
            registry = read_json(self.settings.dcp_registry_path, {})
        return sorted(registry.values(), key=lambda item: item["registered_at"], reverse=True)

    def get_dcp(self, dcp_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            registry = read_json(self.settings.dcp_registry_path, {})
        return registry.get(dcp_id)

    def save_dcp_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            registry = read_json(self.settings.dcp_registry_path, {})
            registry[record["dcp_id"]] = record
            write_json_atomic(self.settings.dcp_registry_path, registry)
        return record

    def register_dcp(self, *, kind: str, filename: str, path: Path) -> Dict[str, Any]:
        record = {
            "dcp_id": uuid.uuid4().hex,
            "kind": kind,
            "filename": filename,
            "path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "registered_at": iso_now(),
        }
        return self.save_dcp_record(record)

    def run_dir(self, run_id: str) -> Path:
        return self.settings.runs_dir / run_id

    def metadata_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "metadata.json"

    def create_run_metadata(self, dcp_record: Dict[str, Any]) -> Dict[str, Any]:
        run_id = uuid.uuid4().hex
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        metadata = {
            "run_id": run_id,
            "dcp_id": dcp_record["dcp_id"],
            "dcp_name": dcp_record["filename"],
            "dcp_path": dcp_record["path"],
            "source": "dashboard",
            "read_only": False,
            "recipe": None,
            "status": "queued",
            "current_stage": "analysis",
            "created_at": iso_now(),
            "updated_at": iso_now(),
            "cancel_requested_at": None,
            "analysis": {
                "status": "queued",
                "requested_at": iso_now(),
                "started_at": None,
                "finished_at": None,
                "output_root": str(run_dir / "analysis"),
                "artifact_dir": None,
                "stdout_log": str(run_dir / "analysis.stdout.log"),
                "stderr_log": str(run_dir / "analysis.stderr.log"),
                "command": [],
                "pid": None,
                "process_group": None,
                "exit_code": None,
                "runner": "pblock",
            },
            "optimization": {
                "status": None,
                "requested_at": None,
                "started_at": None,
                "finished_at": None,
                "output_root": str(run_dir / "optimization"),
                "artifact_dir": None,
                "output_dcp": None,
                "stdout_log": str(run_dir / "optimization.stdout.log"),
                "stderr_log": str(run_dir / "optimization.stderr.log"),
                "command": [],
                "config": {},
                "pid": None,
                "process_group": None,
                "exit_code": None,
                "runner": None,
            },
        }
        write_json_atomic(self.metadata_path(run_id), metadata)
        return metadata

    def read_run_metadata(self, run_id: str) -> Optional[Dict[str, Any]]:
        path = self.metadata_path(run_id)
        if not path.exists():
            imported = {item["run_id"]: item for item in self.discover_imported_runs()}
            return imported.get(run_id)
        with self._lock:
            return read_json(path, None)

    def update_run_metadata(
        self,
        run_id: str,
        mutator: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        path = self.metadata_path(run_id)
        with self._lock:
            metadata = read_json(path, None)
            if metadata is None:
                raise FileNotFoundError(run_id)
            mutated = mutator(metadata)
            mutated["updated_at"] = iso_now()
            write_json_atomic(path, mutated)
        return mutated

    def list_runs(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for metadata_path in sorted(self.settings.runs_dir.glob("*/metadata.json")):
            results.append(read_json(metadata_path, {}))
        results.extend(self.discover_imported_runs(existing_dashboard_runs=results))
        return sorted(results, key=lambda item: item.get("updated_at", item.get("created_at", "")), reverse=True)

    def discover_imported_runs(self, existing_dashboard_runs: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        dashboard_runs = existing_dashboard_runs
        if dashboard_runs is None:
            dashboard_runs = []
            for metadata_path in sorted(self.settings.runs_dir.glob("*/metadata.json")):
                dashboard_runs.append(read_json(metadata_path, {}))

        excluded_dirs = self._dashboard_artifact_dirs(dashboard_runs)
        discovered: List[Dict[str, Any]] = []
        for run_dir in self._candidate_imported_ai_dirs():
            resolved = run_dir.resolve()
            if resolved in excluded_dirs:
                continue
            discovered.append(self._build_imported_ai_metadata(resolved))
        for run_dir in self._candidate_imported_pblock_dirs():
            resolved = run_dir.resolve()
            if resolved in excluded_dirs:
                continue
            discovered.append(self._build_imported_pblock_metadata(resolved))
        return discovered

    def _dashboard_artifact_dirs(self, dashboard_runs: List[Dict[str, Any]]) -> set[Path]:
        artifact_dirs: set[Path] = set()
        for run in dashboard_runs:
            for stage in ("analysis", "optimization"):
                stage_meta = run.get(stage) or {}
                artifact_dir = stage_meta.get("artifact_dir")
                if artifact_dir:
                    artifact_dirs.add(Path(artifact_dir).resolve())
        return artifact_dirs

    def _candidate_imported_ai_dirs(self) -> List[Path]:
        candidates = [path for path in self.settings.repo_root.glob("dcp_optimizer_run-*") if path.is_dir()]
        return sorted(candidates, key=lambda path: path.stat().st_mtime_ns, reverse=True)

    def _candidate_imported_pblock_dirs(self) -> List[Path]:
        roots = [self.settings.repo_root, self.settings.repo_root / "Optimizer"]
        candidates: Dict[Path, Path] = {}
        for root in roots:
            if not root.exists():
                continue
            for path in root.glob("outputrun_*"):
                if path.is_dir():
                    candidates[path.resolve()] = path.resolve()
        return sorted(candidates.values(), key=lambda path: path.stat().st_mtime_ns, reverse=True)

    def _imported_run_id(self, prefix: str, run_dir: Path) -> str:
        digest = hashlib.sha1(str(run_dir.resolve()).encode("utf-8")).hexdigest()[:16]
        return f"{prefix}-{digest}"

    def _build_imported_ai_metadata(self, run_dir: Path) -> Dict[str, Any]:
        token_report = read_artifact_json(run_dir / "token_usage.json") or {}
        created_at = parse_token_report_timestamp(token_report.get("timestamp"), fallback_path=run_dir)
        updated_at = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc).isoformat()
        input_dcp = infer_ai_input_dcp(run_dir)
        output_dcp = infer_ai_output_dcp(run_dir)
        dcp_name = Path(input_dcp).name if input_dcp else run_dir.name
        vivado_log = run_dir / "vivado.log"
        vivado_mcp_log = run_dir / "vivado-mcp.log"
        rapidwright_log = run_dir / "rapidwright.log"
        rapidwright_mcp_log = run_dir / "rapidwright-mcp.log"
        return {
            "run_id": self._imported_run_id("imported-ai", run_dir),
            "dcp_id": self._imported_run_id("imported-dcp", run_dir),
            "dcp_name": dcp_name,
            "dcp_path": input_dcp or str(run_dir.resolve()),
            "recipe": "ai_autopilot",
            "status": "completed",
            "current_stage": None,
            "created_at": created_at,
            "updated_at": updated_at,
            "cancel_requested_at": None,
            "read_only": True,
            "source": "imported_ai",
            "analysis": {
                "status": "completed",
                "requested_at": created_at,
                "started_at": created_at,
                "finished_at": updated_at,
                "output_root": str(run_dir.resolve()),
                "artifact_dir": str(run_dir.resolve()),
                "stdout_log": str(rapidwright_log if rapidwright_log.exists() else vivado_log),
                "stderr_log": str(rapidwright_mcp_log if rapidwright_mcp_log.exists() else vivado_mcp_log),
                "command": [],
                "pid": None,
                "process_group": None,
                "exit_code": 0,
            },
            "optimization": {
                "status": "completed",
                "requested_at": created_at,
                "started_at": created_at,
                "finished_at": updated_at,
                "output_root": str(run_dir.resolve()),
                "artifact_dir": str(run_dir.resolve()),
                "output_dcp": str(output_dcp) if output_dcp is not None else None,
                "stdout_log": str(vivado_log if vivado_log.exists() else rapidwright_log),
                "stderr_log": str(vivado_mcp_log if vivado_mcp_log.exists() else rapidwright_mcp_log),
                "command": [],
                "config": {
                    "model": token_report.get("model"),
                    "debug": True,
                    "continue_when_timing_met": False,
                },
                "pid": None,
                "process_group": None,
                "exit_code": 0,
                "runner": "ai",
            },
        }

    def _build_imported_pblock_metadata(self, run_dir: Path) -> Dict[str, Any]:
        summary = read_artifact_json(run_dir / "run_summary.json") or {}
        baseline_meta = read_artifact_json(run_dir / "baseline_meta.json") or {}
        updated_at = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc).isoformat()
        input_dcp = baseline_meta.get("input_dcp") or summary.get("input_dcp")
        dcp_name = Path(input_dcp).name if input_dcp else run_dir.name
        return {
            "run_id": self._imported_run_id("imported-pblock", run_dir),
            "dcp_id": self._imported_run_id("imported-dcp", run_dir),
            "dcp_name": dcp_name,
            "dcp_path": input_dcp or str(run_dir.resolve()),
            "recipe": "pblock_explorer",
            "status": "completed",
            "current_stage": None,
            "created_at": updated_at,
            "updated_at": updated_at,
            "cancel_requested_at": None,
            "read_only": True,
            "source": "imported_pblock",
            "analysis": {
                "status": "completed",
                "requested_at": updated_at,
                "started_at": updated_at,
                "finished_at": updated_at,
                "output_root": str(run_dir.resolve()),
                "artifact_dir": str(run_dir.resolve()),
                "stdout_log": "",
                "stderr_log": "",
                "command": [],
                "pid": None,
                "process_group": None,
                "exit_code": 0,
            },
            "optimization": {
                "status": "completed",
                "requested_at": updated_at,
                "started_at": updated_at,
                "finished_at": updated_at,
                "output_root": str(run_dir.resolve()),
                "artifact_dir": str(run_dir.resolve()),
                "output_dcp": summary.get("best_output_dcp"),
                "stdout_log": "",
                "stderr_log": "",
                "command": [],
                "config": {},
                "pid": None,
                "process_group": None,
                "exit_code": 0,
                "runner": "pblock",
            },
        }
