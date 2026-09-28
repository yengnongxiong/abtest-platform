"""Runtime settings read from environment variables."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Settings shared by the API and the worker.

    Values come from environment variables (DATABASE_URL, ...), so the same code runs on a
    laptop, inside docker compose, and in CI without config files.
    """

    database_url: str


class MigrateSettings(Settings):
    """Settings for `python -m abtest.db migrate`, which also registers the initial API keys.

    SecretStr keeps the keys out of reprs, logs, and tracebacks.
    """

    abtest_client_key: SecretStr
    abtest_server_key: SecretStr
