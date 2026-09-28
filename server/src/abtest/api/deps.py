"""FastAPI dependencies shared by routers."""

from typing import Annotated

from fastapi import Depends, Request
from psycopg_pool import ConnectionPool


def get_pool(request: Request) -> ConnectionPool:
    """Return the connection pool that the app lifespan opened."""
    pool: ConnectionPool = request.app.state.pool
    return pool


PoolDep = Annotated[ConnectionPool, Depends(get_pool)]
