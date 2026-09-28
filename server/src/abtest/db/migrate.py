"""A small migration runner for the plain SQL files in db/migrations (PRD §10).

Files are named NNNN_description.sql and applied in number order, each in its own
transaction, and recorded in schema_migrations with a checksum of their contents. Rerunning
applies only what's new, and refuses to continue if an applied file was edited: an applied
migration is history, so a change needs a new migration.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
# Any constant works; it only has to be the same for every runner.
LOCK_ID = 7_300_001


@dataclass(frozen=True, slots=True)
class Migration:
    version: str  # the file name without .sql, e.g. "0001_init"
    sql: bytes
    checksum: str  # SHA-256 of the file, hex


class MigrationError(Exception):
    pass


def load_migrations(migrations_dir: Path) -> list[Migration]:
    """Read every migration file, in number order, rejecting bad names and duplicate numbers."""
    migrations: dict[str, Migration] = {}
    for path in sorted(migrations_dir.glob("*.sql")):
        match = MIGRATION_NAME.match(path.name)
        if not match:
            raise MigrationError(f"{path.name}: expected a name like 0002_add_index.sql")
        number = match.group(1)
        if number in migrations:
            raise MigrationError(f"{path.name}: migration number {number} is used twice")
        sql = path.read_bytes()
        migrations[number] = Migration(path.stem, sql, hashlib.sha256(sql).hexdigest())
    if not migrations:
        raise MigrationError(f"no migrations found in {migrations_dir}")
    return list(migrations.values())


def migrate(conn: psycopg.Connection, migrations_dir: Path) -> list[str]:
    """Apply the pending migrations and return their versions.

    Every step holds a transaction-scoped advisory lock, so two runners started together
    (two containers, or tests in parallel) take turns instead of applying a migration twice.
    """
    if not conn.autocommit:
        # Otherwise psycopg nests each `transaction()` block in an outer transaction as a
        # savepoint, and nothing is committed migration by migration.
        raise MigrationError("migrate() needs a connection in autocommit mode")
    migrations = load_migrations(migrations_dir)
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY,"
            " checksum text NOT NULL,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )

    applied = []
    for migration in migrations:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            row = conn.execute(
                "SELECT checksum FROM schema_migrations WHERE version = %s", (migration.version,)
            ).fetchone()
            if row is not None:
                if row[0] != migration.checksum:
                    raise MigrationError(
                        f"{migration.version} was edited after it was applied; "
                        "add a new migration instead"
                    )
                continue
            # A file holds several statements, which psycopg runs in one go when there are
            # no parameters. It is our own checked-in SQL, never user input.
            conn.execute(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                (migration.version, migration.checksum),
            )
            applied.append(migration.version)
    return applied
