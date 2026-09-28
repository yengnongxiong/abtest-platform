# Development image for the api and worker services (see docker-compose.yml).
FROM python:3.14.7-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

WORKDIR /app
# Use the image's Python instead of downloading one; install into /app/.venv.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Dependencies first, so this layer stays cached until the lock file changes.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev

# The SQL migrations live at the repo root; docker-compose.yml passes them in as the
# "migrations" build context. `python -m abtest.db migrate` reads ./db/migrations.
COPY --from=migrations . ./db/migrations
