"""Errors raised by the data layer when a request can't be carried out.

They name what went wrong (a stable `code` for programs, a `message` for people). The API
maps each class to an HTTP status, so the data layer never deals in HTTP.
"""

from typing import Any


class DomainError(Exception):
    def __init__(self, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class NotFound(DomainError):
    """The thing named in the request doesn't exist (in this project)."""


class Conflict(DomainError):
    """The request clashes with the current state: a taken key, a running experiment."""


class Unprocessable(DomainError):
    """The request is well-formed but can't be applied, e.g. an experiment that isn't ready."""


class Unauthorized(DomainError):
    """No valid API key of the right kind."""


class BadRequest(DomainError):
    """The request body can't be read: not JSON, or not the expected envelope."""


class TooLarge(DomainError):
    """The request body is over the size limit."""


class RateLimited(DomainError):
    """Too many requests for this key; retry after `retry_after` seconds."""

    def __init__(self, retry_after: str) -> None:
        super().__init__("rate_limited", "too many requests for this key; slow down")
        self.retry_after = retry_after
