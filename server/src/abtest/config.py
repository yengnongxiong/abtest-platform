"""Runtime settings read from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Settings shared by the API and the worker.

    Values come from environment variables (DATABASE_URL, ...), so the same code runs on a
    laptop, inside docker compose, and in CI without config files.
    """

    database_url: str
