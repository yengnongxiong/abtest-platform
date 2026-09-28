"""Load test of the public API (PRD §18): what SDKs send, as fast as the server takes it.

    make loadtest LOADTEST_USERS=32

Each simulated SDK sends batches of 50 events to POST /v1/events and polls GET /v1/config
with the ETag it last saw (a 304 when nothing changed): six batches per poll, the ratio of
the SDK's defaults (a flush every 5 s, a poll every 30 s). There is no wait between
requests, so the result is the server's throughput and latency at that many concurrent
senders, not a model of real traffic.
"""

import os
import random
from datetime import UTC, datetime
from typing import Any

from locust import FastHttpUser, events, task
from locust.env import Environment
from locust.stats import StatsEntry
from workload import batch_events

BATCH_SIZE = 50
SDK = {"name": "loadtest", "version": "0"}
CLIENT_KEY = os.environ["ABTEST_CLIENT_KEY"]
rng = random.Random()


class Sdk(FastHttpUser):
    etag = ""

    @task(6)
    def post_events(self) -> None:
        body = {"sdk": SDK, "events": batch_events(BATCH_SIZE, datetime.now(UTC), rng)}
        headers = {"X-Client-Key": CLIENT_KEY}
        with self.client.post(
            "/v1/events", json=body, headers=headers, catch_response=True
        ) as response:
            # A 202 alone isn't success: every event must have been stored.
            if response.status_code != 202 or response.json()["accepted"] != BATCH_SIZE:
                response.failure(f"{response.status_code}: {response.text}")

    @task(1)
    def get_config(self) -> None:
        headers = {"X-Client-Key": CLIENT_KEY, "If-None-Match": self.etag}
        with self.client.get("/v1/config", headers=headers, catch_response=True) as response:
            if response.status_code == 200:
                self.etag = response.headers["ETag"]
            elif response.status_code != 304:
                response.failure(f"{response.status_code}: {response.text}")


# Locust's EventHook.add_listener has no type hints.
@events.quitting.add_listener  # type: ignore[untyped-decorator]
def print_summary(environment: Environment, **_kwargs: Any) -> None:
    """One markdown table row for docs/performance.md, from Locust's own statistics."""
    post = environment.stats.get("/v1/events", "POST")
    get = environment.stats.get("/v1/config", "GET")
    total = environment.stats.total
    assert environment.parsed_options is not None

    def percentiles(entry: StatsEntry) -> str:
        return " / ".join(f"{entry.get_response_time_percentile(p):.0f}" for p in (0.5, 0.95, 0.99))

    stored_per_second = post.total_rps * (1 - post.fail_ratio) * BATCH_SIZE  # full batches only
    print("| Users | Events/s | POST p50 / p95 / p99 (ms) | GET p50 / p95 / p99 (ms) | Errors |")
    print("|---:|---:|---:|---:|---:|")
    print(
        f"| {environment.parsed_options.num_users} | {stored_per_second:,.0f}"
        f" | {percentiles(post)} | {percentiles(get)}"
        f" | {total.num_failures:,} of {total.num_requests:,} ({total.fail_ratio:.2%}) |"
    )
