"""Row-by-row inserts vs the batch insert the API uses (PRD §10, §18).

    uv run --project server python loadtest/insert_benchmark.py

Stores batches of 50 events (the SDK's default batch size) into the events table of the
database seed_events.py filled, one transaction per batch as the API does, in two ways:
- row by row: one INSERT ... ON CONFLICT DO NOTHING RETURNING per event, the obvious code;
- batch: the API's own statement (abtest.db.events.INSERT_EVENTS), one per batch.
The two alternate batch by batch, so both meet the same table and cache. Prints markdown:
events/s, batch latency, and the database round-trip time, which row by row pays per event.
"""

import random
import statistics
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from workload import EVENT_SHARES, METRIC_EVENT, PERF_DATABASE

from abtest.config import Settings
from abtest.db.events import ValidEvent, insert_events

BATCH_SIZE = 50
BATCHES = 500  # per strategy, after WARMUP_BATCHES that aren't counted
WARMUP_BATCHES = 20

INSERT_ONE = """
INSERT INTO events (project_id, event_id, user_id, event_name, occurred_at, value, properties)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
RETURNING event_id, occurred_at
"""

type Strategy = Callable[
    [psycopg.Connection, UUID, Sequence[ValidEvent]], set[tuple[UUID, datetime]]
]


def insert_row_by_row(
    conn: psycopg.Connection, project_id: UUID, events: Sequence[ValidEvent]
) -> set[tuple[UUID, datetime]]:
    """What insert_events would be without unnest: a statement, and a round trip, per event.
    Returns the same thing: the keys of the rows that were new."""
    new = set()
    for e in events:
        row = conn.execute(
            INSERT_ONE,
            (project_id, e.event_id, e.user_id, e.name, e.occurred_at, e.value,
             Jsonb(e.properties)),
        ).fetchone()  # fmt: skip
        if row is not None:
            new.add((row[0], row[1]))
    return new


STRATEGIES: dict[str, Strategy] = {
    "row by row": insert_row_by_row,
    "batch (unnest)": insert_events,
}


def random_batch(rng: random.Random) -> list[ValidEvent]:
    names = rng.choices(list(EVENT_SHARES), weights=list(EVENT_SHARES.values()), k=BATCH_SIZE)
    now = datetime.now(UTC)
    return [
        ValidEvent(
            event_id=uuid4(),
            user_id=str(uuid4()),
            name=name,
            occurred_at=now,
            value=49.0 if name == METRIC_EVENT else None,
            properties={},
        )
        for name in names
    ]


def main() -> None:
    rng = random.Random(42)
    url = make_conninfo(Settings().database_url, dbname=PERF_DATABASE)
    with psycopg.connect(url, autocommit=True) as conn:
        row = conn.execute("SELECT id, (SELECT count(*) FROM events) FROM projects").fetchone()
        assert row is not None, "run seed_events.py first"
        project_id, events_before = row

        round_trips = []
        for _ in range(200):
            began = time.perf_counter()
            conn.execute("SELECT 1").fetchone()
            round_trips.append(time.perf_counter() - began)

        latencies: dict[str, list[float]] = {name: [] for name in STRATEGIES}
        for i in range(WARMUP_BATCHES + BATCHES):
            for name, strategy in STRATEGIES.items():
                batch = random_batch(rng)
                began = time.perf_counter()
                with conn.transaction():
                    stored = strategy(conn, project_id, batch)
                elapsed = time.perf_counter() - began
                assert len(stored) == BATCH_SIZE
                if i >= WARMUP_BATCHES:
                    latencies[name].append(elapsed)

    print(
        f"Table: {events_before:,} events before the run. Median database round trip:"
        f" {statistics.median(round_trips) * 1000:.3f} ms. {BATCHES} batches of {BATCH_SIZE}"
        f" per strategy, one transaction each, after {WARMUP_BATCHES} warm-up batches.\n"
    )
    print("| Strategy | Events/s | Batch p50 | Batch p95 | Batch p99 |")
    print("|---|---:|---:|---:|---:|")
    for name, times in latencies.items():
        cuts = statistics.quantiles(times, n=100)
        print(
            f"| {name} | {BATCHES * BATCH_SIZE / sum(times):,.0f}"
            f" | {cuts[49] * 1000:.2f} ms | {cuts[94] * 1000:.2f} ms | {cuts[98] * 1000:.2f} ms |"
        )


if __name__ == "__main__":
    main()
