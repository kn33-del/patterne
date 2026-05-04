from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Sequence

from .settings import Settings


class PreflightError(RuntimeError):
    pass


def _prepend_env_path(name: str, entry: str) -> None:
    current = os.environ.get(name, "")
    parts = [part for part in current.split(":") if part]
    if entry not in parts:
        os.environ[name] = ":".join([entry, *parts]) if parts else entry


def apply_java_environment(settings: Settings) -> Path:
    java_home = settings.java_home.expanduser().resolve()
    os.environ["JAVA_HOME"] = str(java_home)
    _prepend_env_path("PATH", str(java_home / "bin"))
    _prepend_env_path("LD_LIBRARY_PATH", str(java_home / "lib" / "server"))
    return java_home


def _run_checked_command(
    args: Sequence[str],
    *,
    timeout_seconds: int,
) -> str:
    completed = subprocess.run(
        list(args),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=os.environ.copy(),
    )
    output = "\n".join(part.strip() for part in [completed.stdout, completed.stderr] if part.strip()).strip()
    if completed.returncode != 0:
        raise PreflightError(
            f"Command {' '.join(args)} failed with exit code {completed.returncode}.\n{output}".rstrip()
        )
    return output


def run_dashboard_preflight(settings: Settings) -> Dict[str, Any]:
    java_home = apply_java_environment(settings)
    libjvm_path = java_home / "lib" / "server" / "libjvm.so"
    if not libjvm_path.exists():
        raise PreflightError(f"Expected libjvm.so at {libjvm_path}, but it was not found.")

    java_version_output = _run_checked_command(
        ["java", "-version"],
        timeout_seconds=settings.preflight_timeout_seconds,
    )
    rapidwright_version = _run_checked_command(
        [
            settings.python_executable,
            "-c",
            "import rapidwright; from com.xilinx.rapidwright.device import Device; print(Device.RAPIDWRIGHT_VERSION)",
        ],
        timeout_seconds=settings.preflight_timeout_seconds,
    ).splitlines()[-1].strip()

    return {
        "ok": True,
        "java_home": str(java_home),
        "libjvm_path": str(libjvm_path),
        "python_executable": settings.python_executable,
        "java_version_output": java_version_output,
        "rapidwright_version": rapidwright_version,
    }


def main() -> int:
    settings = Settings.default()
    try:
        result = run_dashboard_preflight(settings)
    except PreflightError as exc:
        print(f"Dashboard preflight failed: {exc}", file=sys.stderr)
        return 1

    print("Dashboard preflight passed")
    print(f"JAVA_HOME={result['java_home']}")
    print(result["libjvm_path"])
    print(result["java_version_output"])
    print(result["rapidwright_version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
