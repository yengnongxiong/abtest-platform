"""Structured logs: one JSON object per line, so logs can be searched and aggregated."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

# Attributes every LogRecord has; anything else was passed with `extra=` and is logged too.
_STANDARD_ATTRIBUTES = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry |= {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRIBUTES}
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_json_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
