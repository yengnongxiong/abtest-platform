"""GET /v1/config (ETag, 304, CORS, versioning) and API key management (PRD §11)."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

METRIC = {"key": "m", "name": "M", "kind": "conversion", "event_name": "e", "direction": "increase"}
TWO_VARIANTS = [
    {"key": "control", "name": "Control", "weight_bp": 5000, "is_control": True},
    {"key": "treatment", "name": "Treatment", "weight_bp": 5000},
]


def start_experiment(api: TestClient, admin: dict[str, str], key: str = "exp") -> None:
    api.post("/admin/metrics", json=METRIC, headers=admin)
    body = {
        "key": key,
        "name": "Experiment",
        "hypothesis": "If we change it, conversion will increase because reasons.",
        "traffic_bp": 5_000,
        "mde_relative": 0.1,
        "variants": TWO_VARIANTS,
        "metrics": [{"metric_key": "m", "role": "primary", "expected_baseline": 0.1}],
    }
    assert api.post("/admin/experiments", json=body, headers=admin).status_code == 201
    assert api.post(f"/admin/experiments/{key}/start", headers=admin).status_code == 200


def version(api: TestClient, sdk: dict[str, str]) -> int:
    config: dict[str, Any] = api.get("/v1/config", headers=sdk).json()
    return int(config["config_version"])


def test_config_holds_running_experiments_and_all_flags(
    api: TestClient, admin: dict[str, str], sdk: dict[str, str]
) -> None:
    api.post("/admin/flags", json={"key": "dark-mode", "enabled": True, "rollout_bp": 2500},
             headers=admin)  # fmt: skip
    api.post("/admin/experiments", json={"key": "draft-only", "name": "Draft", "traffic_bp": 1},
             headers=admin)  # fmt: skip
    start_experiment(api, admin)

    config = api.get("/v1/config", headers=sdk).json()

    assert config["flags"] == [{"key": "dark-mode", "enabled": True, "rollout_bp": 2500}]
    assert config["experiments"] == [
        {
            "key": "exp",
            "traffic_bp": 5000,
            "variants": [
                {"key": "control", "weight_bp": 5000, "position": 0},
                {"key": "treatment", "weight_bp": 5000, "position": 1},
            ],
        }
    ]


def test_etag_and_not_modified(api: TestClient, sdk: dict[str, str]) -> None:
    first = api.get("/v1/config", headers=sdk)
    etag = first.headers["ETag"]

    assert etag == f'"config-{first.json()["config_version"]}"'
    assert first.headers["Cache-Control"] == "max-age=30"
    for if_none_match in (etag, f"W/{etag}", f'"config-0", {etag}', "*"):
        again = api.get("/v1/config", headers={**sdk, "If-None-Match": if_none_match})
        assert again.status_code == 304, if_none_match
        assert again.content == b""
        assert again.headers["ETag"] == etag
    stale = api.get("/v1/config", headers={**sdk, "If-None-Match": '"config-0"'})
    assert stale.status_code == 200


def test_the_client_key_can_also_come_from_the_query_string(
    api: TestClient, new_project: tuple[Any, str, str]
) -> None:
    # navigator.sendBeacon can't set headers (PRD §12).
    assert api.get("/v1/config", params={"client_key": new_project[2]}).status_code == 200


CONFIG_CHANGES: dict[str, Callable[[TestClient, dict[str, str]], Any]] = {
    "create flag": lambda api, h: api.post("/admin/flags", json={"key": "f"}, headers=h),
    "update flag": lambda api, h: api.patch(
        "/admin/flags/existing", json={"enabled": True}, headers=h
    ),
    "delete flag": lambda api, h: api.delete("/admin/flags/existing", headers=h),
    "create experiment": lambda api, h: api.post(
        "/admin/experiments", json={"key": "new", "name": "New", "traffic_bp": 0}, headers=h
    ),
    "raise traffic": lambda api, h: api.patch(
        "/admin/experiments/exp", json={"traffic_bp": 9000}, headers=h
    ),
    "stop experiment": lambda api, h: api.post(
        "/admin/experiments/exp/stop", json={"reason": "done"}, headers=h
    ),
    "clone experiment": lambda api, h: api.post(
        "/admin/experiments/exp/clone", json={"new_key": "exp-2"}, headers=h
    ),
}


@pytest.mark.parametrize("change", CONFIG_CHANGES)
def test_every_config_change_bumps_the_version(
    api: TestClient, admin: dict[str, str], sdk: dict[str, str], change: str
) -> None:
    api.post("/admin/flags", json={"key": "existing"}, headers=admin)
    start_experiment(api, admin)
    before = version(api, sdk)

    response = CONFIG_CHANGES[change](api, admin)

    assert response.status_code < 300, response.json()
    assert version(api, sdk) == before + 1


def test_starting_bumps_the_version(
    api: TestClient, admin: dict[str, str], sdk: dict[str, str]
) -> None:
    start_experiment(api, admin, key="first")
    clone = api.post("/admin/experiments/first/clone", json={"new_key": "second"}, headers=admin)
    assert clone.status_code == 201
    before = version(api, sdk)

    api.post("/admin/experiments/second/start", headers=admin)

    assert version(api, sdk) == before + 1


def test_metric_changes_leave_the_config_alone(
    api: TestClient, admin: dict[str, str], sdk: dict[str, str]
) -> None:
    before = version(api, sdk)

    api.post("/admin/metrics", json={"key": "m", "name": "M", "kind": "mean", "event_name": "e",
                                    "direction": "increase"}, headers=admin)  # fmt: skip

    assert version(api, sdk) == before


def test_cors_is_open_on_the_public_api(api: TestClient, sdk: dict[str, str]) -> None:
    preflight = api.options(
        "/v1/config",
        headers={
            "Origin": "https://shop.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Client-Key, If-None-Match",
        },
    )
    response = api.get("/v1/config", headers={**sdk, "Origin": "https://shop.example"})

    assert preflight.status_code == 200
    assert preflight.headers["Access-Control-Allow-Origin"] == "*"
    assert preflight.headers["Access-Control-Max-Age"] == "7200"
    allowed = preflight.headers["Access-Control-Allow-Headers"].lower()
    assert "x-client-key" in allowed and "if-none-match" in allowed
    assert "Access-Control-Allow-Credentials" not in preflight.headers
    assert set(response.headers["Access-Control-Expose-Headers"].split(", ")) == {
        "ETag",
        "Retry-After",
    }


def test_cors_is_closed_on_the_admin_api(api: TestClient, admin: dict[str, str]) -> None:
    response = api.get("/admin/flags", headers={**admin, "Origin": "https://evil.example"})

    assert "Access-Control-Allow-Origin" not in response.headers


def test_a_new_api_key_is_shown_once_and_works(api: TestClient, admin: dict[str, str]) -> None:
    created = api.post("/admin/api-keys", json={"kind": "client"}, headers=admin)

    assert created.status_code == 201
    key = created.json()["key"]
    assert key.startswith("ck_") and created.json()["key_prefix"] == key[:11]
    assert api.get("/v1/config", headers={"X-Client-Key": key}).status_code == 200
    listed = api.get("/admin/api-keys", headers=admin).json()
    assert all("key" not in k for k in listed)  # never shown again
    assert key not in str(listed)


def test_a_revoked_key_stops_working(api: TestClient, admin: dict[str, str]) -> None:
    created = api.post("/admin/api-keys", json={"kind": "client"}, headers=admin).json()

    revoked = api.post(f"/admin/api-keys/{created['id']}/revoke", headers=admin)

    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    assert api.get("/v1/config", headers={"X-Client-Key": created["key"]}).status_code == 401


def test_the_last_server_key_cannot_be_revoked(api: TestClient, admin: dict[str, str]) -> None:
    keys = api.get("/admin/api-keys", headers=admin).json()
    server = next(k for k in keys if k["kind"] == "server")

    refused = api.post(f"/admin/api-keys/{server['id']}/revoke", headers=admin)

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "last_server_key"
    # With a second server key, the first one can go.
    api.post("/admin/api-keys", json={"kind": "server"}, headers=admin)
    assert api.post(f"/admin/api-keys/{server['id']}/revoke", headers=admin).status_code == 200


def test_revoking_an_unknown_key_is_404(api: TestClient, admin: dict[str, str]) -> None:
    response = api.post(
        "/admin/api-keys/00000000-0000-0000-0000-000000000000/revoke", headers=admin
    )

    assert response.status_code == 404
