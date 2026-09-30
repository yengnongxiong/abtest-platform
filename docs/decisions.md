# Design decisions

Architecture Decision Records (ADRs). Each one says what we decided, what we rejected, and what it costs us. Newest decisions go at the bottom.

---

## ADR-001: Local development with Docker Compose Watch (M0)

**Context.** `make dev` has to start five services (db, api, worker, web, demo) from a fresh clone and still give a fast edit–reload loop. The usual approach is to bind-mount source directories into containers. That hides the dependencies installed in the image (`node_modules`, `.venv`) unless you add anonymous-volume workarounds. It also depends on how each Docker runtime shares host folders: Colima, for example, shares only the home directory by default.

**Decision.** Each image contains its code and dependencies, and `docker compose up --build --watch` copies source changes into the running containers (`develop.watch` in `docker-compose.yml`):
- api: `sync` of `server/src`; uvicorn's `--reload` restarts the app.
- worker: `sync+restart`, because the worker has no reloader.
- web: `sync` of `web/` (excluding `node_modules/` and `.next/`); `next dev` hot-reloads.
- demo: `sync` of the static files into nginx.
- A change to a lock file (`uv.lock`, `package-lock.json`) triggers a `rebuild`.

Nothing is bind-mounted.

**Alternatives considered.**
- Bind mounts plus anonymous volumes for `node_modules` and `.venv`: the classic pattern, but more moving parts, and fragile across Docker runtimes.
- Running the services on the host: needs local Postgres, Python, and Node versions kept in step with the images.
- Production-style images with no reload: simple, but every change needs a rebuild.

**Consequences.**
- The stack behaves the same on Docker Desktop, OrbStack, Colima, and in CI (the `compose-smoke` job).
- Sync is one-way. Files written inside a container never appear on the host, which is fine because we edit on the host.
- These are development images. Production images (no reload, `next build`/`next start`) come with deployment in M10.

---

## ADR-002: Synchronous database access (M0)

**Context.** The API, the worker, and the simulator's traffic generator all talk to Postgres. From M3 onward they share the query modules in `abtest.db`.

**Decision.** Use psycopg 3's synchronous `ConnectionPool`, and write route handlers as plain `def`. FastAPI runs plain `def` handlers in a threadpool, so a slow query doesn't block other requests.

**Alternatives considered.** Fully async code (`AsyncConnectionPool` and `async def` handlers). It can hold more concurrent connections per process without threads. But either the worker and simulator would have to become async too, or the query code would exist twice. Async also has its own failure mode: one accidental blocking call stalls every request on the event loop.

**Consequences.**
- One copy of every query, callable from the API, the worker, scripts, and tests.
- Per-process concurrency is bounded by FastAPI's threadpool and the connection pool size. M6 sets the pool size explicitly.
- The M9 load test will show whether this is a bottleneck. If it is, the first fix is more uvicorn worker processes, not a rewrite to async.

---

## ADR-003: Toolchain versions and pins (M0, versions looked up 2026-09-27)

**Context.** The PRD says to use current stable versions, looked up rather than guessed. Several "latest" versions turned out to be incompatible with each other.

**Decision.**

| Tool | Choice | Why |
|---|---|---|
| Python | 3.14 (`requires-python >=3.14`) | Current stable. Every native dependency we need ships 3.14 wheels for macOS arm64 and Linux. |
| Node | 24 LTS (`.nvmrc`) for Docker and CI | Node 25 is end-of-life, and Node 26 becomes LTS only on 2026-10-28. vitest 5 supports 22, 24, and 26+. The SDK itself targets Node 22+ (PRD §12). |
| TypeScript | 6.0.3, pinned exactly, in both `sdk-js` and `web` | TypeScript 7 (the Go-native compiler) is "latest", but typescript-eslint 8.70 only supports `<6.1.0`. |
| PostgreSQL | 16.15 (`postgres:16.15-alpine`, the same tag locally and in CI) | The PRD names version 16. 18 is newer; moving later is a one-tag change. |
| SDK bundler | tsup 8.5.1 | Named in the PRD. Its README now says it is no longer actively maintained (see Consequences). |
| Web | Versions from `create-next-app@16.3.6` (Next 16.3.6, React 19.2.8, ESLint 9), except TypeScript | These are the combinations the Next.js team tests, and eslint-config-next's plugins target ESLint 9. The SDK uses ESLint 10. |
| CI actions | `actions/checkout@v7`, `actions/setup-node@v7`, `astral-sh/setup-uv@v10.2.0` | setup-uv publishes no floating major tag, so it's pinned to an exact release. |

npm dependencies are saved with exact versions (`--save-exact`), and the lock files (`uv.lock`, `package-lock.json`) pin everything transitively.

**Alternatives considered.**
- TypeScript 7: breaks linting today.
- tsdown (tsup's successor): pre-1.0, and not in the PRD's stack.
- PostgreSQL 18: the PRD says 16.

**Consequences.**
- tsup has already cost us one workaround. Its declaration build always sets `baseUrl`, which TypeScript 6 deprecates, so `tsup.config.ts` silences that one deprecation for the declaration step only. Moving to TypeScript 7 will mean replacing tsup, most likely with tsdown.
- Starlette 1.7 deprecates `httpx` for its test client in favor of `httpx2`, httpx's maintained successor (same author, now under the Pydantic org; httpx's last release was December 2024). We switched to `httpx2` after M0, with approval (PRD v1.2).
- Revisit Node when 26 becomes LTS (2026-10-28), and TypeScript when typescript-eslint supports 7.

---

## ADR-004: A pure statistics engine, with every formula written out (M1)

**Context.** The worker (M7), the Monte Carlo simulator (M2), and the sample-size endpoint (M4) all need the statistics, and the project's credibility rests on those numbers being right. PRD §14 fixes the formulas. The open questions were what the engine takes as input, whether to write the formulas ourselves or call library test functions, and what happens when there isn't enough data.

**Decision.**
- **Inputs are per-variant summaries, not raw rows.** `ProportionSummary(n, successes)` for conversions and `MeanSummary(n, sum, sum_sq)` for means. SQL reduces every user to these numbers in one pass, and the engine never touches the database. Both types expose the variant's mean and the variance of that mean, which is all the mSPRT and the delta method need, so one implementation of each serves both metric kinds.
- **Every formula is written out in `abtest/stats`.** SciPy supplies only distribution functions (normal, t, chi-square), and each docstring cites its source.
- **Tests compare against independent implementations**: statsmodels (`proportions_ztest`, `confint_proportions_2indep`, `CompareMeans`, `samplesize_proportions_2indep_onetail`), SciPy (`ttest_ind`, `chisquare`, normal densities for the mSPRT likelihood ratio), and textbook examples that we recomputed before using. statsmodels stays test-only, as the PRD requires.
- **Insufficient data returns a result, never an exception.** `ComparisonResult` leaves every number as `None` and says why in `insufficient_data`. Invalid arguments (alpha outside (0, 1), more conversions than users) are caller bugs and raise `ValueError`.
- `StatisticalTest[S]` is a generic abstract base class with one method, `compare(control, treatment, alpha)`. `MSPRT.compare` adds an optional `previous` state (ADR-005).
- A test (`test_stats_purity.py`) parses every module in `abtest/stats` and fails on any import outside an allowlist (a few standard-library modules such as `math` and `dataclasses`, plus SciPy), so the "no DB, web, or I/O" rule is enforced, not just promised.

**Alternatives considered.**
- *Call statsmodels or SciPy test functions in production code.* Less code, but the cross-check tests would then compare a library with itself, and statsmodels pulls pandas and patsy into the server image.
- *A vectorized NumPy engine that analyzes many simulated experiments at once.* Faster for M2, but harder to read and to type-check. M2 can vectorize the data generation and still call this engine for each analysis.
- *Raw per-user rows as input.* The worker would have to load every user of every experiment into memory.

**Consequences.**
- A formula change has to keep agreeing with statsmodels, SciPy, and the textbook examples.
- `MeanSummary` computes the variance with the one-pass formula `(sum_sq - sum^2/n) / (n - 1)`. It loses precision only when the variance is tiny relative to the squared mean, which per-user metric values are not; rounding below zero is clamped to 0.
- SciPy becomes a runtime dependency of the server (it is in the PRD's stack).

---

## ADR-005: Sequential testing with mSPRT, and how τ is chosen (M1)

**Context.** The dashboard shows results that refresh every few minutes, and PMs look at them whenever they like. A fixed-horizon p-value that is checked repeatedly, with the experiment stopped at the first p < 0.05, has a false-positive rate far above 5%: the "peeking" problem this project exists to demonstrate. The analysis has to stay valid under continuous monitoring, at looks nobody planned in advance.

**Decision.**
- Use the mixture sequential probability ratio test (mSPRT) with a normal mixing distribution and the normal approximation (Johari, Koomen, Pekelis & Walsh, KDD 2017). It gives an always-valid p-value, the running minimum of 1/Λ, and an always-valid CI, the running intersection of the per-look intervals. `analysis_type` defaults to `sequential`.
- **τ (the mixing standard deviation) is set per metric, at start:** τ = expected_baseline × mde_relative, in the metric's own units (PRD v1.1). The guarantee holds for any fixed τ; putting τ on each metric's scale is what gives each metric sensible power.
- **The caller carries the state between looks.** `compare(..., previous=state)` returns the new running-minimum p-value and CI intersection. The engine stays pure, and the worker (M7) stores the state with each snapshot.
- **The p-value and the CI use the same unpooled variance**, so "significant" and "the CI excludes 0" always agree, which a test checks at every look. The fixed-horizon z-test can't promise this, because its p-value uses the pooled standard error.
- **Computed in log space**, because Λ itself overflows a float once the evidence is overwhelming.
- **An empty CI intersection is shown as no CI.** When looks disagree so much that no effect size is consistent with all of them (probability at most α when the model holds; in practice a sign that the effect changed over time), the p-value and verdict are still reported.
- The relative-lift CI is the absolute CI divided by the control mean: a plug-in approximation, labeled as such in the UI (M8).

**Alternatives considered.**
- *Group sequential designs* (O'Brien–Fleming boundaries, alpha spending). They have more power at the planned looks, but need the number and timing of looks, and a maximum sample size, fixed upfront. A dashboard is looked at whenever someone opens it.
- *Bayesian analysis.* A non-goal for the MVP (PRD §3).
- *Fixed-horizon tests only, and trusting people not to peek.* That is exactly the failure mode the project demonstrates.
- *One τ for every metric of an experiment.* Still valid, but a τ sized for a 10% conversion rate is badly scaled for revenue in dollars, so secondary and guardrail metrics would lose power.

**Consequences.**
- At the same sample size, mSPRT intervals are wider than fixed-horizon ones: that is the price of being allowed to look at any time. M2's power simulation measures the cost.
- A badly chosen τ costs power, never validity.
- The guarantee assumes the normal approximation with a variance estimated from the data. M2's `aa_sequential` scenario measures the real false-positive rate.
- The worker must keep the state for each (experiment, metric) pair and never reset it. `/recompute` just adds a look, which is safe under mSPRT.

---

## ADR-006: How the Monte Carlo validation simulates and judges (M2)

**Context.** PRD §16A asks for simulations that prove the statistics behave as claimed: false-positive rates under A/A tests (analyzed once, peeked at, and sequentially), power, SRM detection, and Welch on skewed data. The suite has to be seeded, use the real stats engine, and run in about two minutes on a laptop. The results go in the README, so the way they're judged has to be defensible.

**Decision.**
- **Users arrive in blocks, simulated with binomial draws.** For each block of users (one per look), the number landing in control is Binomial(block, 1/2), and each variant's conversions are Binomial(users, rate). That is exactly the distribution you'd get by simulating each user, at the cost of a few array operations per experiment, whatever the sample size.
- **Only the data generation is vectorized.** Every analysis calls the real engine (`TwoProportionZTest`, `MSPRT` with its carried state, `srm_check`, `WelchTTest`, `verdict`) once per experiment per look, as the worker will.
- **One independent random stream per scenario** (`SeedSequence(seed).spawn`). Resizing one scenario never changes another's numbers, and a rerun with the same seed reproduces every number in `docs/results/summary.md`; only the runtime line changes (checked by running it twice and diffing, on one machine).
- **Pass/fail comes from binomial error, not taste.** The acceptance range for a false-positive rate is the central 95% range of Binomial(experiments, α) / experiments, where a perfectly calibrated test lands 95% of the time. Every rate is reported with an exact (Clopper–Pearson) 95% CI. The fast pytest versions allow 3.29 binomial standard errors, and compare against theory where it exists: the z-test's analytic power curve, and the noncentral chi-square power of the SRM check.
- **Power counts a detection only as a significant *win*,** judged by the real `verdict`. τ is baseline × the true lift, as if the PM had pre-registered the true effect as the MDE.
- **SRM false alarms are measured at one look and across 20 looks.** The PRD asks for one false-alarm rate. The repeated-look rate is what a dashboard that re-checks every snapshot actually produces.

**Alternatives considered.**
- *Simulating individual users with hashed IDs.* Closer to production, but orders of magnitude slower. Hash uniformity gets its own chi-square test in M4.
- *Vectorized re-implementations of the tests for speed.* That would validate a copy of the engine, not the engine.
- *Hand-picked pass bands* (say, "the false-positive rate is between 4% and 6%"). Arbitrary, and too loose or too strict depending on the number of experiments.

**Consequences.**
- `make simulate` takes about half a minute on a laptop. The measured runtime and machine are written into `summary.md` on every run.
- The simulated traffic is stationary and independent: no day-of-week cycles, novelty effects, or correlated users. The results say the math is right, not that real traffic is this well behaved.
- The look schedule (a look every 1,000 users in the A/A scenarios) is part of the result: naive peeking gets worse with more looks, and the mSPRT does not.

---

## ADR-007: PostgreSQL alone, not a columnar store or a queue (M3)

**Context.** Event ingestion and attribution are the data-heavy parts of the system. Platforms at large scale put a queue (Kafka, Kinesis) in front of ingestion and run analysis on a columnar warehouse (ClickHouse, BigQuery, Snowflake). This project runs on one machine; the PRD targets millions of events (M9 measures 1M and 10M).

**Decision.** PostgreSQL 16 is the only data store. It is the source of truth for configuration, exposures, and raw events. Batches are inserted in one `INSERT ... SELECT FROM unnest(...)` statement per request (M6), the events table is range-partitioned by day (ADR-008), and results are precomputed into snapshots by the worker (M7), so no request scans events.

**Alternatives considered.**
- *A queue between the API and the database.* It absorbs bursts and decouples ingestion from storage, but adds a service to run, and the API could no longer tell the SDK "stored" or "duplicate" per event in its 202 response (PRD §11), because the write would happen later.
- *A columnar store for events.* Much faster aggregation at billions of rows, but a second database to keep consistent with exposures and configuration, and no transactional `ON CONFLICT` for duplicate detection.

**Consequences.**
- One service to run, back up, and reason about, and transactional guarantees everywhere: an ingest batch's events and exposures commit together.
- Throughput is bounded by one Postgres instance. M9 measures how far that goes; the numbers belong in `docs/performance.md`, not here.
- The migration path is clear if it's ever needed: put a queue in front of the same insert code, or stream events to a columnar store and keep Postgres for configuration and exposures.

---

## ADR-008: Daily partitions, and the partition key in the primary key (M3)

**Context.** Events arrive continuously, and every attribution query is bounded in time (events after the experiment started, PRD §13). Old events stop mattering once their experiments end.

**Decision.**
- `events` is range-partitioned by `occurred_at`, one partition per UTC day, plus a default partition. The SQL function `ensure_event_partitions(days_ahead)` creates missing partitions from today − 7 days (the API accepts events up to 7 days old) through today + `days_ahead`. The bootstrap calls it after every migration, and the worker daily (M7). It serializes callers with an advisory lock, and writes its bounds as UTC timestamps, so the session's time zone doesn't matter; a test checks both.
- The primary key is `(project_id, event_id, occurred_at)`. Postgres requires every unique constraint on a partitioned table to include the partition key, so uniqueness is only enforced per partition.
- Partitions are created 14 days ahead, so creating one (which briefly locks the parent table) never happens on the busy current day.

**Alternatives considered.**
- *No partitioning.* Simpler, but every attribution query would scan an ever-growing index, and deleting old events would be a slow bulk `DELETE` instead of dropping a table.
- *A unique index on `event_id` alone.* Postgres doesn't allow one on a partitioned table. A separate dedup table keyed by `event_id` would work, at the cost of a second write for every event.
- *Partitioning by `received_at`.* That would make the key server-controlled, but queries filter on `occurred_at`, so they would no longer skip partitions.

**Consequences.**
- **Duplicate detection depends on the client.** Two rows with the same `event_id` but different `occurred_at` are both stored. Retries are idempotent only because the SDK fixes `event_id` and `occurred_at` once, at `track()` time (PRD §12). A test pins this behavior down.
- Time-bounded queries read only the partitions they need; a test checks the plan. M7 and M9 confirm it on the real attribution query.
- **The default partition must stay empty.** An event for a day that has no partition lands in the default partition, and from then on Postgres refuses to create that day's partition. A test demonstrates this. With partitions 14 days ahead and the API rejecting events more than 5 minutes in the future, that only happens if the worker is down for two weeks.

---

## ADR-009: A separate exposures table, derived at ingest time (M3)

**Context.** Attribution needs, for every user in an experiment, the variant they saw and when they first saw it (PRD §13). That could be computed from the raw `$exposure` events on every analysis, or kept up to date in its own table as events arrive.

**Decision.** Keep an `exposures` table with one row per (experiment, user), updated in the same transaction as the event insert (M6). Every `$exposure` event is also stored in `events` (PRD v1.1), so there's one duplicate check and a raw audit log. Only newly inserted exposure events update `exposures`, so a retried batch never touches it twice. The upsert (`abtest/db/exposures.py`) first merges the batch per (experiment, user), because Postgres rejects an `ON CONFLICT DO UPDATE` that affects the same row twice in one statement. It then keeps the earliest time and marks the user `conflicted` if the variants differ.

**Alternatives considered.** *Deriving exposures from `events` on every analysis* (`GROUP BY user` with `min(occurred_at)` over the experiment's `$exposure` events). It needs no extra table, but every snapshot re-reads every exposure event, and conflict detection would be redone on every run instead of once per event.

**Consequences.**
- Attribution joins a compact table (one row per user) instead of the raw event stream. SRM counts are a `GROUP BY variant_id` on the same table.
- Ingestion does a second write per new exposure, in the same transaction.
- The table is derived data: it could be rebuilt from `events` if its logic ever changes.

---

## ADR-010: A small migration runner of our own (M3)

**Context.** The PRD calls for plain numbered SQL migrations and "a small runner" that records applied versions and runs each migration in its own transaction. The stack includes no migration library.

**Decision.** One module, `abtest/db/migrate.py` (91 lines, per `wc -l`):
- Files named `NNNN_description.sql`, applied in number order.
- Each migration runs in its own transaction, together with the insert into `schema_migrations`, so a failed migration leaves no trace.
- A SHA-256 checksum of each applied file is stored. If an applied file changes, the runner refuses to continue, which enforces "never edit an applied migration; add a new one".
- Every step takes a transaction-scoped advisory lock, so concurrent runners (two containers, or parallel tests) take turns. A test starts four at once.

In Docker Compose, a one-shot `migrate` service runs the migrations and the bootstrap. The `api` and `worker` services wait for it to complete successfully, and Compose Watch reruns it when a migration file is added.

**Alternatives considered.** Alembic, yoyo-migrations, or sqitch. They offer more features (downgrades, dependency graphs), but each is a dependency outside the PRD's stack, and Alembic centers on SQLAlchemy models, which this project doesn't use.

**Consequences.**
- No downgrade migrations. The fix for a bad migration is a new migration that reverses it.
- The SQL files are the whole truth about the schema, readable in any SQL tool.

---

## ADR-011: Flags and experiments are evaluated in the SDK, not on the server (M4)

**Context.** Every page view asks "is this flag on for this user?" and "which variant does this user get?". Those answers can come from a server call per evaluation, or from rules the SDK downloads once and applies locally.

**Decision.** The SDK downloads the whole project config (`GET /v1/config`: running experiments with their variants and weights, and every flag) and evaluates locally, with deterministic hashing (`abtest/assignment.py`, ported byte for byte to TypeScript in M5). The server recomputes each exposure's assignment at ingestion (M6), so an SDK that disagrees is flagged (`assignment_mismatch`) instead of silently trusted.

**Alternatives considered.** *Server-side evaluation* (`GET /v1/decide?user=...`). The server always has the latest rules, and the rules stay private, but every evaluation costs a network round trip on the page's critical path, and an outage or a slow network breaks the page, which is exactly what PRD goal G1 ("zero network calls at evaluation time") rules out.

**Consequences.**
- Evaluation is instant and works offline once the config is cached. A change reaches browsers within one poll interval (30 s by default).
- The config is public: anyone with the (public) client key can read flag names, rollout percentages, and experiment keys. Nothing secret may go in a flag or experiment key.
- Two implementations of the same hashing must never drift. `shared/hash_test_vectors.json` (57 hash, 45 assignment, and 27 flag vectors, including every MurmurHash3 tail length and multi-byte UTF-8 at block boundaries) is checked by both test suites. The Python side is anchored to published MurmurHash3 reference values.

---

## ADR-012: Two independent hashes: one for inclusion, one for the variant (M4)

**Context.** An experiment usually starts on a slice of traffic (say 10%) and ramps up. Ramping must never move a user who is already in the experiment to another variant: their earlier behavior would then count for the wrong variant.

**Decision.** Inclusion and variant choice hash different inputs:
- `in experiment` ⇔ bucket(`{key}:traffic:{user}`) < traffic_bp;
- the variant comes from bucket(`{key}:variant:{user}`), walked through the cumulative weights in position order.

Raising `traffic_bp` only admits users whose traffic bucket falls in the new range; everyone already in keeps the same variant bucket and therefore the same variant. Traffic may only increase while an experiment runs (lowering it would drop users), and weights are frozen once it starts.

**Alternatives considered.** *One hash for both* (for example: bucket < traffic_bp means included, and the same bucket, rescaled, picks the variant). Changing traffic_bp then changes the rescaling, and users move between variants when traffic ramps.

**Consequences.**
- Proven by tests: raising traffic from 20% to 60% never moves an assigned user (50,000 users), included users still split 50/50 (independence, chi-square), and a million users split as weighted for 50/50 and 33/33/34 (chi-square p > 0.001, the PRD's bar).
- Keys can't contain ":", because hash inputs are joined with it: `a:b` + `c` must never produce the same input as `a` + `b:c`. The API and the database both enforce the key format.
- Flags use a third input (`{key}:rollout:{user}`), so a flag and an experiment with the same key never correlate.

---

## ADR-013: The config ETag is a version counter, bumped in the same transaction (M4)

**Context.** SDKs poll `GET /v1/config` every 30 seconds. Most polls find nothing changed, so a conditional request (`If-None-Match`) answered with `304 Not Modified` saves the body, and the server's work of building it.

**Decision.**
- `projects.config_version` is incremented by every flag, experiment, or variant write, inside the write's own transaction (`db/projects.bump_config_version`). The ETag is `"config-<version>"`.
- A conditional request reads only the version, one indexed row. If it matches, the answer is 304, with no config built.
- Otherwise the whole document (version, flags, running experiments, variants) comes from **one SQL statement**, so the version and the content are from the same snapshot.
- `Cache-Control: max-age=30`, in line with the SDK's poll interval.

**Alternatives considered.**
- *Hashing the config body* for the ETag. Always correct, but the server builds the full document on every request just to learn that it hasn't changed.
- *A last-modified timestamp.* Two changes within the timestamp's resolution would share an ETag.
- *Reading the version and the content in separate queries.* That is a real race. A change committing in between yields version 5 with version-4 content; the SDK caches it under `"config-5"` and gets 304 forever after, until something else changes.

**Consequences.**
- Every config write touches the project row, so concurrent config writes in one project serialize on it. At admin-dashboard write rates that costs nothing.
- Metric changes don't bump the version: metrics aren't in the SDK config. A test checks both directions: every config write bumps it, and a metric write doesn't.

---

## ADR-014: How the SDK delivers events: at least once, deduplicated by the server (M5)

**Context.** A browser SDK sends events over an unreliable network, from pages that can be closed or backgrounded at any moment. Each event should be stored exactly once. The page must never wait on the SDK, and the SDK must never grow without bound.

**Decision.**
- **At-least-once delivery with client-made ids.** `track()` and exposure logging fix `event_id` (a random UUID) and `occurred_at` when the event is created. Every retry resends identical events, and the server's primary key `(project_id, event_id, occurred_at)` turns a repeat into a counted duplicate (ADR-008).
- **Retry only what can succeed.** Network errors, 5xx, and 429 are retried with exponential backoff and full jitter (a random delay up to 1 s, 2 s, 4 s, … 30 s), or exactly as long as `Retry-After` says. Any other 4xx means the batch can never be accepted: it is dropped and counted in `stats().dropped`, and so are events the API rejects individually.
- **Bounded memory.** Past `maxQueueSize`, the oldest events are dropped and counted.
- **Leaving the page.** On `visibilitychange` to hidden, or `pagehide`, the queue goes out with `navigator.sendBeacon`, which outlives the page. The body is `text/plain` (a CORS-safelisted type, so no preflight), and the client key goes in the query string (beacons can't set headers). Payloads are split under the browsers' 64 KB cap; a single event too big to fit is dropped and counted. If the beacon is refused, a `fetch` with `keepalive` is the fallback.
- **Never block the page.** `ready()` resolves when the config loads or after `readyTimeoutMs`; until then `getVariant` returns null and `isEnabled` false. On a failed poll, the last good config stays in use.

**Alternatives considered.**
- *Exactly-once delivery in the client* (persisting the queue and acknowledgements in localStorage). Much more code, and still not exactly-once across tabs or crashes; server-side deduplication by id is simpler and covers every case.
- *Retrying every error.* A 400 (for example, a malformed batch) would then be retried forever and pin the queue.
- *Sending on `unload`/`beforeunload`.* Unreliable on mobile, where pages are frozen or killed while hidden without an unload event. `visibilitychange` to hidden is the last event mobile browsers reliably fire.

**Consequences.**
- The SDK is 2,556 bytes min+gzip (ESM build; `npm run size`, which CI runs and which fails above 5 KB).
- Browsers cap the total size of in-flight beacons and keepalive requests at about 64 KB per page, so a very long queue can't all survive an unload; what the browser refuses is lost. The default batch size and flush interval (50 events, 5 s) keep the queue short in practice.
- Events are stored at least once and counted once. The client's clock sets `occurred_at` (PRD §13 documents that limitation).

---

## ADR-015: An in-memory rate limiter, per client key (M6)

**Context.** `POST /v1/events` is public: the client key ships in every page. A buggy page (an event in a render loop) or a hostile one could flood ingestion. PRD §11 asks for a per-key limit of 100 requests/s with bursts of 200, answered with `429` and `Retry-After`.

**Decision.** A token bucket per client key, in the API process's memory (`api/rate_limit.py`):
- `burst` tokens to start with, refilled at `rate` per second; each request takes one. The defaults are 100/s and 200, configurable with `RATE_LIMIT_PER_SECOND` and `RATE_LIMIT_BURST`.
- A refused request gets `429` and `Retry-After` in whole seconds, rounded up, so a client that obeys it succeeds. The SDK honors it (ADR-014).
- Only keys that authenticate get a bucket, so random keys can't grow the limiter's memory.
- Buckets are keyed by the key's hash, never the key itself, and a lock guards them because route handlers run in a thread pool.

**Alternatives considered.**
- *Redis (or another shared store).* It limits correctly across many API instances, but it's another service to run, and there is one instance here.
- *Limiting at the load balancer or a gateway.* That's where it belongs in production, but there is no load balancer in the local stack, and the limit is part of what the tests should prove.
- *A fixed window per second.* Simpler, but it allows twice the rate across a window boundary, and it doesn't express bursts.

**Consequences.**
- **Correct for one instance only.** With N API processes behind a load balancer, each allows the full rate, so a key could reach N × 100 requests/s. Moving to Redis (a Lua script doing the same arithmetic) or to the load balancer fixes that, and only this module would change.
- Buckets reset when the process restarts. That's harmless: a restart can grant at most one extra burst.
- The limit is per request, not per event: a request carries up to 500 events, so 100 requests/s is up to 50,000 events/s per key before the limit applies. M9 measures what the database sustains.

---

## ADR-016: How events are attributed, and the limits we accept (M7)

**Context.** A result is only as good as the question "which events count, for whom?". PRD §13 fixes the rule; this records how it's implemented, and the limitations we document rather than correct.

**Decision.** One SQL query per (experiment, metric) (`db/results.py`):
- The population is the experiment's exposures that aren't `conflicted`. The SRM check uses the same population.
- A user's events count if they fall in `[first_exposed_at, min(first_exposed_at + window, cutoff))`: the window includes its start and excludes its end, and the cutoff is now, or the stop time for a stopped experiment.
- A conversion metric counts users with at least one such event. A mean metric sums each user's values (a null value counts as 0; a user with no events counts as 0) and aggregates n, sum, and sum of squares per variant.
- The query also bounds `occurred_at ≥ started_at`, which follows from the rules above but lets Postgres skip every partition from before the start.
- Every edge case (before exposure, each end of the window, after the cutoff, repeat conversions, conflicted users, zero and null values) has a hand-built test.

**Limitations, documented rather than corrected.**
- **Recently exposed users have had less time to convert.** A user exposed an hour ago has had one hour of a 168-hour window. While the experiment runs, rates are therefore diluted toward zero, equally for every variant, so comparisons stay fair. Analyzing only users whose full window has elapsed is a stretch goal (PRD §23).
- **`occurred_at` comes from the client's clock.** A client running behind can report events from before the experiment started, or outside a user's window, and they are then not attributed. We measured this for real: this laptop's clock trails the database container's by 33 ms, and test events sent within 33 ms of starting an experiment were (correctly) not attributed. The traffic generator waits one second after starting, and the tests stamp events by the server's clock.
- **No correction for multiple comparisons.** With several treatment variants, or many secondary metrics, some will look significant by chance. The primary metric's verdict is the decision (PRD §14); the others are context.

**Alternatives considered.**
- *Attributing by `received_at` (the server's clock).* It avoids clock skew, but then batching, retries, and offline devices would place events at the wrong time: the time of the upload, not of the action.
- *Correcting skew per device* (estimating each client's clock offset from `received_at` − `occurred_at`). Out of scope for the MVP, and PRD §13 says to document the limitation, not correct it.

**Consequences.** Early snapshots understate every variant's rate; the lift and its CI remain valid comparisons.

---

## ADR-017: Advisory locks for the worker's jobs and for results (M7)

**Context.** Two things must never happen twice at once. Two worker processes (a scaled deployment, or a restart overlapping the old process) must not both run the same job. And two *looks* at one experiment must not interleave: under mSPRT each look continues from the previous one's state, so two looks computed at the same time from the same state would lose one of them, and the always-valid p-value could appear to go up.

**Decision.**
- Each worker job runs in one transaction that starts with `pg_try_advisory_xact_lock(job id)`. If another worker holds it, the job is skipped (tested).
- Each look (from the worker or from `POST .../recompute`) takes `pg_advisory_xact_lock(hashtext('results:' || experiment id))`. It then reads the latest snapshot, and stamps the new one with `clock_timestamp()` taken **after** the lock, so looks are ordered the way they were taken. (Using the transaction start time would let a long worker transaction write a newer look with an older timestamp.)
- The locks are transaction-scoped: Postgres releases them when the transaction ends, so a crash or a pooled connection can't leak one.

**Alternatives considered.**
- *Session-level advisory locks.* They survive the transaction and must be released by hand, and a pooled connection returned without releasing one would block every later job.
- *A lock table with `SELECT ... FOR UPDATE`, or leader election.* That's more machinery for the same guarantee.
- *Unique constraints on snapshots.* They would stop duplicate rows, but not two looks racing on the same previous state.

**Consequences.**
- A recompute that arrives while the worker is analyzing that experiment waits until the worker's transaction ends. With one small experiment the worker's log showed a 38 ms run. In M9, a look at a 300,121-user experiment on 10M events took 623 ms and 873 ms in two runs (`docs/performance.md`, section 4). The experiment's lock is held until the whole job's transaction ends, so with experiments that size a recompute can wait a second or more.
- A job's writes commit together. Inside the job, each experiment runs in a savepoint, so one experiment that fails is rolled back and logged, and the others still get their looks (a single bad experiment used to block every experiment's results).

---

## ADR-018: Results are precomputed snapshots, not computed on read (M7)

**Context.** The dashboard shows each experiment's results, and a chart of how the lift and its CI moved over time. Results could be computed when someone opens the page, or computed on a schedule and stored.

**Decision.** The worker computes a snapshot for every metric of every running experiment every 5 minutes (`RESULTS_INTERVAL_SECONDS`), plus a final one after an experiment stops. Snapshots are append-only rows in `results_snapshots`, with the per-variant summaries, the comparisons, the SRM check, and the mSPRT state in `data`. The dashboard reads them (`GET .../results`), and `POST .../recompute` adds a look on demand.

**Alternatives considered.** *Computing on read.* It's always current, but every page view would scan exposures and events. The mSPRT would also need its whole history recomputed on every read, because the always-valid p-value is a running minimum over looks: without stored looks there is no well-defined "previous state".

**Consequences.**
- Page loads are a single indexed read (`results_snapshots_latest`), whatever the experiment's size.
- Results are up to 5 minutes old; recompute exists for when that matters.
- The stored series *is* the sequence of looks the mSPRT guarantee is about. Snapshots are never rewritten, so the history shown is the history that was analyzed.
- Storage grows by one row per metric per 5 minutes per running experiment: 8,064 rows per metric over four weeks. In M9, reading those for the chart took 142 ms. Narrowing the series query to the fields the chart draws brought it to 69 ms with the same 1,915 KB response (`docs/performance.md`, section 4). The response still grows with every look, so experiments that run for months would need the series downsampled.

---

## ADR-019: Dashboard auth: one password, a signed cookie, and the server key kept on the server (M8)

**Context.** The dashboard (PRD §17) needs to keep strangers out and to call the admin API, which requires a server key. There is one admin and one project, and the tech stack has no auth library.

**Decision.**
- The admin types `ADMIN_PASSWORD`. It's compared in constant time: both strings are hashed with SHA-256 first, so not even their lengths are compared directly.
- A correct password sets an httpOnly, `SameSite=Lax` cookie (`Secure` in production) holding an expiry time and an HMAC-SHA256 signature of it, keyed by `SESSION_SECRET` (at least 32 characters) through Web Crypto. It lasts 7 days. `crypto.subtle.verify` checks the signature in constant time.
- `proxy.ts` (Next.js 16's name for middleware) redirects every page but /login to /login without a valid cookie, and answers 401 on /api/*.
- Every server action calls `requireSignedIn()` as well. The proxy isn't enough on its own: a server action can be invoked by a POST to *any* route, including /login, which the proxy has to let through.
- The API is called only from the Next.js server (server components, server actions, and one route handler that forwards the sample-size estimate), with `ABTEST_SERVER_KEY` from the server's environment. It's never a `NEXT_PUBLIC_*` variable, so it's never in a bundle, and the browser never talks to the admin API.

**Alternatives considered.**
- *A sessions table in Postgres.* It would allow revoking one session, but needs new API endpoints and storage for a single admin.
- *Auth.js, or a JWT library.* A dependency outside the PRD's stack, for what is 76 lines of Web Crypto code here (`web/lib/session.ts`).
- *HTTP Basic Auth at a reverse proxy.* It depends on the deployment, and it has no sign-out.
- *Calling the API from the browser.* That would put a server key in every visitor's hands.

**Consequences.**
- Sessions are stateless: sign-out deletes the cookie, but a stolen cookie stays valid until it expires. Rotating `SESSION_SECRET` signs everyone out.
- There is no rate limit on sign-in attempts, so a deployment needs a long random `ADMIN_PASSWORD`.
- A page load costs one or two server-side API calls; the browser receives only HTML and the data on the page.

---

## ADR-020: Self-hosted fonts instead of next/font/google (M8)

**Context.** The dashboard uses IBM Plex Sans and IBM Plex Serif. With `next/font/google`, Next.js downloads the font files from Google at build time, and in development when a page first compiles. During M8, Google's CSS briefly listed font URLs of the form `fonts.gstatic.com/l/font?kit=…&…`. Turbopack failed to parse them ("next/font/google queries have exactly one entry"), and every dashboard page returned 500 until the response changed back.

**Decision.** The Latin subsets are committed in `web/app/fonts/` (three woff2 files, 70,684 bytes in all, with the SIL Open Font License) and loaded with `next/font/local`, which ships with Next.js.

**Alternatives considered.**
- *Keep next/font/google and retry.* The failure is outside this repo and intermittent, and it would also break CI builds and the compose smoke test.
- *A `<link>` to Google Fonts at runtime.* The build no longer needs the network, but every visitor's browser contacts Google, and text renders in a fallback font until the fonts arrive.

**Consequences.**
- Builds are reproducible offline, and no request goes to Google.
- Only Latin characters are covered. Other scripts fall back to the system font.
- Font updates are manual (rare).

---

## ADR-021: A covering attribution index, and a query bounded on both sides (M9)

**Context.** The worker's attribution query (`AGGREGATE` in `db/results.py`) joins an experiment's exposures to the metric's events. M3 indexed events on `(project_id, event_name, user_id, occurred_at)`. M9 ran `EXPLAIN (ANALYZE, BUFFERS)` on 1M and 10M seeded events (`make seed`, `make perf`; the plans are in `docs/performance.md`, section 3). Two things showed up:
- The query also reads `events.value` (mean metrics sum it), so every matching index entry still sent Postgres to the table. A metric's events are spread over the whole table, so for a large experiment the index barely beat reading the partitions in full: with 10M events and 300,121 exposed users, the query touched 83,746 pages with it and 98,424 without it.
- The only constant bound on `occurred_at` was the start. The per-user upper bound (`first_exposed_at + window`) isn't a constant, so every future partition (14 of them, created ahead of time) and the default partition were scanned as well.

**Decision.**
- Migration `0002` replaces the index with `(project_id, event_name, user_id, occurred_at) INCLUDE (value)`. `INCLUDE` stores the value in the leaf entries without making it part of the sort key, so the query is answered by an index-only scan. The query counts `occurred_at` rather than `event_id`, so it reads nothing the index lacks.
- The query also states `occurred_at < cutoff`, a constant, so Postgres prunes the partitions after it and the default partition.
- Two tests pin these down: with every other kind of scan disabled, the query still plans as an index-only scan (so the index covers it), and it reads only the partitions between the start and the cutoff.

**Alternatives considered.**
- *Keep the M3 index.* It is what makes small experiments fast (the 5,976-user experiment: 62 ms with no index, 21 ms with it), but it does little for large ones.
- *Raise `work_mem` for the worker.* With 300,121 users, the per-user aggregation spills to disk at the default 4 MB. A one-off trial at 64 MB removed the spill, but the time changed by less than the run-to-run noise, so the setting stays at its default.
- *Key order `(project_id, event_name, occurred_at, user_id)`.* The time bound would become a range scan, but the per-user lookups of a nested-loop plan (which the planner chose for the small experiment with the 0001 index) would lose their key order. Not measured.

**Consequences.**
- The large experiment's query touches 12,138 pages instead of 83,746, and its median time fell from 372 ms to 299 ms (warm cache, 10M events). The small experiment's stayed at about 20 ms.
- The index is larger: each entry carries an 8-byte value, and every insert writes it. At 10M events the covering index (1,285 MB) is as large as the table's rows (1,282 MB). `docs/performance.md` explains why its measured sizes can't isolate the extra column's cost.
- Index-only scans skip the table only for pages the visibility map marks all-visible, so they rely on autovacuum keeping up. Postgres 13+ vacuums insert-only tables, and pages that aren't marked yet are simply read from the table.
- Postgres can't build an index on a partitioned table `CONCURRENTLY`, so migration `0002` blocks writes to events while it runs. On a large live table you would build each partition's index concurrently and attach it.

---

## ADR-022: No hosted deployment: the local stack is the demo (M10)

**Context.** PRD v1.9 ended M10 with a deployment, with the provider still to be chosen. The system is five long-running services: Postgres, the API, the worker, the dashboard, and the demo page. The worker has to run all the time, because it computes every result and creates each day's event partitions. The project has no budget for hosting. The dashboard also sits behind the single admin password (ADR-019), so a visitor to a live URL would see only the sign-in page, and sharing the password would give them full admin rights, since there is only one role.

**Decision.**
- Don't deploy (PRD v1.10).
- Show the system working in three ways:
  - screenshots of the full flow on the local stack, in the README, regenerated by `make screenshots` (amended 2026-09-30, PRD v1.12);
  - the one-command quickstart (`make dev`), verified from a fresh clone in M10;
  - CI's compose smoke test, which builds and starts every service on every push.

**Alternatives considered.**
- *A paid host*: a small virtual server running this compose file behind a reverse proxy for HTTPS, or a platform that runs each Dockerfile (Railway, Fly.io, Render). This is the real way to ship it, but it's a monthly cost for a portfolio project.
- *Free tiers.* The platforms' free plans generally put idle services to sleep, or don't offer always-on background workers, and the worker is the part that must never sleep.
- *A static site with recorded data* (the demo page and a read-only dashboard served from files). This is free, but it's a second, fake version of the app that would have to be kept in sync with the real one.

**Consequences.**
- The images stay development images. Compose runs `uvicorn --reload` and `next dev`, as root inside the containers, with the local-only secrets from `.env.example`.
- A deployment would need:
  - production images: `next build` and `next start`, uvicorn without `--reload`, a non-root user, and no matplotlib in the API image;
  - new API keys (PRD §10), a new `SESSION_SECRET`, and a long random `ADMIN_PASSWORD` (ADR-019 has no sign-in rate limit);
  - HTTPS in front of the API and the dashboard, and a place for the demo page.

  The README lists this as future work.
- Nobody can click a live link. The screenshots and the quickstart have to do that job.

---

## ADR-023: The attribution query never uses a nested loop (health check)

**Context.** Postgres picks the attribution query's join method (`AGGREGATE` in `db/results.py`) from its table statistics. A table that has just received its first rows has none until autovacuum analyzes it, which can take up to a minute. That happens on a new database, and to each day's new events partition. Autovacuum also never analyzes the partitioned `events` table itself, only its partitions. In the health check, `make traffic SCENARIO=checkout_button ARGS="--use-running ..."` on a fresh stack sent 40,000 users, and then its recompute timed out at the client's 5-second limit. `EXPLAIN ANALYZE` showed why: estimating a handful of rows on each side, Postgres chose a nested loop over a materialized scan of every purchase event. It compared every exposure with every event, so the time grew with their product. A minute later, after autovacuum had run, the same recompute answered at once.

**Decision.** `aggregate()` runs `SET LOCAL enable_nestloop = off` before the query, inside the look's transaction. `loadtest/explain_attribution.py` does the same, so `make perf` still measures the query the worker runs. A regression test builds a population that has no statistics and checks that the plan has no nested loop.

**Alternatives considered.**
- *Run `ANALYZE` from the worker*, before each look or daily. A look would still land in the gap before the first analysis of a new partition, and it adds a maintenance job to reason about.
- *A `LATERAL` subquery per exposure.* It forces an index lookup per user, whatever the statistics say, but a large experiment then makes one probe per user per partition, where the hash join makes a single pass.
- *Leave it.* It heals itself within a minute, but the documented first-run flow (create, start, `make traffic`) is exactly the case that hits it.

**Consequences.**
- The query always aggregates a whole population, where a hash join is the right plan. With statistics, Postgres chose hash joins for every row with the covering index in `docs/performance.md`, and `make perf` at 1M planned every row the same after the change.
- A very small experiment on a very large events table loses the option of a per-user index lookup. It reads the metric's events since the start instead, which stays linear.
- `SET LOCAL` lasts until the transaction ends, so the rest of a worker run also plans without nested loops. Those are single-table lookups by key, so nothing else changes.

---

## ADR-024: Read the event body before taking a database connection (health check)

**Context.** `POST /v1/events` checks the client key, which needs a pooled database connection, and reads the body, which can take as long as the client wants. It used to check the key first. The pool has 4 connections, so four clients that sent their headers and then stalled mid-upload held the whole pool. `/health` answered 503 and `GET /v1/config` failed after the pool's 30-second timeout, for as long as the sockets stayed open. Any visitor can do this, because client keys are public.

**Decision.** `post_events` declares the body dependency first. FastAPI resolves dependencies in declaration order, so the body is read, still capped at 1 MB while reading, before checking the key takes a connection. A regression test sends a body in two parts and checks that no connection is in use between them.

**Alternatives considered.**
- *A bigger pool.* That only raises the number of stalled uploads needed.
- *A body-read timeout.* Uvicorn has none, and one would belong in a reverse proxy, which the local stack doesn't have.

**Consequences.** An unauthenticated or rate-limited client's body is read (up to 1 MB) before it gets its 401 or 429. That costs the server at most a megabyte of reading per request, and no database connection. A production deployment would still put a reverse proxy in front, to buffer uploads and time out slow ones.
