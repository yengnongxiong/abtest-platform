"""One error format for every failure: {"error": {"code", "message", "details"}} (PRD §11)."""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from abtest.errors import (
    BadRequest,
    Conflict,
    DomainError,
    NotFound,
    RateLimited,
    TooLarge,
    Unauthorized,
    Unprocessable,
)

STATUS_BY_ERROR: dict[type[DomainError], int] = {
    NotFound: 404,
    Conflict: 409,
    Unprocessable: 422,
    Unauthorized: 401,
    BadRequest: 400,
    TooLarge: 413,
    RateLimited: 429,
}


def error_response(
    status: int,
    code: str,
    message: str,
    details: Any = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": jsonable_encoder(details)}}
    return JSONResponse(body, status_code=status, headers=headers)


async def domain_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)  # registered for DomainError only
    status = STATUS_BY_ERROR.get(type(exc), 400)
    headers = None
    if isinstance(exc, Unauthorized):
        headers = {"WWW-Authenticate": "Bearer"}
    elif isinstance(exc, RateLimited):
        headers = {"Retry-After": exc.retry_after}
    return error_response(status, exc.code, exc.message, exc.details, headers)


async def validation_error(request: Request, exc: Exception) -> JSONResponse:
    """A request that doesn't match its model: 422, with pydantic's list of problems.

    Each problem leaves out the input it rejected: that input can be a number JSON can't
    encode (1e400 parses as infinity), which would turn this answer into a 500.
    """
    assert isinstance(exc, RequestValidationError)
    problems = [{k: v for k, v in problem.items() if k != "input"} for problem in exc.errors()]
    return error_response(422, "validation_error", "the request is invalid", problems)


async def http_error(request: Request, exc: Exception) -> JSONResponse:
    """Framework errors (unknown route, wrong method) in the same format."""
    assert isinstance(exc, HTTPException)
    code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
    return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


async def internal_error(request: Request, exc: Exception) -> JSONResponse:
    """Anything unexpected: a 500 in the same format, with no internals in the body. The
    server still logs the exception."""
    return error_response(500, "internal_error", "something went wrong on the server")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(Exception, internal_error)
