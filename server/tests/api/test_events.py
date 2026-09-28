"""POST /v1/events: validation, duplicates, exposures, limits (PRD §10, §11)."""

import json
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from workload import LARGE, batch_events

from abtest.api.app import create_app
from abtest.assignment import WeightedVariant, assign, in_experiment
from abtest.config import Settings

SDK = {"name": "abtest-sdk-js", "version": "0.1.0"}
VARIANTS = [WeightedVariant("control", 5000, 0), WeightedVariant("treatment", 5000, 1)]


def event(name: str = "purchase", user: str = "user-1", **fields: Any) -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "user_id": user,
        "name": name,
        "occurred_at": datetime.now(UTC).isoformat(),
    } | fields


def exposure(
    user: str, variant: str, experiment: str = "checkout", **fields: Any
) -> dict[str, Any]:
    properties = {"experiment_key": experiment, "variant_key": variant}
    return event("$exposure", user, properties=properties, **fields)


def send(api: TestClient, sdk: dict[str, str], events: list[Any]) -> dict[str, Any]:
    # ASCII-escaped JSON, as JavaScript's JSON.stringify writes it: a lone surrogate travels
    # as the escape "\ud800" (raw UTF-8 can't carry it).
    response = api.post(
        "/v1/events",
        content=json.dumps({"sdk": SDK, "events": events}),
        headers={**sdk, "Content-Type": "application/json"},
    )
    assert response.status_code == 202, response.text
    result: dict[str, Any] = response.json()
    return result


def start(
    api: TestClient, admin: dict[str, str], key: str = "checkout", traffic: int = 10_000
) -> None:
    api.post("/admin/metrics", json={"key": "m", "name": "M", "kind": "conversion",
                                    "event_name": "purchase", "direction": "increase"},
             headers=admin)  # fmt: skip
    body = {
        "key": key,
        "name": key,
        "hypothesis": "If we change it, purchases will increase because it's clearer.",
        "traffic_bp": traffic,
        "mde_relative": 0.1,
        "variants": [
            {"key": "control", "name": "Control", "weight_bp": 5000, "is_control": True},
            {"key": "treatment", "name": "Treatment", "weight_bp": 5000},
        ],
        "metrics": [{"metric_key": "m", "role": "primary", "expected_baseline": 0.1}],
    }
    assert api.post("/admin/experiments", json=body, headers=admin).status_code == 201
    assert api.post(f"/admin/experiments/{key}/start", headers=admin).status_code == 200


def exposures(database: str, project: UUID) -> list[tuple[str, str, datetime, bool, bool]]:
    with psycopg.connect(database) as conn:
        rows = conn.execute(
            "SELECT x.user_id, v.key, x.first_exposed_at, x.conflicted, x.assignment_mismatch"
            " FROM exposures x JOIN variants v ON v.id = x.variant_id"
            " WHERE x.project_id = %s ORDER BY x.user_id",
            (project,),
        ).fetchall()
    return [(u, v, t, c, m) for u, v, t, c, m in rows]


def stored_events(database: str, project: UUID) -> int:
    with psycopg.connect(database) as conn:
        row = conn.execute(
            "SELECT count(*) FROM events WHERE project_id = %s", (project,)
        ).fetchone()
    assert row is not None
    count: int = row[0]
    return count


def test_a_batch_is_stored(
    api: TestClient, sdk: dict[str, str], new_project: tuple[UUID, str, str], api_database: str
) -> None:
    result = send(api, sdk, [event(value=49.5, properties={"plan": "pro"}), event(), event()])

    assert result == {"accepted": 3, "duplicates": 0, "rejected": []}
    assert stored_events(api_database, new_project[0]) == 3


def test_a_retried_batch_is_counted_not_stored_again(
    api: TestClient, sdk: dict[str, str], new_project: tuple[UUID, str, str], api_database: str
) -> None:
    batch = [event(), event()]
    send(api, sdk, batch)

    assert send(api, sdk, batch) == {"accepted": 0, "duplicates": 2, "rejected": []}
    assert stored_events(api_database, new_project[0]) == 2


def test_the_same_event_twice_in_one_batch_is_a_duplicate(
    api: TestClient, sdk: dict[str, str]
) -> None:
    same = event()

    assert send(api, sdk, [same, same]) == {"accepted": 1, "duplicates": 1, "rejected": []}


def hours_ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (event(event_id="not-a-uuid"), "event_id"),
        (event(name="has space"), "name"),
        (event(name=""), "name"),
        (event(user=""), "user_id: must be 1-200 characters"),
        (event(user="x" * 201), "user_id: must be 1-200 characters"),
        (event(user="user-\ud800"), "user_id: must be valid Unicode"),
        (event(user="user-\x00"), "user_id: must not contain NUL"),
        (event(occurred_at="2026-09-28T12:00:00"), "occurred_at: Input should have timezone"),
        (event(occurred_at=hours_ago(24 * 8)), "occurred_at is more than 7 days ago"),
        (event(occurred_at=hours_ago(-0.2)), "occurred_at is more than 5 minutes in the future"),
        (event(value="12"), "value"),
        (event(value=True), "value"),
        # Regression: 1e200 was stored, and squaring it overflowed the attribution query,
        # so every later look at the experiment failed.
        (event(value=1e200), "value: Input should be less than or equal to"),
        (event(value=-1e13), "value: Input should be greater than or equal to"),
        (event(properties=["a"]), "properties"),
        (event(properties={"text": "x" * 5000}), "properties: must be at most 4096 bytes"),
        (event(properties={"text": "a\x00b"}), "properties: must not contain NUL"),
        (event(userId="typo"), "userId: Extra inputs are not permitted"),
        ("not an object", "Input should be a valid dictionary"),
        (event("$exposure", properties={"variant_key": "control"}), "properties.experiment_key"),
        (exposure("user-1", "control", experiment="nope"), "no running experiment with key 'nope'"),
        (exposure("user-1", "blue"), "experiment 'checkout' has no variant 'blue'"),
    ],
)
def test_bad_events_are_rejected_with_a_reason_and_the_rest_accepted(
    api: TestClient, admin: dict[str, str], sdk: dict[str, str], bad: Any, reason: str
) -> None:
    start(api, admin)

    result = send(api, sdk, [bad, event()])

    assert result["accepted"] == 1
    assert len(result["rejected"]) == 1
    assert result["rejected"][0]["index"] == 0
    assert reason in result["rejected"][0]["reason"]


def test_values_up_to_a_trillion_either_way_are_accepted(
    api: TestClient, sdk: dict[str, str]
) -> None:
    events = [event(value=1e12), event(value=-1e12), event(value=49.99)]

    assert send(api, sdk, events) == {"accepted": 3, "duplicates": 0, "rejected": []}


def test_a_text_plain_body_with_the_key_in_the_url_is_accepted(
    api: TestClient, new_project: tuple[UUID, str, str]
) -> None:
    # The SDK's beacon: text/plain avoids a CORS preflight; beacons can't set headers.
    response = api.post(
        "/v1/events",
        params={"client_key": new_project[2]},
        content=json.dumps({"sdk": SDK, "events": [event()]}),
        headers={"Content-Type": "text/plain;charset=UTF-8"},
    )

    assert response.status_code == 202
    assert response.json()["accepted"] == 1


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        (b"{not json", 400, "invalid_json"),
        (b'{"sdk": {"name": "x", "version": "1"}, "events": [NaN]}', 400, "invalid_json"),
        (b"\xff\xfe\xfa", 400, "invalid_json"),
        (json.dumps({"events": []}).encode(), 422, "invalid_batch"),
        (json.dumps({"sdk": SDK, "events": [event()] * 501}).encode(), 422, "invalid_batch"),
        # Regression: 1e400 parses as infinity, and echoing it back in the 422 made a 500.
        (b'{"sdk": 1e400, "events": []}', 422, "invalid_batch"),
        (b" " * 1_000_001, 413, "body_too_large"),
    ],
)
def test_a_body_that_cant_be_read_is_refused_whole(
    api: TestClient, sdk: dict[str, str], body: bytes, status: int, code: str
) -> None:
    response = api.post("/v1/events", content=body, headers=sdk)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_the_size_cap_holds_without_a_content_length(api: TestClient, sdk: dict[str, str]) -> None:
    # A chunked upload declares no length: the cap has to be enforced while reading.
    chunks = iter([b" " * 600_000, b" " * 600_000])

    response = api.post("/v1/events", content=chunks, headers=sdk)

    assert response.status_code == 413


def test_the_body_is_read_before_a_database_connection_is_taken(
    api_database: str, sdk: dict[str, str]
) -> None:
    # Regression: the endpoint took a pooled connection (to check the key) before reading the
    # body. Four clients that stalled mid-upload held all four connections, and the whole API,
    # /health included, stopped answering for as long as they kept their sockets open.
    app = create_app(Settings(database_url=api_database))
    in_use_mid_upload: list[int] = []
    with TestClient(app) as client:
        pool = app.state.pool
        pool.wait()  # every connection open, so the count below is only what's in use

        def slow_body() -> Iterator[bytes]:
            yield b'{"sdk": {"name": "t", "version": "1"}, '
            stats = pool.get_stats()
            in_use_mid_upload.append(stats["pool_size"] - stats["pool_available"])
            yield b'"events": []}'

        response = client.post("/v1/events", content=slow_body(), headers=sdk)

    assert response.status_code == 202
    assert in_use_mid_upload == [0]


def test_events_need_a_client_key(api: TestClient) -> None:
    assert api.post("/v1/events", json={"sdk": SDK, "events": []}).status_code == 401


def test_an_exposure_is_recorded_with_its_time_and_variant(
    api: TestClient,
    admin: dict[str, str],
    sdk: dict[str, str],
    new_project: tuple[UUID, str, str],
    api_database: str,
) -> None:
    start(api, admin)
    variant = assign("checkout", "user-1", 10_000, VARIANTS)
    assert variant is not None
    sent = exposure("user-1", variant)

    send(api, sdk, [sent])

    first_exposed_at = datetime.fromisoformat(sent["occurred_at"])
    assert exposures(api_database, new_project[0]) == [
        ("user-1", variant, first_exposed_at, False, False)
    ]


def test_the_earliest_exposure_wins_and_a_second_variant_is_a_conflict(
    api: TestClient,
    admin: dict[str, str],
    sdk: dict[str, str],
    new_project: tuple[UUID, str, str],
    api_database: str,
) -> None:
    start(api, admin)
    variant = assign("checkout", "user-1", 10_000, VARIANTS)
    other = "treatment" if variant == "control" else "control"
    assert variant is not None
    send(api, sdk, [exposure("user-1", variant, occurred_at=hours_ago(1))])

    send(api, sdk, [exposure("user-1", variant, occurred_at=hours_ago(2))])
    send(api, sdk, [exposure("user-1", other, occurred_at=hours_ago(0.5))])

    [(_, stored_variant, first_exposed_at, conflicted, _)] = exposures(api_database, new_project[0])
    assert stored_variant == variant
    assert datetime.now(UTC) - first_exposed_at > timedelta(hours=1.9)
    assert conflicted


def test_a_variant_the_server_wouldnt_assign_is_flagged_not_rejected(
    api: TestClient,
    admin: dict[str, str],
    sdk: dict[str, str],
    new_project: tuple[UUID, str, str],
    api_database: str,
) -> None:
    start(api, admin, traffic=5_000)
    included = next(f"u{i}" for i in range(1_000) if in_experiment("checkout", f"u{i}", 5_000))
    excluded = next(f"u{i}" for i in range(1_000) if not in_experiment("checkout", f"u{i}", 5_000))
    wrong = "treatment" if assign("checkout", included, 5_000, VARIANTS) == "control" else "control"

    result = send(api, sdk, [exposure(included, wrong), exposure(excluded, "control")])

    assert result["accepted"] == 2
    mismatches = {
        user: mismatch for user, _, _, _, mismatch in exposures(api_database, new_project[0])
    }
    # The SDK claimed a different variant, and a user the server says isn't in the experiment.
    assert mismatches == {included: True, excluded: True}


def test_a_retried_exposure_never_changes_the_exposures_table(
    api: TestClient,
    admin: dict[str, str],
    sdk: dict[str, str],
    new_project: tuple[UUID, str, str],
    api_database: str,
) -> None:
    start(api, admin)
    variant = assign("checkout", "user-1", 10_000, VARIANTS)
    assert variant is not None
    first = exposure("user-1", variant)
    send(api, sdk, [first])
    before = exposures(api_database, new_project[0])

    # Same event_id and occurred_at, so a duplicate, even though its properties now differ.
    other = "treatment" if variant == "control" else "control"
    tampered = first | {"properties": {"experiment_key": "checkout", "variant_key": other}}
    assert send(api, sdk, [tampered])["duplicates"] == 1

    assert exposures(api_database, new_project[0]) == before  # not marked conflicted


def test_an_exposure_after_the_experiment_stopped_is_rejected(
    api: TestClient, admin: dict[str, str], sdk: dict[str, str]
) -> None:
    start(api, admin)
    api.post("/admin/experiments/checkout/stop", json={"reason": "done"}, headers=admin)

    result = send(api, sdk, [exposure("user-1", "control", occurred_at=hours_ago(1))])

    assert result["rejected"][0]["reason"] == "no running experiment with key 'checkout'"


def test_the_rate_limit_returns_429_with_retry_after(
    api_database: str, new_project: tuple[UUID, str, str]
) -> None:
    settings = Settings(database_url=api_database, rate_limit_per_second=1, rate_limit_burst=2)
    with TestClient(create_app(settings)) as client:
        headers = {"X-Client-Key": new_project[2]}
        responses = [
            client.post("/v1/events", json={"sdk": SDK, "events": []}, headers=headers)
            for _ in range(3)
        ]

    assert [r.status_code for r in responses] == [202, 202, 429]
    assert responses[2].headers["Retry-After"] == "1"
    assert responses[2].json()["error"]["code"] == "rate_limited"


def test_a_load_test_batch_is_stored_in_full(
    api: TestClient,
    admin: dict[str, str],
    sdk: dict[str, str],
    new_project: tuple[UUID, str, str],
    api_database: str,
) -> None:
    # loadtest/locustfile.py counts a batch as failed unless every event is stored, so its
    # batches must be valid, and its exposures must match the server's own assignment.
    start(api, admin, key=LARGE.key)

    result = send(api, sdk, batch_events(50, datetime.now(UTC), random.Random(1)))

    assert result == {"accepted": 50, "duplicates": 0, "rejected": []}
    recorded = exposures(api_database, new_project[0])
    assert recorded
    assert not any(mismatch for *_, mismatch in recorded)
