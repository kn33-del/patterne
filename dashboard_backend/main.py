from __future__ import annotations

import os
import tempfile
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask

from .artifacts import (
    display_recipe_name,
    get_attempt_log_path,
    list_artifacts,
    parse_featured_demo_scenario,
    parse_ai_run_artifacts,
    parse_baseline_summary,
    parse_run_artifacts,
    preview_text_file,
    summarize_impact,
)
from .models import (
    AIConfig,
    AIOptimizationPlan,
    AIStatusSummary,
    AnalyzeRequest,
    ArtifactEntry,
    AutopilotRequest,
    BaselineSummary,
    DcpRecord,
    DemoScenario,
    HighFanoutConfig,
    LocalDcpRegistrationRequest,
    OptimizeRequest,
    PblockConfig,
    RunSummary,
    TextPreview,
)
from .preflight import apply_java_environment, run_dashboard_preflight
from .run_manager import RunManager
from .settings import Settings
from .storage import DashboardStore, iso_now, sanitize_filename


SUPPORTED_RECIPES = [
    "quick_timing_rescue",
    "pblock_explorer",
    "high_fanout_optimization",
    "ai_recommended_plan",
    "ai_autopilot",
]
ENABLED_RECIPES = list(SUPPORTED_RECIPES)
PBLOCK_RECIPES = {"quick_timing_rescue", "pblock_explorer"}
AI_RECIPES = {"high_fanout_optimization", "ai_autopilot"}


def _validate_dcp_suffix(filename: str) -> None:
    if not filename.lower().endswith(".dcp"):
        raise HTTPException(status_code=400, detail="Only .dcp files are supported.")


def _stage_artifact_dir(
    manager: RunManager,
    metadata: Dict[str, Any],
    stage: Optional[str],
) -> Optional[Path]:
    if stage == "analysis":
        return manager.resolve_artifact_dir(metadata, "analysis")
    if stage == "optimization":
        return manager.resolve_artifact_dir(metadata, "optimization")
    return manager.resolve_artifact_dir(metadata, "optimization") or manager.resolve_artifact_dir(metadata, "analysis")


def _resolve_artifact_path(artifact_root: Path, artifact_name: str) -> Path:
    candidate = (artifact_root / artifact_name).resolve()
    try:
        candidate.relative_to(artifact_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Artifact path escapes the run artifact directory.") from exc
    if not candidate.exists() or candidate.is_dir():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return candidate


def _coerce_pblock_config(payload: Dict[str, Any]) -> PblockConfig:
    return PblockConfig.model_validate(payload or {})


def _coerce_ai_config(payload: Dict[str, Any]) -> AIConfig:
    return AIConfig.model_validate(payload or {})


def _coerce_high_fanout_config(payload: Dict[str, Any]) -> HighFanoutConfig:
    return HighFanoutConfig.model_validate(payload or {})


def _run_available_actions(metadata: Dict[str, Any], status: str, baseline_ready: bool) -> List[str]:
    if metadata.get("read_only"):
        actions = ["download_artifacts"]
        if metadata.get("optimization", {}).get("output_dcp"):
            actions.append("download_best_dcp")
        return actions
    if status in {"queued", "starting", "analyzing", "running"}:
        return ["cancel"]
    if baseline_ready:
        return list(SUPPORTED_RECIPES)
    return []


def _supports_corundum_test(metadata: Dict[str, Any]) -> bool:
    return RunManager._supports_corundum_test(Path(metadata["dcp_path"]))


def _ensure_dashboard_run_is_writable(metadata: Dict[str, Any]) -> None:
    if metadata.get("read_only"):
        raise HTTPException(status_code=409, detail="Imported runs are read-only.")


def _ensure_baseline_ready(manager: RunManager, metadata: Dict[str, Any], *, recipe_label: str) -> None:
    if manager.resolve_artifact_dir(metadata, "analysis") is None and manager.resolve_artifact_dir(metadata, "optimization") is None:
        raise HTTPException(status_code=409, detail=f"Baseline analysis must complete before {recipe_label} starts.")


def _ensure_optimizer_script(settings: Settings) -> None:
    if not settings.optimizer_script.exists():
        raise HTTPException(status_code=500, detail="optimizer.py was not found.")


def _ensure_ai_backend_ready(*, recipe: str, metadata: Dict[str, Any], settings: Settings) -> None:
    if recipe == "high_fanout_optimization" and _supports_corundum_test(metadata):
        _ensure_optimizer_script(settings)
        return
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise HTTPException(
            status_code=409,
            detail="OPENROUTER_API_KEY is not configured in the backend environment.",
        )
    _ensure_optimizer_script(settings)


def _build_ai_plan(summary: Dict[str, Any]) -> Dict[str, Any]:
    baseline = summary.get("baseline") or {}
    timing = baseline.get("timing") or {}
    baseline_wns = timing.get("wns")
    dcp_name = (summary.get("dcp_name") or "").lower()

    if "corundum" in dcp_name:
        return AIOptimizationPlan(
            recommended_recipe="high_fanout_optimization",
            confidence="high",
            reason="The design name matches the supported Corundum high-fanout flow, so the deterministic fanout path is available immediately.",
            expected_benefit="Reduce fanout-driven delay and recover timing margin.",
            risks=["Fanout edits can improve one path while regressing others.", "Route runtime can still be significant."],
            config={"max_nets": 4, "debug": False, "continue_when_timing_met": True, "model": "x-ai/grok-4.1-fast"},
            stop_conditions=["Stop after the supported high-fanout pass completes.", "Preserve the original checkpoint."],
        ).model_dump()

    if baseline_wns is not None and baseline_wns < 0:
        if baseline_wns > -0.2:
            return AIOptimizationPlan(
                recommended_recipe="quick_timing_rescue",
                confidence="high",
                reason="The design is close to timing closure, so a short safe pblock sweep is the fastest way to try for a clean win.",
                expected_benefit="Recover slack quickly with a small number of low-risk placement experiments.",
                risks=["Physical constraints can still regress timing on some candidates."],
                config={"max_attempts": 4, "seed_count": 4, "elite_count": 2, "continue_after_improvement": False},
                stop_conditions=["Stop after the first improvement over baseline.", "Stop after four successful attempts."],
            ).model_dump()
        return AIOptimizationPlan(
            recommended_recipe="pblock_explorer",
            confidence="medium",
            reason="The baseline slack is materially negative, so a broader physical search is more likely to find a meaningful routing improvement.",
            expected_benefit="Increase timing margin and reduce failing endpoints through placement exploration.",
            risks=["Runtime grows with the number of attempts.", "Some candidates may worsen slack before the best one emerges."],
            config={"max_attempts": 12, "seed_count": 10, "elite_count": 4, "continue_after_improvement": False},
            stop_conditions=["Stop if timing closes and no stronger improvement appears worthwhile.", "Stop at the configured attempt limit."],
        ).model_dump()

    return AIOptimizationPlan(
        recommended_recipe="ai_autopilot",
        confidence="medium",
        reason="The design already meets timing, so the best opportunity is a controlled AI-guided search for additional timing margin.",
        expected_benefit="Push positive slack higher while keeping the optimization history visible.",
        risks=["The AI may spend runtime with little improvement.", "A marginally closed design can regress if the search is too aggressive."],
        config={"model": "x-ai/grok-4.1-fast", "debug": False, "continue_when_timing_met": True},
        stop_conditions=["Stop when timing margin improves over baseline.", "Stop when the runtime budget is reached."],
    ).model_dump()


def _run_summary_from_metadata(manager: RunManager, metadata: Dict[str, Any]) -> Dict[str, Any]:
    analysis_dir = manager.resolve_artifact_dir(metadata, "analysis")
    optimization_dir = manager.resolve_artifact_dir(metadata, "optimization")
    preferred_dir = optimization_dir or analysis_dir
    recipe = metadata.get("recipe")
    source = metadata.get("source", "dashboard")
    runner = (metadata.get("optimization") or {}).get("runner")
    status = manager.infer_status(metadata)
    summary_payload: Dict[str, Any] = {
        "run_id": metadata["run_id"],
        "dcp_id": metadata["dcp_id"],
        "dcp_name": metadata["dcp_name"],
        "dcp_path": metadata["dcp_path"],
        "source": source,
        "read_only": bool(metadata.get("read_only", False)),
        "recipe": recipe,
        "display_recipe": display_recipe_name(recipe),
        "status": status,
        "created_at": metadata["created_at"],
        "updated_at": metadata["updated_at"],
        "current_stage": metadata.get("current_stage"),
        "analysis_artifact_dir": str(analysis_dir) if analysis_dir else metadata["analysis"].get("artifact_dir"),
        "optimization_artifact_dir": str(optimization_dir) if optimization_dir else metadata["optimization"].get("artifact_dir"),
        "baseline": None,
        "impact": {},
        "available_actions": [],
        "best_attempt_num": None,
        "best_output_dcp": None,
        "best_output_artifact": None,
        "best_timing": {},
        "attempt_count": 0,
        "skip_count": 0,
        "attempts": [],
        "progress": {},
        "ai": None,
    }

    if preferred_dir is not None and preferred_dir.exists():
        if runner == "ai" and optimization_dir is not None:
            output_dcp_value = metadata.get("optimization", {}).get("output_dcp")
            parsed = parse_ai_run_artifacts(
                optimization_dir,
                stderr_log_path=Path(metadata["optimization"]["stderr_log"])
                if metadata.get("optimization", {}).get("stderr_log")
                else None,
                output_dcp_path=Path(output_dcp_value) if output_dcp_value else None,
                model=(metadata.get("optimization", {}).get("config") or {}).get("model"),
            )
        else:
            parsed = parse_run_artifacts(preferred_dir)
        summary_payload.update(parsed)
        if summary_payload.get("baseline") is None and analysis_dir is not None and analysis_dir.exists():
            summary_payload["baseline"] = parse_baseline_summary(analysis_dir)
    elif analysis_dir is not None and analysis_dir.exists():
        summary_payload["baseline"] = parse_baseline_summary(analysis_dir)

    optimization_config = metadata.get("optimization", {}).get("config") or {}
    if optimization_config.get("max_attempts") is not None:
        summary_payload["progress"]["max_attempts"] = optimization_config["max_attempts"]
    elif metadata.get("analysis", {}).get("status") is not None and not summary_payload["attempts"]:
        summary_payload["progress"]["max_attempts"] = 0

    summary_payload["impact"] = summarize_impact(summary_payload.get("baseline"), summary_payload.get("best_timing"))
    summary_payload["available_actions"] = _run_available_actions(
        metadata,
        status,
        baseline_ready=summary_payload.get("baseline") is not None,
    )

    return RunSummary.model_validate(summary_payload).model_dump()


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    resolved_settings = settings or Settings.default()
    store = DashboardStore(resolved_settings)
    manager = RunManager(resolved_settings, store)
    preflight_placeholder = {
        "ok": False,
        "skipped": resolved_settings.skip_startup_preflight,
        "java_home": str(resolved_settings.java_home.expanduser()),
        "libjvm_path": str(resolved_settings.java_home.expanduser() / "lib" / "server" / "libjvm.so"),
        "python_executable": resolved_settings.python_executable,
        "java_version_output": None,
        "rapidwright_version": None,
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if resolved_settings.skip_startup_preflight:
            apply_java_environment(resolved_settings)
            app.state.preflight = dict(preflight_placeholder)
        else:
            app.state.preflight = run_dashboard_preflight(resolved_settings)
            app.state.preflight["skipped"] = False
        yield

    app = FastAPI(title="DCP Forge", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = resolved_settings
    app.state.store = store
    app.state.manager = manager
    app.state.preflight = dict(preflight_placeholder)

    @app.middleware("http")
    async def disable_api_caching(request, call_next):
        response: Response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.get("/api/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/demo/featured", response_model=DemoScenario)
    def get_featured_demo() -> Dict[str, Any]:
        try:
            return parse_featured_demo_scenario(resolved_settings.repo_root)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="No featured visitor optimization scenario is available.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/settings")
    def get_settings() -> Dict[str, Any]:
        openrouter_api_key_configured = bool(os.environ.get("OPENROUTER_API_KEY"))
        preflight = getattr(app.state, "preflight", None)
        return {
            "product_name": "DCP Forge",
            "repo_root": str(resolved_settings.repo_root),
            "data_root": str(resolved_settings.data_root),
            "python_executable": resolved_settings.python_executable,
            "optimizer_script": str(resolved_settings.optimizer_script),
            "optimizer_script_present": resolved_settings.optimizer_script.exists(),
            "supported_recipes": SUPPORTED_RECIPES,
            "enabled_recipes": ENABLED_RECIPES,
            "openrouter_api_key_configured": openrouter_api_key_configured,
            "ai_autopilot_ready": openrouter_api_key_configured and resolved_settings.optimizer_script.exists(),
            "frontend_dist_present": resolved_settings.frontend_dist_dir.exists(),
            "java_home": str(resolved_settings.java_home.expanduser()),
            "startup_preflight_enabled": not resolved_settings.skip_startup_preflight,
            "preflight": preflight,
        }

    @app.get("/api/preflight")
    def get_preflight() -> Dict[str, Any]:
        preflight = getattr(app.state, "preflight", None)
        if preflight is None:
            raise HTTPException(status_code=503, detail="Dashboard preflight has not completed yet.")
        return preflight

    @app.get("/api/dcps", response_model=List[DcpRecord])
    def list_dcps() -> List[Dict[str, Any]]:
        return store.list_dcps()

    @app.post("/api/dcps/upload", response_model=DcpRecord)
    async def upload_dcp(file: UploadFile = File(...)) -> Dict[str, Any]:
        filename = file.filename or ""
        _validate_dcp_suffix(filename)
        safe_name = sanitize_filename(filename)
        dcp_id = uuid.uuid4().hex
        final_path = resolved_settings.uploads_dir / f"{dcp_id}_{safe_name}"

        with final_path.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        record = {
            "dcp_id": dcp_id,
            "kind": "upload",
            "filename": safe_name,
            "path": str(final_path.resolve()),
            "size_bytes": final_path.stat().st_size,
            "registered_at": iso_now(),
        }
        return store.save_dcp_record(record)

    @app.post("/api/dcps/register-local", response_model=DcpRecord)
    def register_local_dcp(request: LocalDcpRegistrationRequest) -> Dict[str, Any]:
        path = Path(request.path).expanduser()
        if not path.is_absolute():
            raise HTTPException(status_code=400, detail="Local DCP registration requires an absolute Linux path.")
        _validate_dcp_suffix(path.name)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Local DCP path does not exist.")
        return store.register_dcp(kind="local", filename=path.name, path=path.resolve())

    @app.get("/api/runs")
    def list_runs() -> List[Dict[str, Any]]:
        return [_run_summary_from_metadata(manager, metadata) for metadata in store.list_runs()]

    @app.post("/api/runs/analyze", response_model=RunSummary)
    def analyze_run(request: AnalyzeRequest) -> Dict[str, Any]:
        dcp_record = store.get_dcp(request.dcp_id)
        if dcp_record is None:
            raise HTTPException(status_code=404, detail="Unknown dcp_id.")
        metadata = store.create_run_metadata(dcp_record)
        manager.launch_analysis(metadata["run_id"])
        refreshed = store.read_run_metadata(metadata["run_id"])
        if refreshed is None:
            raise HTTPException(status_code=500, detail="Failed to create run metadata.")
        return _run_summary_from_metadata(manager, refreshed)

    @app.get("/api/runs/{run_id}", response_model=RunSummary)
    @app.get("/api/runs/{run_id}/summary", response_model=RunSummary)
    def get_run_summary(run_id: str) -> Dict[str, Any]:
        metadata = store.read_run_metadata(run_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return _run_summary_from_metadata(manager, metadata)

    @app.get("/api/runs/{run_id}/baseline", response_model=BaselineSummary)
    def get_run_baseline(run_id: str) -> Dict[str, Any]:
        metadata = store.read_run_metadata(run_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        summary = _run_summary_from_metadata(manager, metadata)
        if summary.get("baseline") is not None:
            return summary["baseline"]
        analysis_dir = manager.resolve_artifact_dir(metadata, "analysis")
        if analysis_dir is None or not analysis_dir.exists():
            raise HTTPException(status_code=404, detail="Baseline artifacts are not available yet.")
        return parse_baseline_summary(analysis_dir)

    @app.post("/api/runs/{run_id}/optimize", response_model=RunSummary)
    def optimize_run(run_id: str, request: OptimizeRequest) -> Dict[str, Any]:
        metadata = store.read_run_metadata(run_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        _ensure_dashboard_run_is_writable(metadata)
        status = manager.infer_status(metadata)
        if status in {"starting", "analyzing", "running"}:
            raise HTTPException(status_code=409, detail="The run is already active.")
        if request.recipe == "ai_recommended_plan":
            raise HTTPException(status_code=409, detail="Generate an AI plan from /api/runs/<run_id>/ai/plan before running it.")

        _ensure_baseline_ready(manager, metadata, recipe_label=display_recipe_name(request.recipe))

        if request.recipe == "quick_timing_rescue":
            manager.launch_quick_timing_rescue(run_id, _coerce_pblock_config(request.config), request.recipe)
        elif request.recipe == "pblock_explorer":
            manager.launch_optimization(run_id, _coerce_pblock_config(request.config), request.recipe)
        elif request.recipe == "high_fanout_optimization":
            _ensure_ai_backend_ready(recipe=request.recipe, metadata=metadata, settings=resolved_settings)
            manager.launch_high_fanout_optimization(run_id, _coerce_high_fanout_config(request.config), request.recipe)
        elif request.recipe == "ai_autopilot":
            _ensure_ai_backend_ready(recipe=request.recipe, metadata=metadata, settings=resolved_settings)
            manager.launch_ai_optimization(run_id, _coerce_ai_config(request.config), request.recipe)
        else:
            raise HTTPException(status_code=400, detail="Unsupported optimization recipe.")

        refreshed = store.read_run_metadata(run_id)
        if refreshed is None:
            raise HTTPException(status_code=500, detail="Failed to refresh run metadata.")
        return _run_summary_from_metadata(manager, refreshed)

    @app.post("/api/runs/{run_id}/ai/optimize", response_model=RunSummary)
    def optimize_run_ai(run_id: str, request: AutopilotRequest) -> Dict[str, Any]:
        return optimize_run(
            run_id,
            OptimizeRequest(recipe="ai_autopilot", config=request.config.model_dump()),
        )

    @app.post("/api/runs/{run_id}/ai/plan", response_model=AIOptimizationPlan)
    def get_ai_plan(run_id: str) -> Dict[str, Any]:
        metadata = store.read_run_metadata(run_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        summary = _run_summary_from_metadata(manager, metadata)
        if summary.get("baseline") is None:
            raise HTTPException(status_code=409, detail="Baseline analysis must complete before generating an AI plan.")
        return _build_ai_plan(summary)

    @app.post("/api/runs/{run_id}/cancel", response_model=RunSummary)
    def cancel_run(run_id: str) -> Dict[str, Any]:
        metadata = store.read_run_metadata(run_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        _ensure_dashboard_run_is_writable(metadata)
        manager.cancel_run(run_id)
        refreshed = store.read_run_metadata(run_id)
        if refreshed is None:
            raise HTTPException(status_code=500, detail="Failed to refresh run metadata.")
        return _run_summary_from_metadata(manager, refreshed)

    @app.get("/api/runs/{run_id}/ai/status", response_model=AIStatusSummary)
    def get_ai_status(run_id: str) -> Dict[str, Any]:
        metadata = store.read_run_metadata(run_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        summary = _run_summary_from_metadata(manager, metadata)
        if summary.get("ai") is None:
            raise HTTPException(status_code=404, detail="AI Autopilot data is not available for this run.")
        return summary["ai"]

    @app.get("/api/runs/{run_id}/attempts/{attempt_num}/logs/{log_type}", response_model=TextPreview)
    def get_attempt_log(
        run_id: str,
        attempt_num: int,
        log_type: str,
        full: bool = Query(default=False),
    ) -> Dict[str, Any]:
        metadata = store.read_run_metadata(run_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        artifact_dir = manager.resolve_artifact_dir(metadata, "optimization") or manager.resolve_artifact_dir(metadata, "analysis")
        if artifact_dir is None:
            raise HTTPException(status_code=404, detail="Artifacts are not available yet.")
        try:
            log_path = get_attempt_log_path(artifact_dir, attempt_num, log_type)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail="Unsupported attempt log type.") from exc
        if not log_path.exists():
            raise HTTPException(status_code=404, detail="Requested attempt log was not found.")
        return preview_text_file(log_path, full=full, max_bytes=resolved_settings.artifact_preview_bytes)

    @app.get("/api/runs/{run_id}/wrapper-logs/{stage}/{log_type}", response_model=TextPreview)
    def get_wrapper_log(
        run_id: str,
        stage: str,
        log_type: str,
        full: bool = Query(default=False),
    ) -> Dict[str, Any]:
        metadata = store.read_run_metadata(run_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        if stage not in {"analysis", "optimization"}:
            raise HTTPException(status_code=400, detail="Unsupported stage.")
        if log_type not in {"stdout", "stderr"}:
            raise HTTPException(status_code=400, detail="Unsupported wrapper log type.")
        raw_path = metadata.get(stage, {}).get(f"{log_type}_log")
        if not raw_path:
            raise HTTPException(status_code=404, detail="Wrapper log not found.")
        path = Path(raw_path)
        if not path.exists() or path.is_dir():
            raise HTTPException(status_code=404, detail="Wrapper log not found.")
        return preview_text_file(path, full=full, max_bytes=resolved_settings.artifact_preview_bytes)

    @app.get("/api/runs/{run_id}/artifacts", response_model=List[ArtifactEntry])
    def get_artifacts(run_id: str, stage: Optional[str] = Query(default=None)) -> List[Dict[str, Any]]:
        metadata = store.read_run_metadata(run_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        artifact_dir = _stage_artifact_dir(manager, metadata, stage)
        if artifact_dir is None:
            raise HTTPException(status_code=404, detail="Artifacts are not available yet.")
        return list_artifacts(artifact_dir)

    @app.get("/api/runs/{run_id}/artifacts.zip")
    def download_artifacts_zip(run_id: str, stage: Optional[str] = Query(default=None)) -> FileResponse:
        metadata = store.read_run_metadata(run_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        artifact_dir = _stage_artifact_dir(manager, metadata, stage)
        if artifact_dir is None:
            raise HTTPException(status_code=404, detail="Artifacts are not available yet.")
        temp_zip = tempfile.NamedTemporaryFile(prefix=f"{run_id}-", suffix=".zip", delete=False)
        temp_zip_path = Path(temp_zip.name)
        temp_zip.close()
        with zipfile.ZipFile(temp_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in artifact_dir.rglob("*"):
                if path.is_file():
                    archive.write(path, arcname=path.relative_to(artifact_dir))
        return FileResponse(
            temp_zip_path,
            filename=f"{run_id}-{artifact_dir.name}.zip",
            media_type="application/zip",
            background=BackgroundTask(lambda: temp_zip_path.unlink(missing_ok=True)),
        )

    @app.get("/api/runs/{run_id}/artifacts/{artifact_name:path}")
    def download_artifact(run_id: str, artifact_name: str, stage: Optional[str] = Query(default=None)) -> FileResponse:
        metadata = store.read_run_metadata(run_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        artifact_dir = _stage_artifact_dir(manager, metadata, stage)
        if artifact_dir is None:
            raise HTTPException(status_code=404, detail="Artifacts are not available yet.")
        path = _resolve_artifact_path(artifact_dir, artifact_name)
        return FileResponse(path, filename=path.name)

    @app.get("/{full_path:path}", response_model=None)
    def serve_frontend(full_path: str):
        dist_dir = resolved_settings.frontend_dist_dir
        if not dist_dir.exists():
            return HTMLResponse(
                "<html><body><h1>DCP Forge frontend build not found.</h1><p>Build dashboard_frontend/dist before launching the product UI.</p></body></html>",
                status_code=503,
            )
        requested = (dist_dir / full_path).resolve() if full_path else dist_dir / "index.html"
        try:
            requested.relative_to(dist_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found.")
        if requested.exists() and requested.is_file():
            return FileResponse(requested)
        return FileResponse(dist_dir / "index.html")

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("dashboard_backend.main:app", host="0.0.0.0", port=8000, reload=False)
