"""GET .../results and POST .../recompute, end to end through the API (PRD §11)."""

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from abtest.assignment import WeightedVariant, assign

VARIANTS = [WeightedVariant("control", 5000, 0), WeightedVariant("treatment", 5000, 1)]


def setup_experiment(api: TestClient, admin: dict[str, str]) -> None:
    api.post("/admin/metrics", json={"key": "purchase", "name": "Purchase", "kind": "conversion",
                                    "event_name": "purchase", "direction": "increase"},
             headers=admin)  # fmt: skip
    body = {
        "key": "checkout",
        "name": "Checkout",
        "hypothesis": "If we change the button, purchases will increase because it's clearer.",
        "traffic_bp": 10_000,
        "mde_relative": 0.1,
        "variants": [
            {"key": "control", "name": "Control", "weight_bp": 5000, "is_control": True},
            {"key": "treatment", "name": "Treatment", "weight_bp": 5000},
        ],
        "metrics": [{"metric_key": "purchase", "role": "primary", "expected_baseline": 0.1}],
    }
    assert api.post("/admin/experiments", json=body, headers=admin).status_code == 201


def start(api: TestClient, admin: dict[str, str]) -> datetime:
    """Start the experiment; returns when it started, by the server's clock."""
    response = api.post("/admin/experiments/checkout/start", headers=admin)
    return datetime.fromisoformat(response.json()["started_at"])


def event(name: str, user: str, at: datetime, **fields: Any) -> dict[str, Any]:
    return {"event_id": str(uuid4()), "user_id": user, "name": name,
            "occurred_at": at.isoformat()} | fields  # fmt: skip


def send_traffic(
    api: TestClient, sdk: dict[str, str], started: datetime, users: int
) -> dict[str, int]:
    """Expose `users` users (as the SDK would) and have every third one buy. Returns the
    purchases per variant.

    Events are stamped just after the start by the server's clock, and so before the
    recompute that follows. The test's own clock can trail the database's (by 33 ms on the
    machine this was written on), and events that claim to predate the start are, by
    design, not attributed (ADR-016).
    """
    exposed_at = started + timedelta(milliseconds=1)
    bought_at = started + timedelta(milliseconds=2)
    events, bought = [], {"control": 0, "treatment": 0}
    for i in range(users):
        user = f"user-{i}"
        variant = assign("checkout", user, 10_000, VARIANTS)
        assert variant is not None
        properties = {"experiment_key": "checkout", "variant_key": variant}
        events.append(event("$exposure", user, exposed_at, properties=properties))
        if i % 3 == 0:
            events.append(event("purchase", user, bought_at))
            bought[variant] += 1
    response = api.post("/v1/events", json={"sdk": {"name": "t", "version": "1"}, "events": events},
                        headers=sdk)  # fmt: skip
    assert response.json()["rejected"] == []
    return bought


def test_recompute_then_read_the_results(
    api: TestClient, admin: dict[str, str], sdk: dict[str, str]
) -> None:
    setup_experiment(api, admin)
    bought = send_traffic(api, sdk, start(api, admin), users=200)

    assert api.post("/admin/experiments/checkout/recompute", headers=admin).json() == {
        "metric_keys": ["purchase"]
    }
    results = api.get("/admin/experiments/checkout/results", headers=admin).json()

    latest = results["latest"]["data"]
    assert results["metric_key"] == "purchase"  # the primary metric by default
    assert latest["users"] == 200
    assert {v["key"]: v["conversions"] for v in latest["variants"]} == bought
    assert latest["analysis_type"] == "sequential"
    assert latest["tau"] == 0.1 * 0.1
    [comparison] = latest["comparisons"]
    assert comparison["variant_key"] == "treatment"
    assert comparison["msprt_state"] is not None
    assert len(results["series"]) == 1


def test_every_recompute_adds_a_look_and_never_rewrites_one(
    api: TestClient, admin: dict[str, str], sdk: dict[str, str]
) -> None:
    setup_experiment(api, admin)
    send_traffic(api, sdk, start(api, admin), users=100)
    api.post("/admin/experiments/checkout/recompute", headers=admin)
    first = api.get("/admin/experiments/checkout/results", headers=admin).json()["series"]

    api.post("/admin/experiments/checkout/recompute", headers=admin)
    series = api.get("/admin/experiments/checkout/results", headers=admin).json()["series"]

    assert len(series) == 2
    assert series[0] == first[0]
    assert series[1]["computed_at"] > series[0]["computed_at"]


def test_a_draft_has_no_results(api: TestClient, admin: dict[str, str]) -> None:
    setup_experiment(api, admin)

    recompute = api.post("/admin/experiments/checkout/recompute", headers=admin)
    results = api.get("/admin/experiments/checkout/results", headers=admin)

    assert recompute.status_code == 409
    assert results.status_code == 404


def test_asking_for_a_metric_that_isnt_attached_is_404(
    api: TestClient, admin: dict[str, str]
) -> None:
    setup_experiment(api, admin)
    api.post("/admin/experiments/checkout/start", headers=admin)

    response = api.get(
        "/admin/experiments/checkout/results", params={"metric": "revenue"}, headers=admin
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "metric_not_found"


def test_before_any_snapshot_the_results_are_empty(api: TestClient, admin: dict[str, str]) -> None:
    setup_experiment(api, admin)
    api.post("/admin/experiments/checkout/start", headers=admin)

    results = api.get("/admin/experiments/checkout/results", headers=admin).json()

    assert results["latest"] is None
    assert results["series"] == []


def test_the_experiment_list_shows_the_latest_primary_results(
    api: TestClient, admin: dict[str, str], sdk: dict[str, str]
) -> None:
    setup_experiment(api, admin)
    assert api.get("/admin/experiments", headers=admin).json()[0]["latest_results"] is None
    send_traffic(api, sdk, start(api, admin), users=100)
    api.post("/admin/experiments/checkout/recompute", headers=admin)

    [listed] = api.get("/admin/experiments", headers=admin).json()

    assert listed["latest_results"]["users"] == 100
    assert listed["latest_results"]["srm_flagged"] is False
