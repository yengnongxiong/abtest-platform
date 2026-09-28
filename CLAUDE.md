# CLAUDE.md — abtest-platform

## What this is
A feature flag + A/B testing platform built as a portfolio project for PM and SWE internship applications. The full spec is docs/PRD.md. Read the relevant PRD sections before starting any task. The PRD is the source of truth: if you think something in it is wrong or won't work, stop and tell me. Never silently deviate from it.

## How we work
- One milestone at a time, in order (see the Milestones section of the PRD). Never build features from later milestones.
- Work autonomously and keep going from one milestone to the next in the same session (agreed 2026-09-28). For each milestone:
  0. First check notes/ for a `M<N>-handoff.md` from an interrupted session, and resume from it.
  1. Plan: files, key design choices with the rejected alternative, SQL, and tests.
  2. Build, with tests alongside the code.
  3. Run the full test suite, linters, and type checks, and fix everything.
  4. Commit and push, and confirm CI is green.
  5. Write the milestone report, then continue with the next milestone (the post-M2/M5/M8 code reviews count as milestones).
- Stop and tell me to type /clear and "Continue with the next milestone." only when the context has grown large enough to hurt code quality, preferably at a milestone boundary.
- If a session has to stop mid-milestone (for example, its context is getting full), leave the work uncommitted but passing, and write notes/M<N>-handoff.md: what's done, what's left in order, and the gotchas found.
- Don't wait for plan approval. Stop and ask only if:
  - the PRD won't work as written,
  - a dependency not in the PRD's tech stack is needed, or
  - a failure can't be fixed.
- After M2, M5, and M8, run a strict code review of the whole repo before continuing. Fix high-severity findings and list the rest in the report.
- Milestone report, in chat and saved to notes/M<N>.md (gitignored, never published):
  1. each acceptance criterion, with the command that proves it
  2. commands I can run to see it working
  3. the 3–5 key design decisions, explained for an interview
  4. docs/decisions.md entries added
  5. open doubts, and the files to read most carefully
- Small, reviewable changes. Prefer simple, boring solutions over clever ones.
- Ask before adding any dependency not listed in the PRD's tech stack section.
- I must be able to understand and defend every line in interviews. Favor readable code, clear names, and short docstrings that explain WHY. No dead code, no speculative abstractions.
- Never invent numbers (performance, simulation results, bundle size) in docs or the README. Only write numbers produced by commands actually run in this repo, and note the command that produced them.
- Record significant design decisions in docs/decisions.md using: Context / Decision / Alternatives considered / Consequences.
- Git: commit and push to the public repo (github.com/yengnongxiong/abtest-platform) at the end of each milestone, once all checks pass. Never force-push. Never commit secrets; use .env (gitignored) and keep .env.example up to date.

## Commands (defined in M0; keep this section updated)
- make setup: install host dependencies (uv sync, npm ci for sdk-js and web)
- make dev: docker compose up --build --watch (db, api, worker, web, demo)
- make down: stop the stack
- make migrate: apply SQL migrations and bootstrap (default project, API keys, event partitions) to the db container
- make test: Python + SDK tests (starts the db container first)
- make lint: ruff, mypy, eslint, tsc
- make simulate: run Monte Carlo validation (about 30 s) and regenerate docs/results/
- make traffic SCENARIO=checkout_button: simulated users through the running stack (scenarios/*.yaml), then print the results. ARGS="--use-running --experiment-key <key>" feeds an experiment created and started in the dashboard

## Local environment
- Docker runs via Colima (`colima start` after a reboot). Node 24 via Homebrew (node@24).

## Code standards
- Python 3.14 (the PRD requires 3.12+), managed with uv. Type hints everywhere; mypy strict on src/ and tests/. ruff for lint and format.
- Pydantic v2 for API schemas. psycopg 3 with parametrized SQL only; never build SQL with f-strings or string formatting that contains values.
- Business logic lives outside route handlers. The stats engine is pure: no DB, web, or I/O imports.
- TypeScript: strict mode, no `any`. The SDK has zero runtime dependencies.
- SQL migrations are plain numbered .sql files in db/migrations/. Never edit a migration that has been applied; add a new one. Comment every index with the query it serves.
- Tests: pytest (integration tests use a real Postgres, not mocks), vitest for the SDK. Every bug fix gets a regression test.

## Gotchas
- Assignment hashing must be byte-identical in Python and TypeScript. Any change requires updating shared/hash_test_vectors.json (`cd server && uv run python scripts/generate_hash_vectors.py`) and passing both test suites.
- Unique constraints on the partitioned events table must include occurred_at (the partition key).
- Every statistical function's docstring cites the source of its formula.