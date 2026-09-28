"use client";

import { useState, type ReactNode } from "react";

/** A flagged sample ratio mismatch hides the results until someone chooses to see them. */
export function SrmGate({ pValue, children }: { pValue: string; children: ReactNode }) {
  const [shown, setShown] = useState(false);
  return (
    <>
      <div role="alert" className="border-l-4 border-alert bg-alert-soft px-5 py-4">
        <h3 className="font-semibold text-alert">These results can&apos;t be trusted: sample ratio mismatch</h3>
        <p className="mt-1 max-w-3xl text-sm">
          The variants didn&apos;t get the share of users they were set up for (chi-square p-value {pValue};
          anything below 0.001 is flagged). That almost always means a bug, such as exposures lost from one
          variant&apos;s logging, a redirect that drops some users, or bots filtered unevenly. Any lift below
          is likely wrong. Find the cause, then clone the experiment and run it again.
        </p>
        <button
          type="button"
          aria-expanded={shown}
          onClick={() => setShown(!shown)}
          className="mt-3 text-sm font-medium text-alert underline"
        >
          {shown ? "Hide the results" : "Show the results anyway"}
        </button>
      </div>
      {shown && <div className="mt-6">{children}</div>}
    </>
  );
}
