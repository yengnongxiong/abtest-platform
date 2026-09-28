"use client";

import { useEffect, useState, useTransition } from "react";

import { FormError } from "@/components/FormError";
import type { ActionError } from "@/lib/actions";
import { count, daysNeeded } from "@/lib/format";
import type { Metric, Role } from "@/lib/types";
import { controlAfterRemoving } from "@/lib/variants";

import { createExperiment, type NewExperiment } from "./actions";

const TEMPLATE = "If we [change], then [metric] will [increase/decrease] because [reason].";
const input = "mt-1 block w-full rounded-md border border-rule bg-surface px-3 py-2";

interface VariantRow {
  name: string;
  weight: string; // percent
}

interface MetricRow {
  metricKey: string;
  role: Role;
  baseline: string; // a percent for conversion metrics, a plain value for mean metrics
}

/** "Big button" -> "big-button": a key the API accepts. */
function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

export function NewExperimentForm({ metrics }: { metrics: Metric[] }) {
  const [name, setName] = useState("");
  const [keyInput, setKeyInput] = useState<string | null>(null); // null: follow the name
  const [hypothesis, setHypothesis] = useState(TEMPLATE);
  const [traffic, setTraffic] = useState("100");
  const [variants, setVariants] = useState<VariantRow[]>([
    { name: "Control", weight: "50" },
    { name: "Treatment", weight: "50" },
  ]);
  const [controlIndex, setControlIndex] = useState(0);
  const [metricRows, setMetricRows] = useState<MetricRow[]>([
    { metricKey: metrics[0]?.key ?? "", role: "primary", baseline: "" },
  ]);
  const [mde, setMde] = useState("5");
  const [alpha, setAlpha] = useState("0.05");
  const [analysisType, setAnalysisType] = useState<"sequential" | "fixed_horizon">("sequential");
  const [dailyUsers, setDailyUsers] = useState("");
  const [estimate, setEstimate] = useState<{ query: string; usersPerVariant: number | null } | null>(null);
  const [error, setError] = useState<ActionError | null>(null);
  const [pending, startTransition] = useTransition();

  const key = keyInput ?? slugify(name);
  const totalWeight = variants.reduce((sum, v) => sum + Number(v.weight || 0), 0);
  const weightsAddUp = Math.abs(totalWeight - 100) < 0.005; // to within the API's 0.01% steps
  const primary = metricRows.find((row) => row.role === "primary");
  const primaryMetric = metrics.find((m) => m.key === primary?.metricKey);

  // The live sample-size estimate: only for a conversion primary metric (the formula is for
  // proportions), from the API, through this dashboard's server.
  const baselineRate = Number(primary?.baseline) / 100;
  const mdeRelative = Number(mde) / 100;
  const canEstimate =
    primaryMetric?.kind === "conversion" && baselineRate > 0 && baselineRate < 1 && mdeRelative > 0;
  const query = canEstimate
    ? new URLSearchParams({ baseline: String(baselineRate), mde_relative: String(mdeRelative), alpha, power: "0.8" }).toString()
    : null;
  useEffect(() => {
    if (query === null) {
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetch(`/api/sample-size?${query}`, { signal: controller.signal })
        .then((response) => (response.ok ? response.json() : null))
        .then((data: { users_per_variant: number } | null) =>
          setEstimate({ query, usersPerVariant: data?.users_per_variant ?? null }),
        )
        .catch(() => undefined); // aborted by a newer input
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query]);
  const usersPerVariant = estimate !== null && estimate.query === query ? estimate.usersPerVariant : null;
  const days =
    usersPerVariant !== null
      ? daysNeeded(usersPerVariant, variants.length, Number(traffic) / 100, Number(dailyUsers))
      : null;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const body: NewExperiment = {
      key,
      name,
      hypothesis,
      traffic_bp: Math.round(Number(traffic) * 100),
      analysis_type: analysisType,
      alpha: Number(alpha),
      mde_relative: mde === "" ? null : Number(mde) / 100,
      variants: variants.map((v, i) => ({
        key: slugify(v.name),
        name: v.name,
        weight_bp: Math.round(Number(v.weight) * 100),
        is_control: i === controlIndex,
      })),
      metrics: metricRows.map((row) => {
        const kind = metrics.find((m) => m.key === row.metricKey)?.kind;
        const value = row.baseline === "" ? null : Number(row.baseline);
        return {
          metric_key: row.metricKey,
          role: row.role,
          expected_baseline: value === null ? null : kind === "conversion" ? value / 100 : value,
        };
      }),
    };
    startTransition(async () => setError(await createExperiment(body)));
  }

  if (metrics.length === 0) {
    return (
      <p className="mt-8 rounded-md border border-rule bg-surface px-4 py-3">
        Create a metric first: every experiment needs a primary metric to judge it by.{" "}
        <a href="/metrics" className="text-accent underline">
          Go to metrics
        </a>
      </p>
    );
  }

  return (
    <form onSubmit={submit} className="mt-8 space-y-10">
      <section className="grid gap-4 sm:grid-cols-2">
        <h2 className="text-lg font-semibold sm:col-span-2">What you&apos;re testing</h2>
        <label className="block">
          <span className="text-sm font-medium">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required placeholder="Bigger checkout button" className={input} />
        </label>
        <label className="block">
          <span className="text-sm font-medium">Key (used by the SDK)</span>
          <input value={key} onChange={(e) => setKeyInput(e.target.value)} required pattern="[a-z0-9][a-z0-9_-]{0,63}" className={input} />
        </label>
        <label className="block sm:col-span-2">
          <span className="text-sm font-medium">Hypothesis</span>
          <textarea value={hypothesis} onChange={(e) => setHypothesis(e.target.value)} rows={3} required className={`${input} font-serif text-lg`} />
        </label>
      </section>

      <section>
        <h2 className="text-lg font-semibold">Variants</h2>
        <p className="mt-1 text-sm text-muted">Each user sees one. Weights are shares of the users in the experiment.</p>
        <div className="mt-4 space-y-2">
          {variants.map((variant, i) => (
            <div key={i} className="flex flex-wrap items-center gap-3">
              <input
                aria-label={`Variant ${String(i + 1)} name`}
                value={variant.name}
                onChange={(e) => setVariants(variants.map((v, j) => (j === i ? { ...v, name: e.target.value } : v)))}
                required
                className="min-w-40 flex-1 rounded-md border border-rule bg-surface px-3 py-2"
              />
              <label className="flex items-center gap-2 text-sm">
                <input
                  aria-label={`Variant ${String(i + 1)} weight in percent`}
                  type="number"
                  min="0.01"
                  max="100"
                  step="0.01"
                  value={variant.weight}
                  onChange={(e) => setVariants(variants.map((v, j) => (j === i ? { ...v, weight: e.target.value } : v)))}
                  className="tabular w-24 rounded-md border border-rule bg-surface px-2 py-2"
                />
                %
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="control"
                  aria-label={`Variant ${String(i + 1)} is the control`}
                  checked={controlIndex === i}
                  onChange={() => setControlIndex(i)}
                />
                Control
              </label>
              {variants.length > 2 && (
                <button
                  type="button"
                  onClick={() => {
                    setVariants(variants.filter((_, j) => j !== i));
                    setControlIndex(controlAfterRemoving(controlIndex, i));
                  }}
                  className="text-sm text-muted hover:text-alert"
                >
                  Remove
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-4 text-sm">
          <button type="button" onClick={() => setVariants([...variants, { name: "", weight: "0" }])} className="text-accent hover:underline">
            Add a variant
          </button>
          <span className={weightsAddUp ? "text-muted" : "text-alert"}>
            Weights add up to {totalWeight.toFixed(2).replace(/\.00$/, "")}%
            {!weightsAddUp && " (they must add up to 100%)"}
          </span>
        </div>
        <label className="mt-6 block max-w-xs">
          <span className="text-sm font-medium">Traffic: share of all users in the experiment (%)</span>
          <input type="number" min="0" max="100" step="0.01" value={traffic} onChange={(e) => setTraffic(e.target.value)} className={input} />
          <span className="mt-1 block text-sm text-muted">It can go up while the experiment runs, never down.</span>
        </label>
      </section>

      <section>
        <h2 className="text-lg font-semibold">How you&apos;ll judge it</h2>
        <p className="mt-1 text-sm text-muted">
          The primary metric decides. Expected baseline: what the metric is today, before the change.
        </p>
        <div className="mt-4 space-y-2">
          {metricRows.map((row, i) => {
            const kind = metrics.find((m) => m.key === row.metricKey)?.kind;
            return (
              <div key={i} className="flex flex-wrap items-center gap-3">
                <select
                  aria-label={`Metric ${String(i + 1)}`}
                  value={row.metricKey}
                  onChange={(e) => setMetricRows(metricRows.map((r, j) => (j === i ? { ...r, metricKey: e.target.value } : r)))}
                  className="rounded-md border border-rule bg-surface px-3 py-2"
                >
                  {metrics.map((m) => (
                    <option key={m.key} value={m.key}>
                      {m.name} ({m.kind})
                    </option>
                  ))}
                </select>
                <select
                  aria-label={`Metric ${String(i + 1)} role`}
                  value={row.role}
                  onChange={(e) => setMetricRows(metricRows.map((r, j) => (j === i ? { ...r, role: e.target.value as Role } : r)))}
                  className="rounded-md border border-rule bg-surface px-3 py-2"
                >
                  <option value="primary">Primary: decides the result</option>
                  <option value="secondary">Secondary: context</option>
                  <option value="guardrail">Guardrail: must not get worse</option>
                </select>
                <label className="flex items-center gap-2 text-sm">
                  Expected baseline
                  <input
                    aria-label={`Metric ${String(i + 1)} expected baseline`}
                    type="number"
                    min="0"
                    step="any"
                    value={row.baseline}
                    onChange={(e) => setMetricRows(metricRows.map((r, j) => (j === i ? { ...r, baseline: e.target.value } : r)))}
                    className="tabular w-28 rounded-md border border-rule bg-surface px-2 py-2"
                  />
                  {kind === "conversion" ? "%" : "per user"}
                </label>
                {metricRows.length > 1 && (
                  <button type="button" onClick={() => setMetricRows(metricRows.filter((_, j) => j !== i))} className="text-sm text-muted hover:text-alert">
                    Remove
                  </button>
                )}
              </div>
            );
          })}
        </div>
        <button
          type="button"
          onClick={() => setMetricRows([...metricRows, { metricKey: metrics[0]?.key ?? "", role: "secondary", baseline: "" }])}
          className="mt-3 text-sm text-accent hover:underline"
        >
          Add a metric
        </button>
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-4">
          <h2 className="text-lg font-semibold">Analysis</h2>
          <label className="block">
            <span className="text-sm font-medium">Smallest lift worth detecting (%)</span>
            <input type="number" min="0.01" step="any" value={mde} onChange={(e) => setMde(e.target.value)} className={input} />
          </label>
          <label className="block">
            <span className="text-sm font-medium">False-positive risk (alpha)</span>
            <select value={alpha} onChange={(e) => setAlpha(e.target.value)} className={input}>
              <option value="0.05">5% (95% confidence)</option>
              <option value="0.01">1% (99% confidence)</option>
              <option value="0.1">10% (90% confidence)</option>
            </select>
          </label>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">When you&apos;ll read the results</legend>
            <label className="flex gap-2">
              <input type="radio" checked={analysisType === "sequential"} onChange={() => setAnalysisType("sequential")} />
              <span>
                Any time (sequential). Safe to check as often as you like; needs somewhat more users.
              </span>
            </label>
            <label className="flex gap-2">
              <input type="radio" checked={analysisType === "fixed_horizon"} onChange={() => setAnalysisType("fixed_horizon")} />
              <span>Once, at the planned sample size (fixed horizon). Checking earlier inflates false positives.</span>
            </label>
          </fieldset>
        </div>
        <aside aria-live="polite" className="self-start rounded-md border border-rule bg-surface p-5">
          <h2 className="text-lg font-semibold">Sample size</h2>
          {primaryMetric?.kind === "mean" ? (
            <p className="mt-2 text-muted">
              Not available: the estimate is for conversion metrics, and this primary metric is a mean.
            </p>
          ) : usersPerVariant === null ? (
            <p className="mt-2 text-muted">Enter the primary metric&apos;s expected baseline and the smallest lift to see an estimate.</p>
          ) : (
            <>
              <p className="mt-2 font-serif text-3xl tabular">{count(usersPerVariant)}</p>
              <p className="text-muted">
                users per variant for an 80% chance of detecting a lift of {mde}%, read once at that size.
                {analysisType === "sequential" && " A sequential analysis needs somewhat more to reach the same power."}
              </p>
              <label className="mt-4 block">
                <span className="text-sm font-medium">Visitors per day</span>
                <input type="number" min="1" value={dailyUsers} onChange={(e) => setDailyUsers(e.target.value)} className={input} />
              </label>
              {days !== null && (
                <p className="mt-2">
                  About <strong>{count(days)}</strong> {days === 1 ? "day" : "days"} at {traffic}% traffic.
                </p>
              )}
            </>
          )}
        </aside>
      </section>

      <div className="space-y-3 border-t border-rule pt-6">
        <FormError error={error} />
        <button type="submit" disabled={pending} className="rounded-md bg-accent px-5 py-2.5 font-medium text-white disabled:opacity-60">
          {pending ? "Creating…" : "Create draft"}
        </button>
        <p className="text-sm text-muted">You can review the draft before starting it.</p>
      </div>
    </form>
  );
}
