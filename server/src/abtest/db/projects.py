"""Project-level bookkeeping."""

from uuid import UUID

import psycopg


def bump_config_version(conn: psycopg.Connection, project_id: UUID) -> None:
    """Mark the project's SDK config as changed.

    Every flag, experiment, or variant write calls this in the same transaction (PRD §10).
    The version is the config's ETag, so SDKs refetch exactly when something changed, and
    never see a new version without the change it stands for.
    """
    conn.execute(
        "UPDATE projects SET config_version = config_version + 1 WHERE id = %s", (project_id,)
    )
