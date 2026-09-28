"""One error format for every failure: {"error": {"code", "message", "details"}} (PRD §11)."""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from abtest.errors import Conflict, DomainError, NotFound, Unauthorized, Unprocessable

STATUS_BY_ERROR: dict[type[DomainError], int] = {
    NotFound: 404,
    Conflict: 409,
    Unprocessable: 422,
    Unauthorized: 401,
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
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return error_response(status, exc.code, exc.message, exc.details, headers)


async def validation_error(request: Request, exc: Exception) -> JSONResponse:
    """A request that doesn't match its model: 422, with pydantic's list of problems."""
    assert isinstance(exc, RequestValidationError)
    return error_response(422, "validation_error", "the request is invalid", exc.errors())


async def http_error(request: Request, exc: Exception) -> JSONResponse:
    """Framework errors (unknown route, wrong method) in the same format."""
    assert isinstance(exc, HTTPException)
    code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
    return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(HTTPException, http_error)
