"""FastAPI application factory.

Run with `uvicorn --factory abtest.api.app:create_app`. A factory (rather than a module-level
`app`) means importing this module never reads settings or touches the database, which keeps
tests in control of both.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from abtest.api.cors import PublicCORS
from abtest.api.errors import install_error_handlers
from abtest.api.routers import api_keys, config, experiments, flags, health, metrics, sample_size
from abtest.config import Settings
from abtest.db.pool import create_pool


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API. Tests pass their own Settings; production reads the environment."""
    settings = settings or Settings()
    pool = create_pool(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # open() returns without waiting for a connection, so the API still starts (and
        # reports itself unhealthy on /health) when the database is down.
        pool.open()
        app.state.pool = pool
        yield
        pool.close()

    app = FastAPI(title="abtest-platform API", lifespan=lifespan)
    app.add_middleware(PublicCORS)
    install_error_handlers(app)
    for module in (health, config, metrics, flags, experiments, api_keys, sample_size):
        app.include_router(module.router)
    return app
