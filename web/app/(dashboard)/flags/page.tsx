import { api } from "@/lib/api";

import { deleteFlag, updateFlag } from "./actions";
import { FlagForm } from "./FlagForm";

export default async function FlagsPage() {
  const flags = await api.listFlags();
  return (
    <>
      <h1 className="text-2xl font-semibold">Flags</h1>
      <p className="mt-1 text-muted">
        Turn features on for a share of users. The same user always gets the same answer, and a larger
        rollout only adds users.
      </p>
      <ul className="mt-6 divide-y divide-rule border-y border-rule">
        {flags.map((flag) => (
          <li key={flag.key} className="flex flex-wrap items-center gap-x-6 gap-y-2 py-3">
            <div className="min-w-48 flex-1">
              <p className="font-medium">{flag.key}</p>
              {flag.description && <p className="text-sm text-muted">{flag.description}</p>}
            </div>
            <form action={updateFlag.bind(null, flag.key)}>
              <input type="hidden" name="enabled" value={String(!flag.enabled)} />
              <button
                type="submit"
                aria-label={`${flag.enabled ? "Turn off" : "Turn on"} ${flag.key}`}
                className={`rounded-full px-3 py-1 text-sm font-medium ${flag.enabled ? "bg-win-soft text-win" : "border border-rule text-muted"}`}
              >
                {flag.enabled ? "On" : "Off"}
              </button>
            </form>
            <form action={updateFlag.bind(null, flag.key)} className="flex items-center gap-2">
              <label className="text-sm text-muted" htmlFor={`rollout-${flag.key}`}>
                Rollout
              </label>
              <input
                id={`rollout-${flag.key}`}
                name="rollout_percent"
                type="number"
                min="0"
                max="100"
                step="0.01"
                defaultValue={flag.rollout_bp / 100}
                aria-label={`Rollout of ${flag.key} in percent`}
                className="tabular w-24 rounded-md border border-rule bg-surface px-2 py-1 text-sm"
              />
              <span className="text-sm text-muted">%</span>
              <button type="submit" aria-label={`Save the rollout of ${flag.key}`} className="text-sm text-accent hover:underline">
                Save
              </button>
            </form>
            <form action={deleteFlag.bind(null, flag.key)}>
              <button type="submit" aria-label={`Delete ${flag.key}`} className="text-sm text-muted hover:text-alert">
                Delete
              </button>
            </form>
          </li>
        ))}
        {flags.length === 0 && <li className="py-6 text-muted">No flags yet. Create one below.</li>}
      </ul>
      <h2 className="mt-10 mb-4 text-lg font-semibold">New flag</h2>
      <FlagForm />
    </>
  );
}
