import { useEffect, useMemo, useState, startTransition } from "react";
import { useParams } from "react-router-dom";

import { RecipeCards } from "../components/RecipeCards";
import { PatternFinderDashboard } from "../components/PatternFinderDashboard";
import { StatusPill } from "../components/StatusPill";
import {
  artifactDownloadUrl,
  artifactZipUrl,
  cancelRun,
  defaultAiConfig,
  defaultHighFanoutConfig,
  defaultPblockConfig,
  getAttemptLog,
  getRunBaseline,
  getRunSummary,
  getWrapperLog,
  listArtifacts,
  OptimizeConfig,
  optimizeRun,
  requestAiPlan,
  type AIConfig,
  type AIOptimizationPlan,
  type ArtifactEntry,
  type HighFanoutConfig,
  type PblockConfig,
  type RecipeName,
  type RunSummary,
  type TextPreview
} from "../lib/api";
import {
  formatBytes,
  formatDate,
  formatDelta,
  formatInteger,
  formatMaybeNumber,
  formatPercent,
  isActiveStatus,
  recipeLabel,
  sourceLabel
} from "../lib/format";

const tabs = ["overview", "baseline", "optimization", "attempts", "logs", "artifacts", "ai-plan"] as const;
type TabKey = (typeof tabs)[number];
const recipeActions: RecipeName[] = [
  "quick_timing_rescue",
  "pblock_explorer",
  "high_fanout_optimization",
  "ai_recommended_plan",
  "ai_autopilot"
];

function filterLogText(preview: TextPreview | null, query: string): string {
  const text = preview?.preview_text ?? "Log not available.";
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) {
    return text;
  }
  const matches = text
    .split("\n")
    .filter((line) => line.toLowerCase().includes(trimmed))
    .join("\n");
  return matches || "No matching lines.";
}

export function RunDetailPage() {
  const params = useParams<{ runId: string }>();
  const runId = params.runId ?? "";
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [baseline, setBaseline] = useState<RunSummary["baseline"] | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactEntry[]>([]);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [selectedRecipe, setSelectedRecipe] = useState<RecipeName>("quick_timing_rescue");
  const [pblockConfig, setPblockConfig] = useState<PblockConfig>(defaultPblockConfig);
  const [aiConfig, setAiConfig] = useState<AIConfig>(defaultAiConfig);
  const [highFanoutConfig, setHighFanoutConfig] = useState<HighFanoutConfig>(defaultHighFanoutConfig);
  const [aiPlan, setAiPlan] = useState<AIOptimizationPlan | null>(null);
  const [wrapperLog, setWrapperLog] = useState<TextPreview | null>(null);
  const [attemptLog, setAttemptLog] = useState<TextPreview | null>(null);
  const [wrapperStage, setWrapperStage] = useState<"analysis" | "optimization">("analysis");
  const [wrapperLogType, setWrapperLogType] = useState<"stdout" | "stderr">("stdout");
  const [artifactStage, setArtifactStage] = useState<"analysis" | "optimization">("analysis");
  const [selectedAttempt, setSelectedAttempt] = useState<number | null>(null);
  const [selectedAttemptLogType, setSelectedAttemptLogType] = useState("route");
  const [fullWrapperLog, setFullWrapperLog] = useState(false);
  const [fullAttemptLog, setFullAttemptLog] = useState(false);
  const [logSearch, setLogSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const attempts = summary?.attempts ?? [];
  const aiSummary = summary?.ai ?? null;
  const availableRecipeActions = (summary?.available_actions ?? []).filter((action): action is RecipeName =>
    recipeActions.includes(action as RecipeName)
  );
  const attemptOptions = attempts.filter((attempt) => attempt.attempt_num !== null);
  const attemptRows = [...attempts].sort((left, right) => {
    const leftNum = left.attempt_num ?? left.skip_num ?? 0;
    const rightNum = right.attempt_num ?? right.skip_num ?? 0;
    return leftNum - rightNum;
  });
  const bestArtifactUrl =
    summary?.best_output_artifact && runId
      ? artifactDownloadUrl(runId, summary.best_output_artifact, summary.optimization_artifact_dir ? "optimization" : "analysis")
      : null;

  useEffect(() => {
    if (!runId) {
      return;
    }
    let cancelled = false;
    let intervalId: number | undefined;

    async function loadSummary() {
      try {
        const payload = await getRunSummary(runId);
        if (!cancelled) {
          startTransition(() => {
            setSummary(payload);
            setBaseline((current) => current ?? payload.baseline);
            setArtifactStage(payload.optimization_artifact_dir ? "optimization" : "analysis");
            setWrapperStage(payload.optimization_artifact_dir ? "optimization" : "analysis");
            if (payload.recipe) {
              setSelectedRecipe(payload.recipe);
            }
            if (payload.attempts.length > 0 && selectedAttempt === null) {
              const firstAttempt = payload.attempts.find((attempt) => attempt.attempt_num !== null);
              setSelectedAttempt(firstAttempt?.attempt_num ?? null);
            }
          });
          if (isActiveStatus(payload.status)) {
            intervalId = window.setTimeout(() => {
              void loadSummary();
            }, 2000);
          }
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Failed to load run summary.");
        }
      }
    }

    void loadSummary();
    return () => {
      cancelled = true;
      if (intervalId) {
        window.clearTimeout(intervalId);
      }
    };
  }, [runId, selectedAttempt]);

  useEffect(() => {
    if (!runId || activeTab !== "baseline") {
      return;
    }
    void getRunBaseline(runId)
      .then((payload) => {
        startTransition(() => setBaseline(payload));
      })
      .catch((loadError) => {
        setError(loadError instanceof Error ? loadError.message : "Failed to load baseline.");
      });
  }, [activeTab, runId]);

  useEffect(() => {
    if (!runId || activeTab !== "artifacts") {
      return;
    }
    void listArtifacts(runId, artifactStage)
      .then((payload) => {
        startTransition(() => setArtifacts(payload));
      })
      .catch((loadError) => {
        setError(loadError instanceof Error ? loadError.message : "Failed to load artifacts.");
      });
  }, [activeTab, artifactStage, runId]);

  useEffect(() => {
    if (!runId || activeTab !== "logs") {
      return;
    }
    void getWrapperLog(runId, wrapperStage, wrapperLogType, fullWrapperLog)
      .then((payload) => {
        startTransition(() => setWrapperLog(payload));
      })
      .catch(() => setWrapperLog(null));
  }, [activeTab, fullWrapperLog, runId, wrapperStage, wrapperLogType]);

  useEffect(() => {
    if (!runId || activeTab !== "logs" || selectedAttempt === null) {
      return;
    }
    void getAttemptLog(runId, selectedAttempt, selectedAttemptLogType, fullAttemptLog)
      .then((payload) => {
        startTransition(() => setAttemptLog(payload));
      })
      .catch(() => setAttemptLog(null));
  }, [activeTab, fullAttemptLog, runId, selectedAttempt, selectedAttemptLogType]);

  const filteredWrapperLog = useMemo(() => filterLogText(wrapperLog, logSearch), [wrapperLog, logSearch]);
  const filteredAttemptLog = useMemo(() => filterLogText(attemptLog, logSearch), [attemptLog, logSearch]);

  function updatePblockConfig<K extends keyof PblockConfig>(key: K, value: PblockConfig[K]) {
    startTransition(() => {
      setPblockConfig((current) => ({ ...current, [key]: value }));
    });
  }

  function updateAiConfig<K extends keyof AIConfig>(key: K, value: AIConfig[K]) {
    startTransition(() => {
      setAiConfig((current) => ({ ...current, [key]: value }));
    });
  }

  function updateHighFanoutConfig<K extends keyof HighFanoutConfig>(key: K, value: HighFanoutConfig[K]) {
    startTransition(() => {
      setHighFanoutConfig((current) => ({ ...current, [key]: value }));
    });
  }

  async function handleStartSelectedRecipe() {
    if (!runId || !summary) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      let payload: RunSummary;
      switch (selectedRecipe) {
        case "quick_timing_rescue":
        case "pblock_explorer":
          //payload = await optimizeRun(runId, selectedRecipe, pblockConfig);
          payload = await optimizeRun(runId, selectedRecipe, pblockConfig as any as  OptimizeConfig);
          break;
        case "high_fanout_optimization":
          payload = await optimizeRun(runId, selectedRecipe, highFanoutConfig as any as  OptimizeConfig);
          break;
        case "ai_autopilot":
          payload = await optimizeRun(runId, selectedRecipe, aiConfig as any as  OptimizeConfig);
          break;
        case "ai_recommended_plan":
          if (!aiPlan) {
            const plan = await requestAiPlan(runId);
            startTransition(() => {
              setAiPlan(plan);
              setActiveTab("ai-plan");
            });
            return;
          }
          payload = await optimizeRun(runId, aiPlan.recommended_recipe, aiPlan.config);
          break;
        default:
          return;
      }
      startTransition(() => setSummary(payload));
      setActiveTab(selectedRecipe === "ai_recommended_plan" ? "ai-plan" : "overview");
    } catch (optimizeError) {
      setError(optimizeError instanceof Error ? optimizeError.message : "Failed to start optimization.");
    } finally {
      setBusy(false);
    }
  }

  async function handleGeneratePlan() {
    if (!runId) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const payload = await requestAiPlan(runId);
      startTransition(() => {
        setAiPlan(payload);
        setSelectedRecipe("ai_recommended_plan");
      });
    } catch (planError) {
      setError(planError instanceof Error ? planError.message : "Failed to generate AI plan.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRunAiPlan() {
    if (!runId || !aiPlan) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const payload = await optimizeRun(runId, aiPlan.recommended_recipe, aiPlan.config);
      startTransition(() => setSummary(payload));
      setSelectedRecipe(aiPlan.recommended_recipe);
      setActiveTab("overview");
    } catch (planRunError) {
      setError(planRunError instanceof Error ? planRunError.message : "Failed to run AI plan.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (!runId) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const payload = await cancelRun(runId);
      startTransition(() => setSummary(payload));
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : "Failed to cancel run.");
    } finally {
      setBusy(false);
    }
  }

  if (!summary) {
    return (
      <div className="page-grid">
        <section className="panel section-stack">
          <h1>Run</h1>
          <p>{error ?? "Loading run summary..."}</p>
        </section>
      </div>
    );
  }

  const impact = summary.impact;

  return (
    <div className="page-grid">
      <section className="panel section-stack">
        <div className="section-heading">
          <div className="run-header-row">
            <div>
              <span className="eyebrow">{sourceLabel(summary.source)}</span>
              <h1>{summary.dcp_name}</h1>
              <div className="run-meta">
                <span>{summary.display_recipe}</span>
                <span>{formatDate(summary.updated_at)}</span>
                {summary.read_only ? <span>Read-only</span> : null}
              </div>
            </div>
            <div className="topline-actions">
              {impact.timing_closed ? <span className="outcome-badge outcome-good">Timing Closed</span> : null}
              <StatusPill status={summary.status} />
              {!summary.read_only && isActiveStatus(summary.status) ? (
                <button className="button danger" onClick={handleCancel} disabled={busy}>
                  Cancel
                </button>
              ) : null}
              {bestArtifactUrl ? (
                <a className="button primary" href={bestArtifactUrl}>
                  Best DCP
                </a>
              ) : null}
            </div>
          </div>
        </div>

        <div className="stats-grid">
          <article className="panel inset stat-card">
            <span>Timing Margin Gain</span>
            <strong>{impact.timing_margin_gain_ns === null ? "Not available" : `${formatDelta(impact.timing_margin_gain_ns)} ns`}</strong>
            <small>Baseline to best</small>
          </article>
          <article className="panel inset stat-card">
            <span>Performance Uplift</span>
            <strong>{impact.performance_uplift_mhz === null ? "Not available" : `${formatDelta(impact.performance_uplift_mhz)} MHz`}</strong>
            <small>{formatPercent(impact.performance_uplift_pct)}</small>
          </article>
          <article className="panel inset stat-card">
            <span>Endpoint Reduction</span>
            <strong>{impact.failing_endpoint_reduction === null ? "Not available" : formatInteger(impact.failing_endpoint_reduction)}</strong>
            <small>Failing endpoints removed</small>
          </article>
          <article className="panel inset stat-card">
            <span>Best WNS</span>
            <strong>{formatMaybeNumber(summary.best_timing.wns)}</strong>
            <small>{summary.best_attempt_num !== null ? `Attempt ${summary.best_attempt_num}` : summary.display_recipe}</small>
          </article>
        </div>

        {error ? <p className="error-text">{error}</p> : null}
        <div className="tab-row">
          {tabs.map((tab) => (
            <button
              key={tab}
              className={`tab-button ${activeTab === tab ? "tab-active" : ""}`}
              onClick={() => setActiveTab(tab)}
            >
              {tab.replace("-", " ")}
            </button>
          ))}
        </div>
      </section>

      {activeTab === "overview" ? (
        <>
          <section className="panel section-stack">
            <div className="comparison-grid">
              <article className="panel inset">
                <h3>Baseline</h3>
                <dl className="metric-list">
                  <div>
                    <dt>WNS</dt>
                    <dd>{formatMaybeNumber(summary.baseline?.timing.wns)}</dd>
                  </div>
                  <div>
                    <dt>TNS</dt>
                    <dd>{formatMaybeNumber(summary.baseline?.timing.tns)}</dd>
                  </div>
                  <div>
                    <dt>Fmax</dt>
                    <dd>{formatMaybeNumber(summary.baseline?.timing.estimated_fmax_mhz)} MHz</dd>
                  </div>
                  <div>
                    <dt>Failing endpoints</dt>
                    <dd>{formatInteger(summary.baseline?.timing.failing_endpoints)}</dd>
                  </div>
                </dl>
              </article>
              <article className="panel inset best-card">
                <h3>Best Result</h3>
                <dl className="metric-list">
                  <div>
                    <dt>WNS</dt>
                    <dd>{formatMaybeNumber(summary.best_timing.wns)}</dd>
                  </div>
                  <div>
                    <dt>TNS</dt>
                    <dd>{formatMaybeNumber(summary.best_timing.tns)}</dd>
                  </div>
                  <div>
                    <dt>Fmax</dt>
                    <dd>{formatMaybeNumber(summary.best_timing.estimated_fmax_mhz)} MHz</dd>
                  </div>
                  <div>
                    <dt>Failing endpoints</dt>
                    <dd>{formatInteger(summary.best_timing.failing_endpoints)}</dd>
                  </div>
                </dl>
              </article>
            </div>

            {aiSummary ? (
              <div className="table-shell">
                <table className="data-table">
                  <tbody>
                    <tr>
                      <th>Model</th>
                      <td>{aiSummary.model ?? "Not available"}</td>
                    </tr>
                    <tr>
                      <th>Phase</th>
                      <td>{aiSummary.phase ?? "Not available"}</td>
                    </tr>
                    <tr>
                      <th>Iterations</th>
                      <td>{aiSummary.iteration ?? "Not available"}</td>
                    </tr>
                    <tr>
                      <th>LLM calls</th>
                      <td>{aiSummary.llm_call_count}</td>
                    </tr>
                    <tr>
                      <th>Cost</th>
                      <td>{aiSummary.estimated_cost_usd === null ? "Not available" : `$${formatMaybeNumber(aiSummary.estimated_cost_usd, 4)}`}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            ) : null}
          </section>

          <PatternFinderDashboard />
        </>
      ) : null}

      {activeTab === "baseline" && baseline ? (
        <section className="panel section-stack">
          <div className="table-shell">
            <table className="data-table">
              <tbody>
                <tr>
                  <th>Input DCP</th>
                  <td>{baseline.input_dcp ?? "Not available"}</td>
                </tr>
                <tr>
                  <th>Clock period</th>
                  <td>{formatMaybeNumber(baseline.clock_period_ns ?? baseline.baseline_clock_period_ns)} ns</td>
                </tr>
                <tr>
                  <th>WNS</th>
                  <td>{formatMaybeNumber(baseline.timing.wns)}</td>
                </tr>
                <tr>
                  <th>TNS</th>
                  <td>{formatMaybeNumber(baseline.timing.tns)}</td>
                </tr>
                <tr>
                  <th>Fmax</th>
                  <td>{formatMaybeNumber(baseline.timing.estimated_fmax_mhz)} MHz</td>
                </tr>
                <tr>
                  <th>Failing endpoints</th>
                  <td>{formatInteger(baseline.timing.failing_endpoints)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {activeTab === "optimization" ? (
        <section className="panel section-stack">
          {summary.read_only ? (
            <p className="muted">Imported runs are view-only.</p>
          ) : (
            <>
              <RecipeCards onSelect={(recipe) => setSelectedRecipe(recipe)} activeRecipe={selectedRecipe} enabledRecipes={availableRecipeActions} />

              {(selectedRecipe === "quick_timing_rescue" || selectedRecipe === "pblock_explorer") ? (
                <div className="panel inset form-grid">
                  <div className="section-heading section-heading-inline">
                    <h3>{recipeLabel(selectedRecipe)}</h3>
                    <button className="button primary" onClick={handleStartSelectedRecipe} disabled={busy || isActiveStatus(summary.status)}>
                      Start
                    </button>
                  </div>
                  <div className="field-grid">
                    <label>
                      <span>Max attempts</span>
                      <input type="number" value={pblockConfig.max_attempts} onChange={(event) => updatePblockConfig("max_attempts", Number(event.target.value))} />
                    </label>
                    <label>
                      <span>Seed count</span>
                      <input type="number" value={pblockConfig.seed_count} onChange={(event) => updatePblockConfig("seed_count", Number(event.target.value))} />
                    </label>
                    <label>
                      <span>Elite count</span>
                      <input type="number" value={pblockConfig.elite_count} onChange={(event) => updatePblockConfig("elite_count", Number(event.target.value))} />
                    </label>
                    <label>
                      <span>Random seed</span>
                      <input type="number" value={pblockConfig.random_seed} onChange={(event) => updatePblockConfig("random_seed", Number(event.target.value))} />
                    </label>
                  </div>
                  <details className="details-panel">
                    <summary>Advanced</summary>
                    <div className="field-grid">
                      <label>
                        <span>Place directive</span>
                        <input value={pblockConfig.place_directive} onChange={(event) => updatePblockConfig("place_directive", event.target.value)} />
                      </label>
                      <label>
                        <span>Route directive</span>
                        <input value={pblockConfig.route_directive} onChange={(event) => updatePblockConfig("route_directive", event.target.value)} />
                      </label>
                    </div>
                    <div className="checkbox-row">
                      <label>
                        <input type="checkbox" checked={pblockConfig.use_clock_regions} onChange={(event) => updatePblockConfig("use_clock_regions", event.target.checked)} />
                        Use clock regions
                      </label>
                      <label>
                        <input type="checkbox" checked={pblockConfig.keep_placement} onChange={(event) => updatePblockConfig("keep_placement", event.target.checked)} />
                        Keep placement
                      </label>
                      <label>
                        <input type="checkbox" checked={pblockConfig.keep_routing} onChange={(event) => updatePblockConfig("keep_routing", event.target.checked)} />
                        Keep routing
                      </label>
                      <label>
                        <input
                          type="checkbox"
                          checked={pblockConfig.continue_after_improvement}
                          onChange={(event) => updatePblockConfig("continue_after_improvement", event.target.checked)}
                        />
                        Continue after improvement
                      </label>
                    </div>
                  </details>
                </div>
              ) : null}

              {selectedRecipe === "high_fanout_optimization" ? (
                <div className="panel inset form-grid">
                  <div className="section-heading section-heading-inline">
                    <h3>Fanout Focus</h3>
                    <button className="button primary" onClick={handleStartSelectedRecipe} disabled={busy || isActiveStatus(summary.status)}>
                      Start
                    </button>
                  </div>
                  <div className="field-grid">
                    <label>
                      <span>Max nets</span>
                      <input type="number" value={highFanoutConfig.max_nets} onChange={(event) => updateHighFanoutConfig("max_nets", Number(event.target.value))} />
                    </label>
                    <label>
                      <span>Model</span>
                      <input value={highFanoutConfig.model} onChange={(event) => updateHighFanoutConfig("model", event.target.value)} />
                    </label>
                  </div>
                  <div className="checkbox-row">
                    <label>
                      <input type="checkbox" checked={highFanoutConfig.debug} onChange={(event) => updateHighFanoutConfig("debug", event.target.checked)} />
                      Debug
                    </label>
                    <label>
                      <input
                        type="checkbox"
                        checked={highFanoutConfig.continue_when_timing_met}
                        onChange={(event) => updateHighFanoutConfig("continue_when_timing_met", event.target.checked)}
                      />
                      Continue when timing met
                    </label>
                  </div>
                </div>
              ) : null}

              {selectedRecipe === "ai_autopilot" ? (
                <div className="panel inset form-grid">
                  <div className="section-heading section-heading-inline">
                    <h3>Autopilot</h3>
                    <button className="button primary" onClick={handleStartSelectedRecipe} disabled={busy || isActiveStatus(summary.status)}>
                      Start
                    </button>
                  </div>
                  <div className="field-grid">
                    <label>
                      <span>Model</span>
                      <input value={aiConfig.model} onChange={(event) => updateAiConfig("model", event.target.value)} />
                    </label>
                    <label>
                      <span>Debug</span>
                      <select value={aiConfig.debug ? "on" : "off"} onChange={(event) => updateAiConfig("debug", event.target.value === "on")}>
                        <option value="off">Off</option>
                        <option value="on">On</option>
                      </select>
                    </label>
                  </div>
                  <div className="checkbox-row">
                    <label>
                      <input
                        type="checkbox"
                        checked={aiConfig.continue_when_timing_met}
                        onChange={(event) => updateAiConfig("continue_when_timing_met", event.target.checked)}
                      />
                      Continue when timing met
                    </label>
                  </div>
                </div>
              ) : null}
            </>
          )}
        </section>
      ) : null}

      {activeTab === "attempts" ? (
        <section className="panel section-stack">
          {aiSummary && attemptRows.length === 0 ? (
            <p className="muted">AI-driven runs track iterations through the AI status feed instead of pblock attempt files.</p>
          ) : attemptRows.length === 0 ? (
            <p className="muted">No attempts yet.</p>
          ) : (
            <div className="table-shell">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Rank</th>
                    <th>Attempt</th>
                    <th>Status</th>
                    <th>WNS</th>
                    <th>TNS</th>
                    <th>Failing endpoints</th>
                    <th>Gain</th>
                    <th>Output</th>
                  </tr>
                </thead>
                <tbody>
                  {attemptRows.map((attempt) => (
                    <tr key={`attempt-${attempt.attempt_num ?? attempt.skip_num ?? "na"}`}>
                      <td>{attempt.leaderboard_rank ?? "-"}</td>
                      <td>{attempt.attempt_num ?? `Skip ${attempt.skip_num ?? ""}`}</td>
                      <td>{attempt.status}</td>
                      <td>{formatMaybeNumber(attempt.timing.wns)}</td>
                      <td>{formatMaybeNumber(attempt.timing.tns)}</td>
                      <td>{formatInteger(attempt.timing.failing_endpoints)}</td>
                      <td>
                        {summary.baseline?.timing.wns === null || summary.baseline?.timing.wns === undefined || attempt.timing.wns === null
                          ? "Not available"
                          : `${formatDelta(attempt.timing.wns - (summary.baseline?.timing.wns ?? 0))} ns`}
                      </td>
                      <td>
                        {attempt.relative_output_dcp ? (
                          <a href={artifactDownloadUrl(runId, attempt.relative_output_dcp, "optimization")}>{attempt.relative_output_dcp}</a>
                        ) : (
                          "Not available"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}

      {activeTab === "logs" ? (
        <section className="panel section-stack">
          <div className="log-controls">
            <label>
              <span>Stage</span>
              <select value={wrapperStage} onChange={(event) => setWrapperStage(event.target.value as "analysis" | "optimization")}>
                <option value="analysis">analysis</option>
                {summary.optimization_artifact_dir ? <option value="optimization">optimization</option> : null}
              </select>
            </label>
            <label>
              <span>Wrapper</span>
              <select value={wrapperLogType} onChange={(event) => setWrapperLogType(event.target.value as "stdout" | "stderr")}>
                <option value="stdout">stdout</option>
                <option value="stderr">stderr</option>
              </select>
            </label>
            <label>
              <span>Search</span>
              <input className="text-input" value={logSearch} onChange={(event) => setLogSearch(event.target.value)} />
            </label>
            <label className="toggle-inline">
              <input type="checkbox" checked={fullWrapperLog} onChange={(event) => setFullWrapperLog(event.target.checked)} />
              Load full
            </label>
          </div>
          <pre className="log-panel">{filteredWrapperLog}</pre>

          {attemptOptions.length > 0 ? (
            <>
              <div className="log-controls">
                <label>
                  <span>Attempt</span>
                  <select value={selectedAttempt ?? ""} onChange={(event) => setSelectedAttempt(event.target.value ? Number(event.target.value) : null)}>
                    {attemptOptions.map((attempt) => (
                      <option key={attempt.attempt_num ?? "na"} value={attempt.attempt_num ?? ""}>
                        Attempt {attempt.attempt_num}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Attempt log</span>
                  <select value={selectedAttemptLogType} onChange={(event) => setSelectedAttemptLogType(event.target.value)}>
                    <option value="route">route</option>
                    <option value="place">place</option>
                    <option value="route_status">route_status</option>
                    <option value="timing_summary">timing_summary</option>
                    <option value="write_checkpoint">write_checkpoint</option>
                    <option value="create_and_apply">create_and_apply</option>
                    <option value="error">error</option>
                  </select>
                </label>
                <label className="toggle-inline">
                  <input type="checkbox" checked={fullAttemptLog} onChange={(event) => setFullAttemptLog(event.target.checked)} />
                  Load full
                </label>
              </div>
              <pre className="log-panel">{filteredAttemptLog}</pre>
            </>
          ) : null}
        </section>
      ) : null}

      {activeTab === "artifacts" ? (
        <section className="panel section-stack">
          <div className="topline-actions">
            <label>
              <span>Stage</span>
              <select value={artifactStage} onChange={(event) => setArtifactStage(event.target.value as "analysis" | "optimization")}>
                <option value="analysis">analysis</option>
                {summary.optimization_artifact_dir ? <option value="optimization">optimization</option> : null}
              </select>
            </label>
            <a className="button" href={artifactZipUrl(runId, artifactStage)}>
              Download ZIP
            </a>
          </div>
          <div className="table-shell">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Path</th>
                  <th>Kind</th>
                  <th>Size</th>
                </tr>
              </thead>
              <tbody>
                {[...artifacts]
                  .sort((left, right) => {
                    const leftBest = left.path === summary.best_output_artifact ? -1 : 0;
                    const rightBest = right.path === summary.best_output_artifact ? -1 : 0;
                    if (leftBest !== rightBest) {
                      return leftBest - rightBest;
                    }
                    return left.path.localeCompare(right.path);
                  })
                  .map((artifact) => (
                    <tr key={artifact.path} className={artifact.path === summary.best_output_artifact ? "best-row" : ""}>
                      <td>
                        {artifact.kind === "file" ? (
                          <a href={artifactDownloadUrl(runId, artifact.path, artifactStage)}>{artifact.path}</a>
                        ) : (
                          artifact.path
                        )}
                      </td>
                      <td>{artifact.kind}</td>
                      <td>{artifact.kind === "file" ? formatBytes(artifact.size_bytes) : "-"}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {activeTab === "ai-plan" ? (
        <section className="panel section-stack">
          <div className="topline-actions">
            {!summary.read_only ? (
              <button className="button primary" onClick={handleGeneratePlan} disabled={busy || isActiveStatus(summary.status)}>
                Generate Plan
              </button>
            ) : null}
            {aiPlan && !summary.read_only ? (
              <button className="button" onClick={handleRunAiPlan} disabled={busy || isActiveStatus(summary.status)}>
                Run Plan
              </button>
            ) : null}
          </div>
          {aiPlan ? (
            <div className="table-shell">
              <table className="data-table">
                <tbody>
                  <tr>
                    <th>Recipe</th>
                    <td>{recipeLabel(aiPlan.recommended_recipe)}</td>
                  </tr>
                  <tr>
                    <th>Confidence</th>
                    <td>{aiPlan.confidence}</td>
                  </tr>
                  <tr>
                    <th>Reason</th>
                    <td>{aiPlan.reason}</td>
                  </tr>
                  <tr>
                    <th>Expected benefit</th>
                    <td>{aiPlan.expected_benefit}</td>
                  </tr>
                  <tr>
                    <th>Risks</th>
                    <td>{aiPlan.risks.join(" | ") || "Not available"}</td>
                  </tr>
                  <tr>
                    <th>Stop conditions</th>
                    <td>{aiPlan.stop_conditions.join(" | ") || "Not available"}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          ) : aiSummary ? (
            <div className="table-shell">
              <table className="data-table">
                <tbody>
                  <tr>
                    <th>Phase</th>
                    <td>{aiSummary.phase ?? "Not available"}</td>
                  </tr>
                  <tr>
                    <th>Model</th>
                    <td>{aiSummary.model ?? "Not available"}</td>
                  </tr>
                  <tr>
                    <th>Last message</th>
                    <td>{aiSummary.last_message ?? "Not available"}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">Generate a plan to stage a recommended run.</p>
          )}
        </section>
      ) : null}
    </div>
  );
}
