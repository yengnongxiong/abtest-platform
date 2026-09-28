"""ensure_event_partitions(): daily UTC partitions of events, and the default-partition trap."""

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import errors


def partitions(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT inhrelid::regclass::text FROM pg_inherits"
        " WHERE inhparent = 'events'::regclass ORDER BY 1"
    ).fetchall()
    return [name for (name,) in rows]


def utc_today() -> date:
    return datetime.now(UTC).date()


def test_creates_a_partition_per_day_from_a_week_ago_to_days_ahead(
    conn: psycopg.Connection,
) -> None:
    created = conn.execute("SELECT ensure_event_partitions(14)").fetchone()

    today = utc_today()
    expected_days = [today + timedelta(days=offset) for offset in range(-7, 15)]
    assert created == (len(expected_days),)
    assert partitions(conn) == sorted(
        ["events_default", *(f"events_{day:%Y%m%d}" for day in expected_days)]
    )


def test_is_idempotent(conn: psycopg.Connection) -> None:
    conn.execute("SELECT ensure_event_partitions(14)")

    assert conn.execute("SELECT ensure_event_partitions(14)").fetchone() == (0,)


def test_partition_bounds_are_utc_midnights(conn: psycopg.Connection) -> None:
    conn.execute("SELECT ensure_event_partitions(0)")
    conn.execute("SET TIME ZONE 'UTC'")  # bounds print in the session time zone
    name = f"events_{utc_today():%Y%m%d}"

    row = conn.execute(
        "SELECT pg_get_expr(relpartbound, oid) FROM pg_class WHERE relname = %s", (name,)
    ).fetchone()

    start = datetime.combine(utc_today(), time(), tzinfo=UTC)
    assert row is not None
    bound = row[0]
    assert f"'{start:%Y-%m-%d %H:%M:%S}+00'" in bound
    assert f"'{start + timedelta(days=1):%Y-%m-%d %H:%M:%S}+00'" in bound


def test_the_default_partition_must_stay_empty(conn: psycopg.Connection, project_id: UUID) -> None:
    # The documented trap: an event for a day with no partition yet goes to the default
    # partition, and from then on Postgres refuses to create that day's partition.
    far_future = datetime.combine(utc_today() + timedelta(days=30), time(12), tzinfo=UTC)
    conn.execute(
        "INSERT INTO events (project_id, event_id, user_id, event_name, occurred_at)"
        " VALUES (%s, %s, 'user-1', 'purchase', %s)",
        (project_id, uuid4(), far_future),
    )

    with pytest.raises(errors.CheckViolation, match="default partition"):
        conn.execute("SELECT ensure_event_partitions(30)")


def test_a_time_bounded_query_skips_older_partitions(conn: psycopg.Connection) -> None:
    # Attribution queries bound occurred_at from the experiment's start (PRD §13), so
    # Postgres only reads the partitions from that day on.
    conn.execute("SELECT ensure_event_partitions(3)")
    since = datetime.combine(utc_today() + timedelta(days=1), time(), tzinfo=UTC)

    plan = conn.execute(
        "EXPLAIN (FORMAT JSON) SELECT count(*) FROM events WHERE occurred_at >= %s", (since,)
    ).fetchone()

    assert plan is not None
    scanned = {
        node["Relation Name"] for node in walk(plan[0][0]["Plan"]) if "Relation Name" in node
    }
    later_days = {f"events_{utc_today() + timedelta(days=offset):%Y%m%d}" for offset in (1, 2, 3)}
    assert scanned == later_days | {"events_default"}


def walk(node: dict[str, object]) -> list[dict[str, object]]:
    """A plan node and all nodes below it."""
    children = node.get("Plans", [])
    assert isinstance(children, list)
    return [node, *(descendant for child in children for descendant in walk(child))]
