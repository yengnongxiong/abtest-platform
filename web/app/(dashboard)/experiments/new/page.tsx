import { api } from "@/lib/api";

import { NewExperimentForm } from "./NewExperimentForm";

export default async function NewExperimentPage() {
  const metrics = await api.listMetrics();
  return (
    <>
      <h1 className="text-2xl font-semibold">New experiment</h1>
      <p className="mt-1 max-w-2xl text-muted">
        Write down what you expect and how you&apos;ll judge it before any data comes in. Once the
        experiment starts, its variants, metrics, and analysis are locked.
      </p>
      <NewExperimentForm metrics={metrics} />
    </>
  );
}
