"use client";

import { useActionState } from "react";

import { FormError } from "@/components/FormError";

import { createFlag } from "./actions";

const input = "mt-1 block w-full rounded-md border border-rule bg-surface px-3 py-2";

export function FlagForm() {
  const [error, action, pending] = useActionState(createFlag, null);
  return (
    <form action={action} className="grid gap-4 sm:grid-cols-3">
      <label className="block">
        <span className="text-sm font-medium">Key</span>
        <input name="key" required pattern="[a-z0-9][a-z0-9_-]{0,63}" placeholder="dark-mode" className={input} />
      </label>
      <label className="block sm:col-span-2">
        <span className="text-sm font-medium">What it controls</span>
        <input name="description" placeholder="Dark theme for the web app" className={input} />
      </label>
      <label className="block">
        <span className="text-sm font-medium">Roll out to (% of users)</span>
        <input name="rollout_percent" type="number" min="0" max="100" step="0.01" defaultValue="0" className={input} />
      </label>
      <div className="sm:col-span-3">
        <p className="mb-2 text-sm text-muted">New flags start off. Turn one on from the list once it&apos;s ready.</p>
        <FormError error={error} />
        <button type="submit" disabled={pending} className="mt-2 rounded-md bg-accent px-4 py-2 font-medium text-white disabled:opacity-60">
          {pending ? "Creating…" : "Create flag"}
        </button>
      </div>
    </form>
  );
}
