from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


RunStatus = Literal["queued", "starting", "analyzing", "running", "completed", "failed", "cancelled"]
RunStage = Literal["analysis", "optimization"]
RecipeName = Literal[
    "pblock_explorer",
    "quick_timing_rescue",
    "high_fanout_optimization",
    "ai_recommended_plan",
    "ai_autopilot",
]
RunSource = Literal["dashboard", "imported_ai", "imported_pblock"]


class TimingParsed(BaseModel):
    wns: Optional[float] = None
    tns: Optional[float] = None
    failing_endpoints: Optional[float] = None
    estimated_fmax_mhz: Optional[float] = None


class ImpactSummary(BaseModel):
    timing_margin_gain_ns: Optional[float] = None
    tns_gain_ns: Optional[float] = None
    failing_endpoint_reduction: Optional[float] = None
    performance_uplift_mhz: Optional[float] = None
    performance_uplift_pct: Optional[float] = None
    timing_closed: bool = False
    improved: Optional[bool] = None


class BaselineSummary(BaseModel):
    artifact_dir: Optional[str] = None
    input_dcp: Optional[str] = None
    baseline_clock_period_ns: Optional[float] = None
    clock_period_ns: Optional[float] = None
    timing: TimingParsed = Field(default_factory=TimingParsed)
    utilization: Dict[str, int] = Field(default_factory=dict)
    targets: Dict[str, int] = Field(default_factory=dict)
    analyze_result: Dict[str, Any] = Field(default_factory=dict)
    start_region: Dict[str, Any] = Field(default_factory=dict)
    report_files: Dict[str, str] = Field(default_factory=dict)


class PblockConfig(BaseModel):
    pblock_name: str = "pblock_auto_0"
    apply_to: str = "current_design"
    use_clock_regions: bool = False
    hard_pblock: bool = False
    place_directive: str = "Default"
    route_directive: str = "Default"
    target_lut: Optional[int] = None
    target_ff: Optional[int] = None
    target_dsp: Optional[int] = None
    target_bram: Optional[int] = None
    keep_placement: bool = False
    keep_routing: bool = False
    max_attempts: int = 12
    continue_after_improvement: bool = False
    seed_count: int = 10
    elite_count: int = 4
    random_seed: int = 7


class AIConfig(BaseModel):
    model: str = "x-ai/grok-4.1-fast"
    debug: bool = False
    continue_when_timing_met: bool = False


class HighFanoutConfig(AIConfig):
    max_nets: int = 5


class AttemptSummary(BaseModel):
    attempt_num: Optional[int] = None
    skip_num: Optional[int] = None
    status: str
    candidate_region: Dict[str, Any] = Field(default_factory=dict)
    pblock_ranges: Optional[str] = None
    timing: TimingParsed = Field(default_factory=TimingParsed)
    improved_vs_baseline: Optional[bool] = None
    improved_vs_best_before: Optional[bool] = None
    output_dcp: Optional[str] = None
    relative_output_dcp: Optional[str] = None
    error: Optional[str] = None
    stop_reason: Optional[str] = None
    leaderboard_rank: Optional[int] = None
    available_logs: List[str] = Field(default_factory=list)


class AttemptProgress(BaseModel):
    max_attempts: Optional[int] = None
    current_attempt: Optional[int] = None
    completed_attempts: int = 0
    skipped_candidates: int = 0
    successful_attempts: int = 0


class AIStatusSummary(BaseModel):
    phase: Optional[str] = None
    model: Optional[str] = None
    iteration: Optional[int] = None
    llm_call_count: int = 0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: Optional[float] = None
    initial_timing: TimingParsed = Field(default_factory=TimingParsed)
    best_timing: TimingParsed = Field(default_factory=TimingParsed)
    planned_output_dcp: Optional[str] = None
    output_dcp: Optional[str] = None
    relative_output_dcp: Optional[str] = None
    output_ready: bool = False
    run_dir: Optional[str] = None
    token_report_present: bool = False
    last_message: Optional[str] = None
    error: Optional[str] = None


class AIOptimizationPlan(BaseModel):
    recommended_recipe: RecipeName
    confidence: Literal["low", "medium", "high"]
    reason: str
    expected_benefit: str
    risks: List[str] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)
    stop_conditions: List[str] = Field(default_factory=list)


class DemoMetricSnapshot(BaseModel):
    wns: Optional[float] = None
    tns: Optional[float] = None
    failing_endpoints: Optional[float] = None
    estimated_fmax_mhz: Optional[float] = None


class DemoStep(BaseModel):
    id: str
    order: int
    phase: str
    title: str
    status_label: str
    duration_ms: int
    assistant_text: str
    tool_text: str
    metrics: DemoMetricSnapshot = Field(default_factory=DemoMetricSnapshot)
    highlighted_nets: List[str] = Field(default_factory=list)


class DemoScenario(BaseModel):
    scenario_id: str
    title: str
    duration_ms: int
    featured_run_id: str
    design_name: str
    design_part: str
    input_dcp_label: str
    initial_metrics: DemoMetricSnapshot = Field(default_factory=DemoMetricSnapshot)
    final_metrics: DemoMetricSnapshot = Field(default_factory=DemoMetricSnapshot)
    impact: ImpactSummary = Field(default_factory=ImpactSummary)
    steps: List[DemoStep] = Field(default_factory=list)


class RunSummary(BaseModel):
    run_id: str
    dcp_id: str
    dcp_name: str
    dcp_path: str
    source: RunSource = "dashboard"
    read_only: bool = False
    recipe: Optional[RecipeName] = None
    display_recipe: str = "Analyze"
    status: RunStatus
    created_at: str
    updated_at: str
    current_stage: Optional[RunStage] = None
    analysis_artifact_dir: Optional[str] = None
    optimization_artifact_dir: Optional[str] = None
    baseline: Optional[BaselineSummary] = None
    impact: ImpactSummary = Field(default_factory=ImpactSummary)
    available_actions: List[str] = Field(default_factory=list)
    best_attempt_num: Optional[int] = None
    best_output_dcp: Optional[str] = None
    best_output_artifact: Optional[str] = None
    best_timing: TimingParsed = Field(default_factory=TimingParsed)
    attempt_count: int = 0
    skip_count: int = 0
    attempts: List[AttemptSummary] = Field(default_factory=list)
    progress: AttemptProgress = Field(default_factory=AttemptProgress)
    ai: Optional[AIStatusSummary] = None


class DcpRecord(BaseModel):
    dcp_id: str
    kind: Literal["upload", "local"]
    filename: str
    path: str
    size_bytes: int
    registered_at: str


class AnalyzeRequest(BaseModel):
    dcp_id: str


class LocalDcpRegistrationRequest(BaseModel):
    path: str


class OptimizeRequest(BaseModel):
    recipe: RecipeName
    config: Dict[str, Any] = Field(default_factory=dict)


class AutopilotRequest(BaseModel):
    config: AIConfig = Field(default_factory=AIConfig)


class UnsupportedRecipeResponse(BaseModel):
    supported: bool = False
    recipe: RecipeName
    message: str


class ArtifactEntry(BaseModel):
    path: str
    kind: Literal["file", "directory"]
    size_bytes: int


class TextPreview(BaseModel):
    path: str
    size_bytes: int
    truncated: bool
    preview_text: str
