from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_JAVA_HOME = "/tools/Xilinx/Vivado/2024.2/tps/lnx64/jre21.0.1_12"


def _env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    repo_root: Path
    data_root: Path
    uploads_dir: Path
    runs_dir: Path
    dcp_registry_path: Path
    frontend_dist_dir: Path
    pblock_script: Path
    optimizer_script: Path
    python_executable: str
    java_home: Path = Path(DEFAULT_JAVA_HOME)
    artifact_preview_bytes: int = 64 * 1024
    skip_startup_preflight: bool = False
    preflight_timeout_seconds: int = 60

    @classmethod
    def default(cls) -> "Settings":
        repo_root = Path(
            os.environ.get("DCP_DASHBOARD_REPO_ROOT", Path(__file__).resolve().parents[1])
        ).expanduser().resolve()
        data_root = Path(
            os.environ.get("DCP_DASHBOARD_DATA_DIR", repo_root / "dashboard_data")
        ).expanduser()
        return cls(
            repo_root=repo_root,
            data_root=data_root,
            uploads_dir=data_root / "uploads",
            runs_dir=data_root / "runs",
            dcp_registry_path=data_root / "dcp_registry.json",
            frontend_dist_dir=repo_root / "dashboard_frontend" / "dist",
            pblock_script=repo_root / "Optimizer" / "Pblock_optimizers" / "pblock.py",
            optimizer_script=repo_root / "Optimizer" / "optimizer.py",
            python_executable=os.environ.get("DASHBOARD_PYTHON_EXECUTABLE", sys.executable),
            java_home=Path(os.environ.get("DCP_DASHBOARD_JAVA_HOME", os.environ.get("JAVA_HOME", DEFAULT_JAVA_HOME))),
            skip_startup_preflight=_env_flag("DCP_DASHBOARD_SKIP_PREFLIGHT"),
        )

    def ensure_layout(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
