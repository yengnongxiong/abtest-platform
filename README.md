# abtest-platform

A self-hostable feature-flag and A/B-testing platform, built to show that experiment results can be trusted. A zero-dependency TypeScript SDK assigns users to variants locally with deterministic hashing and batches events to a FastAPI service backed by PostgreSQL. A Python worker attributes conversions and runs a statistics engine that guards against the classic mistakes: peeking (sequential testing with mSPRT), sample ratio mismatch, and bad attribution windows. A Monte Carlo simulator checks that those safeguards actually work. The full spec is in [docs/PRD.md](docs/PRD.md).

> **Status:** under construction. Done: M0 (scaffold: every service starts, but only `GET /health` does real work) and M1 (the statistics engine in `server/src/abtest/stats`, not yet wired to any service). See the milestone list in the PRD (§22).

## Quickstart

Prerequisites: Docker with Compose (Docker Desktop, OrbStack, or Colima) and `make`.

```sh
git clone https://github.com/yengnongxiong/abtest-platform.git
cd abtest-platform
make dev
```

`make dev` creates `.env` from `.env.example` on first run, builds the images, and starts the stack. Source changes sync into the running containers (Docker Compose Watch).

| Service | URL |
|---|---|
| API health check | http://localhost:8000/health |
| Dashboard (placeholder) | http://localhost:3000 |
| SDK demo page (placeholder) | http://localhost:8080 |
| PostgreSQL | `localhost:5432` (credentials in `.env`) |

## Development

Host-side tools: [uv](https://docs.astral.sh/uv/) and Node 24 (see `.nvmrc`).

| Command | What it does |
|---|---|
| `make setup` | Install Python and Node dependencies on your machine |
| `make test` | Start Postgres, then run the Python and SDK tests |
| `make lint` | ruff, mypy, ESLint, and TypeScript checks |
| `make down` | Stop the stack |

Design decisions and their trade-offs are recorded in [docs/decisions.md](docs/decisions.md).

## License

[MIT](LICENSE)
