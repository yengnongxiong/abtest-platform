"""The schema's constraints and conventions, checked against a real migrated database."""

from datetime import UTC, datetime, time, timedelta
from typing import LiteralString
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import errors


def test_every_foreign_key_has_an_index(conn: psycopg.Connection) -> None:
    # PRD §10: an index whose leading columns are the foreign key's columns, so a delete of
    # the referenced row (or a join on the key) never scans the referencing table.
    missing = conn.execute(
        """
        SELECT c.conrelid::regclass::text, c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE c.contype = 'f'
          AND NOT t.relispartition  -- partitions inherit their parent's constraints and indexes
          AND NOT EXISTS (
              SELECT 1 FROM pg_index i
              WHERE i.indrelid = c.conrelid
                AND (i.indkey::int2[])[0:cardinality(c.conkey) - 1] = c.conkey
          )
        """
    ).fetchall()

    assert missing == []


def test_every_index_says_which_query_it_serves(conn: psycopg.Connection) -> None:
    # Indexes that back a PRIMARY KEY or UNIQUE constraint serve that constraint; every other
    # index needs a COMMENT ON INDEX (CLAUDE.md).
    uncommented = conn.execute(
        """
        SELECT i.indexrelid::regclass::text
        FROM pg_index i
        JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public'
          AND NOT t.relispartition
          AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.indexrelid)
          AND obj_description(i.indexrelid, 'pg_class') IS NULL
        """
    ).fetchall()

    assert uncommented == []


@pytest.mark.parametrize("key", ["checkout-button", "a", "exp_2026-09", "x" * 64])
def test_entity_keys_accept_the_documented_format(
    conn: psycopg.Connection, project_id: UUID, key: str
) -> None:
    conn.execute("INSERT INTO flags (project_id, key) VALUES (%s, %s)", (project_id, key))


@pytest.mark.parametrize("key", ["Checkout", "a:b", "-leading-dash", "", "x" * 65, "has space"])
def test_entity_keys_reject_anything_else(
    conn: psycopg.Connection, project_id: UUID, key: str
) -> None:
    # ":" matters most: hash inputs are joined with ":", so it could make two inputs collide.
    with pytest.raises(errors.CheckViolation):
        conn.execute("INSERT INTO flags (project_id, key) VALUES (%s, %s)", (project_id, key))


def test_keys_are_unique_within_a_project(conn: psycopg.Connection, project_id: UUID) -> None:
    conn.execute("INSERT INTO flags (project_id, key) VALUES (%s, 'dark-mode')", (project_id,))

    with pytest.raises(errors.UniqueViolation):
        conn.execute("INSERT INTO flags (project_id, key) VALUES (%s, 'dark-mode')", (project_id,))


def test_an_experiment_has_at_most_one_control(
    conn: psycopg.Connection, experiment_id: UUID
) -> None:
    with pytest.raises(errors.UniqueViolation):
        conn.execute(
            "INSERT INTO variants (experiment_id, key, name, weight_bp, is_control, position)"
            " VALUES (%s, 'second-control', 'Second control', 100, true, 2)",
            (experiment_id,),
        )


@pytest.mark.parametrize(("key", "position"), [("control", 5), ("new", 0)])
def test_variant_keys_and_positions_are_unique(
    conn: psycopg.Connection, experiment_id: UUID, key: str, position: int
) -> None:
    with pytest.raises(errors.UniqueViolation):
        conn.execute(
            "INSERT INTO variants (experiment_id, key, name, weight_bp, position)"
            " VALUES (%s, %s, 'Variant', 100, %s)",
            (experiment_id, key, position),
        )


def test_an_experiment_has_at_most_one_primary_metric(
    conn: psycopg.Connection, project_id: UUID, experiment_id: UUID
) -> None:
    metric_ids = [
        conn.execute(
            "INSERT INTO metrics (project_id, key, name, kind, event_name, direction)"
            " VALUES (%s, %s, 'Metric', 'conversion', 'purchase', 'increase') RETURNING id",
            (project_id, key),
        ).fetchone()
        for key in ("first", "second")
    ]
    insert = "INSERT INTO experiment_metrics (experiment_id, metric_id, role) VALUES (%s, %s, %s)"
    first, second = (row[0] for row in metric_ids if row is not None)
    conn.execute(insert, (experiment_id, first, "primary"))

    with pytest.raises(errors.UniqueViolation):
        conn.execute(insert, (experiment_id, second, "primary"))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE flags SET rollout_bp = 10001",
        "UPDATE flags SET rollout_bp = -1",
        "UPDATE experiments SET traffic_bp = 10001",
        "UPDATE experiments SET alpha = 1",
        "UPDATE experiments SET mde_relative = 0",
        "UPDATE experiments SET status = 'paused'",
        "UPDATE variants SET weight_bp = 0",  # a zero-weight variant breaks the SRM check
        "UPDATE experiment_metrics SET expected_baseline = 0",
    ],
)
def test_out_of_range_values_are_rejected(
    conn: psycopg.Connection, project_id: UUID, experiment_id: UUID, statement: LiteralString
) -> None:
    # Each test's rows are the only rows its transaction sees, so an unfiltered UPDATE only
    # touches the rows made here.
    conn.execute("INSERT INTO flags (project_id, key) VALUES (%s, 'flag')", (project_id,))
    conn.execute(
        "WITH m AS (INSERT INTO metrics (project_id, key, name, kind, event_name, direction)"
        "  VALUES (%s, 'metric', 'Metric', 'conversion', 'purchase', 'increase') RETURNING id)"
        " INSERT INTO experiment_metrics (experiment_id, metric_id, role)"
        " SELECT %s, id, 'primary' FROM m",
        (project_id, experiment_id),
    )

    with pytest.raises(errors.CheckViolation):
        conn.execute(statement)


def test_status_and_lifecycle_timestamps_agree(
    conn: psycopg.Connection, experiment_id: UUID
) -> None:
    # A running experiment must have started_at; a stopped one must have stopped_at. Each
    # expected failure runs in a savepoint, so the test's transaction carries on after it.
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute("UPDATE experiments SET status = 'running' WHERE id = %s", (experiment_id,))

    conn.execute(
        "UPDATE experiments SET status = 'running', started_at = now() WHERE id = %s",
        (experiment_id,),
    )
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute("UPDATE experiments SET status = 'stopped' WHERE id = %s", (experiment_id,))


def insert_event(
    conn: psycopg.Connection, project_id: UUID, event_id: UUID, occurred_at: datetime
) -> int:
    """Insert one event, skipping a duplicate. Returns how many rows were inserted."""
    cursor = conn.execute(
        "INSERT INTO events (project_id, event_id, user_id, event_name, occurred_at)"
        " VALUES (%s, %s, 'user-1', 'purchase', %s) ON CONFLICT DO NOTHING",
        (project_id, event_id, occurred_at),
    )
    return cursor.rowcount


def test_a_retried_event_is_a_duplicate(conn: psycopg.Connection, project_id: UUID) -> None:
    event_id, occurred_at = uuid4(), datetime.now(UTC)

    assert insert_event(conn, project_id, event_id, occurred_at) == 1
    assert insert_event(conn, project_id, event_id, occurred_at) == 0


def test_the_same_event_id_with_another_timestamp_is_not_a_duplicate(
    conn: psycopg.Connection, project_id: UUID
) -> None:
    # The price of partitioning (ADR-008): the primary key must include occurred_at, so
    # idempotent retries rely on the SDK fixing occurred_at once, at track() time.
    event_id, occurred_at = uuid4(), datetime.now(UTC)
    insert_event(conn, project_id, event_id, occurred_at)

    assert insert_event(conn, project_id, event_id, occurred_at + timedelta(seconds=1)) == 1


def test_partitions_are_utc_days_whatever_the_session_time_zone(
    conn: psycopg.Connection, project_id: UUID
) -> None:
    conn.execute("SET TIME ZONE 'America/Chicago'")  # UTC-5 or -6; undone by the rollback
    conn.execute("SELECT ensure_event_partitions(1)")
    # 02:30 UTC today is still yesterday evening in Chicago. The UTC day must decide.
    early_utc = datetime.combine(datetime.now(UTC).date(), time(2, 30), tzinfo=UTC)
    insert_event(conn, project_id, uuid4(), early_utc)

    row = conn.execute("SELECT tableoid::regclass::text FROM events").fetchone()
    assert row == (f"events_{early_utc:%Y%m%d}",)
