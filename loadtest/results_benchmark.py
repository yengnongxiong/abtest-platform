"""The results path at scale: a worker look, and reading four weeks of snapshots (PRD §15, §18).

    uv run --project server python loadtest/results_benchmark.py

On the database seed_events.py filled:
1. A look: compute_snapshots (what the worker and POST .../recompute run) for each
   experiment: the attribution query and the stats for both metrics, then the inserts.
   Each look opens its own connection, as each worker run does.
2. GET /admin/experiments/{key}/results, through the real app in this process, with the
   large experiment's primary metric holding 1 snapshot, then 8,064: four weeks of looks
   every 5 minutes (ADR-018). The copies are of its latest snapshot, so each is full size.
Timings are the median of RUNS runs, after one to fill the cache. Prints markdown.
"""

import statistics
import time
from collections.abc import Callable
from functools import partial

import psycopg
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from workload import EXPERIMENTS, LARGE, PERF_DATABASE

from abtest.api.app import create_app
from abtest.config import MigrateSettings, Settings
from abtest.db.results import ExperimentToAnalyze, experiment_to_analyze
from abtest.results import compute_snapshots

RUNS = 5
FOUR_WEEKS_OF_LOOKS = 4 * 7 * 24 * 12  # one every 5 minutes

KEEP_LATEST = """
DELETE FROM results_snapshots s
WHERE s.experiment_id = %(experiment)s AND s.metric_id = %(metric)s
  AND s.id <> (SELECT max(id) FROM results_snapshots
               WHERE experiment_id = %(experiment)s AND metric_id = %(metric)s)
"""
# Copies of the latest snapshot, one every 5 minutes going back in time.
ADD_EARLIER_LOOKS = """
INSERT INTO results_snapshots
    (experiment_id, metric_id, computed_at, total_users, srm_p_value, srm_flag, data)
SELECT experiment_id, metric_id, computed_at - n * interval '5 minutes', total_users,
       srm_p_value, srm_flag, data
FROM results_snapshots, generate_series(1, %(copies)s) AS n
WHERE experiment_id = %(experiment)s AND metric_id = %(metric)s
"""


def look(url: str, experiment: ExperimentToAnalyze) -> None:
    """One look, committed, as a worker run takes it."""
    with psycopg.connect(url, autocommit=True) as conn, conn.transaction():
        compute_snapshots(conn, experiment)


def median_seconds(run: Callable[[], object]) -> float:
    run()  # fills the cache
    times = []
    for _ in range(RUNS):
        began = time.perf_counter()
        run()
        times.append(time.perf_counter() - began)
    return statistics.median(times)


def main() -> None:
    settings = MigrateSettings()  # DATABASE_URL and the server key, from the environment
    url = make_conninfo(settings.database_url, dbname=PERF_DATABASE)
    with psycopg.connect(url, autocommit=True) as conn:
        row = conn.execute("SELECT id FROM projects ORDER BY created_at LIMIT 1").fetchone()
        assert row is not None, "run seed_events.py first"
        project_id = row[0]

        print("| Look (both metrics) | Exposed users | Time |")
        print("|---|---:|---:|")
        for key in [e.key for e in EXPERIMENTS]:
            experiment = experiment_to_analyze(conn, project_id, key)
            assert experiment is not None
            count = conn.execute(
                "SELECT count(*) FROM exposures WHERE experiment_id = %s", (experiment.id,)
            ).fetchone()
            assert count is not None
            seconds = median_seconds(partial(look, url, experiment))
            print(f"| {key} | {count[0]:,} | {seconds * 1000:,.0f} ms |")

        experiment = experiment_to_analyze(conn, project_id, LARGE.key)
        assert experiment is not None
        metric = next(m for m in experiment.metrics if m.role == "primary")
        ids = {"experiment": experiment.id, "metric": metric.id}
        headers = {"Authorization": f"Bearer {settings.abtest_server_key.get_secret_value()}"}
        print("\n| GET .../results | Snapshots | Response | Time |")
        print("|---|---:|---:|---:|")
        with TestClient(create_app(Settings(database_url=url))) as api:

            def get() -> int:
                response = api.get(f"/admin/experiments/{LARGE.key}/results", headers=headers)
                response.raise_for_status()
                return len(response.content)

            for copies in (0, FOUR_WEEKS_OF_LOOKS - 1):
                with conn.transaction():
                    conn.execute(KEEP_LATEST, ids)
                    conn.execute(ADD_EARLIER_LOOKS, {**ids, "copies": copies})
                seconds = median_seconds(get)
                print(
                    f"| {LARGE.key}, {metric.key} | {copies + 1:,} | {get() / 1024:,.0f} KB"
                    f" | {seconds * 1000:,.0f} ms |"
                )


if __name__ == "__main__":
    main()
