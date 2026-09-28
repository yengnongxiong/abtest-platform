"""Postgres connection pool."""

from psycopg_pool import ConnectionPool


def create_pool(database_url: str) -> ConnectionPool:
    """Create a connection pool without opening it.

    The owner (the API lifespan) opens and closes it explicitly, so connections are only
    made while the app is running, never as a side effect of importing a module.

    Connections are in autocommit mode: a single statement commits on its own, and a
    multi-statement write says so with an explicit `conn.transaction()` block.
    """
    return ConnectionPool(conninfo=database_url, open=False, kwargs={"autocommit": True})
