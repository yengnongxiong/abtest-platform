# Developer commands. Run `make setup` once, then `make dev`.
# Written for GNU Make 3.81 (the version macOS ships), so no newer features.

# Load .env so host-side commands (pytest) see DATABASE_URL. Docker Compose reads .env
# by itself. If .env is missing, make creates it with the rule below, then reloads.
-include .env
export

.PHONY: setup dev down migrate test lint simulate traffic

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
	cd server && uv run ruff check . && uv run ruff format --check . && uv run mypy
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
