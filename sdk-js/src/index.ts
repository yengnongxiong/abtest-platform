/**
 * abtest-platform SDK: feature flags and A/B test assignment in the browser and Node 22+,
 * with zero runtime dependencies (PRD §12).
 *
 *   const client = createClient({ clientKey, apiBaseUrl });
 *   await client.ready();
 *   if (client.getVariant("checkout-button") === "big-button") { ... }
 *   client.track("purchase", { value: 42 });
 */
export { createClient } from "./client";
export type { Client, ClientOptions, ClientStats, TrackOptions } from "./client";
export { SDK_NAME, SDK_VERSION } from "./version";
