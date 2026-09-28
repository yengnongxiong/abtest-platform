"""The simulated workload shared by the load-test scripts (PRD §18).

One definition of the event mix and the experiments, so the seeded table (seed_events.py)
and the live load test (locustfile.py) look alike.
"""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from abtest.assignment import WeightedVariant, assign

PERF_DATABASE = "abtest_perf"  # kept apart from the dev database the dashboard reads

VARIANTS = [WeightedVariant("control", 5000, 0), WeightedVariant("treatment", 5000, 1)]
METRIC_EVENT = "purchase"  # what every experiment's metrics count


@dataclass(frozen=True, slots=True)
class Experiment:
    key: str
    traffic_bp: int
    started_ago: timedelta


# A large experiment (every user) and a small one (2% of users): the attribution query
# needs different plans for the two (docs/performance.md).
LARGE = Experiment("checkout-button", 10_000, timedelta(days=3))
SMALL = Experiment("free-shipping-banner", 200, timedelta(days=1))
EXPERIMENTS = [LARGE, SMALL]

# Each name's share of the events that aren't exposures: a store's funnel, where most events
# are page views and few are purchases.
EVENT_SHARES = {
    "page_view": 0.55,
    "search": 0.17,
    "add_to_cart": 0.15,
    "signup": 0.05,
    "purchase": 0.08,
}
EXPOSURES_PER_BATCH = 2  # of 50 events: about their share in the seeded table


def variant_for(experiment: Experiment, user_id: str) -> str | None:
    """The real assignment code's answer, so the server records no mismatch."""
    return assign(experiment.key, user_id, experiment.traffic_bp, VARIANTS)


def batch_events(size: int, occurred_at: datetime, rng: random.Random) -> list[dict[str, object]]:
    """The events of one POST /v1/events batch: EXPOSURES_PER_BATCH exposures, then events
    named at random in EVENT_SHARES' proportions."""
    names = ["$exposure"] * EXPOSURES_PER_BATCH + rng.choices(
        list(EVENT_SHARES), weights=list(EVENT_SHARES.values()), k=size - EXPOSURES_PER_BATCH
    )
    return [event_json(name, occurred_at) for name in names]


def event_json(name: str, occurred_at: datetime) -> dict[str, object]:
    """One event from a new random user, as the SDK sends it. An "$exposure" is to the
    large experiment, which includes every user."""
    user_id = str(uuid4())
    event: dict[str, object] = {
        "event_id": str(uuid4()),
        "user_id": user_id,
        "name": name,
        "occurred_at": occurred_at.isoformat(),
    }
    if name == "$exposure":
        variant = variant_for(LARGE, user_id)
        event["properties"] = {"experiment_key": LARGE.key, "variant_key": variant}
    elif name == METRIC_EVENT:
        event["value"] = 49.0
    return event
