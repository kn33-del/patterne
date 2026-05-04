import { useEffect, useMemo, useState, startTransition } from "react";
import { Link } from "react-router-dom";

import { getFeaturedDemoScenario, type DemoMetricSnapshot, type DemoScenario } from "../lib/api";
import { formatDelta, formatInteger, formatMaybeNumber } from "../lib/format";

const AUTO_START_DELAY_MS = 700;

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    update();
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", update);
      return () => media.removeEventListener("change", update);
    }
    media.addListener(update);
    return () => media.removeListener(update);
  }, []);

  return reduced;
}

function useAnimatedNumber(target: number | null, reduceMotion: boolean, durationMs = 360) {
  const [value, setValue] = useState<number | null>(target);

  useEffect(() => {
    if (target === null || reduceMotion) {
      setValue(target);
      return;
    }
    const startValue = value ?? target;
    const startTime = performance.now();
    let frameId = 0;

    const tick = (now: number) => {
      const progress = Math.min((now - startTime) / durationMs, 1);
      const eased = 1 - (1 - progress) * (1 - progress);
      setValue(startValue + (target - startValue) * eased);
      if (progress < 1) {
        frameId = window.requestAnimationFrame(tick);
      }
    };

    frameId = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frameId);
  }, [durationMs, reduceMotion, target]);

  return value;
}

function metricLabel(value: number | null, unit: string, digits = 3): string {
  if (value === null) {
    return "Not available";
  }
  return `${formatMaybeNumber(value, digits)} ${unit}`.trim();
}

function metricTarget(scenario: DemoScenario, activeStep: DemoScenario["steps"][number] | null, completed: boolean): DemoMetricSnapshot {
  if (completed) {
    return scenario.final_metrics;
  }
  if (activeStep) {
    return activeStep.metrics;
  }
  return scenario.initial_metrics;
}

export function LiveOptimizationPage() {
  const [scenario, setScenario] = useState<DemoScenario | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeStepIndex, setActiveStepIndex] = useState(-1);
  const [completed, setCompleted] = useState(false);
  const [replayToken, setReplayToken] = useState(0);
  const reduceMotion = usePrefersReducedMotion();

  useEffect(() => {
    let cancelled = false;
    void getFeaturedDemoScenario()
      .then((payload) => {
        if (!cancelled) {
          startTransition(() => {
            setScenario(payload);
            setError(null);
          });
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Optimization session is unavailable.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!scenario) {
      return;
    }

    setActiveStepIndex(-1);
    setCompleted(false);

    if (reduceMotion) {
      startTransition(() => {
        setActiveStepIndex(scenario.steps.length - 1);
        setCompleted(true);
      });
      return;
    }

    const timeouts: number[] = [];
    let elapsed = AUTO_START_DELAY_MS;
    for (const [index, step] of scenario.steps.entries()) {
      timeouts.push(
        window.setTimeout(() => {
          startTransition(() => setActiveStepIndex(index));
        }, elapsed)
      );
      elapsed += step.duration_ms;
    }
    timeouts.push(
      window.setTimeout(() => {
        startTransition(() => setCompleted(true));
      }, elapsed)
    );

    return () => {
      for (const timeoutId of timeouts) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [reduceMotion, replayToken, scenario]);

  const activeStep = scenario && activeStepIndex >= 0 ? scenario.steps[Math.min(activeStepIndex, scenario.steps.length - 1)] : null;
  const visibleSteps = scenario ? scenario.steps.slice(0, completed ? scenario.steps.length : Math.max(activeStepIndex + 1, 0)) : [];
  const targetMetrics = scenario ? metricTarget(scenario, activeStep, completed) : null;
  const animatedWns = useAnimatedNumber(targetMetrics?.wns ?? null, reduceMotion);
  const animatedTns = useAnimatedNumber(targetMetrics?.tns ?? null, reduceMotion);
  const animatedFailingEndpoints = useAnimatedNumber(targetMetrics?.failing_endpoints ?? null, reduceMotion);
  const animatedFmax = useAnimatedNumber(targetMetrics?.estimated_fmax_mhz ?? null, reduceMotion, 420);
  const progressPercent = scenario
    ? completed
      ? 100
      : activeStepIndex < 0
        ? 0
        : ((activeStepIndex + 1) / scenario.steps.length) * 100
    : 0;

  const outcomeCards = useMemo(
    () =>
      scenario
        ? [
            {
              label: "Timing Closed",
              value: scenario.impact.timing_closed ? "Yes" : "No",
              tone: scenario.impact.timing_closed ? "good" : "neutral"
            },
            {
              label: "Timing Margin Gain",
              value: `${formatDelta(scenario.impact.timing_margin_gain_ns)} ns`,
              tone: "good"
            },
            {
              label: "Endpoint Reduction",
              value: formatInteger(scenario.impact.failing_endpoint_reduction),
              tone: "good"
            },
            {
              label: "TNS Recovery",
              value: `${formatDelta(scenario.impact.tns_gain_ns)} ns`,
              tone: "good"
            },
            {
              label: "Performance Uplift",
              value: scenario.impact.performance_uplift_mhz === null ? "Not available" : `${formatDelta(scenario.impact.performance_uplift_mhz)} MHz`,
              tone: "neutral"
            }
          ]
        : [],
    [scenario]
  );

  return (
    <div className="live-opt-shell">
      <div className="live-opt-frame">
        <header className="live-opt-header">
          <div className="live-opt-heading">
            <span className="eyebrow">DCP Forge</span>
            <h1>Live Optimization</h1>
            <p>AI-guided timing recovery is in progress.</p>
          </div>
          <div className="live-opt-actions">
            {activeStep ? <span className="badge">{activeStep.status_label}</span> : <span className="badge">Preparing session</span>}
            <button className="button" type="button" onClick={() => setReplayToken((current) => current + 1)}>
              Replay
            </button>
          </div>
        </header>

        {error ? (
          <section className="panel section-stack">
            <h2>Optimization session unavailable</h2>
            <p className="error-text">{error}</p>
          </section>
        ) : null}

        {!scenario && !error ? (
          <section className="panel section-stack">
            <h2>Preparing optimization session</h2>
            <p className="muted">Loading the featured timing recovery flow.</p>
          </section>
        ) : null}

        {scenario ? (
          <>
            <section className="live-opt-grid">
              <div className="live-opt-column">
                <article className="panel live-design-card">
                  <div className="section-heading">
                    <span className="eyebrow">Loaded Design</span>
                    <h2>{scenario.design_name}</h2>
                  </div>
                  <dl className="metric-list live-design-list">
                    <div>
                      <dt>Input DCP</dt>
                      <dd>{scenario.input_dcp_label}</dd>
                    </div>
                    <div>
                      <dt>Part</dt>
                      <dd>{scenario.design_part}</dd>
                    </div>
                    <div>
                      <dt>Step</dt>
                      <dd>
                        {scenario.steps.length > 0 && activeStepIndex >= 0 ? `${Math.min(activeStepIndex + 1, scenario.steps.length)}/${scenario.steps.length}` : `0/${scenario.steps.length}`}
                      </dd>
                    </div>
                  </dl>
                </article>

                <div className="live-metric-grid">
                  <article className="panel live-metric-card">
                    <span>Current WNS</span>
                    <strong>{metricLabel(animatedWns, "ns")}</strong>
                  </article>
                  <article className="panel live-metric-card">
                    <span>Current TNS</span>
                    <strong>{metricLabel(animatedTns, "ns")}</strong>
                  </article>
                  <article className="panel live-metric-card">
                    <span>Failing Endpoints</span>
                    <strong>{animatedFailingEndpoints === null ? "Not available" : formatInteger(animatedFailingEndpoints)}</strong>
                  </article>
                  <article className="panel live-metric-card">
                    <span>Estimated Fmax</span>
                    <strong>{metricLabel(animatedFmax, "MHz")}</strong>
                  </article>
                </div>
              </div>

              <section className="panel live-timeline-panel">
                <div className="section-heading">
                  <span className="eyebrow">Progress Rail</span>
                  <h2>Implementation flow</h2>
                </div>
                <div className="live-progress-track" aria-hidden="true">
                  <span className="live-progress-fill" style={{ width: `${progressPercent}%` }} />
                </div>
                <ol className="live-step-list">
                  {scenario.steps.map((step, index) => {
                    const state =
                      completed || index < activeStepIndex ? "done" : index === activeStepIndex ? "active" : "upcoming";
                    return (
                      <li key={step.id} className={`live-step-item live-step-${state}`}>
                        <span className="live-step-index">{step.order}</span>
                        <div className="live-step-copy">
                          <strong>{step.title}</strong>
                          <small>{state === "active" ? step.status_label : step.phase}</small>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </section>

              <section className="panel live-transcript-panel">
                <div className="section-heading">
                  <span className="eyebrow">Agent Feed</span>
                  <h2>AI decisions</h2>
                </div>
                <div className="live-transcript-list">
                  {visibleSteps.map((step, index) => (
                    <article
                      key={step.id}
                      className={`live-transcript-item ${index === activeStepIndex && !completed ? "live-transcript-active" : "live-transcript-complete"}`}
                    >
                      <div className="live-transcript-topline">
                        <span className="badge">{step.phase}</span>
                        <small>{step.status_label}</small>
                      </div>
                      <p>{step.assistant_text}</p>
                      <code>{step.tool_text}</code>
                      {step.highlighted_nets.length > 0 ? (
                        <div className="live-net-list">
                          {step.highlighted_nets.map((netName) => (
                            <span key={netName} className="live-net-chip">
                              {netName}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </article>
                  ))}
                </div>
              </section>
            </section>

            <section className={`panel live-outcome-panel ${completed ? "live-outcome-ready" : ""}`}>
              <div className="live-outcome-heading">
                <div>
                  <span className="eyebrow">Outcome</span>
                  <h2>{completed ? "Timing recovered." : "Optimization is still converging."}</h2>
                </div>
                <div className="live-opt-actions">
                  <Link className="button primary" to="/upload">
                    Upload Your DCP
                  </Link>
                  <Link className="button" to="/">
                    Open Workspace
                  </Link>
                </div>
              </div>
              <div className="live-outcome-grid">
                {outcomeCards.map((card) => (
                  <article key={card.label} className={`live-outcome-card live-outcome-${card.tone}`}>
                    <span>{card.label}</span>
                    <strong>{completed ? card.value : "In progress"}</strong>
                  </article>
                ))}
              </div>
            </section>
          </>
        ) : null}
      </div>
    </div>
  );
}
