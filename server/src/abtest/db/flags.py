"""Feature flags (PRD §11). Every change bumps the project's config version."""

from uuid import UUID

import psycopg
from psycopg import errors
from psycopg.rows import class_row

from abtest.db.projects import bump_config_version
from abtest.db.updates import set_columns
from abtest.errors import Conflict, NotFound
from abtest.models import Flag, FlagCreate, FlagUpdate


def list_flags(conn: psycopg.Connection, project_id: UUID) -> list[Flag]:
    with conn.cursor(row_factory=class_row(Flag)) as cur:
        return cur.execute(
            "SELECT key, description, enabled, rollout_bp, created_at, updated_at"
            " FROM flags WHERE project_id = %s ORDER BY key",
            (project_id,),
        ).fetchall()


def get_flag(conn: psycopg.Connection, project_id: UUID, key: str) -> Flag:
    with conn.cursor(row_factory=class_row(Flag)) as cur:
        flag = cur.execute(
            "SELECT key, description, enabled, rollout_bp, created_at, updated_at"
            " FROM flags WHERE project_id = %s AND key = %s",
            (project_id, key),
        ).fetchone()
    if flag is None:
        raise NotFound("flag_not_found", f"no flag with key {key!r}")
    return flag


def create_flag(conn: psycopg.Connection, project_id: UUID, data: FlagCreate) -> Flag:
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO flags (project_id, key, description, enabled, rollout_bp)"
                " VALUES (%s, %s, %s, %s, %s)",
                (project_id, data.key, data.description, data.enabled, data.rollout_bp),
            )
            bump_config_version(conn, project_id)
    except errors.UniqueViolation:
        raise Conflict("flag_exists", f"a flag with key {data.key!r} already exists") from None
    return get_flag(conn, project_id, data.key)


def update_flag(conn: psycopg.Connection, project_id: UUID, key: str, data: FlagUpdate) -> Flag:
    changes = data.model_dump(exclude_none=True)
    with conn.transaction():
        flag_id = _lock(conn, project_id, key)
        if changes:
            set_columns(conn, "flags", flag_id, changes, touch=True)
            bump_config_version(conn, project_id)
    return get_flag(conn, project_id, key)


def delete_flag(conn: psycopg.Connection, project_id: UUID, key: str) -> None:
    with conn.transaction():
        conn.execute("DELETE FROM flags WHERE id = %s", (_lock(conn, project_id, key),))
        bump_config_version(conn, project_id)


def _lock(conn: psycopg.Connection, project_id: UUID, key: str) -> UUID:
    """The flag's id, locked until the transaction ends."""
    row = conn.execute(
        "SELECT id FROM flags WHERE project_id = %s AND key = %s FOR UPDATE", (project_id, key)
    ).fetchone()
    if row is None:
        raise NotFound("flag_not_found", f"no flag with key {key!r}")
    flag_id: UUID = row[0]
    return flag_id
