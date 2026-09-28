"""Metrics: what an experiment measures (PRD §11). They aren't part of the SDK config."""

from uuid import UUID

import psycopg
from psycopg import errors
from psycopg.rows import class_row

from abtest.db.updates import set_columns
from abtest.errors import Conflict, NotFound
from abtest.models import Metric, MetricCreate, MetricUpdate

DEFINITION_FIELDS = {"kind", "event_name", "direction", "window_hours"}


def list_metrics(conn: psycopg.Connection, project_id: UUID) -> list[Metric]:
    with conn.cursor(row_factory=class_row(Metric)) as cur:
        return cur.execute(
            "SELECT key, name, kind, event_name, direction, window_hours, created_at"
            " FROM metrics WHERE project_id = %s ORDER BY key",
            (project_id,),
        ).fetchall()


def create_metric(conn: psycopg.Connection, project_id: UUID, data: MetricCreate) -> Metric:
    try:
        with conn.cursor(row_factory=class_row(Metric)) as cur:
            metric = cur.execute(
                "INSERT INTO metrics"
                " (project_id, key, name, kind, event_name, direction, window_hours)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " RETURNING key, name, kind, event_name, direction, window_hours, created_at",
                (project_id, data.key, data.name, data.kind, data.event_name, data.direction,
                 data.window_hours),
            ).fetchone()  # fmt: skip
    except errors.UniqueViolation:
        raise Conflict("metric_exists", f"a metric with key {data.key!r} already exists") from None
    assert metric is not None  # INSERT ... RETURNING returns the row
    return metric


def update_metric(
    conn: psycopg.Connection, project_id: UUID, key: str, data: MetricUpdate
) -> Metric:
    """Rename a metric at any time; change its definition only while no started experiment
    uses it, because that would silently rewrite results already shown."""
    changes = data.model_dump(exclude_none=True)
    with conn.transaction():
        row = conn.execute(
            "SELECT id FROM metrics WHERE project_id = %s AND key = %s FOR UPDATE",
            (project_id, key),
        ).fetchone()
        if row is None:
            raise NotFound("metric_not_found", f"no metric with key {key!r}")
        metric_id = row[0]
        if DEFINITION_FIELDS & changes.keys() and _used_by_started_experiment(conn, metric_id):
            raise Conflict(
                "metric_in_use",
                "a started experiment uses this metric, so only its name can change",
            )
        set_columns(conn, "metrics", metric_id, changes)
        with conn.cursor(row_factory=class_row(Metric)) as cur:
            metric = cur.execute(
                "SELECT key, name, kind, event_name, direction, window_hours, created_at"
                " FROM metrics WHERE id = %s",
                (metric_id,),
            ).fetchone()
    assert metric is not None
    return metric


def _used_by_started_experiment(conn: psycopg.Connection, metric_id: UUID) -> bool:
    row = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM experiment_metrics em"
        " JOIN experiments e ON e.id = em.experiment_id"
        " WHERE em.metric_id = %s AND e.status <> 'draft')",
        (metric_id,),
    ).fetchone()
    return bool(row and row[0])
