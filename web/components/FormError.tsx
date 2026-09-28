/** An error from a form action, with the API's list of problems when it has one. */
export function FormError({ error }: { error: { message: string; problems?: string[] } | null }) {
  if (error === null) {
    return null;
  }
  return (
    <div role="alert" className="rounded-md border border-alert/30 bg-alert-soft px-4 py-3 text-sm text-alert">
      <p className="font-medium">{error.message}</p>
      {error.problems && error.problems.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {error.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
