"""Storing events (PRD §10, §11)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from abtest.assignment import WeightedVariant

# One statement per batch, never a row at a time. ON CONFLICT DO NOTHING skips duplicates
# (a retried batch, or the same event twice in one batch), and RETURNING says which rows were
# new, so only new exposure events go on to update the exposures table.
INSERT_EVENTS = """
INSERT INTO events (project_id, event_id, user_id, event_name, occurred_at, value, properties)
SELECT %s, *
FROM unnest(%s::uuid[], %s::text[], %s::text[], %s::timestamptz[], %s::float8[], %s::jsonb[])
ON CONFLICT DO NOTHING
RETURNING event_id, occurred_at
"""


@dataclass(frozen=True, slots=True)
class ValidEvent:
    event_id: UUID
    user_id: str
    name: str
    occurred_at: datetime
    value: float | None
    properties: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RunningExperiment:
    id: UUID
    key: str
    traffic_bp: int
    variants: list[WeightedVariant]
    variant_ids: dict[str, UUID]


def insert_events(
    conn: psycopg.Connection, project_id: UUID, events: Sequence[ValidEvent]
) -> set[tuple[UUID, datetime]]:
    """Insert a batch; return the (event_id, occurred_at) of the rows that were new."""
    if not events:
        return set()
    rows = conn.execute(
        INSERT_EVENTS,
        (
            project_id,
            [e.event_id for e in events],
            [e.user_id for e in events],
            [e.name for e in events],
            [e.occurred_at for e in events],
            [e.value for e in events],
            [Jsonb(e.properties) for e in events],
        ),
    ).fetchall()
    return {(event_id, occurred_at) for event_id, occurred_at in rows}


def running_experiments(conn: psycopg.Connection, project_id: UUID) -> dict[str, RunningExperiment]:
    """The project's running experiments by key, with their variants: what an exposure can
    refer to, and what the server needs to recompute its assignment."""
    rows = conn.execute(
        "SELECT e.id, e.key, e.traffic_bp, v.id, v.key, v.weight_bp, v.position"
        " FROM experiments e JOIN variants v ON v.experiment_id = e.id"
        " WHERE e.project_id = %s AND e.status = 'running'"
        " ORDER BY e.key, v.position",
        (project_id,),
    ).fetchall()
    experiments: dict[str, RunningExperiment] = {}
    for experiment_id, key, traffic_bp, variant_id, variant_key, weight_bp, position in rows:
        experiment = experiments.setdefault(
            key, RunningExperiment(experiment_id, key, traffic_bp, [], {})
        )
        experiment.variants.append(WeightedVariant(variant_key, weight_bp, position))
        experiment.variant_ids[variant_key] = variant_id
    return experiments
