import { useMemo, useState } from "react";

type StrategyOutcome = {
  improved?: number;
  regressed?: number;
  no_change?: number;
};

type PatternHistory = {
  strategy_outcomes: Record<string, StrategyOutcome>;
  endpoint_failures: Record<string, number>;
};

const initialPatternHistory: PatternHistory = {
  strategy_outcomes: {
    fanout: { improved: 1, regressed: 2 },
    pblock: { improved: 3, regressed: 0 },
    phys_opt: { improved: 0, regressed: 1, no_change: 2 }
  },
  endpoint_failures: {
    "endpoint/path/X": 3,
    "endpoint/path/Y": 1,
    "cmac_port[0].cmac_subsystem_inst/tx_slice_inst/full_mode.axis_tdata_reg[1][232]/CE": 4
  }
};

function formatStrategyName(strategy: string): string {
  return strategy.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function PatternFinderDashboard() {
  const [history] = useState<PatternHistory>(initialPatternHistory);

  const strategies = useMemo(
    () =>
      Object.entries(history.strategy_outcomes).sort(([leftName, left], [rightName, right]) => {
        const leftTotal = (left.improved ?? 0) + (left.regressed ?? 0) + (left.no_change ?? 0);
        const rightTotal = (right.improved ?? 0) + (right.regressed ?? 0) + (right.no_change ?? 0);
        return rightTotal - leftTotal || leftName.localeCompare(rightName);
      }),
    [history.strategy_outcomes]
  );

  const endpoints = useMemo(
    () => Object.entries(history.endpoint_failures).sort(([, leftCount], [, rightCount]) => rightCount - leftCount),
    [history.endpoint_failures]
  );

  return (
    <section className="panel section-stack pattern-widget">
      <div className="section-heading section-heading-inline">
        <div>
          <span className="eyebrow">Agent Memory</span>
          <h2>Pattern Finder History</h2>
        </div>
        <span className="badge">history.json</span>
      </div>

      <div className="pattern-grid">
        <article className="pattern-section">
          <div className="pattern-section-heading">
            <h3>Strategy Performance</h3>
            <span>{strategies.length} strategies</span>
          </div>

          <div className="pattern-strategy-list">
            {strategies.map(([strategy, outcome]) => {
              const improved = outcome.improved ?? 0;
              const regressed = outcome.regressed ?? 0;
              const noChange = outcome.no_change ?? 0;
              const total = improved + regressed + noChange;
              const shouldSkip = regressed >= 2;

              return (
                <div className="pattern-strategy-row" key={strategy}>
                  <div className="pattern-strategy-topline">
                    <strong>{formatStrategyName(strategy)}</strong>
                    {shouldSkip ? <span className="pattern-warning-badge">Skipped by Agent Rule</span> : null}
                  </div>
                  <div className="pattern-metrics">
                    <span className="pattern-count pattern-count-good">{improved} improved</span>
                    <span className="pattern-count pattern-count-bad">{regressed} regressed</span>
                    <span className="pattern-count pattern-count-neutral">{noChange} no change</span>
                  </div>
                  <div className="pattern-bar" aria-label={`${strategy} strategy outcome distribution`}>
                    <span className="pattern-bar-good" style={{ flexGrow: improved || 0.1 }} />
                    <span className="pattern-bar-bad" style={{ flexGrow: regressed || 0.1 }} />
                    <span className="pattern-bar-neutral" style={{ flexGrow: noChange || 0.1 }} />
                  </div>
                  <small>{total} recorded outcomes</small>
                </div>
              );
            })}
          </div>
        </article>

        <article className="pattern-section">
          <div className="pattern-section-heading">
            <h3>Endpoint Failure Hotspots</h3>
            <span>{endpoints.length} endpoints</span>
          </div>

          <ol className="pattern-endpoint-list">
            {endpoints.map(([endpoint, count], index) => (
              <li key={endpoint}>
                <span className="pattern-rank">{index + 1}</span>
                <div>
                  <strong>{endpoint}</strong>
                  <span>{count} failed {count === 1 ? "run" : "runs"}</span>
                </div>
              </li>
            ))}
          </ol>
        </article>
      </div>
    </section>
  );
}
