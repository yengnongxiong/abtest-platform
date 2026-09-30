# server

The Python side of the platform, one [uv](https://docs.astral.sh/uv/) project: the FastAPI service (`api/`), the worker that computes results (`worker/`), the statistics engine (`stats/`, pure: no database, web, or I/O imports), the simulator (`simulator/`), and the database queries (`db/`). The tests in `tests/` run against a real Postgres, not mocks.

Read first:
- [src/abtest/stats/sequential.py](src/abtest/stats/sequential.py): the sequential test (mSPRT) that keeps results valid however often someone looks
- [src/abtest/assignment.py](src/abtest/assignment.py): how a user is assigned to a variant, identically to the SDK
- [src/abtest/db/results.py](src/abtest/db/results.py): the attribution query, which decides which events count for whom
