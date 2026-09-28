"""Worker entry point: `python -m abtest.worker`.

M0 skeleton: the process starts, idles, and exits cleanly on SIGTERM, which is what
`docker compose stop` sends. The scheduled jobs (results snapshots, partition maintenance)
arrive in M7.
"""

import logging
import signal
import threading
from types import FrameType

log = logging.getLogger("abtest.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        # Only set the flag: signal handlers should do as little as possible.
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    log.info("worker started")
    stop.wait()
    log.info("worker stopped")


if __name__ == "__main__":
    main()
