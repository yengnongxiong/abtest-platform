"""Partial updates (PATCH) with safely composed SQL."""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql


def set_columns(
    conn: psycopg.Connection,
    table: str,
    row_id: UUID,
    changes: Mapping[str, Any],
    *,
    touch: bool = False,
) -> None:
    """UPDATE the given columns of one row, by id; with touch=True, also set updated_at.

    Column and table names are quoted identifiers and every value is a bound parameter, so
    nothing from the request is ever pasted into SQL. The column names come from a request
    model that forbids unknown fields.
    """
    if not changes:
        return
    assignments: list[sql.Composable] = [
        sql.SQL("{} = {}").format(sql.Identifier(column), sql.Placeholder(column))
        for column in changes
    ]
    if touch:
        assignments.append(sql.SQL("updated_at = now()"))
    conn.execute(
        sql.SQL("UPDATE {} SET {} WHERE id = {}").format(
            sql.Identifier(table), sql.SQL(", ").join(assignments), sql.Placeholder("row_id")
        ),
        {**changes, "row_id": row_id},
    )
