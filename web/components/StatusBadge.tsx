import type { Status } from "@/lib/types";

const STYLES: Record<Status, string> = {
  draft: "border border-rule text-muted",
  running: "bg-accent text-white",
  stopped: "bg-ink/10 text-ink",
};

const LABELS: Record<Status, string> = { draft: "Draft", running: "Running", stopped: "Stopped" };

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`rounded-full px-2.5 py-0.5 text-sm font-medium ${STYLES[status]}`}>{LABELS[status]}</span>;
}
