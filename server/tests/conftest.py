"""Shared pytest fixtures."""

import os

import pytest


@pytest.fixture(scope="session")
def database_url() -> str:
    """DSN of a real Postgres. Integration tests never mock the database.

    `make test` starts the db container and loads DATABASE_URL from .env; CI sets it for
    its Postgres service container.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.fail("DATABASE_URL is not set. Run the tests with `make test`.")
    return url
