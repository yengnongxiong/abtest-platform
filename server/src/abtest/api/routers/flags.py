"""Admin: feature flags."""

from fastapi import APIRouter, Response

from abtest.api.deps import ConnDep, ServerProject
from abtest.db import flags
from abtest.models import Flag, FlagCreate, FlagUpdate

router = APIRouter(prefix="/admin/flags", tags=["flags"])


@router.get("")
def list_flags(conn: ConnDep, project: ServerProject) -> list[Flag]:
    return flags.list_flags(conn, project)


@router.post("", status_code=201)
def create_flag(data: FlagCreate, conn: ConnDep, project: ServerProject) -> Flag:
    return flags.create_flag(conn, project, data)


@router.get("/{key}")
def get_flag(key: str, conn: ConnDep, project: ServerProject) -> Flag:
    return flags.get_flag(conn, project, key)


@router.patch("/{key}")
def update_flag(key: str, data: FlagUpdate, conn: ConnDep, project: ServerProject) -> Flag:
    return flags.update_flag(conn, project, key, data)


@router.delete("/{key}", status_code=204, response_class=Response)
def delete_flag(key: str, conn: ConnDep, project: ServerProject) -> None:
    flags.delete_flag(conn, project, key)
