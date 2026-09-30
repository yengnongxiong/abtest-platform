# sdk-js

The TypeScript SDK a website uses to get its variants and flags and to send events. It has zero runtime dependencies, runs in browsers and Node 22+, and is **2,556 bytes** min+gzip (ESM build, measured by `npm run build && npm run size`; CI fails above 5 KB). Assignment happens locally, byte-identical to the server's Python, so deciding a variant never waits on the network.

Read first:
- [src/client.ts](src/client.ts): config polling, exposure logging, batching, retries, and the page-unload beacon
- [src/assignment.ts](src/assignment.ts): MurmurHash3 and the assignment rules, checked against [shared/hash_test_vectors.json](../shared/hash_test_vectors.json)

## Using it

It isn't published to npm: build it with `npm run build` and use `dist/index.js` (ESM) or `dist/index.cjs`.

```ts
import { createClient } from "./sdk/index.js"; // sdk-js/dist/index.js

// clientKey: your project's client key (ABTEST_CLIENT_KEY in .env for the local stack)
const client = createClient({ clientKey, apiBaseUrl: "http://localhost:8000" });
await client.ready(); // never blocks longer than readyTimeoutMs (2 s by default)

if (client.getVariant("checkout-button") === "big-button") {
  // show the big button; the exposure is logged once per user per page session
}
if (client.isEnabled("dark-mode")) {
  // ...
}
client.track("purchase", { value: 49 });
```

- The SDK polls the config (with an ETag) every 30 s, and sends events in batches, retrying safely.
- When the page is hidden or closed, the queue goes out with `sendBeacon`.
- [ADR-014](../docs/decisions.md#adr-014-how-the-sdk-delivers-events-at-least-once-deduplicated-by-the-server-m5) describes the delivery guarantees.
- In Node, call `client.close()` when done, so its timers stop.
