"""Admin: experiments and their lifecycle."""

from fastapi import APIRouter

from abtest.api.deps import ConnDep, ServerProject
from abtest.db import experiments
from abtest.models import (
    CloneRequest,
    Experiment,
    ExperimentCreate,
    ExperimentDetail,
    ExperimentUpdate,
    StopRequest,
)

router = APIRouter(prefix="/admin/experiments", tags=["experiments"])


@router.get("")
def list_experiments(conn: ConnDep, project: ServerProject) -> list[Experiment]:
    return experiments.list_experiments(conn, project)


@router.post("", status_code=201)
def create_experiment(
    data: ExperimentCreate, conn: ConnDep, project: ServerProject
) -> ExperimentDetail:
    return experiments.create_experiment(conn, project, data)


@router.get("/{key}")
def get_experiment(key: str, conn: ConnDep, project: ServerProject) -> ExperimentDetail:
    return experiments.get_experiment(conn, project, key)


@router.patch("/{key}")
def update_experiment(
    key: str, data: ExperimentUpdate, conn: ConnDep, project: ServerProject
) -> ExperimentDetail:
    return experiments.update_experiment(conn, project, key, data)


@router.post("/{key}/start")
def start_experiment(key: str, conn: ConnDep, project: ServerProject) -> ExperimentDetail:
    return experiments.start_experiment(conn, project, key)


@router.post("/{key}/stop")
def stop_experiment(
    key: str, data: StopRequest, conn: ConnDep, project: ServerProject
) -> ExperimentDetail:
    return experiments.stop_experiment(conn, project, key, data.reason)


@router.post("/{key}/clone", status_code=201)
def clone_experiment(
    key: str, data: CloneRequest, conn: ConnDep, project: ServerProject
) -> ExperimentDetail:
    return experiments.clone_experiment(conn, project, key, data.new_key)
