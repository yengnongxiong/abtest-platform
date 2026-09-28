"""The migration runner, against real throwaway databases."""

import shutil
import threading
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from abtest.db.migrate import MigrationError, load_migrations, migrate

# Every file in db/migrations, in the order they must apply.
ALL_MIGRATIONS = ["0001_init", "0002_covering_attribution_index"]
EXPECTED_TABLES = {
    "projects", "api_keys", "metrics", "flags", "experiments", "variants",
    "experiment_metrics", "experiment_changes", "exposures", "events", "events_default",
    "results_snapshots", "schema_migrations",
}  # fmt: skip


def tables(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        " UNION SELECT relname FROM pg_class WHERE relkind = 'p'"
    ).fetchall()
    return {name for (name,) in rows}


def test_applies_every_migration_to_an_empty_database(
    create_database: Callable[[], str], migrations_dir: Path
) -> None:
    with psycopg.connect(create_database(), autocommit=True) as conn:
        applied = migrate(conn, migrations_dir)

        assert applied == ALL_MIGRATIONS
        assert tables(conn) == EXPECTED_TABLES


def test_rerunning_applies_nothing(
    create_database: Callable[[], str], migrations_dir: Path
) -> None:
    with psycopg.connect(create_database(), autocommit=True) as conn:
        migrate(conn, migrations_dir)
        before = conn.execute("SELECT version, applied_at FROM schema_migrations").fetchall()

        assert migrate(conn, migrations_dir) == []
        assert (
            conn.execute("SELECT version, applied_at FROM schema_migrations").fetchall() == before
        )


def test_refuses_to_run_after_an_applied_migration_was_edited(
    create_database: Callable[[], str], migrations_dir: Path, tmp_path: Path
) -> None:
    shutil.copy(migrations_dir / "0001_init.sql", tmp_path)
    with psycopg.connect(create_database(), autocommit=True) as conn:
        migrate(conn, tmp_path)
        with (tmp_path / "0001_init.sql").open("a") as edited:
            edited.write("\n-- an innocent-looking edit\n")

        with pytest.raises(MigrationError, match="edited after it was applied"):
            migrate(conn, tmp_path)


def test_each_migration_commits_on_its_own(
    create_database: Callable[[], str], tmp_path: Path
) -> None:
    # 0001 is fine, 0002 fails halfway: 0001 stays applied, and 0002 leaves no trace.
    (tmp_path / "0001_first.sql").write_text("CREATE TABLE first (id int);")
    (tmp_path / "0002_broken.sql").write_text("CREATE TABLE second (id int); SELECT 1/0;")
    with psycopg.connect(create_database(), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.DivisionByZero):
            migrate(conn, tmp_path)

        assert tables(conn) == {"first", "schema_migrations"}
        versions = conn.execute("SELECT version FROM schema_migrations").fetchall()
        assert versions == [("0001_first",)]


def test_concurrent_runners_apply_each_migration_once(
    create_database: Callable[[], str], migrations_dir: Path
) -> None:
    url = create_database()
    results: list[list[str]] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            with psycopg.connect(url, autocommit=True) as conn:
                results.append(migrate(conn, migrations_dir))
        except BaseException as error:  # surfaced by the assertion below
            errors.append(error)

    runners = [threading.Thread(target=run) for _ in range(4)]
    for runner in runners:
        runner.start()
    for runner in runners:
        runner.join()

    assert errors == []
    assert sorted(results) == [[], [], [], ALL_MIGRATIONS]


@pytest.mark.parametrize(
    ("names", "message"),
    [
        (["1_init.sql"], "expected a name like"),
        (["0001_Init.sql"], "expected a name like"),
        (["0001_a.sql", "0001_b.sql"], "used twice"),
        ([], "no migrations found"),
    ],
)
def test_rejects_badly_named_migrations(tmp_path: Path, names: list[str], message: str) -> None:
    for name in names:
        (tmp_path / name).write_text("SELECT 1;")

    with pytest.raises(MigrationError, match=message):
        load_migrations(tmp_path)


def test_needs_an_autocommit_connection(migrated_database: str, migrations_dir: Path) -> None:
    with psycopg.connect(migrated_database) as conn, pytest.raises(MigrationError):
        migrate(conn, migrations_dir)
