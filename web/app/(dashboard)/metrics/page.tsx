import { api } from "@/lib/api";

import { MetricForm } from "./MetricForm";

export default async function MetricsPage() {
  const metrics = await api.listMetrics();
  return (
    <>
      <h1 className="text-2xl font-semibold">Metrics</h1>
      <p className="mt-1 text-muted">What experiments measure. Each metric counts one event after a user first sees an experiment.</p>
      <table className="mt-6 w-full text-left text-sm">
        <thead className="border-b border-rule text-muted">
          <tr>
            <th className="py-2 pr-4 font-medium">Metric</th>
            <th className="py-2 pr-4 font-medium">Event</th>
            <th className="py-2 pr-4 font-medium">Measured as</th>
            <th className="py-2 pr-4 font-medium">Better when</th>
            <th className="py-2 font-medium">Window</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((metric) => (
            <tr key={metric.key} className="border-b border-rule">
              <td className="py-2 pr-4">
                {metric.name} <span className="text-muted">({metric.key})</span>
              </td>
              <td className="py-2 pr-4">{metric.event_name}</td>
              <td className="py-2 pr-4">{metric.kind === "conversion" ? "Conversion" : "Mean"}</td>
              <td className="py-2 pr-4">{metric.direction === "increase" ? "Higher" : "Lower"}</td>
              <td className="tabular py-2">{metric.window_hours} h</td>
            </tr>
          ))}
          {metrics.length === 0 && (
            <tr>
              <td colSpan={5} className="py-6 text-muted">
                No metrics yet. Create one below; experiments need at least one.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      <h2 className="mt-10 mb-4 text-lg font-semibold">New metric</h2>
      <MetricForm />
    </>
  );
}
