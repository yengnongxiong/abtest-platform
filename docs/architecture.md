# Architecture

How abtest-platform works, in more detail than the [README](../README.md). The spec is [PRD.md](PRD.md); the reasons behind each choice, with the alternatives, are in [decisions.md](decisions.md).

## Contents
[Components](#components) · [The flow](#the-flow) · [Assignment](#assignment) · [Ingestion](#ingestion) · [Attribution](#attribution) · [Statistics](#statistics) · [Security and privacy](#security-and-privacy) · [Design decisions](#design-decisions)

## Components

| Component | Where | What it is |
|---|---|---|
| SDK | [sdk-js/](../sdk-js) | TypeScript, zero runtime dependencies, browsers and Node 22+ |
| API | [server/src/abtest/api/](../server/src/abtest/api) | FastAPI: public config and events endpoints (client key), admin endpoints (server key) |
| Worker | [server/src/abtest/worker/](../server/src/abtest/worker) | Results snapshots, and daily event partitions |
| Stats engine | [server/src/abtest/stats/](../server/src/abtest/stats) | Pure Python: no database, web, or I/O imports |
| Simulator | [server/src/abtest/simulator/](../server/src/abtest/simulator) | Monte Carlo validation, and a traffic generator that goes through the API |
| Dashboard | [web/](../web) | Next.js (App Router), Tailwind, Recharts |
| Demo page | [demo/](../demo) | A static page that uses the SDK the way a real site would |
| Schema | [db/migrations/](../db/migrations) | Plain SQL, applied by a small runner |

## The flow

1. The dashboard calls the admin API (with a server key that stays on the dashboard's server). Every config change bumps the project's `config_version`.
2. The SDK fetches the config, with the version as its ETag, and assigns variants locally: no network call per decision.
3. The SDK sends exposures and events in batches. The API stores them, drops duplicates, and records each user's first exposure.
4. The worker attributes events to variants and runs the statistics engine, writing a results snapshot per metric.
5. The dashboard reads the snapshots.

## Assignment
- A user's bucket is MurmurHash3 (x86, 32-bit, seed 0) of a UTF-8 string, mod 10,000: basis points.
- **Two independent hashes.** A user is in the experiment if the bucket of `"{experiment}:traffic:{user}"` is below the traffic allocation. The variant comes from a different input, `"{experiment}:variant:{user}"`, walked through the cumulative weights. Ramping traffic from 10% to 50% therefore admits new users without moving anyone already assigned ([ADR-012](decisions.md#adr-012-two-independent-hashes-one-for-inclusion-one-for-the-variant-m4)). Flags use a third input, `"{flag}:rollout:{user}"`.
- **Evaluated in the SDK** ([ADR-011](decisions.md#adr-011-flags-and-experiments-are-evaluated-in-the-sdk-not-on-the-server-m4)). The TypeScript port and the Python original must agree byte for byte. [shared/hash_test_vectors.json](../shared/hash_test_vectors.json) holds 57 hash, 45 assignment, and 27 flag vectors, covering unicode, emoji, the empty string, and every MurmurHash3 tail length, and both test suites check them. A million synthetic users split as weighted (a chi-square test, for 50/50 and 33/33/34).
- **Rules for running experiments.** Variants and weights are frozen, traffic can only go up, and stopping is final (clone to rerun). The API enforces all three.

## Ingestion
- `POST /v1/events` takes up to 500 events and stores them with **one** `INSERT ... SELECT FROM unnest(...) ON CONFLICT DO NOTHING RETURNING` per batch. The answer counts what was accepted and what was a duplicate, and lists each rejected event with its reason. One bad event never fails the batch.
- **Idempotent retries.** The SDK fixes each event's `event_id` and `occurred_at` when the event is created, and the primary key `(project_id, event_id, occurred_at)` turns a resent event into a counted duplicate. `occurred_at` has to be in the key, because the table is partitioned by it ([ADR-008](decisions.md#adr-008-daily-partitions-and-the-partition-key-in-the-primary-key-m3), [ADR-014](decisions.md#adr-014-how-the-sdk-delivers-events-at-least-once-deduplicated-by-the-server-m5)).
- **Exposures are derived at ingest time** ([ADR-009](decisions.md#adr-009-a-separate-exposures-table-derived-at-ingest-time-m3)). Newly stored `$exposure` events are upserted into an `exposures` table, one row per (experiment, user), in the same transaction:
  - the earliest exposure time wins;
  - a user seen in two variants is marked `conflicted` and left out of the analysis;
  - the server recomputes every assignment, and marks disagreements with the SDK as `assignment_mismatch`.
- **Rate limit.** Each client key gets an in-memory token bucket (100 requests/s, bursts of 200). Past that, the API answers 429 with `Retry-After`, and the SDK backs off ([ADR-015](decisions.md#adr-015-an-in-memory-rate-limiter-per-client-key-m6)).

## Attribution
For experiment E, metric M, and cutoff T (now, or the stop time) ([ADR-016](decisions.md#adr-016-how-events-are-attributed-and-the-limits-we-accept-m7)):
- The population is E's non-conflicted exposures. The sample-ratio check uses the same population.
- A user's events count if they fall in `[first exposure, min(first exposure + M's window, T))`.
  - A conversion metric counts users with at least one such event.
  - A mean metric sums each user's values, counting a user with no events as 0.
- One SQL query per (experiment, metric) aggregates n, sum, and sum of squares per variant. It bounds `occurred_at` on both sides, so Postgres reads only the daily partitions in between, and a covering index answers it without touching the table ([ADR-021](decisions.md#adr-021-a-covering-attribution-index-and-a-query-bounded-on-both-sides-m9)). It never uses a nested loop, which a table without planner statistics could otherwise pick ([ADR-023](decisions.md#adr-023-the-attribution-query-never-uses-a-nested-loop-health-check)).
- Hand-built tests cover each edge: an event before exposure, each end of the window (to the microsecond), repeat conversions, conflicted users, and users with zero or null values.

## Statistics
The engine ([server/src/abtest/stats/](../server/src/abtest/stats)) is pure and typed, and every formula's docstring cites its source ([ADR-004](decisions.md#adr-004-a-pure-statistics-engine-with-every-formula-written-out-m1)). Tests cross-check it against SciPy, statsmodels, and textbook examples.
- **Fixed horizon:** a two-proportion z-test (pooled standard error for the p-value, unpooled for the CI, delta method for the relative lift) and Welch's t-test for means.
- **Sequential, the default:** mSPRT (Johari et al., *Peeking at A/B Tests*). It gives an always-valid p-value and CI, which stay correct however often someone looks. Its mixing parameter τ is set per metric when the experiment starts: τ = expected baseline × smallest lift worth detecting ([ADR-005](decisions.md#adr-005-sequential-testing-with-msprt-and-how-%CF%84-is-chosen-m1)).
- **Sample ratio mismatch:** a chi-square goodness-of-fit test on the user counts, flagged at p < 0.001. A flagged experiment's results are marked untrustworthy.
- **Sample size:** users per variant for a conversion metric, fixed horizon, two-sided.
- **Insufficient data never crashes.** Fewer than 2 users in a variant, or no variation at all, gives an "insufficient data" result, not an error. A control rate of 0 leaves only the relative lift empty.
- **The worker** writes a snapshot for every metric of every running experiment every 5 minutes, plus a final one after a stop. The mSPRT's state carries from one snapshot to the next ([ADR-018](decisions.md#adr-018-results-are-precomputed-snapshots-not-computed-on-read-m7)). Advisory locks keep two workers from duplicating a job, and two looks from racing on that state ([ADR-017](decisions.md#adr-017-advisory-locks-for-the-workers-jobs-and-for-results-m7)).
- **The dashboard** turns a snapshot into a verdict (significant win, significant loss, not significant, can't be trusted, or insufficient data) and a sentence, for example: "Big button increased Purchase by 13.6% (95% always-valid CI +4.0% to +23.2%). This is statistically significant."

How well this works is measured, not assumed: [results/summary.md](results/summary.md) has the Monte Carlo validation, and [experiment-memo-example.md](experiment-memo-example.md) reads one simulated run as a PM would.

## Security and privacy
- **Two kinds of API key.** Client keys ship in every web page, so they're public by design. They can only read the config and send events. Server keys unlock the admin API and travel only in the `Authorization` header, never in a URL. The database stores only a SHA-256 hash of each key, and the plaintext is shown once, at creation. A server key never works as a client key, and a client key never works as a server key.
- **The dashboard keeps the server key on its own server.** The browser never talks to the admin API ([ADR-019](decisions.md#adr-019-dashboard-auth-one-password-a-signed-cookie-and-the-server-key-kept-on-the-server-m8)).
- **Parametrized SQL only.** Request limits: 1 MB per request, 500 events, 4 KB of properties per event, and event values within ±10¹². The body is read before a database connection is taken, so stalled uploads can't hold the pool ([ADR-024](decisions.md#adr-024-read-the-event-body-before-taking-a-database-connection-health-check)).
- **CORS** is open only on `/v1/*`, without credentials.
- **Secrets come from environment variables.** Every value in `.env.example` is for local use only, and a deployment must replace all of them.
- **Personal data.**
  - The platform stores no personal data beyond the user IDs integrators send, and treats them as opaque strings. Use a random or internal ID, not an email address.
  - Event properties are free-form JSON chosen by the integrating site, and they're stored as sent: **don't put personal data in them.**
  - The SDK's page-unload path sends the client key in a query string (a beacon can't set headers), so it can appear in access logs. That's acceptable only because client keys are public.

## Design decisions

Each has an ADR in [decisions.md](decisions.md): the context, the alternatives, and the consequences.

| Decision | Why, in one line |
|---|---|
| PostgreSQL alone: no queue, no columnar store ([ADR-007](decisions.md#adr-007-postgresql-alone-not-a-columnar-store-or-a-queue-m3)) | One store with transactional ingestion, so the API can say "stored" or "duplicate" per event. M9 measures how far one instance goes |
| Evaluate flags and experiments in the SDK ([ADR-011](decisions.md#adr-011-flags-and-experiments-are-evaluated-in-the-sdk-not-on-the-server-m4)) | No network call on the page's critical path; the server re-checks every assignment |
| Two independent hashes ([ADR-012](decisions.md#adr-012-two-independent-hashes-one-for-inclusion-one-for-the-variant-m4)) | Ramping traffic never moves a user to another variant |
| The config ETag is a version counter ([ADR-013](decisions.md#adr-013-the-config-etag-is-a-version-counter-bumped-in-the-same-transaction-m4)) | A poll that finds nothing new costs one indexed row read and a 304 |
| At-least-once delivery, deduplicated by the server ([ADR-014](decisions.md#adr-014-how-the-sdk-delivers-events-at-least-once-deduplicated-by-the-server-m5)) | Retries are always safe, with far less code than exactly-once in the client |
| The partition key in the primary key ([ADR-008](decisions.md#adr-008-daily-partitions-and-the-partition-key-in-the-primary-key-m3)) | Postgres requires it. The consequence: retries are idempotent only because the SDK fixes `event_id` and `occurred_at` once |
| A separate exposures table, derived at ingest time ([ADR-009](decisions.md#adr-009-a-separate-exposures-table-derived-at-ingest-time-m3)) | Attribution joins one row per user, and conflicts are detected once, not on every analysis |
| mSPRT, with τ set per metric ([ADR-005](decisions.md#adr-005-sequential-testing-with-msprt-and-how-%CF%84-is-chosen-m1)) | Results people can check whenever they like, for 1.75 to 1.88 times the users |
| Advisory locks ([ADR-017](decisions.md#adr-017-advisory-locks-for-the-workers-jobs-and-for-results-m7)) | Two workers never duplicate a job; two looks never race on the mSPRT's state |
| An in-memory rate limiter ([ADR-015](decisions.md#adr-015-an-in-memory-rate-limiter-per-client-key-m6)) | Correct for one API instance. With several, it would move to Redis or the load balancer |
| Precomputed snapshots ([ADR-018](decisions.md#adr-018-results-are-precomputed-snapshots-not-computed-on-read-m7)) | A page load is one indexed read, and the stored snapshots are the mSPRT's history of looks |
| A covering index, found with EXPLAIN ([ADR-021](decisions.md#adr-021-a-covering-attribution-index-and-a-query-bounded-on-both-sides-m9)) | The first index barely helped large experiments. `INCLUDE (value)` made it an index-only scan |
| No hosted deployment ([ADR-022](decisions.md#adr-022-no-hosted-deployment-the-local-stack-is-the-demo-m10)) | No hosting budget; the quickstart, the screenshots, and CI show it working instead |
