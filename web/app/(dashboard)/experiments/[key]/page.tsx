import Link from "next/link";
import { notFound } from "next/navigation";

import { StatusBadge } from "@/components/StatusBadge";
import { VerdictChip } from "@/components/VerdictChip";
import { api, ApiError } from "@/lib/api";
import {
  count,
  daysRunning,
  formatDate,
  percent,
  pValue,
  resultSentence,
  signedPercent,
} from "@/lib/format";
import type { Change, ExperimentDetail, Metric, Results, SnapshotData } from "@/lib/types";

import { Controls } from "./Controls";
import { LiftChart, type LiftPoint } from "./LiftChart";
import { SrmGate } from "./SrmGate";

export default async function ExperimentPage({ params, searchParams }: PageProps<"/experiments/[key]">) {
  const { key } = await params;
  const { metric: requested } = await searchParams;
  let experiment: ExperimentDetail;
  try {
    experiment = await api.getExperiment(key);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    throw error;
  }
  const metrics = await api.listMetrics();
  const primary = experiment.metrics.find((m) => m.role === "primary");
  const chosen =
    experiment.metrics.find((m) => m.metric_key === requested)?.metric_key ?? primary?.metric_key;
  const results =
    experiment.status !== "draft" && chosen !== undefined ? await api.results(key, chosen) : null;
  const days = daysRunning(experiment);

  return (
    <>
      <header className="space-y-4 border-b border-rule pb-6">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">{experiment.name}</h1>
          <StatusBadge status={experiment.status} />
        </div>
        <p className="flex flex-wrap gap-x-4 text-muted">
          <span>Key {experiment.key}</span>
          {experiment.started_at && <span>Started {formatDate(experiment.started_at)}</span>}
          {days !== null && experiment.status === "running" && <span>Day {days}</span>}
          {experiment.stopped_at && <span>Stopped {formatDate(experiment.stopped_at)}</span>}
        </p>
        <Controls experimentKey={experiment.key} status={experiment.status} />
      </header>

      {results !== null && (
        <section aria-labelledby="results-title" className="border-b border-rule py-8">
          <h2 id="results-title" className="text-lg font-semibold">
            Results
          </h2>
          <MetricTabs experiment={experiment} metrics={metrics} chosen={chosen} />
          <ResultsView experiment={experiment} metrics={metrics} results={results} />
        </section>
      )}

      <Protocol experiment={experiment} metrics={metrics} />
      <Changelog changes={experiment.changes} />
    </>
  );
}

function metricName(metrics: Metric[], key: string): string {
  return metrics.find((m) => m.key === key)?.name ?? key;
}

function MetricTabs({ experiment, metrics, chosen }: { experiment: ExperimentDetail; metrics: Metric[]; chosen: string | undefined }) {
  if (experiment.metrics.length < 2) {
    return null;
  }
  return (
    <nav aria-label="Metric" className="mt-3 flex flex-wrap gap-2">
      {experiment.metrics.map((m) => (
        <Link
          key={m.metric_key}
          href={`?metric=${encodeURIComponent(m.metric_key)}`}
          aria-current={m.metric_key === chosen ? "page" : undefined}
          className={`rounded-md px-3 py-1.5 text-sm ${m.metric_key === chosen ? "bg-accent-soft font-medium text-accent" : "border border-rule text-muted"}`}
        >
          {metricName(metrics, m.metric_key)} ({m.role})
        </Link>
      ))}
    </nav>
  );
}

function ResultsView({ experiment, metrics, results }: { experiment: ExperimentDetail; metrics: Metric[]; results: Results }) {
  if (results.latest === null) {
    return (
      <p className="mt-4 text-muted">
        No results yet. They&apos;re computed every 5 minutes; use &ldquo;Update results now&rdquo; to compute them right away.
      </p>
    );
  }
  const { data, computed_at } = results.latest;
  const body = <ResultsBody experiment={experiment} metrics={metrics} data={data} results={results} />;
  return (
    <div className="mt-4 space-y-6">
      <p className="text-sm text-muted">
        {count(data.users)} users, updated {formatDate(computed_at)}
        {experiment.stopped_at !== null &&
          (Date.parse(computed_at) >= Date.parse(experiment.stopped_at)
            ? ". Final results: only events up to the stop count."
            : ". The final results are computed within 5 minutes of the stop.")}
      </p>
      {data.srm.flagged ? <SrmGate pValue={pValue(data.srm.p_value)}>{body}</SrmGate> : body}
    </div>
  );
}

function ResultsBody({ experiment, metrics, data, results }: { experiment: ExperimentDetail; metrics: Metric[]; data: SnapshotData; results: Results }) {
  const sequential = data.analysis_type === "sequential";
  const level = `${String(Math.round((1 - data.alpha) * 100))}%`;
  const intervalName = sequential ? `${level} always-valid confidence interval` : `${level} confidence interval`;
  const variantName = (key: string) => experiment.variants.find((v) => v.key === key)?.name ?? key;
  const conversion = data.metric_kind === "conversion";
  return (
    <div className="space-y-8">
      {data.comparisons.map((comparison) => (
        <article key={comparison.variant_key} className="space-y-3">
          <p className="max-w-3xl font-serif text-2xl leading-snug">
            {resultSentence({
              variantName: variantName(comparison.variant_key),
              metricName: metricName(metrics, data.metric_key),
              metricKind: data.metric_kind,
              direction: data.direction,
              analysisType: data.analysis_type,
              alpha: data.alpha,
              comparison,
            })}
          </p>
          <p className="flex flex-wrap items-center gap-3 text-sm">
            <VerdictChip verdict={comparison.verdict} />
            <span className="text-muted">
              {sequential ? "Always-valid p-value" : "p-value"} {pValue(comparison.p_value)}
            </span>
          </p>
        </article>
      ))}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="border-b border-rule text-muted">
            <tr>
              <th className="py-2 pr-4 font-medium">Variant</th>
              <th className="py-2 pr-4 text-right font-medium">Users</th>
              <th className="py-2 pr-4 text-right font-medium">{conversion ? "Converted" : "Mean per user"}</th>
              {conversion && <th className="py-2 pr-4 text-right font-medium">Rate</th>}
              <th className="py-2 pr-4 text-right font-medium">Lift vs control ({sequential ? `${level} always-valid CI` : `${level} CI`})</th>
              <th className="py-2 pr-4 text-right font-medium">{sequential ? "Always-valid p" : "p"}</th>
              <th className="py-2 font-medium">Verdict</th>
            </tr>
          </thead>
          <tbody className="tabular">
            {data.variants.map((variant) => {
              const comparison = data.comparisons.find((c) => c.variant_key === variant.key);
              const rate = variant.users > 0 ? (conversion ? (variant.conversions ?? 0) : variant.total) / variant.users : null;
              return (
                <tr key={variant.key} className="border-b border-rule">
                  <td className="py-2 pr-4 font-sans">
                    {variantName(variant.key)}
                    {variant.is_control && <span className="ml-2 text-muted">control</span>}
                  </td>
                  <td className="py-2 pr-4 text-right">{count(variant.users)}</td>
                  <td className="py-2 pr-4 text-right">
                    {conversion ? count(variant.conversions ?? 0) : rate === null ? "–" : rate.toFixed(2)}
                  </td>
                  {conversion && <td className="py-2 pr-4 text-right">{rate === null ? "–" : percent(rate, 2)}</td>}
                  <td className="py-2 pr-4 text-right">
                    {variant.is_control || !comparison || comparison.rel_lift === null
                      ? "–"
                      : `${signedPercent(comparison.rel_lift)} (${comparison.rel_ci_low === null ? "?" : signedPercent(comparison.rel_ci_low)} to ${comparison.rel_ci_high === null ? "?" : signedPercent(comparison.rel_ci_high)})`}
                  </td>
                  <td className="py-2 pr-4 text-right">{comparison ? pValue(comparison.p_value) : "–"}</td>
                  <td className="py-2 font-sans">{comparison ? <VerdictChip verdict={comparison.verdict} /> : null}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {data.comparisons.map((comparison) => {
        const points: LiftPoint[] = results.series.flatMap((point) => {
          const c = point.comparisons.find((x) => x.variant_key === comparison.variant_key);
          return c && c.rel_lift !== null && c.rel_ci_low !== null && c.rel_ci_high !== null
            ? [{ time: new Date(point.computed_at).getTime(), lift: c.rel_lift, band: [c.rel_ci_low, c.rel_ci_high] as [number, number] }]
            : [];
        });
        return points.length < 2 ? null : (
          <div key={comparison.variant_key}>
            <h3 className="mb-3 font-medium">{variantName(comparison.variant_key)}: lift over time</h3>
            <LiftChart points={points} intervalName={intervalName} />
          </div>
        );
      })}

      <ul className="space-y-1 text-sm text-muted">
        <li>
          {count(data.conflicted_users)} {data.conflicted_users === 1 ? "user" : "users"} saw more than one variant and{" "}
          {data.conflicted_users === 1 ? "is" : "are"} left out of the analysis.
        </li>
        <li>
          {count(data.mismatched_users)} {data.mismatched_users === 1 ? "exposure" : "exposures"} disagreed with the
          server&apos;s own assignment (usually a sign of an SDK bug).
        </li>
        {sequential ? (
          <li>
            Always-valid results stay correct however often you check them. The percentage interval divides the
            absolute interval by the control&apos;s rate, which is an approximation.
          </li>
        ) : (
          experiment.status === "running" && (
            <li className="text-alert">
              {/* 24.6%: the aa_peeking row of docs/results/summary.md (make simulate, seed 42). */}
              Fixed-horizon results are only valid once, at the planned sample size. Checking early and stopping at the
              first significant result raised false positives from 5% to 24.6% in this project&apos;s simulation (20 checks).
            </li>
          )
        )}
      </ul>
    </div>
  );
}

function Protocol({ experiment, metrics }: { experiment: ExperimentDetail; metrics: Metric[] }) {
  const locked = experiment.status !== "draft";
  return (
    <section aria-labelledby="protocol-title" className="border-b border-rule py-8">
      <h2 id="protocol-title" className="text-lg font-semibold">
        Protocol
      </h2>
      <p className="mt-1 text-sm text-muted">
        {locked ? "Registered before the experiment started; it can't change now." : "Locked once the experiment starts."}
      </p>
      <blockquote className="mt-4 max-w-3xl border-l-2 border-accent pl-4 font-serif text-lg">
        {experiment.hypothesis || "No hypothesis yet."}
      </blockquote>
      <dl className="mt-6 grid gap-x-8 gap-y-4 sm:grid-cols-2">
        <div>
          <dt className="text-sm text-muted">Variants</dt>
          <dd>
            <ul>
              {experiment.variants.map((v) => (
                <li key={v.key}>
                  {v.name} <span className="tabular">{percent(v.weight_bp / 10_000, 0)}</span>
                  {v.is_control && <span className="text-muted"> (control)</span>}
                </li>
              ))}
            </ul>
          </dd>
        </div>
        <div>
          <dt className="text-sm text-muted">Metrics and expected baselines</dt>
          <dd>
            <ul>
              {experiment.metrics.map((m) => (
                <li key={m.metric_key}>
                  {metricName(metrics, m.metric_key)}, {m.role}
                  {m.expected_baseline !== null && (
                    <span className="tabular text-muted">
                      {" "}
                      (baseline {m.kind === "conversion" ? percent(m.expected_baseline) : m.expected_baseline})
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </dd>
        </div>
        <div>
          <dt className="text-sm text-muted">Traffic</dt>
          <dd className="tabular">{percent(experiment.traffic_bp / 10_000, 0)} of users</dd>
        </div>
        <div>
          <dt className="text-sm text-muted">Analysis</dt>
          <dd>
            {experiment.analysis_type === "sequential" ? "Sequential (always valid)" : "Fixed horizon"}, confidence{" "}
            {percent(1 - experiment.alpha, 0)}
            {experiment.mde_relative !== null && `, smallest lift worth detecting ${percent(experiment.mde_relative)}`}
          </dd>
        </div>
      </dl>
    </section>
  );
}

function describeChange(change: Change): string {
  const details = change.details;
  switch (change.action) {
    case "created":
      return "Created";
    case "started":
      return "Started";
    case "traffic_changed":
      return `Traffic raised from ${percent(Number(details.from) / 10_000, 0)} to ${percent(Number(details.to) / 10_000, 0)}`;
    case "stopped":
      return `Stopped: ${String(details.reason)}`;
    case "cloned":
      return `Created as a copy of ${String(details.from)}`;
    default:
      return change.action;
  }
}

function Changelog({ changes }: { changes: Change[] }) {
  return (
    <section aria-labelledby="changelog-title" className="py-8">
      <h2 id="changelog-title" className="text-lg font-semibold">
        Changelog
      </h2>
      <ol className="mt-4 space-y-3 border-l border-rule pl-5">
        {changes.map((change, i) => (
          <li key={i} className="relative">
            <span aria-hidden="true" className="absolute top-2 -left-[24.5px] h-2 w-2 rounded-full bg-accent" />
            <p>{describeChange(change)}</p>
            <p className="text-sm text-muted">{formatDate(change.created_at)}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
