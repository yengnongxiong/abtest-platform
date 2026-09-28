"""Who may call what, and the error format every failure uses (PRD §11, §19)."""

from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient


def assert_error(response_json: object, code: str) -> None:
    assert isinstance(response_json, dict)
    assert set(response_json) == {"error"}
    assert set(response_json["error"]) == {"code", "message", "details"}
    assert response_json["error"]["code"] == code


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer sk_not_a_real_key_000000000000000000"},
        {"Authorization": "Basic c2s6"},
        {"Authorization": "Bearer"},
    ],
)
def test_admin_needs_a_valid_server_key(api: TestClient, headers: dict[str, str]) -> None:
    response = api.get("/admin/flags", headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert_error(response.json(), "unauthorized")


def test_a_client_key_never_opens_the_admin_api(
    api: TestClient, new_project: tuple[UUID, str, str]
) -> None:
    _, _, client_key = new_project

    response = api.get("/admin/flags", headers={"Authorization": f"Bearer {client_key}"})

    assert response.status_code == 401


@pytest.mark.parametrize("where", ["header", "query"])
def test_a_server_key_never_works_as_a_client_key(
    api: TestClient, new_project: tuple[UUID, str, str], where: str
) -> None:
    # Server keys must stay out of browsers and URLs (PRD §19).
    _, server_key, _ = new_project
    if where == "header":
        response = api.get("/v1/config", headers={"X-Client-Key": server_key})
    else:
        response = api.get("/v1/config", params={"client_key": server_key})

    assert response.status_code == 401


def test_a_revoked_key_is_refused(
    api: TestClient,
    admin: dict[str, str],
    new_project: tuple[UUID, str, str],
    api_database: str,
) -> None:
    project, _, _ = new_project
    with psycopg.connect(api_database, autocommit=True) as conn:
        conn.execute(
            "UPDATE api_keys SET revoked_at = now() WHERE project_id = %s AND kind = 'server'",
            (project,),
        )

    assert api.get("/admin/flags", headers=admin).status_code == 401


def test_projects_cannot_see_each_other(
    api: TestClient, admin: dict[str, str], api_database: str
) -> None:
    api.post("/admin/flags", json={"key": "private"}, headers=admin)
    other_key = "sk_" + "o" * 40
    with psycopg.connect(api_database, autocommit=True) as conn:
        row = conn.execute("INSERT INTO projects (name) VALUES ('Other') RETURNING id").fetchone()
        assert row is not None
        conn.execute(
            "INSERT INTO api_keys (project_id, kind, key_prefix, key_hash)"
            " VALUES (%s, 'server', 'sk_ooo', sha256(%s::bytea))",
            (row[0], other_key.encode()),
        )
    other = {"Authorization": f"Bearer {other_key}"}

    assert api.get("/admin/flags", headers=other).json() == []
    assert api.get("/admin/flags/private", headers=other).status_code == 404


def test_unknown_routes_use_the_error_format(api: TestClient) -> None:
    response = api.get("/no/such/route")

    assert response.status_code == 404
    assert_error(response.json(), "not_found")


def test_wrong_methods_use_the_error_format(api: TestClient, admin: dict[str, str]) -> None:
    response = api.put("/admin/flags", headers=admin)

    assert response.status_code == 405
    assert_error(response.json(), "method_not_allowed")


def test_invalid_bodies_list_every_problem(api: TestClient, admin: dict[str, str]) -> None:
    response = api.post(
        "/admin/flags", json={"key": "Bad:Key", "rollout_bp": 20_000}, headers=admin
    )

    assert response.status_code == 422
    assert_error(response.json(), "validation_error")
    locations = {tuple(problem["loc"]) for problem in response.json()["error"]["details"]}
    assert locations == {("body", "key"), ("body", "rollout_bp")}


def test_unknown_fields_are_rejected_not_ignored(api: TestClient, admin: dict[str, str]) -> None:
    # A typo like "rollout" for "rollout_bp" must not silently leave the flag at 0%.
    response = api.post("/admin/flags", json={"key": "typo", "rollout": 5000}, headers=admin)

    assert response.status_code == 422
