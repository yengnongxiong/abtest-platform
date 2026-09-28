"""Admin API for metrics and flags (PRD §11)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

PURCHASE = {
    "key": "purchase",
    "name": "Purchase",
    "kind": "conversion",
    "event_name": "purchase",
    "direction": "increase",
}


def started_experiment_using(api: TestClient, admin: dict[str, str], metric_key: str) -> None:
    body: dict[str, Any] = {
        "key": "uses-metric",
        "name": "Uses the metric",
        "hypothesis": "If we change it, purchases will increase because it's better.",
        "traffic_bp": 10_000,
        "mde_relative": 0.1,
        "variants": [
            {"key": "control", "name": "Control", "weight_bp": 5000, "is_control": True},
            {"key": "treatment", "name": "Treatment", "weight_bp": 5000},
        ],
        "metrics": [{"metric_key": metric_key, "role": "primary", "expected_baseline": 0.1}],
    }
    assert api.post("/admin/experiments", json=body, headers=admin).status_code == 201
    assert api.post("/admin/experiments/uses-metric/start", headers=admin).status_code == 200


def test_create_and_list_metrics(api: TestClient, admin: dict[str, str]) -> None:
    created = api.post("/admin/metrics", json=PURCHASE, headers=admin)

    assert created.status_code == 201
    assert created.json()["window_hours"] == 168  # the default: one week
    assert [m["key"] for m in api.get("/admin/metrics", headers=admin).json()] == ["purchase"]


def test_metric_keys_are_unique(api: TestClient, admin: dict[str, str]) -> None:
    api.post("/admin/metrics", json=PURCHASE, headers=admin)

    response = api.post("/admin/metrics", json=PURCHASE, headers=admin)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "metric_exists"


def test_a_metric_can_be_redefined_until_a_started_experiment_uses_it(
    api: TestClient, admin: dict[str, str]
) -> None:
    api.post("/admin/metrics", json=PURCHASE, headers=admin)
    assert (
        api.patch("/admin/metrics/purchase", json={"window_hours": 24}, headers=admin).json()[
            "window_hours"
        ]
        == 24
    )

    started_experiment_using(api, admin, "purchase")
    redefine = api.patch("/admin/metrics/purchase", json={"event_name": "order"}, headers=admin)
    rename = api.patch("/admin/metrics/purchase", json={"name": "Orders"}, headers=admin)

    # Redefining would silently change results already shown; renaming is harmless.
    assert redefine.status_code == 409
    assert redefine.json()["error"]["code"] == "metric_in_use"
    assert rename.status_code == 200
    assert rename.json()["name"] == "Orders"


def test_patching_an_unknown_metric_is_404(api: TestClient, admin: dict[str, str]) -> None:
    assert api.patch("/admin/metrics/nope", json={"name": "X"}, headers=admin).status_code == 404


def test_flag_crud(api: TestClient, admin: dict[str, str]) -> None:
    created = api.post("/admin/flags", json={"key": "dark-mode"}, headers=admin)
    assert created.status_code == 201
    assert created.json() | {"created_at": None, "updated_at": None} == {
        "key": "dark-mode",
        "description": "",
        "enabled": False,
        "rollout_bp": 0,
        "created_at": None,
        "updated_at": None,
    }

    updated = api.patch(
        "/admin/flags/dark-mode", json={"enabled": True, "rollout_bp": 2500}, headers=admin
    ).json()
    assert (updated["enabled"], updated["rollout_bp"]) == (True, 2500)
    assert updated["updated_at"] > updated["created_at"]
    assert api.get("/admin/flags/dark-mode", headers=admin).json() == updated

    assert api.delete("/admin/flags/dark-mode", headers=admin).status_code == 204
    assert api.get("/admin/flags/dark-mode", headers=admin).status_code == 404
    assert api.delete("/admin/flags/dark-mode", headers=admin).status_code == 404


def test_flag_keys_are_unique(api: TestClient, admin: dict[str, str]) -> None:
    api.post("/admin/flags", json={"key": "dark-mode"}, headers=admin)

    assert api.post("/admin/flags", json={"key": "dark-mode"}, headers=admin).status_code == 409


@pytest.mark.parametrize("rollout_bp", [-1, 10_001])
def test_rollout_is_in_basis_points(
    api: TestClient, admin: dict[str, str], rollout_bp: int
) -> None:
    response = api.post("/admin/flags", json={"key": "f", "rollout_bp": rollout_bp}, headers=admin)

    assert response.status_code == 422
