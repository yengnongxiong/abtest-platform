import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  ANONYMOUS_ID_STORAGE_KEY,
  backoffMs,
  chunkUnder,
  createClient,
  MAX_UNLOAD_BYTES,
  parseRetryAfter,
  type Client,
  type ClientOptions,
} from "../src/client";
import { SDK_NAME, SDK_VERSION } from "../src/version";

const CONFIG = {
  config_version: 3,
  flags: [{ key: "dark-mode", enabled: true, rollout_bp: 10_000 }],
  experiments: [
    {
      key: "checkout-button",
      traffic_bp: 10_000,
      variants: [
        { key: "control", weight_bp: 5000, position: 0 },
        { key: "treatment", weight_bp: 5000, position: 1 },
      ],
    },
    { key: "paused", traffic_bp: 0, variants: [{ key: "only", weight_bp: 10_000, position: 0 }] },
  ],
};

interface Call {
  url: string;
  init: RequestInit | undefined;
}

/** A fake API: records every request and answers with the given handlers. */
function fakeApi(handlers: {
  config?: (call: Call) => Response | Promise<Response>;
  events?: (call: Call) => Response | Promise<Response>;
}) {
  const calls: Call[] = [];
  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const call = { url: String(input), init };
    calls.push(call);
    if (call.url.includes("/v1/config")) {
      return Promise.resolve(
        (handlers.config ?? (() => json(CONFIG, { ETag: '"config-3"' })))(call),
      );
    }
    return Promise.resolve((handlers.events ?? (() => json(accepted())))(call));
  });
  const eventBatches = () =>
    calls
      .filter((c) => c.url.includes("/v1/events"))
      .map((c) => JSON.parse(String(c.init?.body)) as { sdk: unknown; events: WireEvent[] });
  return { fetch, calls, eventBatches };
}

interface WireEvent {
  event_id: string;
  user_id: string;
  name: string;
  occurred_at: string;
  value?: number;
  properties?: Record<string, unknown>;
}

function json(body: unknown, headers: Record<string, string> = {}, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers });
}

function accepted(rejected: unknown[] = []): unknown {
  return { accepted: 1, duplicates: 0, rejected };
}

let clients: Client[] = [];

function client(api: ReturnType<typeof fakeApi>, options: Partial<ClientOptions> = {}): Client {
  const created = createClient({
    clientKey: "ck_test",
    apiBaseUrl: "https://api.example/",
    userId: "user-1",
    fetch: api.fetch,
    ...options,
  });
  clients.push(created);
  return created;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(async () => {
  await Promise.all(clients.map((c) => c.close()));
  clients = [];
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("config and evaluation", () => {
  test("evaluates locally once the config has loaded", async () => {
    const api = fakeApi({});
    const sdk = client(api);
    expect(sdk.getVariant("checkout-button")).toBeNull(); // not loaded yet: never blocks

    await sdk.ready();

    expect(["control", "treatment"]).toContain(sdk.getVariant("checkout-button"));
    expect(sdk.isEnabled("dark-mode")).toBe(true);
    expect(sdk.isEnabled("unknown")).toBe(false);
    expect(sdk.getVariant("unknown")).toBeNull();
    expect(api.calls[0]?.init?.headers).toEqual({ "X-Client-Key": "ck_test" });
    expect(api.calls[0]?.url).toBe("https://api.example/v1/config");
  });

  test("ready() gives up after readyTimeoutMs instead of blocking the page", async () => {
    const api = fakeApi({ config: () => new Promise<Response>(() => undefined) });
    const sdk = client(api, { readyTimeoutMs: 2_000 });
    let isReady = false;
    void sdk.ready().then(() => {
      isReady = true;
    });

    await vi.advanceTimersByTimeAsync(1_999);
    expect(isReady).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(isReady).toBe(true);
    expect(sdk.getVariant("checkout-button")).toBeNull();
  });

  test("polls with If-None-Match and keeps the last good config", async () => {
    let answer: () => Response = () => json(CONFIG, { ETag: '"config-3"' });
    const api = fakeApi({ config: () => answer() });
    const sdk = client(api, { configPollIntervalMs: 30_000 });
    await sdk.ready();

    answer = () => new Response(null, { status: 304 });
    await vi.advanceTimersByTimeAsync(30_000);
    answer = () => json({ error: "boom" }, {}, 500);
    await vi.advanceTimersByTimeAsync(30_000);

    const configCalls = api.calls.filter((c) => c.url.endsWith("/v1/config"));
    expect(configCalls).toHaveLength(3);
    expect(configCalls[1]?.init?.headers).toEqual({
      "X-Client-Key": "ck_test",
      "If-None-Match": '"config-3"',
    });
    expect(sdk.isEnabled("dark-mode")).toBe(true); // still the config from the first load
  });
});

describe("robustness", () => {
  test("a slow, older config never replaces a newer one", async () => {
    const releaseOld: { resolve?: (r: Response) => void } = {};
    let call = 0;
    const api = fakeApi({
      config: () => {
        call += 1;
        if (call === 1) {
          return new Promise<Response>((resolve) => (releaseOld.resolve = resolve));
        }
        return json({ ...CONFIG, config_version: 4, flags: [] }, { ETag: '"config-4"' });
      },
    });
    const sdk = client(api, { configPollIntervalMs: 1_000, readyTimeoutMs: 10 });

    await vi.advanceTimersByTimeAsync(1_000); // the second poll answers first: version 4
    releaseOld.resolve?.(json(CONFIG, { ETag: '"config-3"' })); // then the first: version 3
    await vi.advanceTimersByTimeAsync(0);

    expect(sdk.isEnabled("dark-mode")).toBe(false); // still version 4, which has no flags
  });

  test("a malformed config makes getVariant return null instead of throwing", async () => {
    const broken = {
      ...CONFIG,
      experiments: [{ key: "broken", traffic_bp: 10_000, variants: [] }],
    };
    const sdk = client(fakeApi({ config: () => json(broken) }));
    await sdk.ready();

    expect(sdk.getVariant("broken")).toBeNull();
  });
});

describe("exposures", () => {
  test("are logged once per experiment and user per page session", async () => {
    const api = fakeApi({});
    const sdk = client(api);
    await sdk.ready();

    const variant = sdk.getVariant("checkout-button");
    sdk.getVariant("checkout-button");
    sdk.setUser("user-2");
    sdk.getVariant("checkout-button");
    sdk.getVariant("paused"); // traffic 0: not in the experiment, so no exposure
    await sdk.flush();

    const events = api.eventBatches().flatMap((b) => b.events);
    expect(events.map((e) => [e.name, e.user_id])).toEqual([
      ["$exposure", "user-1"],
      ["$exposure", "user-2"],
    ]);
    expect(events[0]?.properties).toEqual({
      experiment_key: "checkout-button",
      variant_key: variant,
    });
  });
});

describe("tracking and batching", () => {
  test("events carry an id and a time fixed at track() time", async () => {
    const api = fakeApi({});
    const sdk = client(api);

    sdk.track("purchase", { value: 42.5, properties: { plan: "pro" } });
    await sdk.flush();

    const [batch] = api.eventBatches();
    expect(batch?.sdk).toEqual({ name: SDK_NAME, version: SDK_VERSION });
    const event = batch?.events[0];
    expect(event).toMatchObject({ name: "purchase", user_id: "user-1", value: 42.5 });
    expect(event?.properties).toEqual({ plan: "pro" });
    expect(event?.event_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(event?.occurred_at).toBe(new Date().toISOString());
  });

  test("a full batch is sent at once; a partial one waits for the flush interval", async () => {
    const api = fakeApi({});
    const sdk = client(api, { maxBatchSize: 3, flushIntervalMs: 5_000 });

    for (let i = 0; i < 3; i++) sdk.track(`event-${String(i)}`);
    await vi.advanceTimersByTimeAsync(0);
    expect(api.eventBatches().map((b) => b.events.length)).toEqual([3]);

    sdk.track("event-3");
    await vi.advanceTimersByTimeAsync(4_000);
    expect(api.eventBatches()).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(api.eventBatches().map((b) => b.events.length)).toEqual([3, 1]);
    expect(sdk.stats()).toEqual({ queued: 0, dropped: 0 });
  });

  test("past maxQueueSize the oldest events are dropped and counted", async () => {
    const api = fakeApi({});
    const sdk = client(api, { maxQueueSize: 3, maxBatchSize: 50 });

    for (let i = 0; i < 5; i++) sdk.track(`event-${String(i)}`);

    expect(sdk.stats()).toEqual({ queued: 3, dropped: 2 });
    await sdk.flush();
    expect(api.eventBatches()[0]?.events.map((e) => e.name)).toEqual([
      "event-2",
      "event-3",
      "event-4",
    ]);
  });

  test("an event JSON can't encode is dropped alone and never blocks later events", async () => {
    // Regression: such an event used to fail its batch on every retry, forever.
    const api = fakeApi({});
    const sdk = client(api);
    const circular: Record<string, unknown> = {};
    circular.self = circular;

    sdk.track("bad", { properties: circular });
    sdk.track("bad-value", { value: Number.NaN });
    sdk.track("good");
    await sdk.flush();

    expect(api.eventBatches().flatMap((b) => b.events.map((e) => e.name))).toEqual(["good"]);
    expect(sdk.stats()).toEqual({ queued: 0, dropped: 2 });
  });

  test("properties are snapshotted at track() time", async () => {
    const api = fakeApi({});
    const sdk = client(api);
    const properties = { plan: "pro" };

    sdk.track("purchase", { properties });
    properties.plan = "changed later";
    await sdk.flush();

    expect(api.eventBatches()[0]?.events[0]?.properties).toEqual({ plan: "pro" });
  });

  test("events the API rejects are counted as dropped", async () => {
    const api = fakeApi({ events: () => json(accepted([{ index: 0, reason: "bad name" }]), {}, 202) });
    const sdk = client(api);

    sdk.track("purchase");
    await sdk.flush();

    expect(sdk.stats()).toEqual({ queued: 0, dropped: 1 });
  });
});

describe("retries", () => {
  test("a 5xx is retried with jittered backoff, resending identical events", async () => {
    let status = 503;
    const api = fakeApi({ events: () => json({}, {}, status) });
    vi.spyOn(Math, "random").mockReturnValue(0.5); // first backoff: 0.5 x 1 s
    const sdk = client(api, { flushIntervalMs: 100 });

    sdk.track("purchase");
    await sdk.flush();
    expect(sdk.stats().queued).toBe(1);

    status = 202;
    await vi.advanceTimersByTimeAsync(400); // flush ticks inside the backoff do nothing
    expect(api.eventBatches()).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(200);
    const batches = api.eventBatches();
    expect(batches).toHaveLength(2);
    expect(batches[1]?.events).toEqual(batches[0]?.events); // same event_id and occurred_at
    expect(sdk.stats()).toEqual({ queued: 0, dropped: 0 });
  });

  test("a 429 waits for Retry-After", async () => {
    let first = true;
    const api = fakeApi({
      events: () => {
        const response = first ? json({}, { "Retry-After": "3" }, 429) : json(accepted(), {}, 202);
        first = false;
        return response;
      },
    });
    const sdk = client(api, { flushIntervalMs: 500 });

    sdk.track("purchase");
    await sdk.flush();
    await vi.advanceTimersByTimeAsync(2_900);
    expect(api.eventBatches()).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(api.eventBatches()).toHaveLength(2);
  });

  test("a network error is retried", async () => {
    let fail = true;
    const api = fakeApi({
      events: () => {
        if (fail) {
          fail = false;
          throw new TypeError("network down");
        }
        return json(accepted(), {}, 202);
      },
    });
    vi.spyOn(Math, "random").mockReturnValue(0);
    const sdk = client(api, { flushIntervalMs: 100 });

    sdk.track("purchase");
    await sdk.flush();
    await vi.advanceTimersByTimeAsync(100);

    expect(sdk.stats()).toEqual({ queued: 0, dropped: 0 });
  });

  test("a 4xx other than 429 is not retried: the batch is dropped and counted", async () => {
    const api = fakeApi({ events: () => json({}, {}, 400) });
    const sdk = client(api, { flushIntervalMs: 100 });

    sdk.track("purchase");
    await sdk.flush();
    await vi.advanceTimersByTimeAsync(1_000);

    expect(api.eventBatches()).toHaveLength(1);
    expect(sdk.stats()).toEqual({ queued: 0, dropped: 1 });
  });

  test("backoff is full jitter, doubling from 1 s up to 30 s", () => {
    vi.spyOn(Math, "random").mockReturnValue(0.999999);
    expect([1, 2, 3, 6, 10].map((a) => Math.round(backoffMs(a)))).toEqual([
      1_000, 2_000, 4_000, 30_000, 30_000,
    ]);
    vi.spyOn(Math, "random").mockReturnValue(0);
    expect(backoffMs(5)).toBe(0);
  });

  test("Retry-After is seconds or an HTTP date", () => {
    vi.setSystemTime(new Date("2026-09-28T12:00:00Z")); // HTTP dates have 1-second precision
    expect(parseRetryAfter("3")).toBe(3_000);
    expect(parseRetryAfter(new Date(Date.now() + 5_000).toUTCString())).toBe(5_000);
    expect(parseRetryAfter("soon")).toBeNull();
    expect(parseRetryAfter(null)).toBeNull();
  });
});

describe("unload: the beacon path", () => {
  function fakePage(sendBeaconResult = true) {
    const page = new EventTarget() as EventTarget & { visibilityState: string };
    page.visibilityState = "visible";
    const sendBeacon = vi.fn<(url: string, body: string) => boolean>(() => sendBeaconResult);
    vi.stubGlobal("document", page);
    vi.stubGlobal("navigator", { sendBeacon });
    const hide = () => {
      page.visibilityState = "hidden";
      page.dispatchEvent(new Event("visibilitychange"));
    };
    return { sendBeacon, hide };
  }

  test("hiding the page sends the queue by beacon, as text/plain with the key in the URL", () => {
    const { sendBeacon, hide } = fakePage();
    const api = fakeApi({});
    const sdk = client(api);
    sdk.track("purchase");

    hide();

    expect(sendBeacon).toHaveBeenCalledTimes(1);
    const [url, body] = sendBeacon.mock.calls[0] ?? [];
    expect(url).toBe("https://api.example/v1/events?client_key=ck_test");
    // A string body is sent as text/plain, so the browser needs no CORS preflight.
    expect(typeof body).toBe("string");
    expect((JSON.parse(String(body)) as { events: WireEvent[] }).events[0]?.name).toBe("purchase");
    expect(sdk.stats().queued).toBe(0);
  });

  test("falls back to a keepalive fetch when the beacon is refused", () => {
    const { hide } = fakePage(false);
    const api = fakeApi({});
    const sdk = client(api);
    sdk.track("purchase");

    hide();

    const unloadCall = api.calls.find((c) => c.url.includes("client_key="));
    expect(unloadCall?.init).toMatchObject({
      method: "POST",
      keepalive: true,
      headers: { "Content-Type": "text/plain" },
    });
  });

  test("payloads are split under the browser's 64 KB cap; an event too big alone is dropped", () => {
    const event = (size: number) => ({
      event_id: "00000000-0000-0000-0000-000000000000",
      user_id: "user-1",
      name: "big",
      occurred_at: "2026-09-28T00:00:00.000Z",
      properties: { padding: "x".repeat(size) },
    });
    const events = [event(25_000), event(25_000), event(25_000), event(70_000), event(10)];

    const { chunks, tooBig } = chunkUnder(events, MAX_UNLOAD_BYTES);

    expect(tooBig).toBe(1);
    expect(chunks.map((c) => (JSON.parse(c) as { events: unknown[] }).events.length)).toEqual([
      2, 2,
    ]);
    for (const chunk of chunks) {
      expect(new TextEncoder().encode(chunk).length).toBeLessThanOrEqual(MAX_UNLOAD_BYTES);
    }
  });
});

describe("users", () => {
  function anonymousClient(api: ReturnType<typeof fakeApi>): Client {
    const created = createClient({ clientKey: "ck_test", apiBaseUrl: "https://api.example", fetch: api.fetch });
    clients.push(created);
    return created;
  }

  test("an anonymous id is generated once and kept in localStorage", async () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
    });
    const api = fakeApi({});

    for (const sdk of [anonymousClient(api), anonymousClient(api)]) {
      sdk.track("visit");
      await sdk.flush();
    }

    const stored = store.get(ANONYMOUS_ID_STORAGE_KEY);
    expect(stored).toMatch(/^[0-9a-f-]{36}$/);
    const users = api.eventBatches().flatMap((b) => b.events.map((e) => e.user_id));
    expect(users).toEqual([stored, stored]); // the second page load reused the stored id
  });

  test("works without localStorage, or when it throws", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
    });

    expect(() => anonymousClient(fakeApi({}))).not.toThrow();
  });

  test.each(["", "x".repeat(201), "user-\ud800"])("refuses the user id %j", (userId) => {
    expect(() => client(fakeApi({}), { userId })).toThrow(TypeError);
  });
});

describe("close", () => {
  test("stops polling and flushing, after one last flush", async () => {
    const api = fakeApi({});
    const sdk = client(api, { flushIntervalMs: 100, configPollIntervalMs: 100 });
    sdk.track("purchase");

    await sdk.close();
    const callsAtClose = api.calls.length;
    await vi.advanceTimersByTimeAsync(1_000);
    sdk.track("after-close");

    expect(api.eventBatches()).toHaveLength(1);
    expect(api.calls.length).toBe(callsAtClose);
    expect(sdk.stats()).toEqual({ queued: 0, dropped: 1 });
  });
});
