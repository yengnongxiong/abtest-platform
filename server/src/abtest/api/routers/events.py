"""Public: POST /v1/events, where the SDK's batches arrive (PRD §11)."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from abtest.api.deps import ClientKey, ClientProject, ConnDep
from abtest.api.rate_limit import TokenBucketLimiter, retry_after_header
from abtest.errors import RateLimited, TooLarge
from abtest.ingest import ingest, parse_batch
from abtest.keys import hash_key
from abtest.models import EventsResult

router = APIRouter(prefix="/v1", tags=["public"])

MAX_BODY_BYTES = 1_000_000


def rate_limited_project(request: Request, project: ClientProject, key: ClientKey) -> UUID:
    """The caller's project, once its key is within the rate limit. Only valid keys get a
    bucket, so random keys can't grow the limiter's memory."""
    limiter: TokenBucketLimiter = request.app.state.rate_limiter
    assert key is not None  # client_project already rejected a missing key
    wait = limiter.acquire(hash_key(key))
    if wait is not None:
        raise RateLimited(retry_after_header(wait))
    return project


async def raw_body(request: Request) -> bytes:
    """The body, read with a 1 MB cap. The cap is checked while reading, so a sender that
    lies about (or omits) Content-Length still can't make the server buffer more."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise TooLarge("body_too_large", f"the body must be at most {MAX_BODY_BYTES} bytes")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise TooLarge("body_too_large", f"the body must be at most {MAX_BODY_BYTES} bytes")
    return bytes(body)


@router.post("/events", status_code=202)
def post_events(
    body: Annotated[bytes, Depends(raw_body)],
    project: Annotated[UUID, Depends(rate_limited_project)],
    conn: ConnDep,
) -> EventsResult:
    """Store a batch of events. Duplicates are counted, not stored again; each invalid event
    comes back with its index and the reason.

    FastAPI resolves dependencies in the order they're declared, and `body` comes first: the
    upload is read before checking the key takes a pooled connection. The other way round, a
    client that stalls mid-upload holds a connection, and four of them hold the whole pool.
    """
    return ingest(conn, project, parse_batch(body), datetime.now(UTC))
