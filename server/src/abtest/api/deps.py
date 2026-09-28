"""FastAPI dependencies shared by routers: a database connection, and who is calling."""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import Depends, Header, Query, Request
from psycopg_pool import ConnectionPool

from abtest.db.api_keys import project_for_key
from abtest.errors import Unauthorized


def get_pool(request: Request) -> ConnectionPool:
    """Return the connection pool that the app lifespan opened."""
    pool: ConnectionPool = request.app.state.pool
    return pool


PoolDep = Annotated[ConnectionPool, Depends(get_pool)]


def get_conn(pool: PoolDep) -> Iterator[psycopg.Connection]:
    """A pooled connection for the whole request, shared by every dependency that asks.

    Connections are in autocommit mode: each write opens its own explicit transaction, so
    nothing depends on when FastAPI finishes this dependency relative to the response.
    """
    with pool.connection() as conn:
        yield conn


ConnDep = Annotated[psycopg.Connection, Depends(get_conn)]


def server_project(conn: ConnDep, authorization: Annotated[str | None, Header()] = None) -> UUID:
    """Admin endpoints: `Authorization: Bearer <server key>` -> the key's project.

    Server keys are only ever read from this header, never from a URL (PRD §19).
    """
    scheme, _, key = (authorization or "").strip().partition(" ")
    key = key.strip()
    project = project_for_key(conn, key, "server") if scheme.lower() == "bearer" else None
    if project is None:
        raise Unauthorized(
            "unauthorized", "send a valid server key as 'Authorization: Bearer <key>'"
        )
    return project


def client_key(
    x_client_key: Annotated[str | None, Header()] = None,
    client_key: Annotated[str | None, Query()] = None,
) -> str | None:
    """Public endpoints: the client key from the X-Client-Key header, or from the client_key
    query parameter (navigator.sendBeacon can't set headers)."""
    return x_client_key or client_key


ClientKey = Annotated[str | None, Depends(client_key)]


def client_project(conn: ConnDep, key: ClientKey) -> UUID:
    project = project_for_key(conn, key, "client") if key else None
    if project is None:
        raise Unauthorized("unauthorized", "send a valid client key in the X-Client-Key header")
    return project


ServerProject = Annotated[UUID, Depends(server_project)]
ClientProject = Annotated[UUID, Depends(client_project)]
