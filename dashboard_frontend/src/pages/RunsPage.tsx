import { useEffect, useState, startTransition } from "react";
import { Link } from "react-router-dom";

import { StatusPill } from "../components/StatusPill";
import { listRuns, type RunSummary } from "../lib/api";
import { formatDate, formatDelta, formatMaybeNumber, recipeLabel, sourceLabel } from "../lib/format";

function sortRuns(runs: RunSummary[]): RunSummary[] {
  return [...runs].sort((left, right) => {
    const leftImpact = left.impact.timing_margin_gain_ns ?? Number.NEGATIVE_INFINITY;
    const rightImpact = right.impact.timing_margin_gain_ns ?? Number.NEGATIVE_INFINITY;
    if (leftImpact !== rightImpact) {
      return rightImpact > leftImpact ? 1 : -1;
    }
    return new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime();
  });
}

export function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void listRuns()
      .then((rows) => {
        if (!cancelled) {
          startTransition(() => setRuns(sortRuns(rows)));
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Failed to load runs.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="page-grid">
      <section className="panel section-stack">
        <div className="section-heading section-heading-inline">
          <h1>Runs</h1>
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
                  <th>Status</th>
                  <th>Timing Gain</th>
                  <th>Closed</th>
                  <th>Best WNS</th>
                  <th>Best DCP</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.run_id}>
                    <td>
                      <Link to={`/runs/${run.run_id}`}>{run.dcp_name}</Link>
                      <div className="table-subtext">{formatDate(run.updated_at)}</div>
                    </td>
                    <td>{recipeLabel(run.recipe)}</td>
                    <td>{sourceLabel(run.source)}</td>
                    <td>
                      <StatusPill status={run.status} />
                    </td>
                    <td>{run.impact.timing_margin_gain_ns === null ? "Not available" : `${formatDelta(run.impact.timing_margin_gain_ns)} ns`}</td>
                    <td>{run.impact.timing_closed ? <span className="outcome-badge outcome-good">Timing Closed</span> : "No"}</td>
                    <td>{formatMaybeNumber(run.best_timing.wns)}</td>
                    <td>{run.best_output_artifact ?? "Not available"}</td>
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
