"""Postgres connection pool."""

from psycopg_pool import ConnectionPool


def create_pool(database_url: str) -> ConnectionPool:
    """Create a connection pool without opening it.

    The owner (the API lifespan) opens and closes it explicitly, so connections are only
    made while the app is running, never as a side effect of importing a module.
    """
    return ConnectionPool(conninfo=database_url, open=False)
