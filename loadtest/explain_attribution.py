"""EXPLAIN (ANALYZE, BUFFERS) of the attribution query, with and without its index (PRD §18).

    uv run --project server python loadtest/explain_attribution.py

Runs the worker's own query (abtest.db.results.AGGREGATE, for the primary metric) on the
database seed_events.py filled, for the large and the small experiment, in three states:
- "no index": only the primary key, which can't help this query;
- "0001 index": the original (project_id, event_name, user_id, occurred_at) index, built
  here in one go by CREATE INDEX;
- "0002 index": the current one, which adds INCLUDE (value) so that it covers the query. It
  is the migrated index, which grew as the rows were loaded, like a live table's.
Each state is set up inside a transaction that is rolled back, so the database is left as
the migrations made it. Times are of the query itself, warm: one run to fill the cache,
then the median (and range) of RUNS runs. EXPLAIN ANALYZE then runs once for the plan and
its buffer counts; its own timing is inflated by measuring every row, so it isn't used.
Prints markdown: a summary table, then every plan.
"""

import re
import statistics
import time
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.conninfo import make_conninfo
from workload import EXPERIMENTS, PERF_DATABASE

from abtest.config import Settings
from abtest.db.results import AGGREGATE, NO_NESTED_LOOP, aggregate_params, experiment_to_analyze

STATES = {
    "no index": ["DROP INDEX events_attribution"],
    "0001 index": [
        "DROP INDEX events_attribution",
        "CREATE INDEX attribution_0001 ON events (project_id, event_name, user_id, occurred_at)",
    ],
    "0002 index": [],  # as migrated
}
RUNS = 7
# The attribution index's size, whichever state's index it is, over all partitions.
INDEX_SIZE = """
SELECT coalesce(pg_size_pretty(sum(pg_relation_size(child.inhrelid))), 'none')
FROM pg_inherits child JOIN pg_class parent ON parent.oid = child.inhparent
WHERE parent.relname IN ('events_attribution', 'attribution_0001')
"""


@dataclass(frozen=True, slots=True)
class Measured:
    experiment: str
    users: int
    state: str
    index_size: str
    times_ms: list[float]
    plan: list[str]

    @property
    def buffers(self) -> str:
        """The top node's buffer counts: the whole query's."""
        return next(line.strip() for line in self.plan if "Buffers:" in line).removeprefix(
            "Buffers: "
        )

    @property
    def shape(self) -> str:
        """The join method, and how each events partition is read."""
        text = "\n".join(self.plan)
        join = re.search(r"(Nested Loop|Hash (?:Right |Left )?Join|Merge (?:Right )?Join)", text)
        scans = re.findall(
            r"(Seq Scan|Index Only Scan|(?<!Bitmap )Index Scan|Bitmap Heap Scan).* on events_", text
        )
        kinds = ", ".join(f"{kind} ({scans.count(kind)})" for kind in dict.fromkeys(scans))
        return f"{join.group(1) if join else '?'}; {kinds}"


def measure(
    conn: psycopg.Connection, experiment_key: str, state: str, cutoff: datetime
) -> Measured:
    row = conn.execute("SELECT id FROM projects ORDER BY created_at LIMIT 1").fetchone()
    assert row is not None
    experiment = experiment_to_analyze(conn, row[0], experiment_key)
    assert experiment is not None, "run seed_events.py first"
    metric = next(m for m in experiment.metrics if m.role == "primary")
    params = aggregate_params(experiment, metric, cutoff)
    users = conn.execute(
        "SELECT count(*) FROM exposures WHERE experiment_id = %s", (experiment.id,)
    ).fetchone()
    assert users is not None
    with conn.transaction(force_rollback=True):
        for statement in STATES[state]:
            conn.execute(statement)  # fixed DDL from STATES, never values
        conn.execute(NO_NESTED_LOOP)  # as aggregate() plans it for the worker
        size = conn.execute(INDEX_SIZE).fetchone()
        assert size is not None
        conn.execute(AGGREGATE, params).fetchall()  # fills the cache
        times = []
        for _ in range(RUNS):
            began = time.perf_counter()
            conn.execute(AGGREGATE, params).fetchall()
            times.append((time.perf_counter() - began) * 1000)
        explained = conn.execute("EXPLAIN (ANALYZE, BUFFERS) " + AGGREGATE, params)
        plan = [line for (line,) in explained]
    return Measured(experiment_key, users[0], state, size[0], times, plan)


def main() -> None:
    url = make_conninfo(Settings().database_url, dbname=PERF_DATABASE)
    # prepare_threshold=None: psycopg never turns the repeated query into a prepared
    # statement, so every run is planned for its own values, like the EXPLAIN.
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        row = conn.execute(
            "SELECT now(), (SELECT count(*) FROM events), current_setting('server_version'),"
            " current_setting('shared_buffers'), current_setting('work_mem')"
        ).fetchone()
        assert row is not None
        cutoff, events, version, shared_buffers, work_mem = row
        results = [
            measure(conn, experiment.key, state, cutoff)
            for experiment in EXPERIMENTS
            for state in STATES
        ]

    print(f"Database: {events:,} events. PostgreSQL {version}, shared_buffers {shared_buffers},")
    print(f"work_mem {work_mem}. Cutoff: {cutoff:%Y-%m-%d %H:%M} UTC. Times: {RUNS} warm runs.\n")
    print("| Experiment (exposed users) | Index (size) | Plan | Median time (range) | Buffers |")
    print("|---|---|---|---:|---|")
    for m in results:
        print(
            f"| {m.experiment} ({m.users:,}) | {m.state} ({m.index_size}) | {m.shape}"
            f" | {statistics.median(m.times_ms):,.0f} ms"
            f" ({min(m.times_ms):,.0f} to {max(m.times_ms):,.0f}) | {m.buffers} |"
        )
    for m in results:
        print(f"\n<details><summary>{m.experiment}, {m.state}</summary>\n\n```text")
        print("\n".join(m.plan))
        print("```\n\n</details>")


if __name__ == "__main__":
    main()
