"""Results: attribute events to variants and run the stats engine, one look at a time
(PRD §13, §14, §15).

`analyze` is pure: aggregates in, a snapshot out. `compute_snapshots` does the database work
around it, for the worker and for POST /admin/experiments/{key}/recompute.
"""

from dataclasses import asdict
from datetime import datetime
from uuid import UUID

import psycopg

from abtest.db.results import (
    ExperimentToAnalyze,
    MetricToAnalyze,
    VariantAggregate,
    aggregate,
    experiment_to_analyze,
    exposure_counts,
    insert_snapshot,
    latest_snapshot,
    lock_experiment,
)
from abtest.errors import Conflict
from abtest.models import (
    Comparison,
    MSPRTStateOut,
    SnapshotData,
    SrmOut,
    VariantSummary,
)
from abtest.stats import (
    MSPRT,
    ComparisonResult,
    MeanSummary,
    MSPRTState,
    ProportionSummary,
    TwoProportionZTest,
    WelchTTest,
    srm_check,
    verdict,
)

type Summary = ProportionSummary | MeanSummary


def analyze(
    experiment: ExperimentToAnalyze,
    metric: MetricToAnalyze,
    aggregates: dict[UUID, VariantAggregate],
    conflicted_users: int,
    mismatched_users: int,
    previous: SnapshotData | None,
    cutoff: datetime,
) -> SnapshotData:
    """One look: per-variant summaries, the SRM check, and each treatment vs the control."""
    empty = VariantAggregate(users=0, converters=0, total=0.0, total_sq=0.0)
    variants = sorted(experiment.variants, key=lambda v: v.position)
    rows = {v.key: aggregates.get(v.id, empty) for v in variants}
    srm = srm_check([rows[v.key].users for v in variants], [v.weight_bp for v in variants])

    control = next(v for v in variants if v.is_control)
    tau = _tau(experiment, metric) if experiment.analysis_type == "sequential" else None
    previous_states = {
        c.variant_key: c.msprt_state for c in (previous.comparisons if previous else [])
    }
    comparisons = []
    for variant in variants:
        if variant.is_control:
            continue
        result: ComparisonResult
        state = None
        if tau is not None:
            earlier = previous_states.get(variant.key)
            sequential = MSPRT(tau).compare(
                _summary(metric, rows[control.key]),
                _summary(metric, rows[variant.key]),
                experiment.alpha,
                previous=MSPRTState(**earlier.model_dump()) if earlier else None,
            )
            result = sequential
            if sequential.state is not None:
                state = MSPRTStateOut(**asdict(sequential.state))
        else:
            result = _fixed_horizon(metric, rows[control.key], rows[variant.key], experiment.alpha)
        comparisons.append(
            Comparison(
                variant_key=variant.key,
                abs_diff=result.abs_diff,
                rel_lift=result.rel_lift,
                ci_low=result.ci_low,
                ci_high=result.ci_high,
                rel_ci_low=result.rel_ci_low,
                rel_ci_high=result.rel_ci_high,
                p_value=result.p_value,
                significant=result.significant,
                insufficient_data=result.insufficient_data,
                verdict=verdict(result, metric.direction, srm.flagged),
                msprt_state=state,
            )
        )

    return SnapshotData(
        cutoff=cutoff,
        analysis_type="sequential" if tau is not None else "fixed_horizon",
        alpha=experiment.alpha,
        tau=tau,
        metric_key=metric.key,
        metric_kind=metric.kind,
        direction=metric.direction,
        window_hours=metric.window_hours,
        users=sum(row.users for row in rows.values()),
        conflicted_users=conflicted_users,
        mismatched_users=mismatched_users,
        srm=SrmOut(
            p_value=srm.p_value, flagged=srm.flagged, insufficient_data=srm.insufficient_data
        ),
        variants=[
            VariantSummary(
                key=v.key,
                is_control=v.is_control,
                weight_bp=v.weight_bp,
                users=rows[v.key].users,
                conversions=rows[v.key].converters if metric.kind == "conversion" else None,
                total=rows[v.key].total,
                total_sq=rows[v.key].total_sq,
            )
            for v in variants
        ],
        comparisons=comparisons,
    )


def compute_snapshots(conn: psycopg.Connection, experiment: ExperimentToAnalyze) -> int:
    """Add one look (a snapshot per attached metric) for a started experiment.

    Runs inside the caller's transaction. The experiment's results lock makes concurrent
    looks (the worker and a recompute) take turns, so each continues from the one before.
    Returns the number of exposure rows read, for the worker's logs.
    """
    now = lock_experiment(conn, experiment.id)
    # A stopped experiment's final look counts events up to the stop, never after.
    cutoff = experiment.stopped_at if experiment.stopped_at is not None else now
    conflicted, mismatched = exposure_counts(conn, experiment.id)
    users = 0
    for metric in experiment.metrics:
        data = analyze(
            experiment,
            metric,
            aggregate(conn, experiment, metric, cutoff),
            conflicted,
            mismatched,
            latest_snapshot(conn, experiment.id, metric.id),
            cutoff,
        )
        insert_snapshot(conn, experiment.id, metric.id, now, data)
        users = data.users
    return users + conflicted


def recompute(conn: psycopg.Connection, project_id: UUID, key: str) -> list[str]:
    """POST .../recompute: one new look now. Earlier snapshots are never rewritten; under
    mSPRT an extra look is always safe."""
    with conn.transaction():
        experiment = experiment_to_analyze(conn, project_id, key)
        if experiment is None:
            raise Conflict(
                "not_started", f"no started experiment with key {key!r}: nothing to analyze"
            )
        compute_snapshots(conn, experiment)
    return [metric.key for metric in experiment.metrics]


def _fixed_horizon(
    metric: MetricToAnalyze, control: VariantAggregate, treatment: VariantAggregate, alpha: float
) -> ComparisonResult:
    """The z-test for a conversion metric, Welch's t-test for a mean metric."""
    if metric.kind == "conversion":
        return TwoProportionZTest().compare(
            ProportionSummary(n=control.users, successes=control.converters),
            ProportionSummary(n=treatment.users, successes=treatment.converters),
            alpha,
        )
    return WelchTTest().compare(
        MeanSummary(n=control.users, sum=control.total, sum_sq=control.total_sq),
        MeanSummary(n=treatment.users, sum=treatment.total, sum_sq=treatment.total_sq),
        alpha,
    )


def _summary(metric: MetricToAnalyze, row: VariantAggregate) -> Summary:
    if metric.kind == "conversion":
        return ProportionSummary(n=row.users, successes=row.converters)
    return MeanSummary(n=row.users, sum=row.total, sum_sq=row.total_sq)


def _tau(experiment: ExperimentToAnalyze, metric: MetricToAnalyze) -> float:
    """The metric's mSPRT mixing standard deviation: expected baseline x MDE (PRD §14).
    Both are required before an experiment can start."""
    assert metric.expected_baseline is not None and experiment.mde_relative is not None
    return metric.expected_baseline * experiment.mde_relative
