/**
 * The SDK client (PRD §12): fetches the config, assigns users locally, and batches events
 * to the API with retries. Evaluation (getVariant, isEnabled) never touches the network.
 */

import { assign, flagEnabled, hasLoneSurrogate, type WeightedVariant } from "./assignment";
import { SDK_NAME, SDK_VERSION } from "./version";

export interface ClientOptions {
  clientKey: string;
  apiBaseUrl: string;
  userId?: string;
  flushIntervalMs?: number;
  maxBatchSize?: number;
  maxQueueSize?: number;
  configPollIntervalMs?: number;
  readyTimeoutMs?: number;
  fetch?: typeof fetch;
}

export interface TrackOptions {
  value?: number;
  properties?: Record<string, unknown>;
}

export interface ClientStats {
  /** Events waiting to be sent. */
  queued: number;
  /** Events that will never be stored: queue overflow, unload payloads over the browser's
   * cap, and events the API refused. */
  dropped: number;
}

export interface Client {
  ready(): Promise<void>;
  setUser(userId: string): void;
  getVariant(experimentKey: string): string | null;
  isEnabled(flagKey: string): boolean;
  track(name: string, options?: TrackOptions): void;
  flush(): Promise<void>;
  close(): Promise<void>;
  stats(): ClientStats;
}

interface Config {
  config_version: number;
  flags: { key: string; enabled: boolean; rollout_bp: number }[];
  experiments: { key: string; traffic_bp: number; variants: WeightedVariant[] }[];
}

interface WireEvent {
  event_id: string;
  user_id: string;
  name: string;
  occurred_at: string;
  value?: number;
  properties?: Record<string, unknown>;
}

type SendOutcome =
  | { kind: "sent"; rejected: number }
  | { kind: "failed" } // a 4xx other than 429: sending it again can't succeed
  | { kind: "retry"; retryAfterMs: number | null };

export const ANONYMOUS_ID_STORAGE_KEY = "abtest_anonymous_id";
const BASE_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 30_000;
// Browsers cap beacon and keepalive bodies at 64 KiB; stay a little under it.
export const MAX_UNLOAD_BYTES = 60_000;
const encoder = new TextEncoder();

export function createClient(options: ClientOptions): Client {
  const fetchFn = options.fetch ?? globalThis.fetch.bind(globalThis);
  const baseUrl = options.apiBaseUrl.replace(/\/+$/, "");
  const flushIntervalMs = options.flushIntervalMs ?? 5_000;
  const maxBatchSize = options.maxBatchSize ?? 50;
  const maxQueueSize = options.maxQueueSize ?? 1_000;
  const configPollIntervalMs = options.configPollIntervalMs ?? 30_000;
  const readyTimeoutMs = options.readyTimeoutMs ?? 2_000;

  let config: Config | null = null;
  let etag: string | null = null;
  let userId = options.userId ?? anonymousId();
  checkUserId(userId);
  let queue: WireEvent[] = [];
  let dropped = 0;
  let closed = false;
  // Exposures already logged in this page session, as "experiment\nuser".
  const exposed = new Set<string>();
  // Retry state: after a failed send, nothing is sent before retryAt.
  let attempt = 0;
  let retryAt = 0;
  let inflight: Promise<void> | null = null;

  async function loadConfig(): Promise<void> {
    try {
      const headers: Record<string, string> = { "X-Client-Key": options.clientKey };
      if (etag !== null) {
        headers["If-None-Match"] = etag;
      }
      const response = await fetchFn(`${baseUrl}/v1/config`, { headers });
      if (response.status === 304 || !response.ok) {
        return; // unchanged, or a failure: keep the last good config
      }
      const body: unknown = await response.json();
      if (isConfig(body)) {
        config = body;
        etag = response.headers.get("ETag");
      }
    } catch {
      // Network error: keep the last good config and try again at the next poll.
    }
  }

  // ready(): the first config load or the timeout, whichever comes first.
  let readyTimer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<void>((resolve) => {
    readyTimer = setTimeout(resolve, readyTimeoutMs);
  });
  const readyPromise = Promise.race([loadConfig(), timeout]).finally(() => {
    clearTimeout(readyTimer);
  });
  const timers = [
    setInterval(() => void loadConfig(), configPollIntervalMs),
    setInterval(() => void flush(), flushIntervalMs),
  ];

  function enqueue(event: WireEvent): void {
    if (closed) {
      dropped += 1;
      return;
    }
    queue.push(event);
    trimQueue();
    if (queue.length >= maxBatchSize) {
      void flush();
    }
  }

  function trimQueue(): void {
    const excess = queue.length - maxQueueSize;
    if (excess > 0) {
      queue.splice(0, excess); // drop the oldest
      dropped += excess;
    }
  }

  function flush(): Promise<void> {
    inflight ??= drain().finally(() => {
      inflight = null;
    });
    return inflight;
  }

  async function drain(): Promise<void> {
    while (queue.length > 0 && Date.now() >= retryAt) {
      // Take the batch out of the queue while it's in flight, so overflow trimming meanwhile
      // can't remove the wrong events.
      const batch = queue.splice(0, maxBatchSize);
      const outcome = await send(batch);
      if (outcome.kind === "retry") {
        queue = [...batch, ...queue];
        trimQueue();
        attempt += 1;
        retryAt = Date.now() + (outcome.retryAfterMs ?? backoffMs(attempt));
        return;
      }
      attempt = 0;
      dropped += outcome.kind === "failed" ? batch.length : outcome.rejected;
    }
  }

  async function send(batch: WireEvent[]): Promise<SendOutcome> {
    try {
      const response = await fetchFn(`${baseUrl}/v1/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Client-Key": options.clientKey },
        body: payload(batch),
      });
      if (response.status === 429 || response.status >= 500) {
        return { kind: "retry", retryAfterMs: parseRetryAfter(response.headers.get("Retry-After")) };
      }
      if (!response.ok) {
        return { kind: "failed" };
      }
      const body: unknown = await response.json().catch(() => null);
      return { kind: "sent", rejected: rejectedCount(body) };
    } catch {
      return { kind: "retry", retryAfterMs: null }; // network error
    }
  }

  /**
   * The page is going away (or into the background, where it may be killed without
   * warning): send everything now with navigator.sendBeacon, which outlives the page.
   * A beacon can't set headers, so the key goes in the query string, and the body is
   * text/plain: a CORS-safelisted type that needs no preflight (the API parses it as JSON).
   */
  function flushOnUnload(): void {
    const events = queue.splice(0);
    const url = `${baseUrl}/v1/events?client_key=${encodeURIComponent(options.clientKey)}`;
    const { chunks, tooBig } = chunkUnder(events, MAX_UNLOAD_BYTES);
    dropped += tooBig;
    for (const body of chunks) {
      const beaconSent =
        typeof navigator !== "undefined" &&
        typeof navigator.sendBeacon === "function" &&
        navigator.sendBeacon(url, body);
      if (!beaconSent) {
        // No beacon, or the browser refused it: a keepalive fetch also outlives the page.
        void fetchFn(url, {
          method: "POST",
          headers: { "Content-Type": "text/plain" },
          body,
          keepalive: true,
        }).catch(() => undefined);
      }
    }
  }

  function onVisibilityChange(): void {
    if (document.visibilityState === "hidden") {
      flushOnUnload();
    }
  }

  const hasDocument = typeof document !== "undefined";
  const hasWindowEvents = typeof globalThis.addEventListener === "function";
  if (hasDocument) {
    document.addEventListener("visibilitychange", onVisibilityChange);
  }
  if (hasWindowEvents) {
    globalThis.addEventListener("pagehide", flushOnUnload);
  }

  return {
    ready: () => readyPromise,

    setUser(newUserId: string): void {
      checkUserId(newUserId);
      userId = newUserId;
    },

    getVariant(experimentKey: string): string | null {
      const experiment = config?.experiments.find((e) => e.key === experimentKey);
      if (experiment === undefined) {
        return null;
      }
      const variant = assign(experiment.key, userId, experiment.traffic_bp, experiment.variants);
      const seen = `${experiment.key}\n${userId}`;
      if (variant !== null && !exposed.has(seen)) {
        exposed.add(seen);
        const properties = { experiment_key: experiment.key, variant_key: variant };
        enqueue(newEvent("$exposure", userId, { properties }));
      }
      return variant;
    },

    isEnabled(flagKey: string): boolean {
      const flag = config?.flags.find((f) => f.key === flagKey);
      return flag !== undefined && flagEnabled(flag.key, userId, flag.enabled, flag.rollout_bp);
    },

    track(name: string, trackOptions?: TrackOptions): void {
      enqueue(newEvent(name, userId, trackOptions));
    },

    flush,

    async close(): Promise<void> {
      closed = true;
      timers.forEach(clearInterval);
      if (hasDocument) {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
      if (hasWindowEvents) {
        globalThis.removeEventListener("pagehide", flushOnUnload);
      }
      retryAt = 0; // one last attempt, even during a backoff
      await flush();
    },

    stats: () => ({ queued: queue.length, dropped }),
  };
}

/**
 * The event's id and time are fixed here, at track() time, and never change: a retried
 * batch resends identical events, which the API recognizes as duplicates (PRD §10, §12).
 */
function newEvent(name: string, userId: string, options?: TrackOptions): WireEvent {
  const event: WireEvent = {
    event_id: crypto.randomUUID(),
    user_id: userId,
    name,
    occurred_at: new Date().toISOString(),
  };
  if (options?.value !== undefined) {
    event.value = options.value;
  }
  if (options?.properties !== undefined) {
    event.properties = options.properties;
  }
  return event;
}

function payload(events: WireEvent[]): string {
  return JSON.stringify({ sdk: { name: SDK_NAME, version: SDK_VERSION }, events });
}

/** Group events into request bodies of at most maxBytes; count events too big to send alone. */
export function chunkUnder(
  events: WireEvent[],
  maxBytes: number,
): { chunks: string[]; tooBig: number } {
  const chunks: string[] = [];
  let tooBig = 0;
  let current: WireEvent[] = [];
  for (const event of events) {
    if (byteLength(payload([...current, event])) <= maxBytes) {
      current.push(event);
    } else if (byteLength(payload([event])) > maxBytes) {
      tooBig += 1;
    } else {
      chunks.push(payload(current));
      current = [event];
    }
  }
  if (current.length > 0) {
    chunks.push(payload(current));
  }
  return { chunks, tooBig };
}

/** Exponential backoff with full jitter: a random delay up to 1 s, 2 s, 4 s, ... 30 s. */
export function backoffMs(attempt: number): number {
  const cap = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** (attempt - 1));
  return Math.random() * cap;
}

/** Retry-After is either a number of seconds or an HTTP date. */
export function parseRetryAfter(header: string | null): number | null {
  if (header === null) {
    return null;
  }
  if (/^\d+$/.test(header.trim())) {
    return Number(header.trim()) * 1_000;
  }
  const date = Date.parse(header);
  return Number.isNaN(date) ? null : Math.max(0, date - Date.now());
}

function anonymousId(): string {
  // localStorage can be missing (Node) or throw (privacy modes, sandboxed iframes);
  // then the id lasts for this page session only.
  try {
    const stored = globalThis.localStorage.getItem(ANONYMOUS_ID_STORAGE_KEY);
    if (stored !== null) {
      return stored;
    }
  } catch {
    // fall through to a new id
  }
  const id = crypto.randomUUID();
  try {
    globalThis.localStorage.setItem(ANONYMOUS_ID_STORAGE_KEY, id);
  } catch {
    // memory only
  }
  return id;
}

function checkUserId(id: string): void {
  const length = [...id].length; // code points, as the API counts them
  if (length < 1 || length > 200 || hasLoneSurrogate(id)) {
    throw new TypeError("userId must be 1-200 characters of valid Unicode");
  }
}

function rejectedCount(body: unknown): number {
  if (typeof body === "object" && body !== null && "rejected" in body) {
    return Array.isArray(body.rejected) ? body.rejected.length : 0;
  }
  return 0;
}

function isConfig(body: unknown): body is Config {
  return (
    typeof body === "object" &&
    body !== null &&
    "config_version" in body &&
    typeof body.config_version === "number" &&
    "flags" in body &&
    Array.isArray(body.flags) &&
    "experiments" in body &&
    Array.isArray(body.experiments)
  );
}

function byteLength(text: string): number {
  return encoder.encode(text).length;
}
