# Developer commands. Run `make setup` once, then `make dev`.
# Written for GNU Make 3.81 (the version macOS ships), so no newer features.

# Load .env so host-side commands (pytest) see DATABASE_URL. Docker Compose reads .env
# by itself. If .env is missing, make creates it with the rule below, then reloads.
-include .env
export

.PHONY: setup dev down migrate test lint simulate traffic seed perf loadtest screenshots

# Copy the example only when .env is missing, so local edits are never overwritten.
.env:
	cp .env.example .env

setup: .env
	cd server && uv sync
	cd sdk-js && npm ci
	cd web && npm ci

dev: .env
	docker compose up --build --watch

down:
	docker compose down

# Integration tests need a real Postgres, so start (or reuse) the db container first.
test: .env
	docker compose up --detach --wait db
	cd server && uv run pytest
	cd sdk-js && npm test

lint:
	cd server && uv run ruff check . ../loadtest ../scripts \
		&& uv run ruff format --check . ../loadtest ../scripts && uv run mypy
	cd sdk-js && npm run lint && npm run typecheck
	cd web && npm run lint && npm run typecheck

# Migrate the database in DATABASE_URL (from .env). Run from the repo root, where db/migrations is.
migrate: .env
	docker compose up --detach --wait db
	uv run --project server python -m abtest.db migrate

# Run from the repo root so the results land in docs/results.
simulate:
	uv run --project server python -m abtest.simulator validate --seed 42

# Simulated users through the running stack (`make dev`), e.g. make traffic SCENARIO=srm_bug.
# ARGS passes options on, e.g. ARGS="--use-running --experiment-key my-test" to feed an
# experiment created and started in the dashboard.
SCENARIO ?= checkout_button
ARGS ?=
traffic:
	uv run --project server python -m abtest.simulator traffic --scenario scenarios/$(SCENARIO).yaml $(ARGS)

# Performance (docs/performance.md). `make seed` recreates a separate database, abtest_perf,
# holding EVENTS events; `make perf` measures it: EXPLAIN of the attribution query, a worker
# look and the results read, then row-by-row vs batch inserts (last, because they add rows).
EVENTS ?= 1000000
seed: .env
	docker compose up --detach --wait db
	uv run --project server python loadtest/seed_events.py --events $(EVENTS)

perf: .env
	uv run --project server python loadtest/explain_attribution.py
	uv run --project server python loadtest/results_benchmark.py
	uv run --project server python loadtest/insert_benchmark.py

# Load test (after `make seed`): an API on abtest_perf, run as in production (no --reload),
# with the rate limit raised so it measures ingestion rather than the limiter; then Locust.
LOADTEST_USERS ?= 32
LOADTEST_WORKERS ?= 1
LOADTEST_TIME ?= 60s
LOADTEST_API = abtest-loadtest-api
loadtest: .env
	-@docker rm --force $(LOADTEST_API) > /dev/null 2>&1
	docker compose up --detach --wait db
	docker compose run --build --rm --detach --no-deps --name $(LOADTEST_API) \
		--publish 8001:8000 \
		--env DATABASE_URL=postgresql://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@db:5432/abtest_perf \
		--env RATE_LIMIT_PER_SECOND=1000000 --env RATE_LIMIT_BURST=1000000 \
		api uvicorn --factory abtest.api.app:create_app --host 0.0.0.0 --port 8000 \
		--workers $(LOADTEST_WORKERS)
	curl --silent --fail --output /dev/null --retry 30 --retry-all-errors --retry-delay 1 \
		http://localhost:8001/health
	uv run --project server locust --locustfile loadtest/locustfile.py --headless --only-summary \
		--host http://localhost:8001 --users $(LOADTEST_USERS) --spawn-rate $(LOADTEST_USERS) \
		--run-time $(LOADTEST_TIME) --reset-stats; \
	status=$$?; docker stop $(LOADTEST_API) > /dev/null; exit $$status

# The README's screenshots (docs/screenshots/), taken with Playwright from a run against an
# empty stack: see scripts/README.md.
screenshots: .env
	uv run --project server python scripts/screenshots.py
