/**
 * Numbers and sentences for people who aren't statisticians (PRD §17, goal G7). Pure
 * functions: every sentence the dashboard shows is built (and tested) here, from a
 * template, never by a language model.
 */

import type { AnalysisType, Comparison, Direction, MetricKind, Verdict } from "./types";

/** 0.073 -> "+7.3%". */
export function signedPercent(value: number, digits = 1): string {
  const text = `${Math.abs(value * 100).toFixed(digits)}%`;
  if (Number(text.replace("%", "")) === 0) {
    return `0${digits > 0 ? `.${"0".repeat(digits)}` : ""}%`;
  }
  return value > 0 ? `+${text}` : `−${text}`; // a true minus sign
}

/** 0.1 -> "10.0%". */
export function percent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function pValue(p: number | null): string {
  if (p === null) {
    return "n/a";
  }
  return p < 0.001 ? "< 0.001" : p.toFixed(3);
}

export function count(value: number): string {
  return value.toLocaleString("en-US");
}

export const VERDICTS: Record<Verdict, { label: string; tone: "good" | "bad" | "neutral" | "warning" }> = {
  significant_win: { label: "Significant win", tone: "good" },
  significant_loss: { label: "Significant loss", tone: "bad" },
  not_significant: { label: "Not significant", tone: "neutral" },
  srm_untrustworthy: { label: "Can't be trusted: sample ratio mismatch", tone: "warning" },
  insufficient_data: { label: "Not enough data yet", tone: "neutral" },
};

export interface SentenceInput {
  variantName: string;
  metricName: string;
  metricKind: MetricKind;
  direction: Direction;
  analysisType: AnalysisType;
  alpha: number;
  comparison: Comparison;
}

/**
 * The result in one or two plain sentences, for example: "Big button increased purchase by
 * 4.1% (95% CI 1.2% to 7.0%). This is statistically significant."
 */
export function resultSentence(input: SentenceInput): string {
  const { comparison: c, variantName, metricName } = input;
  if (c.verdict === "srm_untrustworthy") {
    return (
      "These results can't be trusted: the variants didn't get the share of users they were " +
      "set up for, which usually means a bug in how users are assigned or logged."
    );
  }
  if (c.rel_lift === null || c.rel_ci_low === null || c.rel_ci_high === null) {
    return c.insufficient_data !== null
      ? `Not enough data to compare ${variantName} yet.`
      : `${variantName} can't be compared as a percentage change: the control's ${metricName} is zero.`;
  }
  const change =
    c.rel_lift > 0 ? "increased" : c.rel_lift < 0 ? "decreased" : "didn't change";
  const size = c.rel_lift === 0 ? "" : ` by ${signedPercent(Math.abs(c.rel_lift))
    .replace("+", "")}`;
  const level = `${Math.round((1 - input.alpha) * 100)}%`;
  const interval = input.analysisType === "sequential" ? `${level} always-valid CI` : `${level} CI`;
  const evidence = `(${interval} ${signedPercent(c.rel_ci_low)} to ${signedPercent(c.rel_ci_high)})`;
  const conclusion = c.significant
    ? "This is statistically significant."
    : input.analysisType === "sequential"
      ? "This is not statistically significant yet."
      : "This is not statistically significant.";
  return `${variantName} ${change} ${metricName}${size} ${evidence}. ${conclusion}`;
}

/**
 * Y-axis ticks for lifts between `low` and `high`: whole multiples of a round step (1%, 2%, 5%,
 * 10%, ...) that include 0, so each gridline sits exactly at the value its label shows.
 */
export function liftTicks(low: number, high: number): number[] {
  const from = Math.min(low, 0);
  const to = Math.max(high, 0);
  const steps = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10];
  const step = steps.find((s) => (to - from) / s <= 6) ?? 10;
  const first = Math.floor(from / step);
  const last = Math.ceil(to / step);
  return Array.from({ length: last - first + 1 }, (_, i) => (first + i) * step);
}

/** Days until each variant has `usersPerVariant` users. */
export function daysNeeded(
  usersPerVariant: number,
  variantCount: number,
  trafficShare: number,
  dailyUsers: number,
): number | null {
  if (dailyUsers <= 0 || trafficShare <= 0 || variantCount < 1) {
    return null;
  }
  return Math.ceil((usersPerVariant * variantCount) / (dailyUsers * trafficShare));
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }) + " UTC";
}

/** Whole days an experiment has run (until it stopped), at least 1 once started; null for a
 * draft. It reads the clock, so call it from server components, which render once per
 * request. */
export function daysRunning(
  experiment: { started_at: string | null; stopped_at: string | null },
  now = Date.now(),
): number | null {
  if (experiment.started_at === null) {
    return null;
  }
  const end = experiment.stopped_at ? new Date(experiment.stopped_at).getTime() : now;
  return Math.max(1, Math.ceil((end - new Date(experiment.started_at).getTime()) / 86_400_000));
}
