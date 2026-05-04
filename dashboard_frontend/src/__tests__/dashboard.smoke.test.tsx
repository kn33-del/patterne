import { MemoryRouter, Route, Routes } from "react-router-dom";
import { act, render, screen } from "@testing-library/react";

import App from "../App";
import { RecipeCards } from "../components/RecipeCards";
import { formatMaybeNumber } from "../lib/format";
import { DashboardPage } from "../pages/DashboardPage";
import { RunDetailPage } from "../pages/RunDetailPage";

const summaryPayload = {
  run_id: "run-1",
  dcp_id: "dcp-1",
  dcp_name: "demo.dcp",
  dcp_path: "/tmp/demo.dcp",
  source: "dashboard",
  read_only: false,
  recipe: "pblock_explorer",
  display_recipe: "Pblock Explorer",
  status: "completed",
  created_at: "2026-04-30T12:00:00Z",
  updated_at: "2026-04-30T12:05:00Z",
  current_stage: null,
  analysis_artifact_dir: "/tmp/analysis",
  optimization_artifact_dir: "/tmp/optimization",
  baseline: {
    artifact_dir: "/tmp/analysis",
    input_dcp: "/tmp/demo.dcp",
    baseline_clock_period_ns: 5.0,
    clock_period_ns: 5.0,
    timing: {
      wns: -0.282,
      tns: -95.728,
      failing_endpoints: 302,
      estimated_fmax_mhz: 189.32
    },
    utilization: { LUT: 43, FF: 260 },
    targets: { LUT: 64, FF: 390 },
    analyze_result: {},
    start_region: {},
    report_files: {}
  },
  impact: {
    timing_margin_gain_ns: 0.391,
    tns_gain_ns: 85.628,
    failing_endpoint_reduction: 302,
    performance_uplift_mhz: 15.13,
    performance_uplift_pct: 7.99,
    timing_closed: true,
    improved: true
  },
  available_actions: [
    "quick_timing_rescue",
    "pblock_explorer",
    "high_fanout_optimization",
    "ai_recommended_plan",
    "ai_autopilot"
  ],
  best_attempt_num: 4,
  best_output_dcp: "/tmp/optimization/best.dcp",
  best_output_artifact: "best.dcp",
  best_timing: {
    wns: 0.109,
    tns: -10.1,
    failing_endpoints: 0,
    estimated_fmax_mhz: 204.45
  },
  attempt_count: 4,
  skip_count: 1,
  attempts: [
    {
      attempt_num: 4,
      skip_num: null,
      status: "success",
      candidate_region: {},
      pblock_ranges: "SLICE_X0Y0:SLICE_X1Y1",
      timing: {
        wns: 0.109,
        tns: -10.1,
        failing_endpoints: 0,
        estimated_fmax_mhz: 204.45
      },
      improved_vs_baseline: true,
      improved_vs_best_before: true,
      output_dcp: "/tmp/optimization/best.dcp",
      relative_output_dcp: "best.dcp",
      error: null,
      stop_reason: null,
      leaderboard_rank: 1,
      available_logs: ["route", "timing_summary"]
    }
  ],
  progress: {
    max_attempts: 12,
    current_attempt: 4,
    completed_attempts: 4,
    skipped_candidates: 1,
    successful_attempts: 1
  },
  ai: null
};

let activeSummaryPayload: any = summaryPayload;
let runsPayload: any[] = [summaryPayload];
const demoScenarioPayload = {
  scenario_id: "featured-route-cluster-bench",
  title: "route_cluster_bench timing recovery",
  duration_ms: 10550,
  featured_run_id: "dcp_optimizer_run-20260502_110423",
  design_name: "route_cluster_bench_top",
  design_part: "xc7z020clg484-1",
  input_dcp_label: "route_cluster_bench.dcp",
  initial_metrics: {
    wns: -0.282,
    tns: -6.732,
    failing_endpoints: 105,
    estimated_fmax_mhz: 189.322
  },
  final_metrics: {
    wns: 0.109,
    tns: 0,
    failing_endpoints: 0,
    estimated_fmax_mhz: 204.457
  },
  impact: summaryPayload.impact,
  steps: [
    {
      id: "load-checkpoint",
      order: 1,
      phase: "setup",
      title: "Load checkpoint",
      status_label: "Checkpoint opened",
      duration_ms: 700,
      assistant_text: "Starting from the loaded design and preserving the original checkpoint.",
      tool_text: "open_checkpoint /home/vik/route_cluster_bench.dcp",
      metrics: {
        wns: -0.282,
        tns: -6.732,
        failing_endpoints: 105,
        estimated_fmax_mhz: 189.322
      },
      highlighted_nets: []
    },
    {
      id: "identify-fanout",
      order: 2,
      phase: "analysis",
      title: "Identify critical high-fanout selector nets",
      status_label: "Targets selected",
      duration_ms: 950,
      assistant_text: "The violation is concentrated around selector fanout, so the optimization stays focused before widening scope.",
      tool_text: "get_critical_high_fanout_nets, analyze_critical_path_spread",
      metrics: {
        wns: -0.282,
        tns: -6.732,
        failing_endpoints: 105,
        estimated_fmax_mhz: 189.322
      },
      highlighted_nets: ["u_bench/sel_a[0]", "u_bench/sel_a[1]", "u_bench/sel_b[0]"]
    },
    {
      id: "signoff",
      order: 3,
      phase: "signoff",
      title: "Report timing closed",
      status_label: "Timing closed",
      duration_ms: 850,
      assistant_text: "The design is now timing clean, with the original failing endpoints removed and positive margin restored.",
      tool_text: "report_timing_summary, write_checkpoint route_c_optim.dcp",
      metrics: {
        wns: 0.109,
        tns: 0,
        failing_endpoints: 0,
        estimated_fmax_mhz: 204.457
      },
      highlighted_nets: []
    }
  ]
};

describe("dashboard smoke", () => {
  beforeEach(() => {
    activeSummaryPayload = summaryPayload;
    runsPayload = [summaryPayload];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.includes("/api/runs/") && input.includes("/summary")) {
          return new Response(JSON.stringify(activeSummaryPayload), { status: 200 });
        }
        if (input.includes("/api/runs/") && input.includes("/baseline")) {
          return new Response(JSON.stringify(activeSummaryPayload.baseline), { status: 200 });
        }
        if (input.includes("/api/demo/featured")) {
          return new Response(JSON.stringify(demoScenarioPayload), { status: 200 });
        }
        if (input.includes("/artifacts")) {
          return new Response(JSON.stringify([{ path: "best.dcp", kind: "file", size_bytes: 2048 }]), { status: 200 });
        }
        if (input.includes("/wrapper-logs")) {
          return new Response(JSON.stringify({ path: "stdout", size_bytes: 12, truncated: false, preview_text: "wrapper" }), { status: 200 });
        }
        if (input.includes("/attempts/")) {
          return new Response(JSON.stringify({ path: "route", size_bytes: 12, truncated: false, preview_text: "attempt log" }), { status: 200 });
        }
        if (input.includes("/api/runs") && !input.includes("/summary")) {
          return new Response(JSON.stringify(runsPayload), { status: 200 });
        }
        return new Response("{}", { status: 200 });
      })
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("renders recipe cards without MVP or roadmap wording", () => {
    render(<RecipeCards />);
    expect(screen.getByText("Quick Rescue")).toBeInTheDocument();
    expect(screen.queryByText(/roadmap/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/mvp/i)).not.toBeInTheDocument();
  });

  it("formats missing values as not available", () => {
    expect(formatMaybeNumber(null)).toBe("Not available");
  });

  it("shows timing margin gain on the dashboard instead of leading with raw fmax", async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    );

    expect(await screen.findByText("Best Timing Margin Gain")).toBeInTheDocument();
    expect(screen.getByText("+0.391 ns")).toBeInTheDocument();
  });

  it("shows timing closed on run detail when baseline WNS becomes positive", async () => {
    render(
      <MemoryRouter initialEntries={["/runs/run-1"]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunDetailPage />} />
        </Routes>
      </MemoryRouter>
    );

    expect(await screen.findAllByText("Timing Closed")).not.toHaveLength(0);
    expect(screen.getAllByText("Best DCP").length).toBeGreaterThan(0);
  });

  it("renders imported runs as read-only", async () => {
    activeSummaryPayload = {
      ...summaryPayload,
      source: "imported_ai",
      read_only: true,
      display_recipe: "Autopilot"
    };

    render(
      <MemoryRouter initialEntries={["/runs/run-1"]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunDetailPage />} />
        </Routes>
      </MemoryRouter>
    );

    expect(await screen.findByText("Read-only")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  });

  it("renders the standalone live optimization route without workspace nav and replays to completion", async () => {
    vi.useFakeTimers();

    render(
      <MemoryRouter initialEntries={["/optimize"]}>
        <App />
      </MemoryRouter>
    );

    expect(await screen.findByText("Live Optimization")).toBeInTheDocument();
    expect(screen.queryByText("Dashboard")).not.toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(13000);
    });

    expect(await screen.findByText("Timing recovered.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload Your DCP" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Workspace" })).toBeInTheDocument();
    expect(screen.queryByText(/demo/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/sample/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/roadmap/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/mvp/i)).not.toBeInTheDocument();
  });
});
