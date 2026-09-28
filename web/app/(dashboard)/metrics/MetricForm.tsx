"use client";

import { useActionState } from "react";

import { FormError } from "@/components/FormError";

import { createMetric } from "./actions";

const input = "mt-1 block w-full rounded-md border border-rule bg-surface px-3 py-2";

export function MetricForm() {
  const [error, action, pending] = useActionState(createMetric, null);
  return (
    <form action={action} className="grid gap-4 sm:grid-cols-2">
      <label className="block">
        <span className="text-sm font-medium">Name</span>
        <input name="name" required placeholder="Checkout conversion" className={input} />
      </label>
      <label className="block">
        <span className="text-sm font-medium">Key</span>
        <input name="key" required pattern="[a-z0-9][a-z0-9_-]{0,63}" placeholder="checkout" className={input} />
      </label>
      <label className="block">
        <span className="text-sm font-medium">Event it counts</span>
        <input name="event_name" required placeholder="purchase" className={input} />
      </label>
      <label className="block">
        <span className="text-sm font-medium">Measured as</span>
        <select name="kind" className={input}>
          <option value="conversion">Conversion: did the user do it at least once?</option>
          <option value="mean">Mean: the average of each user&apos;s total value</option>
        </select>
      </label>
      <label className="block">
        <span className="text-sm font-medium">Better when it</span>
        <select name="direction" className={input}>
          <option value="increase">goes up</option>
          <option value="decrease">goes down</option>
        </select>
      </label>
      <label className="block">
        <span className="text-sm font-medium">Counts events within (hours of first exposure)</span>
        <input name="window_hours" type="number" min="1" defaultValue="168" required className={input} />
      </label>
      <div className="sm:col-span-2">
        <FormError error={error} />
        <button type="submit" disabled={pending} className="mt-2 rounded-md bg-accent px-4 py-2 font-medium text-white disabled:opacity-60">
          {pending ? "Creating…" : "Create metric"}
        </button>
      </div>
    </form>
  );
}
