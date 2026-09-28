"use client";

import { useState, useTransition } from "react";

import { FormError } from "@/components/FormError";
import type { ActionError } from "@/lib/actions";
import type { Status } from "@/lib/types";

import { cloneExperiment, recomputeResults, startExperiment, stopExperiment } from "./actions";

const button = "rounded-md px-4 py-2 font-medium disabled:opacity-60";

/** Start, stop, clone, and recompute: the lifecycle actions for this experiment's status. */
export function Controls({ experimentKey, status }: { experimentKey: string; status: Status }) {
  const [error, setError] = useState<ActionError | null>(null);
  const [pending, startTransition] = useTransition();
  const [open, setOpen] = useState<"stop" | "clone" | null>(null);
  const [reason, setReason] = useState("");
  const [newKey, setNewKey] = useState(`${experimentKey}-2`);

  function act(action: () => Promise<ActionError | null>) {
    startTransition(async () => {
      setError(await action());
    });
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {status === "draft" && (
          <button type="button" disabled={pending} onClick={() => act(() => startExperiment(experimentKey))} className={`${button} bg-accent text-white`}>
            Start experiment
          </button>
        )}
        {status === "running" && (
          <button type="button" onClick={() => setOpen(open === "stop" ? null : "stop")} className={`${button} border border-rule bg-surface`}>
            Stop
          </button>
        )}
        {status !== "draft" && (
          <button type="button" disabled={pending} onClick={() => act(() => recomputeResults(experimentKey))} className={`${button} border border-rule bg-surface`}>
            {pending ? "Working…" : "Update results now"}
          </button>
        )}
        <button type="button" onClick={() => setOpen(open === "clone" ? null : "clone")} className={`${button} border border-rule bg-surface`}>
          Clone
        </button>
      </div>
      {open === "stop" && status === "running" && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            act(() => stopExperiment(experimentKey, reason));
          }}
          className="flex flex-wrap items-end gap-2"
        >
          <label className="block min-w-64 flex-1">
            <span className="text-sm font-medium">Why are you stopping it? Stopping is final.</span>
            <input value={reason} onChange={(e) => setReason(e.target.value)} required placeholder="Reached the planned sample size" className="mt-1 block w-full rounded-md border border-rule bg-surface px-3 py-2" />
          </label>
          <button type="submit" disabled={pending} className={`${button} bg-alert text-white`}>
            Stop experiment
          </button>
        </form>
      )}
      {open === "clone" && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            act(() => cloneExperiment(experimentKey, newKey));
          }}
          className="flex flex-wrap items-end gap-2"
        >
          <label className="block min-w-64 flex-1">
            <span className="text-sm font-medium">Key for the copy (a new draft with the same design)</span>
            <input value={newKey} onChange={(e) => setNewKey(e.target.value)} required pattern="[a-z0-9][a-z0-9_-]{0,63}" className="mt-1 block w-full rounded-md border border-rule bg-surface px-3 py-2" />
          </label>
          <button type="submit" disabled={pending} className={`${button} bg-accent text-white`}>
            Create copy
          </button>
        </form>
      )}
      <FormError error={error} />
    </div>
  );
}
