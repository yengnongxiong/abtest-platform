"""The SDK config document served by GET /v1/config (PRD §11)."""

from uuid import UUID

import psycopg
from psycopg.rows import class_row

from abtest.models import Config

# One statement, so the version and the content come from the same snapshot. Built from
# separate queries, a concurrent change could produce "version 5" with version-4 content:
# the SDK would cache the stale content under a current ETag, and every later request would
# get 304 Not Modified.
LOAD_CONFIG = """
SELECT p.config_version,
       coalesce((
           SELECT json_agg(json_build_object(
                      'key', f.key, 'enabled', f.enabled, 'rollout_bp', f.rollout_bp)
                  ORDER BY f.key)
           FROM flags f WHERE f.project_id = p.id
       ), '[]') AS flags,
       coalesce((
           SELECT json_agg(json_build_object(
                      'key', e.key,
                      'traffic_bp', e.traffic_bp,
                      'variants', (
                          SELECT json_agg(json_build_object(
                                     'key', v.key, 'weight_bp', v.weight_bp,
                                     'position', v.position)
                                 ORDER BY v.position)
                          FROM variants v WHERE v.experiment_id = e.id)
                  ) ORDER BY e.key)
           FROM experiments e WHERE e.project_id = p.id AND e.status = 'running'
       ), '[]') AS experiments
FROM projects p
WHERE p.id = %s
"""


def config_version(conn: psycopg.Connection, project_id: UUID) -> int:
    """Just the version: enough to answer a conditional request with 304."""
    row = conn.execute(
        "SELECT config_version FROM projects WHERE id = %s", (project_id,)
    ).fetchone()
    assert row is not None  # the project owns the key that authenticated the request
    version: int = row[0]
    return version


def load_config(conn: psycopg.Connection, project_id: UUID) -> Config:
    with conn.cursor(row_factory=class_row(Config)) as cur:
        config = cur.execute(LOAD_CONFIG, (project_id,)).fetchone()
    assert config is not None  # the project owns the key that authenticated the request
    return config
