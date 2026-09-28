import Link from "next/link";

import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import { count, daysRunning } from "@/lib/format";

export default async function ExperimentsPage() {
  const [experiments, metrics] = await Promise.all([api.listExperiments(), api.listMetrics()]);
  const metricName = (key: string) => metrics.find((m) => m.key === key)?.name ?? key;
  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Experiments</h1>
          <p className="mt-1 text-muted">Results update every 5 minutes while an experiment runs.</p>
        </div>
        <Link href="/experiments/new" className="rounded-md bg-accent px-4 py-2 font-medium text-white">
          New experiment
        </Link>
      </div>
      <div className="mt-6 overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="border-b border-rule text-muted">
            <tr>
              <th className="py-2 pr-4 font-medium">Experiment</th>
              <th className="py-2 pr-4 font-medium">Status</th>
              <th className="py-2 pr-4 font-medium">Primary metric</th>
              <th className="py-2 pr-4 text-right font-medium">Users</th>
              <th className="py-2 pr-4 text-right font-medium">Days running</th>
              <th className="py-2 font-medium">Health</th>
            </tr>
          </thead>
          <tbody>
            {experiments.map((experiment) => {
              const primary = experiment.metrics.find((m) => m.role === "primary");
              const latest = experiment.latest_results;
              const days = daysRunning(experiment);
              return (
                <tr key={experiment.key} className="border-b border-rule">
                  <td className="py-3 pr-4">
                    <Link href={`/experiments/${experiment.key}`} className="font-medium text-accent hover:underline">
                      {experiment.name}
                    </Link>
                    <p className="text-muted">{experiment.key}</p>
                  </td>
                  <td className="py-3 pr-4">
                    <StatusBadge status={experiment.status} />
                  </td>
                  <td className="py-3 pr-4">{primary ? metricName(primary.metric_key) : "None yet"}</td>
                  <td className="tabular py-3 pr-4 text-right">{latest ? count(latest.users) : "–"}</td>
                  <td className="tabular py-3 pr-4 text-right">
                    {days ?? "–"}
                  </td>
                  <td className="py-3">
                    {latest?.srm_flagged ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-alert-soft px-2.5 py-0.5 font-medium text-alert">
                        <span aria-hidden="true">!</span> Sample ratio mismatch
                      </span>
                    ) : latest ? (
                      <span className="text-muted">No problems found</span>
                    ) : (
                      <span className="text-muted">No results yet</span>
                    )}
                  </td>
                </tr>
              );
            })}
            {experiments.length === 0 && (
              <tr>
                <td colSpan={6} className="py-8 text-muted">
                  No experiments yet. Start by writing down what you expect to happen: create a new experiment.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
