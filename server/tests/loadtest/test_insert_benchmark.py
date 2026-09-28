"""loadtest/insert_benchmark.py compares two ways of doing the same thing."""

import random
from uuid import UUID

import psycopg
import pytest
from insert_benchmark import STRATEGIES, Strategy, random_batch


@pytest.mark.parametrize("strategy", STRATEGIES.values(), ids=STRATEGIES.keys())
def test_each_strategy_stores_new_events_and_skips_duplicates(
    conn: psycopg.Connection, project_id: UUID, strategy: Strategy
) -> None:
    # The comparison is only fair if row by row does what the API's batch insert does:
    # store new events, skip duplicates, and return the keys of the new rows.
    conn.execute("SELECT ensure_event_partitions(1)")
    rng = random.Random(1)
    batch = random_batch(rng)

    assert strategy(conn, project_id, batch) == {(e.event_id, e.occurred_at) for e in batch}

    retried = [*batch[:10], *random_batch(rng)[:5]]  # 10 duplicates, 5 new
    assert strategy(conn, project_id, retried) == {
        (e.event_id, e.occurred_at) for e in retried[10:]
    }
