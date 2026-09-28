import { VERDICTS } from "@/lib/format";
import type { Verdict } from "@/lib/types";

const TONES = {
  good: { className: "bg-win-soft text-win", icon: "▲" },
  bad: { className: "bg-loss-soft text-loss", icon: "▼" },
  neutral: { className: "bg-paper text-muted border border-rule", icon: "●" },
  warning: { className: "bg-alert-soft text-alert", icon: "!" },
} as const;

/** The verdict as a label with an icon: color is never the only signal. */
export function VerdictChip({ verdict }: { verdict: Verdict }) {
  const { label, tone } = VERDICTS[verdict];
  const style = TONES[tone];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-sm font-medium ${style.className}`}>
      <span aria-hidden="true">{style.icon}</span>
      {label}
    </span>
  );
}
