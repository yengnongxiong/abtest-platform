"use client";

/**
 * Shown when a page or action fails, for example when the API is down. In production Next.js
 * hides server error messages from the browser, so this says what to check, plus the
 * reference that matches the server's log line.
 */
export default function DashboardError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <div role="alert" className="max-w-xl space-y-3">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="text-muted">
        The dashboard couldn&apos;t complete that request. If it keeps happening, check that the API is running and
        that the dashboard&apos;s server key is still valid.
      </p>
      {error.digest && <p className="text-sm text-muted">Reference: {error.digest}</p>}
      <button type="button" onClick={retry} className="rounded-md bg-accent px-4 py-2 font-medium text-white">
        Try again
      </button>
    </div>
  );
}
