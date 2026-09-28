/**
 * The admin API, called from the Next.js server only (PRD §17). It sends the server key,
 * which lives in the server's environment (not NEXT_PUBLIC_*), so it never reaches a browser.
 */

import { connection } from "next/server";

import type {
  ApiKey,
  ApiKeyCreated,
  Experiment,
  ExperimentDetail,
  Flag,
  Metric,
  Results,
} from "./types";

/** An API error in the platform's format: {"error": {code, message, details}}. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: unknown,
  ) {
    super(message);
  }
}

function settings(): { url: string; key: string } {
  const url = process.env.ABTEST_API_URL;
  const key = process.env.ABTEST_SERVER_KEY;
  if (!url || !key) {
    throw new Error("Set ABTEST_API_URL and ABTEST_SERVER_KEY in the dashboard's environment.");
  }
  return { url: url.replace(/\/+$/, ""), key };
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  // API data is live: render per request, never at build time (when there's no API to call).
  await connection();
  const { url, key } = settings();
  const headers: Record<string, string> = { Authorization: `Bearer ${key}` };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(`${url}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store", // results change every few minutes; never serve a cached copy
  });
  if (response.status === 204) {
    return undefined as T;
  }
  const data: unknown = await response.json();
  if (!response.ok) {
    throw toApiError(response.status, data);
  }
  return data as T;
}

function toApiError(status: number, data: unknown): ApiError {
  if (typeof data === "object" && data !== null && "error" in data) {
    const error = data.error as { code?: string; message?: string; details?: unknown };
    return new ApiError(status, error.code ?? "error", error.message ?? "Request failed", error.details);
  }
  return new ApiError(status, "error", `The API answered ${String(status)}`, null);
}

const path = encodeURIComponent;

export const api = {
  listExperiments: () => request<Experiment[]>("GET", "/admin/experiments"),
  getExperiment: (key: string) => request<ExperimentDetail>("GET", `/admin/experiments/${path(key)}`),
  createExperiment: (body: unknown) => request<ExperimentDetail>("POST", "/admin/experiments", body),
  startExperiment: (key: string) =>
    request<ExperimentDetail>("POST", `/admin/experiments/${path(key)}/start`),
  stopExperiment: (key: string, reason: string) =>
    request<ExperimentDetail>("POST", `/admin/experiments/${path(key)}/stop`, { reason }),
  cloneExperiment: (key: string, newKey: string) =>
    request<ExperimentDetail>("POST", `/admin/experiments/${path(key)}/clone`, { new_key: newKey }),
  updateExperiment: (key: string, body: unknown) =>
    request<ExperimentDetail>("PATCH", `/admin/experiments/${path(key)}`, body),
  results: (key: string, metric: string) =>
    request<Results>(
      "GET",
      `/admin/experiments/${path(key)}/results?metric=${encodeURIComponent(metric)}`,
    ),
  recompute: (key: string) =>
    request<{ metric_keys: string[] }>("POST", `/admin/experiments/${path(key)}/recompute`),

  listMetrics: () => request<Metric[]>("GET", "/admin/metrics"),
  createMetric: (body: unknown) => request<Metric>("POST", "/admin/metrics", body),
  updateMetric: (key: string, body: unknown) =>
    request<Metric>("PATCH", `/admin/metrics/${path(key)}`, body),

  listFlags: () => request<Flag[]>("GET", "/admin/flags"),
  createFlag: (body: unknown) => request<Flag>("POST", "/admin/flags", body),
  updateFlag: (key: string, body: unknown) =>
    request<Flag>("PATCH", `/admin/flags/${path(key)}`, body),
  deleteFlag: (key: string) => request<undefined>("DELETE", `/admin/flags/${path(key)}`),

  listApiKeys: () => request<ApiKey[]>("GET", "/admin/api-keys"),
  createApiKey: (kind: "client" | "server") =>
    request<ApiKeyCreated>("POST", "/admin/api-keys", { kind }),
  revokeApiKey: (id: string) => request<ApiKey>("POST", `/admin/api-keys/${path(id)}/revoke`),

  sampleSize: (params: URLSearchParams) =>
    request<{ users_per_variant: number }>("GET", `/admin/sample-size?${params.toString()}`),
};
