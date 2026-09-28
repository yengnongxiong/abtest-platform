"""Event ingestion (PRD §10, §11): validate each event on its own, store the batch in one
statement, and record exposures for newly stored exposure events, in one transaction."""

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from pydantic import ValidationError

from abtest.assignment import assign
from abtest.db.events import RunningExperiment, ValidEvent, insert_events, running_experiments
from abtest.db.exposures import Exposure, upsert_exposures
from abtest.errors import BadRequest, Unprocessable
from abtest.models import EXPOSURE_EVENT, EventBatch, EventIn, EventsResult, Rejection

# The API accepts events up to 7 days old (the oldest partition ensure_event_partitions
# creates) and up to 5 minutes ahead (client clocks drift a little).
MAX_AGE = timedelta(days=7)
MAX_AHEAD = timedelta(minutes=5)


def parse_batch(body: bytes) -> EventBatch:
    """Parse the raw body as JSON, whatever its Content-Type: the SDK's beacons send it as
    text/plain to avoid a CORS preflight."""
    try:
        data = json.loads(body, parse_constant=_refuse_constant)
    except ValueError, RecursionError:  # includes invalid UTF-8
        raise BadRequest("invalid_json", "the body must be a JSON object") from None
    try:
        return EventBatch.model_validate(data)
    except ValidationError as error:
        raise Unprocessable(
            "invalid_batch", "the body must be {sdk, events} with at most 500 events",
            error.errors(include_url=False, include_context=False),
        ) from None  # fmt: skip


def ingest(
    conn: psycopg.Connection, project_id: UUID, batch: EventBatch, now: datetime
) -> EventsResult:
    with conn.transaction():
        experiments = running_experiments(conn, project_id)
        valid, rejected = validate_events(batch.events, project_id, experiments, now)
        new = insert_events(conn, project_id, [event for event, _ in valid])
        # Exposures only for events stored just now, so a retried batch changes nothing. If
        # one batch holds the same event twice, only its first copy was stored (rows are
        # inserted in order), so only that copy counts.
        exposures: list[Exposure] = []
        counted: set[tuple[UUID, datetime]] = set()
        for event, exposure in valid:
            key = (event.event_id, event.occurred_at)
            if exposure is not None and key in new and key not in counted:
                counted.add(key)
                exposures.append(exposure)
        upsert_exposures(conn, exposures)
    return EventsResult(accepted=len(new), duplicates=len(valid) - len(new), rejected=rejected)


def validate_events(
    raw_events: Sequence[Any],
    project_id: UUID,
    experiments: dict[str, RunningExperiment],
    now: datetime,
) -> tuple[list[tuple[ValidEvent, Exposure | None]], list[Rejection]]:
    """Split a batch into storable events (each with its exposure, for "$exposure" events)
    and rejections with reasons. One bad event never fails the batch."""
    valid: list[tuple[ValidEvent, Exposure | None]] = []
    rejected: list[Rejection] = []
    for index, raw in enumerate(raw_events):
        try:
            event = EventIn.model_validate(raw)
        except ValidationError as error:
            rejected.append(Rejection(index=index, reason=_reason(error)))
            continue
        if event.occurred_at < now - MAX_AGE:
            rejected.append(Rejection(index=index, reason="occurred_at is more than 7 days ago"))
            continue
        if event.occurred_at > now + MAX_AHEAD:
            rejected.append(
                Rejection(index=index, reason="occurred_at is more than 5 minutes in the future")
            )
            continue
        exposure = None
        if event.name == EXPOSURE_EVENT:
            exposure_or_reason = _exposure(event, project_id, experiments)
            if isinstance(exposure_or_reason, str):
                rejected.append(Rejection(index=index, reason=exposure_or_reason))
                continue
            exposure = exposure_or_reason
        stored = ValidEvent(
            event_id=event.event_id,
            user_id=event.user_id,
            name=event.name,
            occurred_at=event.occurred_at,
            value=event.value,
            properties=event.properties,
        )
        valid.append((stored, exposure))
    return valid, rejected


def _exposure(
    event: EventIn, project_id: UUID, experiments: dict[str, RunningExperiment]
) -> Exposure | str:
    """The exposure this event records, or the reason it can't be accepted.

    Only running experiments are accepted: an exposure that arrives after its experiment
    stopped is rejected even if it happened earlier (a documented limitation, PRD §11).
    The server recomputes the assignment; a disagreement is flagged, not rejected, so SDK
    bugs show up on the dashboard instead of silently skewing results.
    """
    experiment_key = event.properties["experiment_key"]
    variant_key = event.properties["variant_key"]
    experiment = experiments.get(experiment_key)
    if experiment is None:
        return f"no running experiment with key {experiment_key!r}"
    variant_id = experiment.variant_ids.get(variant_key)
    if variant_id is None:
        return f"experiment {experiment_key!r} has no variant {variant_key!r}"
    expected = assign(experiment.key, event.user_id, experiment.traffic_bp, experiment.variants)
    return Exposure(
        project_id=project_id,
        experiment_id=experiment.id,
        variant_id=variant_id,
        user_id=event.user_id,
        exposed_at=event.occurred_at,
        assignment_mismatch=expected != variant_key,
    )


def _reason(error: ValidationError) -> str:
    """The first problem, as 'field: message'."""
    first = error.errors(include_url=False)[0]
    message = first["msg"].removeprefix("Value error, ")
    location = ".".join(str(part) for part in first["loc"])
    return f"{location}: {message}" if location else message


def _refuse_constant(name: str) -> None:
    """Python's json accepts NaN and Infinity, which aren't JSON (and which jsonb refuses)."""
    raise ValueError(f"{name} is not valid JSON")
