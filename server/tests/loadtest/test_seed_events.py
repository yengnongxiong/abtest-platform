"""loadtest/seed_events.py: the seeded data is what docs/performance.md says it is."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

import numpy as np
import psycopg
import pytest
import seed_events
from seed_events import HISTORY, Seeded, seed
from workload import EXPERIMENTS, variant_for

from abtest.db.bootstrap import bootstrap
from abtest.db.migrate import migrate
from abtest.db.results import experiment_to_analyze
from abtest.results import compute_snapshots

EVENTS = 4_000


@pytest.fixture(scope="module")
def seeded(
    create_database: Callable[[], str], migrations_dir: Path
) -> Iterator[tuple[psycopg.Connection, Seeded]]:
    """A small seeded database, as `seed_events.py --events 4000` would leave it."""
    with psycopg.connect(create_database(), autocommit=True) as conn:
        migrate(conn, migrations_dir)
        bootstrap(conn, "ck_" + token_urlsafe(32), "sk_" + token_urlsafe(32))
        yield conn, seed(conn, EVENTS, rng_seed=1)


def test_every_event_is_stored_in_a_daily_partition(
    seeded: tuple[psycopg.Connection, Seeded],
) -> None:
    conn, _ = seeded
    row = conn.execute(
        "SELECT count(*), max(occurred_at) - min(occurred_at) <= %s, max(occurred_at) <= now()"
        " FROM events",
        (HISTORY,),
    ).fetchone()
    assert row == (EVENTS, True, True)
    assert conn.execute("SELECT count(*) FROM events_default").fetchone() == (0,)


def test_events_are_written_in_time_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # As live ingestion appends them. Small slices, so the order holds across slices too.
    monkeypatch.setattr(seed_events, "SLICE_EVENTS", 500)
    rng = np.random.default_rng(1)
    user_ids = seed_events.random_uuids(rng, 100)
    exposures = seed_events.choose_exposures(rng, user_ids)
    since = datetime.now(UTC) - HISTORY

    rows = list(seed_events.event_rows(rng, uuid4(), user_ids, exposures, since, 2_000))

    times = [str(row[4]) for row in rows]  # ISO 8601 text of one width: sorts by time
    assert len(times) == 2_000
    assert times == sorted(times)


def test_each_exposure_has_its_event_and_the_real_assignment(
    seeded: tuple[psycopg.Connection, Seeded],
) -> None:
    conn, result = seeded
    rows = conn.execute(
        "SELECT e.key, x.user_id, v.key, x.first_exposed_at >= e.started_at,"
        "  (SELECT count(*) FROM events ev WHERE ev.event_name = '$exposure'"
        "   AND ev.user_id = x.user_id AND ev.occurred_at = x.first_exposed_at"
        "   AND ev.properties = jsonb_build_object('experiment_key', e.key, 'variant_key', v.key))"
        " FROM exposures x JOIN experiments e ON e.id = x.experiment_id"
        " JOIN variants v ON v.id = x.variant_id"
    ).fetchall()
    by_key = {experiment.key: experiment for experiment in EXPERIMENTS}

    assert {key: sum(1 for row in rows if row[0] == key) for key in by_key} == result.exposures
    assert all(result.exposures.values())  # both experiments have exposed users
    for key, user, variant, after_start, events in rows:
        assert variant == variant_for(by_key[key], user)
        assert after_start
        assert events == 1
    exposure_events = conn.execute(
        "SELECT count(*) FROM events WHERE event_name = '$exposure'"
    ).fetchone()
    assert exposure_events == (len(rows),)


def test_the_worker_can_analyze_every_seeded_experiment(
    seeded: tuple[psycopg.Connection, Seeded],
) -> None:
    conn, result = seeded
    row = conn.execute("SELECT id, now() FROM projects").fetchone()
    assert row is not None
    project_id, now = row
    for experiment in EXPERIMENTS:
        analyzed = experiment_to_analyze(conn, project_id, experiment.key)
        assert analyzed is not None
        assert abs(now - analyzed.started_at - experiment.started_ago) < timedelta(minutes=1)
        with conn.transaction(force_rollback=True):
            # compute_snapshots returns the exposure rows it analyzed.
            assert compute_snapshots(conn, analyzed) == result.exposures[experiment.key]
