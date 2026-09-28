"""Admin: an experiment's results, and computing a fresh look on demand (PRD §11)."""

from typing import Annotated

from fastapi import APIRouter, Query

from abtest.api.deps import ConnDep, ServerProject
from abtest.db.results import experiment_to_analyze, snapshot_history
from abtest.errors import NotFound
from abtest.models import Recomputed, Results
from abtest.results import recompute

router = APIRouter(prefix="/admin/experiments", tags=["results"])


@router.get("/{key}/results")
def get_results(
    key: str,
    conn: ConnDep,
    project: ServerProject,
    metric: Annotated[str | None, Query(description="A metric key; the primary if omitted")] = None,
) -> Results:
    """The latest snapshot for one metric, and the time series of every snapshot."""
    experiment = experiment_to_analyze(conn, project, key)
    if experiment is None:
        raise NotFound("experiment_not_found", f"no started experiment with key {key!r}")
    wanted = metric or next(m.key for m in experiment.metrics if m.role == "primary")
    chosen = next((m for m in experiment.metrics if m.key == wanted), None)
    if chosen is None:
        raise NotFound("metric_not_found", f"metric {wanted!r} isn't attached to {key!r}")
    latest, series = snapshot_history(conn, experiment.id, chosen.id)
    return Results(experiment_key=key, metric_key=chosen.key, latest=latest, series=series)


@router.post("/{key}/recompute")
def post_recompute(key: str, conn: ConnDep, project: ServerProject) -> Recomputed:
    """Write one new snapshot per metric now. Earlier snapshots are never rewritten."""
    return Recomputed(metric_keys=recompute(conn, project, key))
