"""Public: GET /v1/config, what the SDK needs to assign users locally (PRD §11)."""

from typing import Annotated

from fastapi import APIRouter, Header, Response

from abtest.api.deps import ClientProject, ConnDep
from abtest.db.config import config_version, load_config
from abtest.models import Config

router = APIRouter(prefix="/v1", tags=["public"])

# SDKs poll every 30 s anyway; a shared cache may serve a copy for that long.
CACHE_CONTROL = "max-age=30"
# Each project gets its own config at the same URL, chosen by the X-Client-Key header. Without
# this, a CDN or proxy could serve one project's config to another project's SDK.
VARY = "X-Client-Key"


def etag_for(version: int) -> str:
    return f'"config-{version}"'


def matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 If-None-Match: '*', or a comma-separated list of tags; weak tags (W/) match
    too, because the comparison for GET is weak."""
    if if_none_match is None:
        return False
    tags = [tag.strip().removeprefix("W/") for tag in if_none_match.split(",")]
    return "*" in tags or etag in tags


@router.get("/config", response_model=Config)
def get_config(
    conn: ConnDep,
    project: ClientProject,
    response: Response,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Config | Response:
    """The running experiments and all flags. Answers 304 when the SDK's copy is current."""
    etag = etag_for(config_version(conn, project))
    if matches(if_none_match, etag):
        return Response(
            status_code=304, headers={"ETag": etag, "Cache-Control": CACHE_CONTROL, "Vary": VARY}
        )
    config = load_config(conn, project)
    # The version read with the content, which may be newer than the one checked above.
    response.headers["ETag"] = etag_for(config.config_version)
    response.headers["Cache-Control"] = CACHE_CONTROL
    response.headers["Vary"] = VARY
    return config
