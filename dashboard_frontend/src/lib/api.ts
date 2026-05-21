export type RunStatus =
  | "queued"
  | "starting"
  | "analyzing"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type RecipeName =
  | "pblock_explorer"
  | "quick_timing_rescue"
  | "high_fanout_optimization"
  | "ai_recommended_plan"
  | "ai_autopilot";

export type RunSource = "dashboard" | "imported_ai" | "imported_pblock";

export interface TimingParsed {
  wns: number | null;
  tns: number | null;
  failing_endpoints: number | null;
  estimated_fmax_mhz: number | null;
}

export interface ImpactSummary {
  timing_margin_gain_ns: number | null;
  tns_gain_ns: number | null;
  failing_endpoint_reduction: number | null;
  performance_uplift_mhz: number | null;
  performance_uplift_pct: number | null;
  timing_closed: boolean;
  improved: boolean | null;
}

export interface BaselineSummary {
  artifact_dir: string | null;
  input_dcp: string | null;
  baseline_clock_period_ns: number | null;
  clock_period_ns: number | null;
  timing: TimingParsed;
  utilization: Record<string, number>;
  targets: Record<string, number>;
  analyze_result: Record<string, unknown>;
  start_region: Record<string, unknown>;
  report_files: Record<string, string>;
}

export interface AttemptSummary {
  attempt_num: number | null;
  skip_num: number | null;
  status: string;
  candidate_region: Record<string, unknown>;
  pblock_ranges: string | null;
  timing: TimingParsed;
  improved_vs_baseline: boolean | null;
  improved_vs_best_before: boolean | null;
  output_dcp: string | null;
  relative_output_dcp: string | null;
  error: string | null;
  stop_reason: string | null;
  leaderboard_rank: number | null;
  available_logs: string[];
}

export interface AttemptProgress {
  max_attempts: number | null;
  current_attempt: number | null;
  completed_attempts: number;
  skipped_candidates: number;
  successful_attempts: number;
}

export interface AIConfig {
  model: string;
  debug: boolean;
  continue_when_timing_met: boolean;
}

export interface HighFanoutConfig extends AIConfig {
  max_nets: number;
}

export interface AIStatusSummary {
  phase: string | null;
  model: string | null;
  iteration: number | null;
  llm_call_count: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost_usd: number | null;
  initial_timing: TimingParsed;
  best_timing: TimingParsed;
  planned_output_dcp: string | null;
  output_dcp: string | null;
  relative_output_dcp: string | null;
  output_ready: boolean;
  run_dir: string | null;
  token_report_present: boolean;
  last_message: string | null;
  error: string | null;
}

export interface AIOptimizationPlan {
  recommended_recipe: RecipeName;
  confidence: "low" | "medium" | "high";
  reason: string;
  expected_benefit: string;
  risks: string[];
  config: Record<string, unknown>;
  stop_conditions: string[];
}

export interface DemoMetricSnapshot {
  wns: number | null;
  tns: number | null;
  failing_endpoints: number | null;
  estimated_fmax_mhz: number | null;
}

export interface DemoStep {
  id: string;
  order: number;
  phase: string;
  title: string;
  status_label: string;
  duration_ms: number;
  assistant_text: string;
  tool_text: string;
  metrics: DemoMetricSnapshot;
  highlighted_nets: string[];
}

export interface DemoScenario {
  scenario_id: string;
  title: string;
  duration_ms: number;
  featured_run_id: string;
  design_name: string;
  design_part: string;
  input_dcp_label: string;
  initial_metrics: DemoMetricSnapshot;
  final_metrics: DemoMetricSnapshot;
  impact: ImpactSummary;
  steps: DemoStep[];
}

export interface RunSummary {
  run_id: string;
  dcp_id: string;
  dcp_name: string;
  dcp_path: string;
  source: RunSource;
  read_only: boolean;
  recipe: RecipeName | null;
  display_recipe: string;
  status: RunStatus;
  created_at: string;
  updated_at: string;
  current_stage: "analysis" | "optimization" | null;
  analysis_artifact_dir: string | null;
  optimization_artifact_dir: string | null;
  baseline: BaselineSummary | null;
  impact: ImpactSummary;
  available_actions: string[];
  best_attempt_num: number | null;
  best_output_dcp: string | null;
  best_output_artifact: string | null;
  best_timing: TimingParsed;
  attempt_count: number;
  skip_count: number;
  attempts: AttemptSummary[];
  progress: AttemptProgress;
  ai: AIStatusSummary | null;
}

export interface DcpRecord {
  dcp_id: string;
  kind: "upload" | "local";
  filename: string;
  path: string;
  size_bytes: number;
  registered_at: string;
}

export interface SettingsResponse {
  product_name: string;
  repo_root: string;
  data_root: string;
  python_executable: string;
  optimizer_script: string;
  optimizer_script_present: boolean;
  supported_recipes: RecipeName[];
  enabled_recipes: RecipeName[];
  openrouter_api_key_configured: boolean;
  ai_autopilot_ready: boolean;
  frontend_dist_present: boolean;
  java_home: string;
  startup_preflight_enabled: boolean;
  preflight: Record<string, unknown> | null;
}

export interface PatternHistory {
  strategy_outcomes: Record<string, {
    improved?: number;
    regressed?: number;
    no_change?: number;
  }>;
  endpoint_failures: Record<string, number>;
}

export interface TextPreview {
  path: string;
  size_bytes: number;
  truncated: boolean;
  preview_text: string;
}

export interface ArtifactEntry {
  path: string;
  kind: "file" | "directory";
  size_bytes: number;
}

export interface PblockConfig {
  pblock_name: string;
  apply_to: string;
  use_clock_regions: boolean;
  hard_pblock: boolean;
  place_directive: string;
  route_directive: string;
  target_lut: number | null;
  target_ff: number | null;
  target_dsp: number | null;
  target_bram: number | null;
  keep_placement: boolean;
  keep_routing: boolean;
  max_attempts: number;
  continue_after_improvement: boolean;
  seed_count: number;
  elite_count: number;
  random_seed: number;
}

export type OptimizeConfig = Record<string, unknown>;

export const defaultAiConfig: AIConfig = {
  model: "x-ai/grok-4.1-fast",
  debug: false,
  continue_when_timing_met: true
};

export const defaultHighFanoutConfig: HighFanoutConfig = {
  model: "x-ai/grok-4.1-fast",
  debug: false,
  continue_when_timing_met: true,
  max_nets: 5
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { cache: "no-store", ...init });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : body.message ?? detail;
    } catch {
      detail = response.statusText;
    }
    throw new Error(detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export async function getSettings(): Promise<SettingsResponse> {
  return request("/api/settings");
}

export async function getFeaturedDemoScenario(): Promise<DemoScenario> {
  return request("/api/demo/featured");
}

export async function getPatternHistory(): Promise<PatternHistory> {
  return request("/api/pattern-history");
}

export async function listDcps(): Promise<DcpRecord[]> {
  return request("/api/dcps");
}

export async function uploadDcp(file: File): Promise<DcpRecord> {
  const form = new FormData();
  form.append("file", file);
  return request("/api/dcps/upload", { method: "POST", body: form });
}

export async function registerLocalDcp(path: string): Promise<DcpRecord> {
  return request("/api/dcps/register-local", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path })
  });
}

export async function listRuns(): Promise<RunSummary[]> {
  return request("/api/runs");
}

export async function analyzeDcp(dcpId: string): Promise<RunSummary> {
  return request("/api/runs/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dcp_id: dcpId })
  });
}

export async function getRunSummary(runId: string): Promise<RunSummary> {
  return request(`/api/runs/${runId}/summary`);
}

export async function getRunBaseline(runId: string): Promise<BaselineSummary> {
  return request(`/api/runs/${runId}/baseline`);
}

export async function optimizeRun(
  runId: string,
  recipe: RecipeName,
  config: OptimizeConfig
): Promise<RunSummary> {
  return request(`/api/runs/${runId}/optimize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recipe, config })
  });
}

export async function optimizeAiRun(runId: string, config: AIConfig): Promise<RunSummary> {
  return request(`/api/runs/${runId}/ai/optimize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config })
  });
}

export async function requestAiPlan(runId: string): Promise<AIOptimizationPlan> {
  return request(`/api/runs/${runId}/ai/plan`, {
    method: "POST"
  });
}

export async function getAiStatus(runId: string): Promise<AIStatusSummary> {
  return request(`/api/runs/${runId}/ai/status`);
}

export async function cancelRun(runId: string): Promise<RunSummary> {
  return request(`/api/runs/${runId}/cancel`, { method: "POST" });
}

export async function listArtifacts(
  runId: string,
  stage?: "analysis" | "optimization"
): Promise<ArtifactEntry[]> {
  const suffix = stage ? `?stage=${stage}` : "";
  return request(`/api/runs/${runId}/artifacts${suffix}`);
}

export async function getAttemptLog(
  runId: string,
  attemptNum: number,
  logType: string,
  full = false
): Promise<TextPreview> {
  const params = new URLSearchParams();
  if (full) {
    params.set("full", "true");
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  return request(`/api/runs/${runId}/attempts/${attemptNum}/logs/${logType}${suffix}`);
}

export async function getWrapperLog(
  runId: string,
  stage: "analysis" | "optimization",
  logType: "stdout" | "stderr",
  full = false
): Promise<TextPreview> {
  const params = new URLSearchParams();
  if (full) {
    params.set("full", "true");
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  return request(`/api/runs/${runId}/wrapper-logs/${stage}/${logType}${suffix}`);
}

export const defaultPblockConfig: PblockConfig = {
  pblock_name: "pblock_auto_0",
  apply_to: "current_design",
  use_clock_regions: false,
  hard_pblock: false,
  place_directive: "Default",
  route_directive: "Default",
  target_lut: null,
  target_ff: null,
  target_dsp: null,
  target_bram: null,
  keep_placement: false,
  keep_routing: false,
  max_attempts: 12,
  continue_after_improvement: false,
  seed_count: 10,
  elite_count: 4,
  random_seed: 7
};

export function artifactDownloadUrl(
  runId: string,
  path: string,
  stage?: "analysis" | "optimization"
): string {
  const params = new URLSearchParams();
  if (stage) {
    params.set("stage", stage);
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  return `/api/runs/${runId}/artifacts/${encodeURI(path)}${suffix}`;
}

export function artifactZipUrl(runId: string, stage?: "analysis" | "optimization"): string {
  const params = new URLSearchParams();
  if (stage) {
    params.set("stage", stage);
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  return `/api/runs/${runId}/artifacts.zip${suffix}`;
}
