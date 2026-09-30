"""Attribution queries and results snapshots (PRD §13, §15).

Attribution decides which events count for whom: `AGGREGATE` reduces an experiment's users to
per-variant totals in one SQL pass, so the stats engine never sees raw rows (ADR-004, ADR-016).
Each result is stored as a snapshot, and the stored series is the mSPRT's history of looks
(ADR-018).
"""

from datetime import datetime, timedelta
from uuid import UUID

import psycopg
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from abtest.models import Direction, MetricKind, Role, SeriesPoint, Snapshot, SnapshotData


class MetricToAnalyze(BaseModel):
    id: UUID
    key: str
    kind: MetricKind
    direction: Direction
    event_name: str
    window_hours: int
    expected_baseline: float | None
    role: Role


class VariantToAnalyze(BaseModel):
    id: UUID
    key: str
    weight_bp: int
    is_control: bool
    position: int


class ExperimentToAnalyze(BaseModel):
    id: UUID
    project_id: UUID
    key: str
    status: str
    started_at: datetime
    stopped_at: datetime | None
    analysis_type: str
    alpha: float
    mde_relative: float | None
    metrics: list[MetricToAnalyze]
    variants: list[VariantToAnalyze]


class VariantAggregate(BaseModel):
    users: int
    converters: int
    total: float
    total_sq: float


# Completed by appending a literal WHERE clause (never values).
SELECT_EXPERIMENTS = """
SELECT e.id, e.project_id, e.key, e.status, e.started_at, e.stopped_at, e.analysis_type,
       e.alpha, e.mde_relative,
       coalesce((
           SELECT json_agg(json_build_object(
                      'id', m.id, 'key', m.key, 'kind', m.kind, 'direction', m.direction,
                      'event_name', m.event_name, 'window_hours', m.window_hours,
                      'expected_baseline', em.expected_baseline, 'role', em.role)
                  ORDER BY m.key)
           FROM experiment_metrics em JOIN metrics m ON m.id = em.metric_id
           WHERE em.experiment_id = e.id
       ), '[]') AS metrics,
       coalesce((
           SELECT json_agg(json_build_object(
                      'id', v.id, 'key', v.key, 'weight_bp', v.weight_bp,
                      'is_control', v.is_control, 'position', v.position)
                  ORDER BY v.position)
           FROM variants v WHERE v.experiment_id = e.id
       ), '[]') AS variants
FROM experiments e
"""

# One row per variant: the metric aggregated over the experiment's non-conflicted users.
# Each user's events count only inside [first exposure, first exposure + window), and never
# after the cutoff (now, or when the experiment stopped).
# - The constant bounds (started_at, and the cutoff on its own) are implied by the others,
#   but stating them lets Postgres skip every partition outside them.
# - Every events column read here is in the events_attribution index, so Postgres can read
#   the index alone (an index-only scan). That's why it counts occurred_at, not event_id:
#   either is NULL when the LEFT JOIN finds no event.
AGGREGATE = """
WITH per_user AS (
    SELECT x.variant_id,
           count(ev.occurred_at) AS events,
           coalesce(sum(ev.value), 0) AS total
    FROM exposures x
    LEFT JOIN events ev
        ON ev.project_id = %(project_id)s
       AND ev.event_name = %(event_name)s
       AND ev.user_id = x.user_id
       AND ev.occurred_at >= %(started_at)s
       AND ev.occurred_at < %(cutoff)s
       AND ev.occurred_at >= x.first_exposed_at
       AND ev.occurred_at < least(x.first_exposed_at + %(window)s, %(cutoff)s)
    WHERE x.experiment_id = %(experiment_id)s AND NOT x.conflicted
    GROUP BY x.variant_id, x.user_id
)
SELECT variant_id,
       count(*) AS users,
       count(*) FILTER (WHERE events > 0) AS converters,
       sum(total) AS total,
       sum(total * total) AS total_sq
FROM per_user
GROUP BY variant_id
"""

# Postgres picks AGGREGATE's join from its statistics, and a table that has just received its
# first rows (a new database, or each day's new partition) has none until autovacuum analyzes
# it, up to a minute later. Postgres then guessed a handful of events and chose a nested loop
# that compared every exposure with every event, so the time grew with their product. The
# query always aggregates a whole population, where a hash join is the right plan; with
# statistics, Postgres picks one anyway (ADR-023). Local to the transaction.
NO_NESTED_LOOP = "SET LOCAL enable_nestloop = off"


def experiments_due(conn: psycopg.Connection) -> list[ExperimentToAnalyze]:
    """Every running experiment, and every stopped one still missing its final snapshot
    (none computed at or after it stopped)."""
    with conn.cursor(row_factory=class_row(ExperimentToAnalyze)) as cur:
        return cur.execute(
            SELECT_EXPERIMENTS
            + " WHERE e.status = 'running' OR (e.status = 'stopped' AND NOT EXISTS ("
            " SELECT 1 FROM results_snapshots s"
            " WHERE s.experiment_id = e.id AND s.computed_at >= e.stopped_at))"
            " ORDER BY e.started_at"
        ).fetchall()


def experiment_to_analyze(
    conn: psycopg.Connection, project_id: UUID, key: str
) -> ExperimentToAnalyze | None:
    """A started experiment, by key; None for a draft or an unknown key."""
    with conn.cursor(row_factory=class_row(ExperimentToAnalyze)) as cur:
        return cur.execute(
            SELECT_EXPERIMENTS + " WHERE e.project_id = %s AND e.key = %s AND e.status <> 'draft'",
            (project_id, key),
        ).fetchone()


def lock_experiment(conn: psycopg.Connection, experiment_id: UUID) -> datetime:
    """Take the experiment's results lock until the transaction ends, and return the time.

    The worker and POST .../recompute both add looks. Under mSPRT each look continues from
    the previous one's state, so two looks computed at once from the same state would lose
    one of them. The lock makes looks take turns. The time is read after the lock is held,
    so looks are timestamped in the order they were taken.
    """
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('results:' || %s::text))", (experiment_id,))
    row = conn.execute("SELECT clock_timestamp()").fetchone()
    assert row is not None
    now: datetime = row[0]
    return now


def aggregate_params(
    experiment: ExperimentToAnalyze, metric: MetricToAnalyze, cutoff: datetime
) -> dict[str, object]:
    """AGGREGATE's parameters. Separate, so loadtest/explain_attribution.py can EXPLAIN
    exactly the query the worker runs."""
    return {
        "project_id": experiment.project_id,
        "experiment_id": experiment.id,
        "event_name": metric.event_name,
        "started_at": experiment.started_at,
        "window": timedelta(hours=metric.window_hours),
        "cutoff": cutoff,
    }


def aggregate(
    conn: psycopg.Connection,
    experiment: ExperimentToAnalyze,
    metric: MetricToAnalyze,
    cutoff: datetime,
) -> dict[UUID, VariantAggregate]:
    """Run AGGREGATE. Call it inside a transaction, which NO_NESTED_LOOP's setting lasts for."""
    conn.execute(NO_NESTED_LOOP)
    rows = conn.execute(AGGREGATE, aggregate_params(experiment, metric, cutoff)).fetchall()
    return {
        variant_id: VariantAggregate(
            users=users, converters=converters, total=total, total_sq=total_sq
        )
        for variant_id, users, converters, total, total_sq in rows
    }


def exposure_counts(conn: psycopg.Connection, experiment_id: UUID) -> tuple[int, int]:
    """(conflicted users, users with an assignment mismatch), shown on the dashboard."""
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE conflicted), count(*) FILTER (WHERE assignment_mismatch)"
        " FROM exposures WHERE experiment_id = %s",
        (experiment_id,),
    ).fetchone()
    assert row is not None
    return row[0], row[1]


def latest_snapshot(
    conn: psycopg.Connection, experiment_id: UUID, metric_id: UUID
) -> SnapshotData | None:
    row = conn.execute(
        "SELECT data FROM results_snapshots WHERE experiment_id = %s AND metric_id = %s"
        " ORDER BY computed_at DESC, id DESC LIMIT 1",
        (experiment_id, metric_id),
    ).fetchone()
    return None if row is None else SnapshotData.model_validate(row[0])


def insert_snapshot(
    conn: psycopg.Connection,
    experiment_id: UUID,
    metric_id: UUID,
    computed_at: datetime,
    data: SnapshotData,
) -> None:
    conn.execute(
        "INSERT INTO results_snapshots"
        " (experiment_id, metric_id, computed_at, total_users, srm_p_value, srm_flag, data)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (experiment_id, metric_id, computed_at, data.users, data.srm.p_value, data.srm.flagged,
         Jsonb(data.model_dump(mode="json"))),
    )  # fmt: skip


def snapshot_history(
    conn: psycopg.Connection, experiment_id: UUID, metric_id: UUID
) -> tuple[Snapshot | None, list[SeriesPoint]]:
    """The latest snapshot in full, and every snapshot compactly, oldest first.

    The series reads only the columns and the part of `data` the chart needs, not every
    look's whole `data`: four weeks of looks every 5 minutes are 8,064 rows
    (docs/performance.md measures it).
    """
    latest_row = conn.execute(
        "SELECT id, computed_at, data FROM results_snapshots"
        " WHERE experiment_id = %s AND metric_id = %s ORDER BY computed_at DESC, id DESC LIMIT 1",
        (experiment_id, metric_id),
    ).fetchone()
    if latest_row is None:
        return None, []
    latest_id, latest_at, data = latest_row
    latest = Snapshot(computed_at=latest_at, data=SnapshotData.model_validate(data))
    # Ends at that same snapshot, even if the worker adds a look between the two queries.
    rows = conn.execute(
        "SELECT computed_at, total_users, srm_flag, data->'comparisons' FROM results_snapshots"
        " WHERE experiment_id = %s AND metric_id = %s AND (computed_at, id) <= (%s, %s)"
        " ORDER BY computed_at, id",
        (experiment_id, metric_id, latest_at, latest_id),
    ).fetchall()
    series = [
        SeriesPoint(
            computed_at=computed_at, users=users, srm_flagged=srm_flagged, comparisons=comparisons
        )
        for computed_at, users, srm_flagged, comparisons in rows
    ]
    return latest, series
