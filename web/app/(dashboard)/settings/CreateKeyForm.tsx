"use client";

import { useActionState } from "react";

import { FormError } from "@/components/FormError";

import { createKey } from "./actions";

export function CreateKeyForm() {
  const [state, action, pending] = useActionState(createKey, null);
  return (
    <form action={action} className="space-y-4">
      <fieldset className="flex flex-wrap gap-6">
        <legend className="mb-2 text-sm font-medium">Kind of key</legend>
        <label className="flex items-center gap-2">
          <input type="radio" name="kind" value="client" defaultChecked />
          Client key: for the SDK in web pages (public)
        </label>
        <label className="flex items-center gap-2">
          <input type="radio" name="kind" value="server" />
          Server key: for the admin API (keep secret)
        </label>
      </fieldset>
      <button type="submit" disabled={pending} className="rounded-md bg-accent px-4 py-2 font-medium text-white disabled:opacity-60">
        {pending ? "Creating…" : "Create key"}
      </button>
      {state !== null && "created" in state && (
        <div role="status" className="rounded-md border border-win/30 bg-win-soft px-4 py-3">
          <p className="font-medium text-win">Copy this {state.created.kind} key now. It won&apos;t be shown again.</p>
          <input
            readOnly
            value={state.created.key}
            aria-label="New key"
            onFocus={(event) => event.currentTarget.select()}
            className="tabular mt-2 w-full rounded-md border border-rule bg-surface px-3 py-2"
          />
        </div>
      )}
      {state !== null && "error" in state && <FormError error={state.error} />}
    </form>
  );
}
