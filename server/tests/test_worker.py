"""The worker must shut down cleanly on SIGTERM (what `docker compose stop` sends)."""

import signal
import subprocess
import sys


def test_worker_exits_cleanly_on_sigterm() -> None:
    worker = subprocess.Popen(
        [sys.executable, "-m", "abtest.worker"],
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stderr is not None
        # Wait until the signal handlers are installed before sending the signal.
        assert "worker started" in worker.stderr.readline()

        worker.send_signal(signal.SIGTERM)
        _, stderr = worker.communicate(timeout=5)
    finally:
        worker.kill()  # no-op if it already exited; guarantees no stray process

    assert worker.returncode == 0
    assert "worker stopped" in stderr
