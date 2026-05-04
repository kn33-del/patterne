import { stageLabel } from "../lib/format";
import type { RunStatus } from "../lib/api";

export function StatusPill({ status }: { status: RunStatus }) {
  return <span className={`status-pill status-${status}`}>{stageLabel(status)}</span>;
}
