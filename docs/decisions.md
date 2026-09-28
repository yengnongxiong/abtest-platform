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
- Starlette 1.7 prints a deprecation warning suggesting `httpx2` instead of `httpx` for its test client. We stay on `httpx`, which the PRD lists and which still works, until a switch is approved.
- Revisit Node when 26 becomes LTS (2026-10-28), and TypeScript when typescript-eslint supports 7.
