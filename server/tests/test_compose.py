"""docker-compose.yml hands the API the settings .env.example says you can set."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_the_api_container_receives_the_optional_rate_limit_settings() -> None:
    # .env.example documents RATE_LIMIT_PER_SECOND and RATE_LIMIT_BURST. Until M9 the api
    # service didn't pass them on, so setting them in .env silently changed nothing.
    compose: dict[str, Any] = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    documented = {
        line.lstrip("# ").split("=")[0]
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line.startswith("# RATE_LIMIT_")
    }

    assert documented == {"RATE_LIMIT_PER_SECOND", "RATE_LIMIT_BURST"}
    assert documented <= compose["services"]["api"]["environment"].keys()
