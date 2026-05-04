import { useEffect, useState, startTransition } from "react";
import { Link } from "react-router-dom";

import { RecipeCards } from "../components/RecipeCards";
import { StatusPill } from "../components/StatusPill";
import { listRuns, type RunSummary } from "../lib/api";
import { formatDate, formatDelta, formatInteger, recipeLabel, sourceLabel } from "../lib/format";

function impactValue(run: RunSummary): number {
  return run.impact.timing_margin_gain_ns ?? Number.NEGATIVE_INFINITY;
}

function compareImpact(left: RunSummary, right: RunSummary): number {
  const leftImpact = impactValue(left);
  const rightImpact = impactValue(right);
  if (leftImpact === rightImpact) {
    return 0;
  }
  return rightImpact > leftImpact ? 1 : -1;
}

export function DashboardPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const runRows = await listRuns();
        if (!cancelled) {
          startTransition(() => {
            setRuns(runRows);
            setError(null);
          });
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Dashboard request failed.");
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const sortedByImpact = [...runs].sort(compareImpact);
  const bestRun = sortedByImpact[0] ?? null;
  const activeRuns = runs.filter((run) => ["queued", "starting", "analyzing", "running"].includes(run.status));
  const timingClosedRuns = runs.filter((run) => run.impact.timing_closed);
  const bestEndpointReduction = runs.reduce<number | null>((best, run) => {
    const reduction = run.impact.failing_endpoint_reduction;
    if (reduction === null || reduction === undefined) {
      return best;
    }
    return best === null ? reduction : Math.max(best, reduction);
  }, null);

  return (
    <div className="page-grid">
      <section className="panel compact-hero">
        <div>
          <span className="eyebrow">DCP Forge</span>
          <h2>Timing wins, attempts, artifacts.</h2>
        </div>
        <div className="hero-actions">
          <Link className="button primary" to="/upload">
            Add DCP
          </Link>
          <Link className="button" to="/runs">
            View runs
          </Link>
        </div>
      </section>

      <section className="stats-grid">
        <article className="panel stat-card">
          <span>Best Timing Margin Gain</span>
          <strong>{bestRun ? `${formatDelta(bestRun.impact.timing_margin_gain_ns)} ns` : "Not available"}</strong>
          <small>{bestRun ? bestRun.dcp_name : "No runs yet"}</small>
        </article>
        <article className="panel stat-card">
          <span>Timing Closed Runs</span>
          <strong>{timingClosedRuns.length}</strong>
          <small>Negative slack to non-negative</small>
        </article>
        <article className="panel stat-card">
          <span>Active Runs</span>
          <strong>{activeRuns.length}</strong>
          <small>{runs.length} tracked total</small>
        </article>
        <article className="panel stat-card">
          <span>Best Endpoint Reduction</span>
          <strong>{bestEndpointReduction === null ? "Not available" : formatInteger(bestEndpointReduction)}</strong>
          <small>Failing endpoints removed</small>
        </article>
      </section>

      <section className="panel section-stack">
        <div className="section-heading section-heading-inline">
          <h2>Recipes</h2>
          <Link className="text-link" to="/runs">
            Open a run
          </Link>
        </div>
        <RecipeCards />
      </section>

      <section className="panel section-stack">
        <div className="section-heading section-heading-inline">
          <h2>Recent Wins</h2>
          <Link className="text-link" to="/runs">
            All runs
          </Link>
        </div>
        {error ? <p className="error-text">{error}</p> : null}
        {runs.length === 0 ? (
          <p className="muted">No runs yet.</p>
        ) : (
          <div className="table-shell">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Design</th>
                  <th>Recipe</th>
                  <th>Source</th>
                  <th>Gain</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {sortedByImpact.slice(0, 6).map((run) => (
                  <tr key={run.run_id}>
                    <td>
                      <Link to={`/runs/${run.run_id}`}>{run.dcp_name}</Link>
                      <div className="table-subtext">{formatDate(run.updated_at)}</div>
                    </td>
                    <td>{recipeLabel(run.recipe)}</td>
                    <td>{sourceLabel(run.source)}</td>
                    <td>{run.impact.timing_margin_gain_ns === null ? "Not available" : `${formatDelta(run.impact.timing_margin_gain_ns)} ns`}</td>
                    <td>
                      <div className="inline-status">
                        {run.impact.timing_closed ? <span className="outcome-badge outcome-good">Timing Closed</span> : null}
                        <StatusPill status={run.status} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
