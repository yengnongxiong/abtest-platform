"""GET /health: is the API up, and can it reach the database?"""

from typing import Literal

import psycopg
from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from abtest.api.deps import PoolDep

router = APIRouter()

# Short enough that a load balancer or compose healthcheck gets an answer quickly, long
# enough to ride out a momentarily busy pool.
DB_TIMEOUT_SECONDS = 2.0


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]


@router.get("/health")
def health(pool: PoolDep, response: Response) -> HealthResponse:
    """Return 200 when a pooled connection can run `SELECT 1`, otherwise 503."""
    try:
        with pool.connection(timeout=DB_TIMEOUT_SECONDS) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error:  # includes psycopg_pool.PoolTimeout (no connection within timeout)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="unavailable")
    return HealthResponse(status="ok")
