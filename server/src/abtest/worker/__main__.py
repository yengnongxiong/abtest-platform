"""Worker entry point: `python -m abtest.worker` (PRD §15).

A single loop runs two jobs: compute_results every RESULTS_INTERVAL_SECONDS (default 300),
and maintain_partitions daily. Each run opens a fresh connection, so a database restart
only costs the runs that happen while it's down. SIGTERM (what `docker compose stop`
sends) stops the loop between runs.
"""

import logging
import signal
import threading
import time
from collections.abc import Callable
from types import FrameType
from typing import Any

import psycopg

from abtest.config import Settings
from abtest.logs import configure_json_logging
from abtest.worker.jobs import compute_results, maintain_partitions

log = logging.getLogger("abtest.worker")

PARTITIONS_INTERVAL_SECONDS = 24 * 60 * 60


def run_job(
    name: str, job: Callable[[psycopg.Connection], dict[str, Any]], database_url: str
) -> None:
    """Run one job and log its outcome and duration. A failure is logged, never raised: the
    next run tries again."""
    started = time.perf_counter()
    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            outcome = job(conn)
    except Exception:
        log.exception("job failed", extra={"job": name})
        return
    duration_ms = round((time.perf_counter() - started) * 1000)
    log.info("job done", extra={"job": name, "duration_ms": duration_ms, **outcome})


def main() -> None:
    configure_json_logging()
    settings = Settings()  # values come from the environment
    stop = threading.Event()

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        # Only set the flag: signal handlers should do as little as possible.
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    schedule = [
        ("compute_results", compute_results, settings.results_interval_seconds),
        ("maintain_partitions", maintain_partitions, PARTITIONS_INTERVAL_SECONDS),
    ]
    next_run = {name: 0.0 for name, _, _ in schedule}  # everything is due at start
    log.info(
        "worker started", extra={"results_interval_seconds": settings.results_interval_seconds}
    )
    while not stop.is_set():
        for name, job, interval in schedule:
            if time.monotonic() >= next_run[name]:
                run_job(name, job, settings.database_url)
                next_run[name] = time.monotonic() + interval
        stop.wait(max(0.0, min(next_run.values()) - time.monotonic()))
    log.info("worker stopped")


if __name__ == "__main__":
    main()
