"""Experiments and their lifecycle (PRD §9, §11).

- A draft can change freely.
- Starting freezes the variants and weights. After that, traffic can only go up (lowering
  it would drop users who were already assigned), and only the name can change.
- Stopping is final; clone the experiment to run it again.

Every write locks the experiment's row first, so concurrent requests (two starts, a stop
racing a traffic change) are applied one after the other, and every write bumps the
project's config version in the same transaction.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg import errors
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from abtest.db.projects import bump_config_version
from abtest.db.updates import set_columns
from abtest.errors import Conflict, NotFound, Unprocessable
from abtest.models import (
    Experiment,
    ExperimentCreate,
    ExperimentDetail,
    ExperimentMetricIn,
    ExperimentUpdate,
    VariantIn,
)

# One query per request, however many experiments: variants and metrics are aggregated
# into JSON arrays in the same statement instead of a query per experiment. The statement
# is completed by appending a literal WHERE clause (never values).
SELECT_EXPERIMENTS = """
SELECT e.key, e.name, e.hypothesis, e.status, e.traffic_bp, e.analysis_type, e.alpha,
       e.mde_relative, e.started_at, e.stopped_at, e.stop_reason, e.created_at, e.updated_at,
       coalesce((
           SELECT json_agg(json_build_object(
                      'key', v.key, 'name', v.name, 'weight_bp', v.weight_bp,
                      'is_control', v.is_control, 'position', v.position)
                  ORDER BY v.position)
           FROM variants v WHERE v.experiment_id = e.id
       ), '[]') AS variants,
       coalesce((
           SELECT json_agg(json_build_object(
                      'metric_key', m.key, 'kind', m.kind, 'role', em.role,
                      'expected_baseline', em.expected_baseline)
                  ORDER BY array_position(ARRAY['primary', 'secondary', 'guardrail'], em.role),
                           m.key)
           FROM experiment_metrics em JOIN metrics m ON m.id = em.metric_id
           WHERE em.experiment_id = e.id
       ), '[]') AS metrics,
       (
           -- The newest snapshot of the primary metric, for the dashboard's list
           -- (served by results_snapshots_latest).
           SELECT json_build_object(
                      'computed_at', s.computed_at, 'users', s.total_users,
                      'srm_flagged', s.srm_flag)
           FROM experiment_metrics em
           JOIN results_snapshots s ON s.experiment_id = e.id AND s.metric_id = em.metric_id
           WHERE em.experiment_id = e.id AND em.role = 'primary'
           ORDER BY s.computed_at DESC, s.id DESC
           LIMIT 1
       ) AS latest_results
"""

CHANGES = """,
       coalesce((
           SELECT json_agg(json_build_object(
                      'action', c.action, 'details', c.details, 'created_at', c.created_at)
                  ORDER BY c.id)
           FROM experiment_changes c WHERE c.experiment_id = e.id
       ), '[]') AS changes
"""


@dataclass(frozen=True, slots=True)
class Locked:
    """An experiment's row, locked until the end of the transaction."""

    id: UUID
    status: str
    traffic_bp: int


def list_experiments(conn: psycopg.Connection, project_id: UUID) -> list[Experiment]:
    with conn.cursor(row_factory=class_row(Experiment)) as cur:
        return cur.execute(
            SELECT_EXPERIMENTS
            + " FROM experiments e WHERE e.project_id = %s ORDER BY e.created_at DESC",
            (project_id,),
        ).fetchall()


def get_experiment(conn: psycopg.Connection, project_id: UUID, key: str) -> ExperimentDetail:
    with conn.cursor(row_factory=class_row(ExperimentDetail)) as cur:
        experiment = cur.execute(
            SELECT_EXPERIMENTS
            + CHANGES
            + " FROM experiments e WHERE e.project_id = %s AND e.key = %s",
            (project_id, key),
        ).fetchone()
    if experiment is None:
        raise NotFound("experiment_not_found", f"no experiment with key {key!r}")
    return experiment


def create_experiment(
    conn: psycopg.Connection, project_id: UUID, data: ExperimentCreate
) -> ExperimentDetail:
    with conn.transaction():
        experiment_id = _insert(conn, project_id, data)
        _log(conn, experiment_id, "created", {})
        bump_config_version(conn, project_id)
    return get_experiment(conn, project_id, data.key)


def update_experiment(
    conn: psycopg.Connection, project_id: UUID, key: str, data: ExperimentUpdate
) -> ExperimentDetail:
    changes = data.model_dump(exclude_none=True, exclude={"variants", "metrics"})
    replaced = {name for name in ("variants", "metrics") if getattr(data, name) is not None}
    with conn.transaction():
        current = _lock(conn, project_id, key)
        if current.status != "draft":
            allowed = {"name", "traffic_bp"} if current.status == "running" else {"name"}
            refused = (changes.keys() | replaced) - allowed
            if refused:
                raise Conflict(
                    "experiment_locked",
                    f"a {current.status} experiment can only change: {', '.join(sorted(allowed))}",
                    {"fields": sorted(refused)},
                )
            if changes.get("traffic_bp", current.traffic_bp) < current.traffic_bp:
                raise Conflict(
                    "traffic_decrease",
                    "traffic can only go up while an experiment runs: lowering it would drop "
                    "users who were already assigned",
                )
        if changes or replaced:
            set_columns(conn, "experiments", current.id, changes, touch=True)
        if data.variants is not None:
            _replace_variants(conn, current.id, data.variants)
        if data.metrics is not None:
            _replace_metrics(conn, project_id, current.id, data.metrics)
        new_traffic = changes.get("traffic_bp", current.traffic_bp)
        if current.status == "running" and new_traffic != current.traffic_bp:
            _log(
                conn, current.id, "traffic_changed", {"from": current.traffic_bp, "to": new_traffic}
            )
        if changes or replaced:
            bump_config_version(conn, project_id)
    return get_experiment(conn, project_id, key)


def start_experiment(conn: psycopg.Connection, project_id: UUID, key: str) -> ExperimentDetail:
    with conn.transaction():
        current = _lock(conn, project_id, key)
        if current.status != "draft":
            raise Conflict("not_a_draft", f"only a draft can start; this one is {current.status}")
        # Share-lock the attached metrics: their definitions can't change while this
        # transaction decides the experiment is ready (see metrics.update_metric).
        conn.execute(
            "SELECT 1 FROM metrics WHERE id IN"
            " (SELECT metric_id FROM experiment_metrics WHERE experiment_id = %s) FOR SHARE",
            (current.id,),
        )
        problems = start_problems(get_experiment(conn, project_id, key))
        if problems:
            raise Unprocessable(
                "not_ready", "the experiment isn't ready to start", {"problems": problems}
            )
        conn.execute(
            "UPDATE experiments SET status = 'running', started_at = now(), updated_at = now()"
            " WHERE id = %s",
            (current.id,),
        )
        _log(conn, current.id, "started", {})
        bump_config_version(conn, project_id)
    return get_experiment(conn, project_id, key)


def stop_experiment(
    conn: psycopg.Connection, project_id: UUID, key: str, reason: str
) -> ExperimentDetail:
    with conn.transaction():
        current = _lock(conn, project_id, key)
        if current.status != "running":
            raise Conflict(
                "not_running", f"only a running experiment can stop; this one is {current.status}"
            )
        conn.execute(
            "UPDATE experiments SET status = 'stopped', stopped_at = now(), stop_reason = %s,"
            " updated_at = now() WHERE id = %s",
            (reason, current.id),
        )
        _log(conn, current.id, "stopped", {"reason": reason})
        bump_config_version(conn, project_id)
    return get_experiment(conn, project_id, key)


def clone_experiment(
    conn: psycopg.Connection, project_id: UUID, key: str, new_key: str
) -> ExperimentDetail:
    """A new draft with the same design (to rerun a stopped experiment, for example)."""
    with conn.transaction():
        source = get_experiment(conn, project_id, key)
        copy = ExperimentCreate(
            key=new_key,
            name=source.name,
            hypothesis=source.hypothesis,
            traffic_bp=source.traffic_bp,
            analysis_type=source.analysis_type,
            alpha=source.alpha,
            mde_relative=source.mde_relative,
            variants=[
                VariantIn(key=v.key, name=v.name, weight_bp=v.weight_bp, is_control=v.is_control)
                for v in source.variants
            ],
            metrics=[
                ExperimentMetricIn(
                    metric_key=m.metric_key, role=m.role, expected_baseline=m.expected_baseline
                )
                for m in source.metrics
            ],
        )
        experiment_id = _insert(conn, project_id, copy)
        _log(conn, experiment_id, "cloned", {"from": key})
        bump_config_version(conn, project_id)
    return get_experiment(conn, project_id, new_key)


def start_problems(experiment: Experiment) -> list[str]:
    """Everything that stops an experiment from starting (PRD §11), all at once, so the PM
    can fix them in one go."""
    problems = []
    if not experiment.hypothesis.strip():
        problems.append("write a hypothesis")
    variants = experiment.variants
    if len(variants) < 2:
        problems.append("add at least 2 variants")
    if sum(v.is_control for v in variants) != 1:
        problems.append("mark exactly one variant as the control")
    total = sum(v.weight_bp for v in variants)
    if total != 10_000:
        problems.append(f"variant weights must add up to 10000 basis points, not {total}")
    if sum(m.role == "primary" for m in experiment.metrics) != 1:
        problems.append("attach exactly one primary metric")
    if experiment.mde_relative is None:
        problems.append("set the minimum detectable effect (mde_relative)")
    for metric in experiment.metrics:
        if metric.expected_baseline is None:
            problems.append(f"set an expected baseline for metric {metric.metric_key!r}")
        elif metric.kind == "conversion" and metric.expected_baseline >= 1:
            problems.append(
                f"metric {metric.metric_key!r} is a conversion rate, so its expected baseline "
                "must be below 1"
            )
    return problems


def _insert(conn: psycopg.Connection, project_id: UUID, data: ExperimentCreate) -> UUID:
    try:
        row = conn.execute(
            "INSERT INTO experiments (project_id, key, name, hypothesis, traffic_bp,"
            " analysis_type, alpha, mde_relative) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING id",
            (project_id, data.key, data.name, data.hypothesis, data.traffic_bp,
             data.analysis_type, data.alpha, data.mde_relative),
        ).fetchone()  # fmt: skip
    except errors.UniqueViolation:
        raise Conflict(
            "experiment_exists", f"an experiment with key {data.key!r} already exists"
        ) from None
    assert row is not None  # INSERT ... RETURNING returns the row
    experiment_id: UUID = row[0]
    _replace_variants(conn, experiment_id, data.variants)
    _replace_metrics(conn, project_id, experiment_id, data.metrics)
    return experiment_id


def _lock(conn: psycopg.Connection, project_id: UUID, key: str) -> Locked:
    row = conn.execute(
        "SELECT id, status, traffic_bp FROM experiments"
        " WHERE project_id = %s AND key = %s FOR UPDATE",
        (project_id, key),
    ).fetchone()
    if row is None:
        raise NotFound("experiment_not_found", f"no experiment with key {key!r}")
    return Locked(*row)


def _replace_variants(
    conn: psycopg.Connection, experiment_id: UUID, variants: list[VariantIn]
) -> None:
    """Variants in list order become positions 0, 1, 2, ..."""
    conn.execute("DELETE FROM variants WHERE experiment_id = %s", (experiment_id,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO variants (experiment_id, key, name, weight_bp, is_control, position)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (experiment_id, v.key, v.name, v.weight_bp, v.is_control, position)
                for position, v in enumerate(variants)
            ],
        )


def _replace_metrics(
    conn: psycopg.Connection,
    project_id: UUID,
    experiment_id: UUID,
    metrics: list[ExperimentMetricIn],
) -> None:
    keys = [m.metric_key for m in metrics]
    ids: dict[str, UUID] = dict(
        conn.execute(
            "SELECT key, id FROM metrics WHERE project_id = %s AND key = ANY(%s)",
            (project_id, keys),
        ).fetchall()
    )
    unknown = [key for key in keys if key not in ids]
    if unknown:
        raise Unprocessable("unknown_metric", "no such metric", {"metric_keys": unknown})
    conn.execute("DELETE FROM experiment_metrics WHERE experiment_id = %s", (experiment_id,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO experiment_metrics (experiment_id, metric_id, role, expected_baseline)"
            " VALUES (%s, %s, %s, %s)",
            [(experiment_id, ids[m.metric_key], m.role, m.expected_baseline) for m in metrics],
        )


def _log(
    conn: psycopg.Connection, experiment_id: UUID, action: str, details: dict[str, Any]
) -> None:
    """Add an entry to the experiment's changelog, shown on the dashboard."""
    conn.execute(
        "INSERT INTO experiment_changes (experiment_id, action, details) VALUES (%s, %s, %s)",
        (experiment_id, action, Jsonb(details)),
    )
