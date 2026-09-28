"""Seed a separate database with N events, for the performance measurements (PRD §18).

    uv run --project server python loadtest/seed_events.py --events 10000000

Recreates the database abtest_perf on the server in DATABASE_URL, so the dev database the
dashboard reads is left alone. It applies the real migrations and bootstrap (the default
project, the API keys from .env, and the event partitions), then loads with COPY:
- events // 20 users, active over the last 7 days (about 20 events each);
- two running 50/50 experiments whose metrics count "purchase" (workload.EXPERIMENTS): a
  large one (every user, started 3 days ago) and a small one (2% of users, started 1 day
  ago). 60% of the users they include are exposed, each at a random time since the start,
  to the variant the real assignment code gives them: one exposures row and one
  "$exposure" event each;
- the other events: random users, times, and names (workload.EVENT_SHARES).
Events are written in time order, the way live ingestion appends them, so rows sit on disk
roughly as they would in production, and only one day's indexes are being written at a time.
Finally it runs VACUUM ANALYZE, so query plans see fresh statistics and the visibility map
is set. (Autovacuum does that for each partition eventually, but never analyzes the
partitioned table itself; see ADR-023.)
"""

import argparse
import json
import math
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import numpy as np
import psycopg
from numpy.typing import NDArray
from psycopg import sql
from psycopg.conninfo import make_conninfo
from workload import (
    EVENT_SHARES,
    EXPERIMENTS,
    METRIC_EVENT,
    PERF_DATABASE,
    VARIANTS,
    Experiment,
    variant_for,
)

from abtest.config import MigrateSettings
from abtest.db.bootstrap import bootstrap
from abtest.db.migrate import migrate

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"
EVENTS_PER_USER = 20
EXPOSED_SHARE = 0.6
HISTORY = timedelta(days=7)  # the API accepts events up to 7 days old
SLICE_EVENTS = 1_000_000  # events generated and sorted at a time, to bound memory

COPY_EVENTS = (
    "COPY events (project_id, event_id, user_id, event_name, occurred_at, value, properties)"
    " FROM STDIN"
)
COPY_EXPOSURES = (
    "COPY exposures (project_id, experiment_id, variant_id, user_id, first_exposed_at) FROM STDIN"
)

type Micros = NDArray[np.int64]  # times, as microseconds since the start of the history


@dataclass(frozen=True, slots=True)
class Seeded:
    events: int
    users: int
    exposures: dict[str, int]  # by experiment key
    copy_seconds: float


@dataclass(frozen=True, slots=True)
class Exposures:
    """Every experiment's exposures, in time order."""

    users: NDArray[np.int64]  # indexes into the user ids
    times: Micros
    experiments: list[str]
    variants: list[str]


def seed(conn: psycopg.Connection, events: int, rng_seed: int) -> Seeded:
    """Load the experiments, exposures, and `events` events into a migrated, bootstrapped
    database. The same seed gives the same rows, apart from times, which are relative to
    now, and database ids."""
    if events < EVENTS_PER_USER:
        raise ValueError(f"seed at least {EVENTS_PER_USER} events")
    rng = np.random.default_rng(rng_seed)
    row = conn.execute("SELECT id, now() FROM projects ORDER BY created_at LIMIT 1").fetchone()
    assert row is not None, "bootstrap creates the default project"
    project_id, now = row
    since = now - HISTORY
    metric_ids = create_metrics(conn, project_id)
    ids = {
        e.key: create_experiment(conn, project_id, e, now - e.started_ago, metric_ids)
        for e in EXPERIMENTS
    }

    user_ids = random_uuids(rng, events // EVENTS_PER_USER)
    exposures = choose_exposures(rng, user_ids)

    began = time.perf_counter()
    with conn.cursor() as cur:
        with cur.copy(COPY_EXPOSURES) as copy:
            at = as_text(since, exposures.times)
            for i, user in enumerate(exposures.users):
                experiment_id, variant_ids = ids[exposures.experiments[i]]
                variant_id = variant_ids[exposures.variants[i]]
                copy.write_row((project_id, experiment_id, variant_id, user_ids[user], at[i]))
        with cur.copy(COPY_EVENTS) as copy:
            for event in event_rows(rng, project_id, user_ids, exposures, since, events):
                copy.write_row(event)
    copy_seconds = time.perf_counter() - began
    counts = {e.key: exposures.experiments.count(e.key) for e in EXPERIMENTS}
    return Seeded(events, len(user_ids), counts, copy_seconds)


def choose_exposures(rng: np.random.Generator, user_ids: list[str]) -> Exposures:
    """For each experiment, EXPOSED_SHARE of the users it includes, each exposed at a random
    time between its start and now."""
    users: list[int] = []
    experiments: list[str] = []
    variants: list[str] = []
    times: list[Micros] = []
    for experiment in EXPERIMENTS:
        exposed = 0
        for i in np.flatnonzero(rng.random(len(user_ids)) < EXPOSED_SHARE).tolist():
            variant = variant_for(experiment, user_ids[i])
            if variant is not None:
                users.append(i)
                experiments.append(experiment.key)
                variants.append(variant)
                exposed += 1
        start = micros(HISTORY - experiment.started_ago)
        times.append(rng.integers(start, micros(HISTORY), exposed))
    all_times = np.concatenate(times)
    order = np.argsort(all_times, kind="stable")
    return Exposures(
        users=np.array(users, dtype=np.int64)[order],
        times=all_times[order],
        experiments=[experiments[i] for i in order],
        variants=[variants[i] for i in order],
    )


def event_rows(
    rng: np.random.Generator,
    project_id: UUID,
    user_ids: list[str],
    exposures: Exposures,
    since: datetime,
    events: int,
) -> Iterator[tuple[object, ...]]:
    """Every event as a COPY row, in time order: the history is cut into equal slices, and
    each slice's exposure events and other events are generated and sorted together."""
    others = events - len(exposures.users)
    slices = math.ceil(others / SLICE_EVENTS)
    bounds = np.linspace(0, micros(HISTORY), slices + 1).astype(np.int64)
    for k in range(slices):
        start, end = int(bounds[k]), int(bounds[k + 1])
        n = others // slices + (1 if k < others % slices else 0)
        first, last = np.searchsorted(exposures.times, [start, end])
        names = rng.choice(list(EVENT_SHARES), size=n, p=list(EVENT_SHARES.values()))
        values = np.full(n, None, dtype=object)
        purchases = names == METRIC_EVENT
        # Order totals: log-normal, around $37 at the median.
        values[purchases] = np.round(rng.lognormal(mean=3.6, sigma=0.8, size=purchases.sum()), 2)

        times = np.concatenate([exposures.times[first:last], rng.integers(start, end, n)])
        users = np.concatenate([exposures.users[first:last], rng.integers(0, len(user_ids), n)])
        names_all = ["$exposure"] * (last - first) + names.tolist()
        values_all = [None] * (last - first) + values.tolist()
        properties = [
            json.dumps({"experiment_key": experiment, "variant_key": variant})
            for experiment, variant in zip(
                exposures.experiments[first:last], exposures.variants[first:last], strict=True
            )
        ] + ["{}"] * n

        order = np.argsort(times, kind="stable")
        at = as_text(since, times[order])
        event_ids = random_uuids(rng, len(order))
        for row, i in enumerate(order.tolist()):
            yield (project_id, event_ids[row], user_ids[users[i]], names_all[i], at[row],
                   values_all[i], properties[i])  # fmt: skip


def create_metrics(conn: psycopg.Connection, project_id: UUID) -> dict[str, UUID]:
    """A conversion metric and a mean metric on purchases; their ids by key."""
    rows = conn.execute(
        "INSERT INTO metrics (project_id, key, name, kind, event_name, direction)"
        " VALUES (%(p)s, 'purchase', 'Purchase', 'conversion', %(e)s, 'increase'),"
        "        (%(p)s, 'revenue', 'Revenue', 'mean', %(e)s, 'increase')"
        " RETURNING key, id",
        {"p": project_id, "e": METRIC_EVENT},
    ).fetchall()
    return {key: metric_id for key, metric_id in rows}


def create_experiment(
    conn: psycopg.Connection,
    project_id: UUID,
    experiment: Experiment,
    started_at: datetime,
    metric_ids: dict[str, UUID],
) -> tuple[UUID, dict[str, UUID]]:
    """A running experiment with the purchase metric as primary and revenue as secondary.
    Returns its id and its variant ids by key."""
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO experiments (project_id, key, name, hypothesis, status, traffic_bp,"
            "  mde_relative, started_at)"
            " VALUES (%s, %s, %s, 'Seeded for the performance measurements.', 'running', %s,"
            "  0.08, %s)"
            " RETURNING id",
            (project_id, experiment.key, experiment.key, experiment.traffic_bp, started_at),
        ).fetchone()
        assert row is not None
        experiment_id: UUID = row[0]
        variant_ids = {}
        for variant in VARIANTS:
            row = conn.execute(
                "INSERT INTO variants (experiment_id, key, name, weight_bp, is_control, position)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (experiment_id, variant.key, variant.key, variant.weight_bp,
                 variant.position == 0, variant.position),
            ).fetchone()  # fmt: skip
            assert row is not None
            variant_ids[variant.key] = row[0]
        conn.execute(
            "INSERT INTO experiment_metrics (experiment_id, metric_id, role, expected_baseline)"
            " VALUES (%s, %s, 'primary', 0.3), (%s, %s, 'secondary', 20)",
            (experiment_id, metric_ids["purchase"], experiment_id, metric_ids["revenue"]),
        )
    return experiment_id, variant_ids


def random_uuids(rng: np.random.Generator, n: int) -> list[str]:
    """n random UUIDs as 32 hex digits (Postgres accepts them without hyphens)."""
    digits = rng.bytes(16 * n).hex()
    return [digits[i : i + 32] for i in range(0, 32 * n, 32)]


def micros(span: timedelta) -> int:
    return span // timedelta(microseconds=1)


def as_text(since: datetime, times: Micros) -> list[str]:
    """Times (microseconds after `since`) as ISO 8601 UTC text, which COPY parses."""
    start = np.datetime64(since.astimezone(UTC).replace(tzinfo=None), "us")
    stamps = start + times.astype("timedelta64[us]")
    return [f"{text}Z" for text in np.datetime_as_string(stamps, unit="us").tolist()]


def recreate_database(admin_url: str, name: str) -> str:
    """Drop and create the database `name` on the server of admin_url; return its DSN."""
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return make_conninfo(admin_url, dbname=name)


def sizes(conn: psycopg.Connection) -> list[tuple[str, str]]:
    """The events table's size on disk: its rows, and each of its indexes (all partitions)."""
    rows = conn.execute(
        "SELECT 'events (rows)', pg_size_pretty(sum(pg_relation_size(inhrelid)))"
        " FROM pg_inherits WHERE inhparent = 'events'::regclass"
        " UNION ALL"
        " SELECT parent.relname, pg_size_pretty(sum(pg_relation_size(child.inhrelid)))"
        " FROM pg_class parent JOIN pg_inherits child ON child.inhparent = parent.oid"
        " WHERE parent.relkind = 'I' AND parent.relname LIKE 'events%%'"
        " GROUP BY parent.relname"
    ).fetchall()
    return [(name, size) for name, size in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--events", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    settings = MigrateSettings()  # DATABASE_URL and the API keys, from the environment
    url = recreate_database(settings.database_url, PERF_DATABASE)
    with psycopg.connect(url, autocommit=True) as conn:
        migrate(conn, MIGRATIONS_DIR)
        bootstrap(
            conn,
            settings.abtest_client_key.get_secret_value(),
            settings.abtest_server_key.get_secret_value(),
        )
        seeded = seed(conn, args.events, args.seed)
        began = time.perf_counter()
        conn.execute("VACUUM (ANALYZE) events")
        conn.execute("VACUUM (ANALYZE) exposures")
        vacuum_seconds = time.perf_counter() - began
        print(f"database: {PERF_DATABASE}")
        print(f"events: {seeded.events:,} ({seeded.users:,} users)")
        for key, count in seeded.exposures.items():
            print(f"exposed to {key}: {count:,}")
        print(
            f"COPY: {seeded.copy_seconds:.1f} s"
            f" ({seeded.events / seeded.copy_seconds:,.0f} events/s, with the indexes in place)"
        )
        print(f"VACUUM ANALYZE: {vacuum_seconds:.1f} s")
        for name, size in sizes(conn):
            print(f"size of {name}: {size}")


if __name__ == "__main__":
    main()
