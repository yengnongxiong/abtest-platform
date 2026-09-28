"""The experiment lifecycle through the admin API (PRD §9, §11)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

HYPOTHESIS = (
    "If we enlarge the button, checkout conversion will increase because it's easier to find."
)


def design(**overrides: Any) -> dict[str, Any]:
    """A complete, startable experiment design."""
    body: dict[str, Any] = {
        "key": "checkout-button",
        "name": "Checkout button",
        "hypothesis": HYPOTHESIS,
        "traffic_bp": 5_000,
        "mde_relative": 0.05,
        "variants": [
            {"key": "control", "name": "Control", "weight_bp": 5000, "is_control": True},
            {"key": "big-button", "name": "Big button", "weight_bp": 5000},
        ],
        "metrics": [
            {"metric_key": "purchase", "role": "primary", "expected_baseline": 0.1},
            {"metric_key": "revenue", "role": "guardrail", "expected_baseline": 12.5},
        ],
    }
    return body | overrides


@pytest.fixture
def metrics(api: TestClient, admin: dict[str, str]) -> None:
    for key, kind in (("purchase", "conversion"), ("revenue", "mean")):
        body = {"key": key, "name": key.title(), "kind": kind, "event_name": key,
                "direction": "increase"}  # fmt: skip
        assert api.post("/admin/metrics", json=body, headers=admin).status_code == 201


@pytest.fixture
def running(api: TestClient, admin: dict[str, str], metrics: None) -> str:
    """The URL of a started experiment."""
    assert api.post("/admin/experiments", json=design(), headers=admin).status_code == 201
    assert api.post("/admin/experiments/checkout-button/start", headers=admin).status_code == 200
    return "/admin/experiments/checkout-button"


def test_create_a_draft(api: TestClient, admin: dict[str, str], metrics: None) -> None:
    response = api.post("/admin/experiments", json=design(), headers=admin)

    assert response.status_code == 201
    experiment = response.json()
    assert experiment["status"] == "draft"
    assert experiment["analysis_type"] == "sequential"  # the default
    assert [(v["key"], v["position"]) for v in experiment["variants"]] == [
        ("control", 0),
        ("big-button", 1),
    ]
    assert [(m["metric_key"], m["kind"], m["role"]) for m in experiment["metrics"]] == [
        ("purchase", "conversion", "primary"),
        ("revenue", "mean", "guardrail"),
    ]
    assert [c["action"] for c in experiment["changes"]] == ["created"]
    listed = api.get("/admin/experiments", headers=admin).json()
    assert [e["key"] for e in listed] == ["checkout-button"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"variants": design()["variants"] * 2}, "variant keys must be unique"),
        (
            {
                "variants": [
                    {"key": k, "name": k, "weight_bp": 5000, "is_control": True} for k in ("a", "b")
                ]
            },
            "at most one variant can be the control",
        ),
        (
            {"metrics": [{"metric_key": k, "role": "primary"} for k in ("purchase", "revenue")]},
            "at most one metric can be primary",
        ),
    ],
)
def test_clashing_variants_or_metrics_are_rejected(
    api: TestClient, admin: dict[str, str], metrics: None, change: dict[str, Any], message: str
) -> None:
    response = api.post("/admin/experiments", json=design(**change), headers=admin)

    assert response.status_code == 422
    assert message in str(response.json())


def test_an_unknown_metric_is_rejected(api: TestClient, admin: dict[str, str]) -> None:
    body = design(metrics=[{"metric_key": "nope", "role": "primary", "expected_baseline": 0.1}])

    response = api.post("/admin/experiments", json=body, headers=admin)

    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"metric_keys": ["nope"]}


def test_starting_an_incomplete_draft_lists_every_problem(
    api: TestClient, admin: dict[str, str]
) -> None:
    body = {"key": "empty", "name": "Empty", "traffic_bp": 10_000}
    api.post("/admin/experiments", json=body, headers=admin)

    response = api.post("/admin/experiments/empty/start", headers=admin)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "not_ready"
    assert response.json()["error"]["details"]["problems"] == [
        "write a hypothesis",
        "add at least 2 variants",
        "mark exactly one variant as the control",
        "variant weights must add up to 10000 basis points, not 0",
        "attach exactly one primary metric",
        "set the minimum detectable effect (mde_relative)",
    ]


def test_start(api: TestClient, admin: dict[str, str], running: str) -> None:
    experiment = api.get(running, headers=admin).json()

    assert experiment["status"] == "running"
    assert experiment["started_at"] is not None
    assert [c["action"] for c in experiment["changes"]] == ["created", "started"]


def test_a_running_experiment_cannot_start_again(
    api: TestClient, admin: dict[str, str], running: str
) -> None:
    response = api.post(f"{running}/start", headers=admin)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_a_draft"


@pytest.mark.parametrize(
    "change",
    [
        {"variants": design()["variants"][::-1]},
        {"metrics": []},
        {"alpha": 0.01},
        {"mde_relative": 0.2},
        {"analysis_type": "fixed_horizon"},
        {"hypothesis": "Something else"},
    ],
)
def test_a_running_experiments_design_is_frozen(
    api: TestClient, admin: dict[str, str], running: str, change: dict[str, Any]
) -> None:
    response = api.patch(running, json=change, headers=admin)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "experiment_locked"


def test_traffic_can_only_go_up_while_running(
    api: TestClient, admin: dict[str, str], running: str
) -> None:
    lower = api.patch(running, json={"traffic_bp": 4_000}, headers=admin)
    higher = api.patch(running, json={"traffic_bp": 8_000}, headers=admin)

    assert lower.status_code == 409
    assert lower.json()["error"]["code"] == "traffic_decrease"
    assert higher.status_code == 200
    assert higher.json()["traffic_bp"] == 8_000
    assert higher.json()["changes"][-1] | {"created_at": None} == {
        "action": "traffic_changed",
        "details": {"from": 5_000, "to": 8_000},
        "created_at": None,
    }


def test_a_running_experiment_can_be_renamed(
    api: TestClient, admin: dict[str, str], running: str
) -> None:
    response = api.patch(running, json={"name": "Bigger checkout button"}, headers=admin)

    assert response.status_code == 200
    assert response.json()["name"] == "Bigger checkout button"


def test_stopping_is_final(api: TestClient, admin: dict[str, str], running: str) -> None:
    stopped = api.post(f"{running}/stop", json={"reason": "Reached sample size"}, headers=admin)

    assert stopped.status_code == 200
    assert (stopped.json()["status"], stopped.json()["stop_reason"]) == (
        "stopped",
        "Reached sample size",
    )
    assert stopped.json()["changes"][-1]["details"] == {"reason": "Reached sample size"}
    for response in (
        api.post(f"{running}/stop", json={"reason": "Again"}, headers=admin),
        api.post(f"{running}/start", headers=admin),
        api.patch(running, json={"traffic_bp": 10_000}, headers=admin),
    ):
        assert response.status_code == 409
    assert api.patch(running, json={"name": "Old test"}, headers=admin).status_code == 200


def test_only_a_running_experiment_can_stop(
    api: TestClient, admin: dict[str, str], metrics: None
) -> None:
    api.post("/admin/experiments", json=design(), headers=admin)

    response = api.post(
        "/admin/experiments/checkout-button/stop", json={"reason": "x"}, headers=admin
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_running"


def test_clone_reruns_a_stopped_experiment_as_a_draft(
    api: TestClient, admin: dict[str, str], running: str
) -> None:
    api.post(f"{running}/stop", json={"reason": "Bug found"}, headers=admin)

    clone = api.post(f"{running}/clone", json={"new_key": "checkout-button-2"}, headers=admin)

    assert clone.status_code == 201
    original = api.get(running, headers=admin).json()
    design_fields = ("name", "hypothesis", "traffic_bp", "analysis_type", "alpha",
                     "mde_relative", "variants", "metrics")  # fmt: skip
    assert {f: clone.json()[f] for f in design_fields} == {f: original[f] for f in design_fields}
    assert clone.json()["status"] == "draft"
    assert clone.json()["changes"][0]["details"] == {"from": "checkout-button"}
    taken = api.post(f"{running}/clone", json={"new_key": "checkout-button-2"}, headers=admin)
    assert taken.status_code == 409


def test_a_draft_can_replace_its_variants(
    api: TestClient, admin: dict[str, str], metrics: None
) -> None:
    created = api.post("/admin/experiments", json=design(), headers=admin).json()
    three_way = [
        {"key": "a", "name": "A", "weight_bp": 3300, "is_control": True},
        {"key": "b", "name": "B", "weight_bp": 3300},
        {"key": "c", "name": "C", "weight_bp": 3400},
    ]

    response = api.patch(
        "/admin/experiments/checkout-button", json={"variants": three_way}, headers=admin
    )

    assert [(v["key"], v["position"]) for v in response.json()["variants"]] == [
        ("a", 0),
        ("b", 1),
        ("c", 2),
    ]
    assert response.json()["updated_at"] > created["updated_at"]


def test_a_conversion_baseline_must_be_a_rate(
    api: TestClient, admin: dict[str, str], metrics: None
) -> None:
    body = design(metrics=[{"metric_key": "purchase", "role": "primary", "expected_baseline": 1.5}])
    api.post("/admin/experiments", json=body, headers=admin)

    response = api.post("/admin/experiments/checkout-button/start", headers=admin)

    assert response.json()["error"]["details"]["problems"] == [
        "metric 'purchase' is a conversion rate, so its expected baseline must be below 1"
    ]


def test_unknown_experiments_are_404(api: TestClient, admin: dict[str, str]) -> None:
    for response in (
        api.get("/admin/experiments/nope", headers=admin),
        api.post("/admin/experiments/nope/start", headers=admin),
        api.post("/admin/experiments/nope/clone", json={"new_key": "x"}, headers=admin),
    ):
        assert response.status_code == 404


def test_sample_size(api: TestClient, admin: dict[str, str]) -> None:
    response = api.get(
        "/admin/sample-size", params={"baseline": 0.1, "mde_relative": 0.1}, headers=admin
    )
    impossible = api.get(
        "/admin/sample-size", params={"baseline": 0.6, "mde_relative": 1.0}, headers=admin
    )

    assert response.json() == {"users_per_variant": 14_751}  # alpha 0.05, power 0.8
    assert impossible.status_code == 422  # 0.6 x 2 is not a rate
