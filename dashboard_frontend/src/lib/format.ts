import type { RecipeName, RunSource, RunStatus } from "./api";

export function formatMaybeNumber(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "Not available";
  }
  return value.toFixed(digits);
}

export function formatDelta(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "Not available";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}`;
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "Not available";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

export function formatInteger(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "Not available";
  }
  return Math.round(value).toString();
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

export function isActiveStatus(status: RunStatus): boolean {
  return ["queued", "starting", "analyzing", "running"].includes(status);
}

export function stageLabel(status: RunStatus): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "starting":
      return "Starting";
    case "analyzing":
      return "Analyzing";
    case "running":
      return "Running";
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
  }
}

export function recipeLabel(recipe: RecipeName | null | undefined): string {
  switch (recipe) {
    case "quick_timing_rescue":
      return "Quick Rescue";
    case "pblock_explorer":
      return "Pblock Explorer";
    case "high_fanout_optimization":
      return "Fanout Focus";
    case "ai_recommended_plan":
      return "AI Plan";
    case "ai_autopilot":
      return "Autopilot";
    default:
      return "Analyze";
  }
}

export function sourceLabel(source: RunSource): string {
  switch (source) {
    case "dashboard":
      return "Dashboard";
    case "imported_ai":
      return "Imported AI";
    case "imported_pblock":
      return "Imported Pblock";
  }
}
