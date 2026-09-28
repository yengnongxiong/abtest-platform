"""GET /health against a real database, and against one that can't be reached."""

from fastapi.testclient import TestClient

from abtest.api.app import create_app
from abtest.config import Settings


def test_health_is_ok_when_database_is_reachable(database_url: str) -> None:
    app = create_app(Settings(database_url=database_url))

    with TestClient(app) as client:  # the context manager runs the lifespan (opens the pool)
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_unavailable_when_database_is_unreachable() -> None:
    # Nothing listens on port 1, so every connection attempt is refused immediately.
    app = create_app(Settings(database_url="postgresql://abtest@127.0.0.1:1/abtest"))

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
