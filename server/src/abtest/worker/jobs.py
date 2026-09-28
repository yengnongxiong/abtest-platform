"""The worker's scheduled jobs (PRD §15).

Each job runs in one transaction that first tries a transaction-scoped advisory lock. If
another worker holds it, the job is skipped: two workers never duplicate work. The lock is
released when the transaction ends, so it can't leak, even on a pooled connection.
"""

from typing import Any

import psycopg

from abtest.db.results import experiments_due
from abtest.results import compute_snapshots

RESULTS_LOCK_ID = 7_300_101
PARTITIONS_LOCK_ID = 7_300_102
PARTITION_DAYS_AHEAD = 14


def compute_results(conn: psycopg.Connection) -> dict[str, Any]:
    """A snapshot for every metric of every running experiment, plus a final one for each
    stopped experiment that doesn't have one yet."""
    with conn.transaction():
        if not _try_lock(conn, RESULTS_LOCK_ID):
            return {"skipped": "another worker holds the lock"}
        experiments = experiments_due(conn)
        exposure_rows = sum(compute_snapshots(conn, experiment) for experiment in experiments)
    return {
        "experiments": len(experiments),
        "snapshots": sum(len(e.metrics) for e in experiments),
        "exposure_rows": exposure_rows,  # rows scanned: each experiment's population
    }


def maintain_partitions(conn: psycopg.Connection) -> dict[str, Any]:
    with conn.transaction():
        if not _try_lock(conn, PARTITIONS_LOCK_ID):
            return {"skipped": "another worker holds the lock"}
        row = conn.execute("SELECT ensure_event_partitions(%s)", (PARTITION_DAYS_AHEAD,)).fetchone()
    assert row is not None
    return {"partitions_created": row[0]}


def _try_lock(conn: psycopg.Connection, lock_id: int) -> bool:
    row = conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (lock_id,)).fetchone()
    return bool(row and row[0])
