"""The worker process shuts down cleanly on SIGTERM (what `docker compose stop` sends)."""

import os
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import psycopg

from abtest.db.migrate import migrate


def test_worker_exits_cleanly_on_sigterm(
    create_database: Callable[[], str], migrations_dir: Path
) -> None:
    # Its own database: the worker runs its jobs as soon as it starts.
    url = create_database()
    with psycopg.connect(url, autocommit=True) as conn:
        migrate(conn, migrations_dir)
    worker = subprocess.Popen(
        [sys.executable, "-m", "abtest.worker"],
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "DATABASE_URL": url},
    )
    try:
        assert worker.stderr is not None
        # Wait until the signal handlers are installed before sending the signal.
        assert "worker started" in worker.stderr.readline()

        worker.send_signal(signal.SIGTERM)
        _, stderr = worker.communicate(timeout=10)
    finally:
        worker.kill()  # no-op if it already exited; guarantees no stray process

    assert worker.returncode == 0
    assert "worker stopped" in stderr
    assert '"job": "compute_results"' in stderr  # its first run, logged as JSON
