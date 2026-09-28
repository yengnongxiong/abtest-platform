"""The traffic generator, end to end against the in-process API (PRD §16B)."""

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from abtest.simulator.traffic import Scenario, run, summary

SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"


def small_scenario(drop_rate: float | None = None) -> Scenario:
    """400 users, a large effect (10% -> 50%), optionally losing some big-button exposures."""
    bug = None if drop_rate is None else {"variant": "big-button", "drop_rate": drop_rate}
    return Scenario.model_validate(
        {
            "seed": 1,
            "users": 400,
            "arrival_rate": 1_000_000,
            "metric": {"key": "purchase", "name": "Purchase", "event_name": "purchase"},
            "experiment": {
                "key": "sim",
                "name": "Simulated",
                "hypothesis": "If we do it, purchases will increase because it is better.",
                "traffic_bp": 10_000,
                "analysis_type": "fixed_horizon",
                "mde_relative": 0.1,
                "expected_baseline": 0.1,
                "variants": [
                    {"key": "control", "name": "C", "weight_bp": 5000, "is_control": True,
                     "conversion_rate": 0.1},
                    {"key": "big-button", "name": "B", "weight_bp": 5000, "conversion_rate": 0.5},
                ],
            },
            "srm_bug": bug,
        }
    )  # fmt: skip


def clients(api: TestClient, keys: tuple[UUID, str, str]) -> tuple[TestClient, TestClient]:
    """Two clients on the already-running app: one with the server key, one with the client key."""
    admin = TestClient(api.app, headers={"Authorization": f"Bearer {keys[1]}"})
    public = TestClient(api.app, headers={"X-Client-Key": keys[2]})
    return admin, public


@pytest.mark.parametrize("name", ["checkout_button", "srm_bug"])
def test_the_shipped_scenarios_load(name: str) -> None:
    scenario = Scenario.load(SCENARIOS / f"{name}.yaml")

    rates = [v.conversion_rate for v in scenario.experiment.variants]
    assert rates == [0.10, 0.108]  # the true +8% lift
    assert (scenario.srm_bug is not None) == (name == "srm_bug")


def test_an_srm_bug_must_name_a_variant() -> None:
    with pytest.raises(ValidationError, match=r"srm_bug\.variant"):
        Scenario.model_validate(
            small_scenario().model_dump() | {"srm_bug": {"variant": "nope", "drop_rate": 0.1}}
        )


def test_simulated_users_flow_through_the_api(
    api: TestClient, new_project: tuple[UUID, str, str]
) -> None:
    results: dict[str, Any] = run(small_scenario(), *clients(api, new_project))

    data = results["latest"]["data"]
    assert data["users"] == 400
    control, treatment = data["variants"]
    assert treatment["conversions"] > control["conversions"]  # 50% vs 10%
    assert data["comparisons"][0]["verdict"] == "significant_win"
    report = summary(small_scenario(), results)
    assert "true lift +400.0%" in report


def test_lost_exposures_are_caught_by_the_srm_check(
    api: TestClient, new_project: tuple[UUID, str, str]
) -> None:
    results = run(small_scenario(drop_rate=0.5), *clients(api, new_project))

    data = results["latest"]["data"]
    assert data["srm"]["flagged"]
    assert data["comparisons"][0]["verdict"] == "srm_untrustworthy"
