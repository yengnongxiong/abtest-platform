"""The worker's jobs against a real database of their own (PRD §15)."""

import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from abtest.db.migrate import migrate
from abtest.logs import JSONFormatter
from abtest.worker.jobs import (
    PARTITIONS_LOCK_ID,
    RESULTS_LOCK_ID,
    compute_results,
    maintain_partitions,
)


@pytest.fixture
def db(create_database: Callable[[], str], migrations_dir: Path) -> str:
    """A fresh migrated database per test: the jobs see (and commit to) everything in it."""
    url = create_database()
    with psycopg.connect(url, autocommit=True) as conn:
        migrate(conn, migrations_dir)
    return url


@pytest.fixture
def conn(db: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(db, autocommit=True) as connection:
        yield connection


def started_experiment(conn: psycopg.Connection, stopped: bool = False) -> UUID:
    """A running (or stopped) 50/50 experiment with one conversion metric and a few users."""
    now = datetime.now(UTC)
    row = conn.execute("INSERT INTO projects (name) VALUES ('Worker test') RETURNING id").fetchone()
    assert row is not None
    project = row[0]
    row = conn.execute(
        "INSERT INTO experiments (project_id, key, name, traffic_bp, status, started_at,"
        " stopped_at, mde_relative) VALUES (%s, 'exp', 'Exp', 10000, %s, %s, %s, 0.1)"
        " RETURNING id",
        (project, "stopped" if stopped else "running", now - timedelta(hours=2),
         now - timedelta(minutes=30) if stopped else None),
    ).fetchone()  # fmt: skip
    assert row is not None
    experiment = row[0]
    variants = {}
    for position, key in enumerate(("control", "treatment")):
        row = conn.execute(
            "INSERT INTO variants (experiment_id, key, name, weight_bp, is_control, position)"
            " VALUES (%s, %s, %s, 5000, %s, %s) RETURNING id",
            (experiment, key, key, key == "control", position),
        ).fetchone()
        assert row is not None
        variants[key] = row[0]
    conn.execute(
        "WITH m AS (INSERT INTO metrics (project_id, key, name, kind, event_name, direction)"
        "  VALUES (%s, 'purchase', 'Purchase', 'conversion', 'purchase', 'increase')"
        "  RETURNING id)"
        " INSERT INTO experiment_metrics (experiment_id, metric_id, role, expected_baseline)"
        " SELECT %s, id, 'primary', 0.1 FROM m",
        (project, experiment),
    )
    for i in range(40):
        conn.execute(
            "INSERT INTO exposures (project_id, experiment_id, variant_id, user_id,"
            " first_exposed_at) VALUES (%s, %s, %s, %s, %s)",
            (project, experiment, variants["control" if i % 2 else "treatment"], f"u{i}",
             now - timedelta(hours=1)),
        )  # fmt: skip
    # A purchase after the stop: the final look must not count it.
    conn.execute(
        "INSERT INTO events (project_id, event_id, user_id, event_name, occurred_at)"
        " VALUES (%s, %s, 'u1', 'purchase', %s)",
        (project, uuid4(), now - timedelta(minutes=10)),
    )
    return UUID(str(experiment))


def snapshots(conn: psycopg.Connection, experiment: UUID) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT data FROM results_snapshots WHERE experiment_id = %s ORDER BY computed_at",
        (experiment,),
    ).fetchall()
    return [data for (data,) in rows]


def conversions(snapshot: dict[str, Any], variant: str) -> int:
    count: int = next(v["conversions"] for v in snapshot["variants"] if v["key"] == variant)
    return count


def test_each_run_adds_a_look_at_every_running_experiment(conn: psycopg.Connection) -> None:
    experiment = started_experiment(conn)

    assert compute_results(conn) == {"experiments": 1, "snapshots": 1, "exposure_rows": 40}
    compute_results(conn)

    looks = snapshots(conn, experiment)
    assert len(looks) == 2
    assert looks[-1]["users"] == 40
    assert conversions(looks[-1], "control") == 1  # u1 (control) bought inside the window


def test_a_stopped_experiment_gets_one_final_look_up_to_the_stop(conn: psycopg.Connection) -> None:
    experiment = started_experiment(conn, stopped=True)

    compute_results(conn)
    compute_results(conn)  # nothing left to do

    [final] = snapshots(conn, experiment)
    assert conversions(final, "control") == 0  # u1 bought after the stop
    stopped_at = conn.execute(
        "SELECT stopped_at FROM experiments WHERE id = %s", (experiment,)
    ).fetchone()
    assert stopped_at is not None
    assert datetime.fromisoformat(final["cutoff"]) == stopped_at[0]


@pytest.mark.parametrize(
    ("job", "lock_id"),
    [(compute_results, RESULTS_LOCK_ID), (maintain_partitions, PARTITIONS_LOCK_ID)],
)
def test_a_job_is_skipped_while_another_worker_runs_it(
    db: str,
    conn: psycopg.Connection,
    job: Callable[[psycopg.Connection], dict[str, Any]],
    lock_id: int,
) -> None:
    with psycopg.connect(db) as other_worker:
        other_worker.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))  # held until rollback

        assert job(conn) == {"skipped": "another worker holds the lock"}


def test_maintain_partitions_creates_what_is_missing(conn: psycopg.Connection) -> None:
    assert maintain_partitions(conn) == {"partitions_created": 22}
    assert maintain_partitions(conn) == {"partitions_created": 0}


def test_logs_are_one_json_object_per_line() -> None:
    record = logging.makeLogRecord(
        {
            "name": "abtest.worker",
            "levelname": "INFO",
            "msg": "job done",
            "job": "x",
            "duration_ms": 12,
        }
    )

    entry = json.loads(JSONFormatter().format(record))

    assert entry | {"time": None} == {
        "time": None,
        "level": "info",
        "logger": "abtest.worker",
        "message": "job done",
        "job": "x",
        "duration_ms": 12,
    }
