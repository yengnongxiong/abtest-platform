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
