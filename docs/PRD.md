# PRD: abtest-platform — Feature Flags & A/B Testing Platform
Owner: Yengnong Xiong · Status: v1.5 (see §24 Changelog) · Type: Portfolio project (PM + SWE)

## 1. Summary
abtest-platform is a self-hostable feature flag and A/B testing platform.
- A TypeScript SDK assigns users to experiment variants locally with deterministic hashing and sends events in batches.
- A FastAPI service ingests events into PostgreSQL.
- A Python worker attributes conversions to variants and runs a statistics engine.
- A Next.js dashboard lets a PM create experiments and read results in plain language.
- A Monte Carlo simulator proves the statistics are correct.

Correctness, clear tradeoffs, and honest measured results matter more than feature count.

## 2. Problem
A/B tests are how product teams make decisions, but three common mistakes produce confident wrong answers:
- Peeking: checking results repeatedly and stopping the first time p < 0.05 inflates false positives far above 5%.
- Sample ratio mismatch (SRM): a bug sends unequal traffic to variants and silently biases results.
- Bad attribution: counting conversions that happened before the user saw the change, or long after.

Commercial tools guard against these, but to most people using them they are a black box. This project builds the full pipeline and uses simulation to demonstrate that each safeguard works.

## 3. Goals and non-goals
Goals:
- G1: Deterministic, sticky assignment with zero network calls at evaluation time.
- G2: Idempotent, batched event ingestion that is safe to retry.
- G3: Correct attribution: count metric events only after a user's first exposure and within the metric window.
- G4: Correct statistics: fixed-horizon tests with confidence intervals, SRM detection, and sequential testing that stays valid under continuous monitoring.
- G5: Prove G4 with Monte Carlo simulation and publish the charts in the README.
- G6: Measure ingestion throughput and query performance, and publish real numbers.
- G7: Results a non-statistician can read. Verdicts are in plain language, and results are hidden behind a warning when SRM is detected.

Non-goals (MVP): multi-armed bandits, mutually exclusive layers, CUPED, Bayesian analysis, mobile SDKs, visual editor, billing/multi-org SaaS, SSO.

## 4. Users
- PM at a small startup. Wants to know "did it work, and can I trust it?" Needs a pre-registered hypothesis, a sample size estimate, and a clear verdict.
- Engineer integrating the SDK. Wants a tiny, dependency-free SDK that never blocks page load and never double-counts.

## 5. Success criteria for this project
- A/A simulation: the fixed-horizon false positive rate (FPR) falls inside the Monte Carlo 95% band around 5%.
- Peeking simulation: FPR is inflated for naive peeking and ≤ 5% (within Monte Carlo error) for sequential testing.
- Simulated traffic with a known true effect: the dashboard's confidence interval contains the true effect.
- SDK bundle ≤ 5 KB min+gzip (measure and report the actual size).
- Load test and EXPLAIN ANALYZE results are published (real numbers only).
- A fresh clone plus `make dev` gives a working local stack.

## 6. Architecture
Components:
1. sdk-js (TypeScript): fetches config, evaluates flags and experiments locally, logs exposures, batches and retries events.
2. api (FastAPI): public endpoints (client key) for config and events; admin endpoints (server key) for management and results.
3. PostgreSQL 16: source of truth. The events table is range-partitioned by day.
4. worker (Python): computes results snapshots on a schedule and maintains partitions.
5. stats (Python package): pure; no DB or web imports.
6. simulator (Python): (a) in-memory Monte Carlo validation, (b) end-to-end traffic generator that goes through the HTTP API.
7. web (Next.js): manage metrics, flags, and experiments; read results.
8. demo: a static page that uses the SDK to show the pipeline end to end.

Flow:
1. The dashboard calls the admin API, which writes to Postgres and bumps config_version.
2. The SDK fetches GET /v1/config (with ETag) and assigns variants locally.
3. The SDK sends exposures and events to POST /v1/events, which writes to Postgres.
4. The worker computes snapshots.
5. The dashboard reads the snapshots.

The README must include a Mermaid diagram of this flow.

## 7. Repo layout
```
abtest-platform/
  server/                  Python project (uv)
    src/abtest/
      api/                 FastAPI app, routers, schemas, auth, rate limiting
      assignment.py        hashing + assignment (must match the SDK exactly)
      stats/               statistics engine (pure)
      worker/              scheduled jobs, attribution queries
      simulator/           monte carlo validation + traffic generator
      db/                  connection pool, migration runner, query modules
      config.py            settings from env (pydantic-settings)
    tests/
  db/migrations/           0001_init.sql, 0002_... (plain SQL)
  sdk-js/                  TypeScript SDK (tsup, vitest)
  web/                     Next.js dashboard
  demo/                    static demo page using the SDK
  loadtest/                locustfile.py, seed scripts
  scenarios/               traffic generator scenario YAML files
  shared/hash_test_vectors.json
  docs/                    PRD.md, decisions.md, results/, performance.md, memo template
  docker-compose.yml
  Makefile
  .github/workflows/ci.yml
```

## 8. Tech stack (ask before adding anything else)
- Python 3.12+ with: uv, FastAPI, Pydantic v2, pydantic-settings, psycopg 3 + psycopg_pool, mmh3, NumPy, SciPy, matplotlib, PyYAML, httpx2, pytest, ruff, mypy. statsmodels is a test-only dependency, used for cross-checking.
- TypeScript SDK: tsup, vitest. The SDK has zero runtime dependencies.
- Web: Next.js (App Router), TypeScript, Tailwind CSS, Recharts.
- Infrastructure and tooling: PostgreSQL 16, Docker Compose, GitHub Actions, Locust.
- Approved additions (v1.1):
  - uvicorn: the ASGI server that runs FastAPI.
  - ESLint, typescript-eslint, @eslint/js: dev-only, for SDK linting.
  - nginx (Docker image): serves the static demo page.
  - scipy-stubs, types-PyYAML: dev-only type stubs for mypy strict.
- Approved change (v1.2): httpx2 replaces httpx. It is httpx's maintained successor (same author, now under the Pydantic org); httpx's last release was 0.28.1 in December 2024, and Starlette's test client deprecates it.
- Use current stable versions; look them up rather than guessing. Chosen versions and pins are recorded in docs/decisions.md.

## 9. Assignment
- Hash: MurmurHash3 x86_32, seed 0, UTF-8 input, unsigned 32-bit result.
  - Python: mmh3.hash(s, 0, signed=False).
  - TypeScript: implement it with no dependency.
  - Both must pass shared/hash_test_vectors.json: ≥ 50 vectors covering ASCII, unicode, emoji, the empty string, long strings, and numeric-looking IDs.
- bucket(s) = hash(s) mod 10000 (basis points).
- Inclusion: a user is in the experiment if bucket(f"{exp_key}:traffic:{user_id}") < traffic_bp.
- Variant: let v = bucket(f"{exp_key}:variant:{user_id}"). Walk the variants ordered by position, accumulating weight_bp, and pick the first where v < the cumulative weight.
- Two independent hashes are used so that raising traffic_bp adds new users without moving anyone already assigned.
- Flags: a flag is on if enabled and bucket(f"{flag_key}:rollout:{user_id}") < rollout_bp.
- Rules for running experiments:
  - Variants and weights are frozen.
  - traffic_bp can only increase.
  - Stopping is final; clone the experiment to rerun it.
- Test: 1,000,000 synthetic IDs, split across a 50/50 and a 33/33/34 experiment, pass a chi-square uniformity test (p > 0.001).

## 10. Data model (PostgreSQL)
All ids are uuid (gen_random_uuid()) unless noted. All timestamps are timestamptz.

Tables:
- projects: id, name, config_version bigint default 1, created_at.
- api_keys: id, project_id, kind ('client' | 'server'), key_prefix, key_hash unique, created_at, revoked_at.
  - Keys are random, prefixed by kind.
  - Store the SHA-256 of the full key; show the plaintext once, at creation.
- metrics: id, project_id, key, name, kind ('conversion' | 'mean'), event_name, direction ('increase' | 'decrease'), window_hours default 168, created_at. Unique (project_id, key).
  - Mean metrics sum events.value per user.
- flags: id, project_id, key, description, enabled bool, rollout_bp (0–10000), created_at, updated_at. Unique (project_id, key).
- experiments: id, project_id, key, name, hypothesis text, status ('draft' | 'running' | 'stopped'), traffic_bp, analysis_type ('fixed_horizon' | 'sequential', default 'sequential'), alpha default 0.05, mde_relative numeric, started_at, stopped_at, stop_reason, created_at, updated_at. Unique (project_id, key).
  - The expected baseline lives on experiment_metrics (one per attached metric), not here.
- variants: id, experiment_id, key, name, weight_bp, is_control, position.
  - Unique (experiment_id, key) and unique (experiment_id, position).
  - Partial unique index: exactly one control per experiment.
- experiment_metrics: experiment_id, metric_id, role ('primary' | 'secondary' | 'guardrail'), expected_baseline numeric. PK (experiment_id, metric_id).
  - Partial unique index: exactly one primary metric per experiment.
  - expected_baseline is the PM's pre-registered baseline for that metric: a rate for conversion metrics, or the expected per-user mean for mean metrics. It sets the metric's mSPRT τ (§14), and the primary metric's baseline feeds the sample-size estimate.
- experiment_changes: id bigserial, experiment_id, action, details jsonb, created_at.
  - This is the changelog shown on the dashboard (start, stop, traffic changes).
- exposures: project_id, experiment_id, variant_id, user_id text, first_exposed_at, conflicted bool default false, assignment_mismatch bool default false. PK (experiment_id, user_id).
- events: project_id, event_id uuid, user_id text, event_name text, occurred_at, received_at default now(), value double precision null, properties jsonb default '{}'.
  - PARTITION BY RANGE (occurred_at), with daily partitions plus a default partition.
  - PRIMARY KEY (project_id, event_id, occurred_at).
  - Every accepted event is stored here, including "$exposure" events. This gives one duplicate-detection path (by event_id) and a raw audit log. The exposures table (above) is derived from it at ingest time.
- results_snapshots: id bigserial, experiment_id, metric_id, computed_at, total_users, srm_p_value, srm_flag, data jsonb.
  - data holds the per-variant summaries and comparisons described in §14.

Required rules and SQL:
- Every config change (flag, experiment, or variant) increments projects.config_version in the same transaction.
- Metric, flag, and experiment keys match ^[a-z0-9][a-z0-9_-]{0,63}$. Hash inputs are joined with ":", so a key containing ":" could make two different inputs hash identically.
- ensure_event_partitions(days_ahead int) is a SQL function that creates any missing daily partitions, using UTC day boundaries. The worker calls it daily with 14.
  - The range runs from today − 7 days through today + days_ahead, because the API accepts events up to 7 days old.
  - The default partition should stay empty. Postgres refuses to create a daily partition while the default partition holds rows that belong to that day.
- The events PK includes occurred_at because unique constraints on a partitioned table must include the partition key. Document the consequence in decisions.md: retries are idempotent only because the SDK creates event_id and occurred_at once, at track() time.
- Indexes:
  - events (project_id, event_name, user_id, occurred_at)
  - results_snapshots (experiment_id, metric_id, computed_at DESC)
  - an index on every foreign key
  - Comment each index with the query it serves.
- Exposure upsert: ON CONFLICT (experiment_id, user_id) DO UPDATE.
  - Keep LEAST(first_exposed_at).
  - Set conflicted = true if the variant_id differs.
  - Conflicted users are excluded from analysis and counted on the dashboard.
  - Upsert exposures only for "$exposure" events that were newly inserted into events, using the RETURNING rows, so a retried batch never touches exposures.
  - First aggregate within the batch per (experiment_id, user_id): keep the earliest time, and mark conflicted if the batch itself holds two variants. Postgres rejects an ON CONFLICT DO UPDATE that affects the same row twice in one statement.
- The server recomputes each exposure's assignment using the same hashing. If it disagrees with the SDK's variant, set assignment_mismatch = true (don't reject the event). That includes the case where the server computes that the user is not in the experiment. The dashboard shows the count.
- Batch inserts use a single INSERT ... SELECT FROM unnest(...) ON CONFLICT DO NOTHING RETURNING per batch. Never insert row by row.
- Migrations are plain numbered SQL files. A small runner applies them, records applied versions in schema_migrations, and runs each migration in its own transaction.
  - In docker compose, a one-shot migrate service runs the runner and the bootstrap below. The api and worker wait for it to finish, so a fresh clone plus `make dev` gets a migrated database.
- Bootstrap: after migrating, create a default project if none exists, and register the client and server keys supplied through env vars. Store their hashes, as for any key.
  - .env.example holds clearly labeled local-only keys, so a fresh clone works with no manual steps.
  - A deploy must generate new keys.

## 11. API
- Error format: {"error": {"code", "message", "details"}} with proper HTTP status codes.
- All request and response bodies are Pydantic models.

Public endpoints:
- Authentication: the client key, sent via the X-Client-Key header or a client_key query parameter (needed for sendBeacon).
- CORS is open for /v1/*, with no credentials.
  - Allow the X-Client-Key, If-None-Match, and Content-Type request headers.
  - Expose the ETag and Retry-After response headers so the SDK can read them.
  - Set Access-Control-Max-Age so browsers cache the preflight.

GET /v1/config
- Response: {config_version, flags:[{key, enabled, rollout_bp}], experiments:[{key, traffic_bp, variants:[{key, weight_bp, position}]}]}. Running experiments only.
- ETag = "config-<version>"; a matching If-None-Match returns 304.
- Cache-Control: max-age=30, with Vary: X-Client-Key (the content depends on that header).

POST /v1/events
- Request body: {sdk:{name, version}, events:[{event_id, user_id, name, occurred_at, value?, properties?}]}.
- Body parsing: the body is parsed as JSON whatever its Content-Type. The SDK's sendBeacon path sends text/plain (see §12). The 1 MB limit is enforced while reading the raw body.
- Limits:
  - ≤ 500 events per request and ≤ 1 MB body
  - properties ≤ 4 KB per event
  - user_id 1–200 characters of valid Unicode. Lone surrogates are rejected: Python cannot UTF-8-encode them, while JS silently replaces them, so the two hashes would disagree. IDs are hashed as-is, with no Unicode normalization.
  - name matches ^[A-Za-z0-9_$.:-]{1,100}$
  - occurred_at within [now − 7 days, now + 5 min]
- Exposure events:
  - Events named "$exposure" carry properties.experiment_key and properties.variant_key.
  - They are accepted only for running experiments; an unknown experiment or variant is rejected.
  - Like every event, they are stored in the events table. Newly inserted ones are then upserted into the exposures table (§10).
  - An exposure that arrives after its experiment stopped is rejected, even if its occurred_at is earlier. This is a documented limitation.
- Response: 202 {accepted, duplicates, rejected:[{index, reason}]}. One bad event never fails the whole batch.
- Rate limit per key: in-memory token bucket (default 100 requests/s, burst 200, both configurable via env). Exceeding it returns 429 with Retry-After.
  - Document in decisions.md that this works for one instance and would move to Redis or the load balancer with multiple instances.

Admin endpoints (Authorization: Bearer <server key>):
- Admin endpoints act on the project that owns the server key. The dashboard is therefore single-project.
- Metrics: POST /admin/metrics, GET /admin/metrics, PATCH /admin/metrics/{key}.
  - name can always be edited.
  - Definition fields (event_name, kind, direction, window_hours) can be edited only while no started experiment uses the metric. Otherwise existing results would change retroactively.
- Flags: POST /admin/flags, GET /admin/flags, and GET/PATCH/DELETE /admin/flags/{key}.
- Experiments: POST /admin/experiments, GET /admin/experiments, and GET/PATCH /admin/experiments/{key}.
  - PATCH on a running experiment may only raise traffic_bp or edit the name. A stopped experiment may only be renamed. In PATCH bodies, a missing or null field means unchanged.
- API keys: GET /admin/api-keys, POST /admin/api-keys (returns the plaintext key once), POST /admin/api-keys/{id}/revoke.
  - The project's last active server key can't be revoked (409), which would lock everyone out of the admin API.
  - A server key never works as a client key, and a client key never works as a server key.
- POST /admin/experiments/{key}/start validates that the experiment has:
  - a hypothesis
  - ≥ 2 variants, with exactly one control
  - weights summing to 10000
  - exactly one primary metric
  - mde_relative set, and an expected_baseline for every attached metric (below 1 for a conversion metric, because it is a rate)
  - A start that fails lists every problem at once (error details.problems).
- POST /admin/experiments/{key}/stop {reason}
- POST /admin/experiments/{key}/clone {new_key}
- GET /admin/experiments/{key}/results?metric=<key>: the latest snapshot plus the time series of snapshots.
- POST /admin/experiments/{key}/recompute
- GET /admin/sample-size?baseline=&mde_relative=&alpha=&power=: users needed per variant (fixed-horizon, two-sided). Defined for conversion metrics only, because the formula is for proportions.
- GET /health: checks the DB connection.

## 12. SDK (sdk-js)
Public API:
- createClient({clientKey, apiBaseUrl, userId?, flushIntervalMs=5000, maxBatchSize=50, maxQueueSize=1000, configPollIntervalMs=30000, readyTimeoutMs=2000, fetch?})
- ready(): resolves when the config loads or after readyTimeoutMs, whichever comes first. It never blocks the page. Until the config loads, getVariant returns null and isEnabled returns false.
- setUser(userId). If no userId is given, generate an anonymous id with crypto.randomUUID(). Persist it in localStorage when available (wrapped in try/catch, falling back to memory).
- getVariant(experimentKey): returns the variant key, or null if the experiment isn't running or the user isn't included. Logs a "$exposure" event once per (experiment, user) per page session.
- isEnabled(flagKey): returns a boolean.
- track(name, {value?, properties?}): creates event_id (crypto.randomUUID) and occurred_at immediately, so retries are idempotent.
- flush(), close(), stats() (queue size and dropped count).
  - dropped counts every event that will never be stored: queue overflow, events too big for an unload payload, batches refused with a 4xx other than 429 (retrying can't help), and events the API rejects individually.
  - A flush sends the whole queue in batches of at most maxBatchSize, whichever way it was triggered.
  - createClient and setUser throw a TypeError for a user id the API would reject (empty, over 200 characters, or with a lone surrogate).

Behavior:
- Config: fetch with If-None-Match, poll on the interval, and keep the last good config on failure.
- Queue flushing happens:
  - on the flush interval
  - when maxBatchSize is reached
  - on visibilitychange → hidden or pagehide, using navigator.sendBeacon (client_key goes in the query string because sendBeacon can't set headers), falling back to fetch with keepalive
    - The beacon body is sent as text/plain. A beacon with Content-Type application/json becomes a credentialed CORS request, which the no-credentials CORS policy would block. text/plain is a CORS-safelisted type, so no preflight is needed.
    - Browsers cap beacon and keepalive payloads at 64 KB. Unload flushes are split into chunks under that cap; events that still don't fit are dropped and counted.
- Retries on network errors, 5xx, or 429: exponential backoff with full jitter (1 s up to a max of 30 s), honoring Retry-After.
- Overflow: past maxQueueSize, drop the oldest events and count the drops.
- Runs in modern browsers and Node 22+. Ships ESM and CJS builds. Report the min+gzip size in the README.
  - Node 22 is the floor because Node 18 and 20 are end-of-life, Node 18 has no global crypto without a flag, and vitest cannot run on either. CI tests the SDK on Node 22 and 24.
- The hashing/assignment module is pure and shared by getVariant and isEnabled; it is tested against the shared vectors.

## 13. Metrics and attribution
For experiment E, metric M, and analysis cutoff T (now, or stopped_at if stopped):
- Population: exposures for E where not conflicted.
- Conversion metric: a user converts if they have ≥ 1 event named M.event_name with first_exposed_at ≤ occurred_at < min(first_exposed_at + window, T).
- Mean metric: the per-user sum of value over the same window (0 if the user has no events; a null value counts as 0). Aggregate n, sum, and sum of squares per variant.
- SRM uses the same population as the analysis: non-conflicted exposures.
- Queries must include occurred_at ≥ E.started_at so Postgres prunes old partitions. Verify this with EXPLAIN.
- Known limitation: recently exposed users have had less time to convert. The MVP accepts this and documents it in decisions.md; analyzing only users whose full window has elapsed is a stretch goal.
- Known limitation: occurred_at comes from the client's clock. A client whose clock runs behind can produce events that fall before started_at or outside the window. Document this; don't correct for it.
- Tests with hand-built fixtures must cover:
  - a conversion before exposure (not counted)
  - a conversion after the window (not counted)
  - events exactly at the boundaries
  - multiple conversions (counted once for conversion metrics)
  - conflicted users (excluded)
  - zero-value users in mean metrics

## 14. Statistics engine (server/src/abtest/stats)
The engine is object-oriented, pure, and typed. Every formula's docstring cites its source.

Summaries:
- ProportionSummary(n, successes)
- MeanSummary(n, sum, sum_sq), with mean and sample variance (n − 1)

Tests:
- Abstract base class StatisticalTest with compare(control, treatment, alpha), which returns ComparisonResult(abs_diff, rel_lift, ci_low, ci_high, rel_ci_low, rel_ci_high, p_value, significant, insufficient_data).
- TwoProportionZTest: pooled standard error for the p-value; unpooled standard error for the CI of the difference; delta method for the relative lift CI. Two-sided.
- WelchTTest: Welch–Satterthwaite degrees of freedom; p-value and CI from the t-distribution.
- MSPRT (sequential test; normal approximation; Johari et al., "Peeking at A/B Tests"). For a difference estimate θ̂ with variance V and mixing variance τ²:
  - Λ = sqrt(V/(V+τ²)) · exp(τ²·θ̂² / (2V(V+τ²)))
  - Always-valid p-value: p_n = min(p_{n−1}, 1/Λ_n).
  - Always-valid CI radius = sqrt((V(V+τ²)/τ²) · (2·ln(1/α) + ln((V+τ²)/V))), intersected with all previous CIs.
  - τ is fixed per metric at experiment start: τ_m = expected_baseline_m × mde_relative (absolute units), where expected_baseline_m comes from experiment_metrics.
    - The error guarantee holds for any fixed τ. Choosing τ on the metric's own scale is what gives each metric reasonable power.
  - The worker carries the running-min p-value and CI intersection across snapshots.
    - compare() takes that previous state as an input and returns the new state, so the engine stays pure.
  - Relative-lift CI = the always-valid absolute CI ÷ the control mean (a plug-in approximation, labeled as such in the UI).
  - If the intersected CI becomes empty (the looks disagree; probability ≤ α when the model holds), no CI is reported. The p-value and significance still are.

Other functions:
- srm_check(observed_counts, expected_weights): chi-square goodness-of-fit p-value. Flag SRM if p < 0.001.
- sample_size_two_proportions(baseline, mde_relative, alpha, power): returns n per variant. mde_relative is a positive fraction, and the formula targets baseline × (1 + mde_relative).
- Verdict for the UI:
  - significant_win or significant_loss (respecting the metric's direction)
  - not_significant
  - srm_untrustworthy
  - insufficient_data
- Insufficient data never crashes the engine; the result says "insufficient data" instead. This covers:
  - a variant with fewer than 2 users
  - no variation: every user within each variant has the same value, so there is no standard error
  - a control mean ≤ 0: only the relative-lift fields are left empty (undefined at 0; a misleading sign below 0)
  - SRM expected counts under 5 (the chi-square approximation doesn't hold, so the check is skipped)
- Multiple comparisons (3+ variants, many secondary metrics) are not corrected in the MVP. The primary metric's verdict is the decision. Document this as a limitation.

Testing: unit tests cross-check results against SciPy and statsmodels (test-only) and against hand-worked textbook examples.

## 15. Worker
Run with `python -m abtest.worker`. A single loop runs these jobs:
- compute_results: every RESULTS_INTERVAL_SECONDS (default 300), for each running experiment and each of its metrics, plus once more when an experiment stops. Writes a results_snapshot.
  - "Once more when stopped": each run also handles stopped experiments that have no snapshot computed at or after stopped_at.
  - POST /admin/experiments/{key}/recompute writes one new snapshot now. It never rewrites earlier snapshots. Under mSPRT an extra look is safe.
- maintain_partitions: daily; calls ensure_event_partitions(14).

Each job is guarded by an advisory lock, so two workers never duplicate work. Use pg_try_advisory_xact_lock: it is transaction-scoped, so it is released automatically and can't leak on a pooled connection. Logs are structured JSON with the job name, duration, and rows scanned. The worker shuts down gracefully on SIGTERM.

## 16. Simulator
### A. Monte Carlo validation
The simulation is in-memory, vectorized NumPy, uses the real stats engine, is seeded, and the whole suite should run in about 2 minutes on a laptop. Scenarios:
1. aa_fixed: ≥ 1,000 A/A experiments (baseline 10%), each analyzed once. Report the FPR with its 95% binomial CI.
2. aa_peeking: the same experiments, analyzed every 1,000 users (total across both arms) for 20 looks, stopping at the first p < 0.05. Report the FPR.
3. aa_sequential: the same looks, using MSPRT always-valid p-values. Report the FPR. τ = baseline × a stated MDE; record both in summary.md.
4. power: relative lifts {2%, 5%, 10%} × a range of sample sizes. Report detection rate for fixed-horizon vs sequential, overlaid with the analytic power curve.
5. srm: a bug drops 2% of treatment exposures. Report the SRM detection rate vs sample size, plus the SRM false alarm rate on A/A tests.
6. skewed_means: a lognormal revenue metric. Report Welch calibration at small vs large n.

Command: `python -m abtest.simulator validate --seed 42`. It writes docs/results/*.png and docs/results/summary.md, including the parameters, the seed, and the actual numbers from the run.

### B. Traffic generator
Command: `python -m abtest.simulator traffic --scenario scenarios/<name>.yaml --api http://localhost:8000`.
- The scenario YAML defines the experiment, its variants, the true conversion rate per variant, an optional SRM bug, the user count, and the arrival rate.
- The generator creates and starts the experiment via the admin API, then sends exposures and events through the public API in batches, using the real assignment code.
- The generator is seeded, so a run is reproducible. A single run's CI containing the true lift is one draw; CI coverage across many runs is proven by §16A.
- Ship two scenarios: checkout_button (true +8% lift) and srm_bug.

Pytest includes small, fast calibration tests (marked slow where needed). Their tolerances are derived from binomial error, not hand-picked.

## 17. Dashboard (web/)
Auth:
- A single admin password (ADMIN_PASSWORD env var) gives an httpOnly signed session cookie.
  - The cookie is signed with an HMAC keyed by SESSION_SECRET (env var), using Web Crypto, so no dependency is needed.
- The server key lives only in the Next.js server environment; the browser never sees it. All API calls happen server-side.
- The dashboard works on one project: the project that owns its server key.

Pages:
- /experiments: status, primary metric, users, days running, SRM badge.
- /experiments/new:
  - key, name, and hypothesis (prefilled with the template "If we [change], then [metric] will [increase/decrease] because [reason]")
  - variants with weights, and traffic %
  - primary, secondary, and guardrail metrics, each with its expected baseline
  - MDE, alpha, and analysis type
  - a live estimate of the required sample size, and of the days needed given a daily traffic number the user enters
    - Shown only when the primary metric is a conversion metric; for a mean metric it reads "not available", because the formula is for proportions.
- /experiments/[key]:
  - Overview, changelog, and start/stop/clone controls.
  - Results for each metric: variant, users, conversions or mean, lift vs control with CI, p-value (or always-valid p), and a verdict chip.
  - A plain-language sentence generated from a template (not an LLM), for example: "Variant B increased checkout conversion by 4.1% (95% CI 1.2% to 7.0%). This is statistically significant."
  - A chart of lift with CI over time.
  - Counts of conflicted and assignment-mismatch users.
  - If SRM is flagged: a red banner explaining why the results can't be trusted, with the results collapsed behind a "Show anyway" button.
- /flags, /metrics, and /settings (API keys: create, revoke, show once).

Style: clean, responsive, and accessible. Tailwind; no heavy component library.

## 18. Performance and load testing
- loadtest/seed_events.py: generate 1M and 10M events using COPY.
- Run EXPLAIN (ANALYZE, BUFFERS) on the attribution queries before and after adding indexes, and confirm partition pruning. Record the output in docs/performance.md.
- loadtest/locustfile.py: POST /v1/events in batches of 50, plus GET /v1/config with ETag. Report sustained events/s, p50/p95/p99 latency, error rate, and machine specs.
- Compare naive row-by-row inserts against the unnest batch insert.
- Publish only numbers from actual runs.

## 19. Security
- Server keys are hashed and never logged.
- Client keys can only read config and write events. Admin endpoints require the server key.
- Parametrized SQL only.
- Request size limits.
- CORS is open only on /v1/*.
- Secrets come from env vars, with an up-to-date .env.example.
- No PII is stored beyond opaque user IDs; document this.
  - Event properties are client-controlled JSON. Document that integrators must not put PII in them.
- Access logs can contain the client_key query parameter (sendBeacon path). This is acceptable because client keys are public by design. Server keys travel only in the Authorization header and never appear in URLs.

## 20. Testing and CI
- pytest covers unit tests and integration tests. Integration tests run against a real Postgres: either from docker compose, or a fixture that creates a throwaway database and runs the migrations.
- vitest covers the SDK. The shared hash vectors are tested in both suites.
- GitHub Actions runs:
  - ruff, mypy, and pytest (with a Postgres service container)
  - SDK lint, typecheck, test, and build
  - web lint, typecheck, and build
  - a compose smoke test: bring the stack up with `docker compose up --wait` and check that every service responds. It proves the "fresh clone + `make dev`" criterion on every push.

## 21. Documentation deliverables
README.md, containing:
- a one-paragraph pitch
- a demo GIF (placeholder until I record it)
- the Mermaid architecture diagram
- how it works: assignment, ingestion, attribution, statistics
- validation results (charts plus the summary table)
- performance results
- key design decisions
- limitations and future work
- a one-command quickstart

docs/decisions.md, with ADRs covering at least:
- Postgres vs a columnar store or queue at this scale
- local vs server-side flag evaluation
- two independent hashes
- the partition key in the unique constraint
- a separate exposures table vs deriving exposures from events
- the choice of mSPRT and of τ
- advisory locks
- in-memory rate limiting
- precomputed snapshots vs computing results on read

Other docs:
- docs/experiment-memo-template.md (hypothesis, design, sample size, results, decision, learnings), plus one filled-in example from the checkout_button scenario, clearly labeled as simulated.
- docs/performance.md and docs/results/summary.md.

## 22. Milestones
Every milestone ends with tests passing, lint and type checks clean, and its acceptance criteria demonstrated.

- M0 Scaffold.
  - Build: repo layout, uv project, sdk-js and web skeletons, docker-compose (postgres, api, worker, web, demo), Makefile (dev, migrate, test, lint, simulate), .env.example, CI.
  - Skeleton scope:
    - api serves only GET /health.
    - The worker idles until SIGTERM.
    - web and demo are placeholder pages.
    - `make migrate` and `make simulate` print the milestone they arrive in.
    - Directories are created by the milestone that first fills them.
  - Accept: a fresh clone plus `make dev` runs everything; CI is green.
- M1 Stats engine (§14).
  - Accept: tests cross-checked against SciPy and statsmodels pass; stats/ has no DB or web imports.
- M2 Monte Carlo validation (§16A).
  - Accept: the A/A FPR falls inside the expected band; the sequential FPR is ≤ α within Monte Carlo error; the charts and summary.md are generated from a real run.
- M3 Database (§10).
  - Accept: migrations apply on an empty DB; the runner is idempotent; tests cover the constraints, the partition function, and the exposure upsert.
- M4 Assignment, admin API, and config endpoint (§9, §11). The results and recompute endpoints come with M7, because they read and write the worker's snapshots.
  - Accept: the hash vectors pass; the uniformity test passes; the lifecycle rules are enforced and tested; ETag/304 works; config_version bumps on changes.
- M5 SDK and demo page (§12).
  - Accept: vitest passes (vectors, batching, retry/backoff, exposure dedupe, beacon path); bundle size is measured; the demo works against the local API.
- M6 Ingestion (§11 events, §10 rules).
  - Accept: duplicates are counted, not inserted; rejected events come back with reasons; exposures keep the earliest time; conflicts and mismatches are flagged; the rate limit returns 429.
- M7 Worker, attribution, and traffic generator (§13, §15, §16B), plus GET /admin/experiments/{key}/results and POST /admin/experiments/{key}/recompute.
  - Accept: the attribution edge-case tests pass; the checkout_button CI contains the true lift; the srm_bug scenario is flagged.
- M8 Dashboard (§17).
  - Accept: the full flow works in the browser: create metric → create experiment → start → run scenario → read results. The SRM banner appears for srm_bug.
- M9 Performance (§18).
  - Accept: docs/performance.md contains real numbers and EXPLAIN output.
- M10 Docs and deploy (§21).
  - Accept: the README is complete; the quickstart is verified from a fresh clone; the app is deployed (I'll choose the provider).

## 23. Stretch goals
- CUPED
- mutually exclusive layers
- holdout groups
- full-window-only analysis
- a Python server-side SDK
- a Bayesian results view
- a Playwright e2e test of the dashboard

## 24. Changelog
### v1.5 — M5 decisions (2026-09-28)
- §12: what stats().dropped counts; a flush sends the whole queue; invalid user ids throw; events JSON can't encode are dropped at track() time.
- §11: GET /v1/config also sends `Vary: X-Client-Key`, so a shared cache never serves one project's config to another.

### v1.4 — M3 and M4 decisions (2026-09-28)
- §10: variant keys follow the same format as other keys; weight_bp ≥ 1 (the SRM check needs positive shares); expected_baseline > 0.
- §11: last-server-key guard; keys never cross kinds; stopped experiments can only be renamed; null in PATCH means unchanged; conversion baselines below 1; start errors list every problem.
- §22: the results and recompute endpoints move from M4 to M7, where snapshots exist.

### v1.3 — M1 stats engine (2026-09-28)
Edge cases the engine had to decide; each is written into §14.
- ComparisonResult carries insufficient_data (the reason), and the verdict gains insufficient_data.
- Zero variance counts as insufficient data. Relative lift needs a control mean > 0, not just ≠ 0.
- mSPRT: an empty intersected CI is reported as no CI.
- Sample size: mde_relative is a positive fraction, and the target is baseline × (1 + mde_relative).

### v1.2 — after M0 (2026-09-28)
- §8: httpx2 replaces httpx (approved).
- Repo: MIT license (approved).

### v1.1 — kickoff review (2026-09-27)
Resolutions agreed at the project kickoff. Each change is written into the section named.
- §8: approved uvicorn, ESLint + typescript-eslint + @eslint/js, nginx (demo), scipy-stubs, types-PyYAML.
- §10: expected_baseline moved from experiments to experiment_metrics. One baseline per attached metric, giving a per-metric mSPRT τ.
- §10, §11: "$exposure" events are stored in events too. Exposures are upserted only for newly inserted rows, after aggregating within the batch. This gives uniform duplicate counting and a raw audit log.
- §10: key format rule. Partition range covers today − 7 days, to avoid the default-partition trap. A migrate compose service and a bootstrap for the default project and keys.
- §10: assignment_mismatch also covers the case where the server computes "not included".
- §11: CORS header details; JSON body parsed whatever the Content-Type; user_id must be valid Unicode; configurable rate limits; exposures after a stop are rejected.
- §11: PATCH rules for metrics and for running experiments (the "description" column didn't exist); api-key endpoints; admin scope is the key's project; sample size is for conversion metrics only.
- §12: SDK floor is Node 22+, not 18+. Beacons are sent as text/plain and chunked under 64 KB.
- §13: null values count as 0; SRM population; client clock-skew limitation.
- §14: per-metric τ; compare() takes previous state; relative CI under mSPRT; insufficient-data handling; multiple-comparisons limitation.
- §15: final-snapshot detection, /recompute semantics, pg_try_advisory_xact_lock.
- §16: peeking looks count total users; A/A sequential τ recorded; traffic generator is seeded.
- §17: SESSION_SECRET; single-project dashboard; per-metric baselines on the new-experiment form; sample-size estimate scope.
- §19: PII in properties; client keys in access logs.
- §20: compose smoke-test CI job.
- §22: M0 skeleton scope.