"""Shared pytest fixtures.

Integration tests use a real Postgres, never mocks: each test session creates throwaway
databases on the server named by DATABASE_URL and drops them at the end.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from abtest.db.migrate import migrate

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"


@pytest.fixture(scope="session")
def database_url() -> str:
    """DSN of a real Postgres server.

    `make test` starts the db container and loads DATABASE_URL from .env; CI sets it for
    its Postgres service container.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.fail("DATABASE_URL is not set. Run the tests with `make test`.")
    return url


@pytest.fixture(scope="session")
def create_database(database_url: str) -> Iterator[Callable[[], str]]:
    """A factory for empty throwaway databases; returns each one's DSN."""
    created: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as admin:

        def create() -> str:
            name = f"abtest_test_{uuid4().hex[:12]}"
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            created.append(name)
            return make_conninfo(database_url, dbname=name)

        yield create
        for name in created:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def migrations_dir() -> Path:
    return MIGRATIONS_DIR


@pytest.fixture(scope="session")
def migrated_database(create_database: Callable[[], str]) -> str:
    """One database with every migration applied, shared by the session's tests."""
    url = create_database()
    with psycopg.connect(url, autocommit=True) as conn:
        migrate(conn, MIGRATIONS_DIR)
    return url


@pytest.fixture
def conn(migrated_database: str) -> Iterator[psycopg.Connection]:
    """A connection to the migrated database. Everything a test does is rolled back, so tests
    never see each other's rows.

    The test runs inside an outer transaction, so a `conn.transaction()` block in the code
    under test becomes a savepoint. On an idle connection it would start, and commit, a real
    transaction that no rollback here could undo.
    """
    with (
        psycopg.connect(migrated_database) as connection,
        connection.transaction(force_rollback=True),
    ):
        yield connection


@pytest.fixture
def project_id(conn: psycopg.Connection) -> UUID:
    row = conn.execute(
        "INSERT INTO projects (name) VALUES ('Test project') RETURNING id"
    ).fetchone()
    assert row is not None
    project: UUID = row[0]
    return project


@pytest.fixture
def experiment_id(conn: psycopg.Connection, project_id: UUID) -> UUID:
    """A draft experiment with a 50/50 control and treatment."""
    row = conn.execute(
        "INSERT INTO experiments (project_id, key, name, traffic_bp)"
        " VALUES (%s, 'checkout-button', 'Checkout button', 10000) RETURNING id",
        (project_id,),
    ).fetchone()
    assert row is not None
    experiment: UUID = row[0]
    conn.execute(
        "INSERT INTO variants (experiment_id, key, name, weight_bp, is_control, position)"
        " VALUES (%s, 'control', 'Control', 5000, true, 0),"
        "        (%s, 'treatment', 'Treatment', 5000, false, 1)",
        (experiment, experiment),
    )
    return experiment


@pytest.fixture
def variants(conn: psycopg.Connection, experiment_id: UUID) -> dict[str, UUID]:
    """The test experiment's variant ids, by key."""
    rows = conn.execute(
        "SELECT key, id FROM variants WHERE experiment_id = %s", (experiment_id,)
    ).fetchall()
    return {key: variant_id for key, variant_id in rows}
