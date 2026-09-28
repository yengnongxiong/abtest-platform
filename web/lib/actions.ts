import { ApiError } from "./api";

export interface ActionError {
  message: string;
  problems?: string[];
}

/** An API failure as something a form can show: the message, and each problem it lists. */
export function toActionError(error: unknown): ActionError {
  if (!(error instanceof ApiError)) {
    throw error; // not an API answer: let Next.js show its error page
  }
  return { message: error.message, problems: problemsOf(error.details) };
}

function problemsOf(details: unknown): string[] | undefined {
  if (Array.isArray(details)) {
    // pydantic's validation errors: [{loc: [...], msg: "..."}]
    return details.map((item: { loc?: unknown[]; msg?: string }) => {
      const field = (item.loc ?? []).filter((part) => part !== "body").join(".");
      return field ? `${field}: ${item.msg ?? ""}` : (item.msg ?? "");
    });
  }
  if (typeof details === "object" && details !== null && "problems" in details && Array.isArray(details.problems)) {
    return details.problems.map(String); // e.g. why an experiment can't start
  }
  return undefined;
}
