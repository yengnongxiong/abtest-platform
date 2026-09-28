"""Recording exposures: who first saw which variant of an experiment, and when (PRD §10)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg

# One statement per batch:
# 1. unnest the batch's arrays into rows;
# 2. collapse rows for the same (experiment, user): the earliest time, the variant seen first,
#    and conflicted if the batch itself holds two variants. Postgres rejects an ON CONFLICT DO
#    UPDATE that touches the same row twice in one statement, so this step is required;
# 3. insert, or merge into the existing row: keep the earliest time, keep the variant already
#    recorded, and mark the user conflicted if the variants differ.
UPSERT_EXPOSURES = """
INSERT INTO exposures AS e
    (project_id, experiment_id, variant_id, user_id, first_exposed_at, conflicted,
     assignment_mismatch)
SELECT project_id,
       experiment_id,
       (array_agg(variant_id ORDER BY exposed_at))[1],
       user_id,
       min(exposed_at),
       count(DISTINCT variant_id) > 1,
       bool_or(assignment_mismatch)
FROM unnest(%s::uuid[], %s::uuid[], %s::uuid[], %s::text[], %s::timestamptz[], %s::boolean[])
    AS batch (project_id, experiment_id, variant_id, user_id, exposed_at, assignment_mismatch)
GROUP BY project_id, experiment_id, user_id
ON CONFLICT (experiment_id, user_id) DO UPDATE SET
    first_exposed_at = LEAST(e.first_exposed_at, EXCLUDED.first_exposed_at),
    conflicted = e.conflicted OR EXCLUDED.conflicted OR e.variant_id <> EXCLUDED.variant_id,
    assignment_mismatch = e.assignment_mismatch OR EXCLUDED.assignment_mismatch
"""


@dataclass(frozen=True, slots=True)
class Exposure:
    """One "$exposure" event, as ingestion hands it over."""

    project_id: UUID
    experiment_id: UUID
    variant_id: UUID
    user_id: str
    exposed_at: datetime
    # The server's own assignment for this user disagreed with the SDK's variant.
    assignment_mismatch: bool


def upsert_exposures(conn: psycopg.Connection, exposures: Sequence[Exposure]) -> None:
    """Record a batch of exposures in one statement.

    Call it only with exposures from newly inserted "$exposure" events (ingestion, M6), so
    a retried batch never touches this table twice.
    """
    if not exposures:
        return
    conn.execute(
        UPSERT_EXPOSURES,
        (
            [x.project_id for x in exposures],
            [x.experiment_id for x in exposures],
            [x.variant_id for x in exposures],
            [x.user_id for x in exposures],
            [x.exposed_at for x in exposures],
            [x.assignment_mismatch for x in exposures],
        ),
    )
