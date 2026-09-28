"""Admin: metrics."""

from fastapi import APIRouter

from abtest.api.deps import ConnDep, ServerProject
from abtest.db import metrics
from abtest.models import Metric, MetricCreate, MetricUpdate

router = APIRouter(prefix="/admin/metrics", tags=["metrics"])


@router.get("")
def list_metrics(conn: ConnDep, project: ServerProject) -> list[Metric]:
    return metrics.list_metrics(conn, project)


@router.post("", status_code=201)
def create_metric(data: MetricCreate, conn: ConnDep, project: ServerProject) -> Metric:
    return metrics.create_metric(conn, project, data)


@router.patch("/{key}")
def update_metric(key: str, data: MetricUpdate, conn: ConnDep, project: ServerProject) -> Metric:
    return metrics.update_metric(conn, project, key, data)
