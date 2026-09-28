"""CORS for the public API only (PRD §11, §19)."""

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

PUBLIC_PREFIX = "/v1/"


class PublicCORS:
    """Starlette's CORSMiddleware, applied only to paths under /v1/.

    Browsers on any site may call the public endpoints (config and events), without
    credentials. The admin API gets no CORS headers at all, so a browser page on another
    site can't call it even with a stolen server key in hand; the dashboard calls it from
    its own server.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.cors = CORSMiddleware(
            app,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["X-Client-Key", "If-None-Match", "Content-Type"],
            # Let the SDK read these: the config ETag, and the retry hint on 429.
            expose_headers=["ETag", "Retry-After"],
            max_age=7200,  # cache the preflight for 2 hours, Chrome's upper limit
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(PUBLIC_PREFIX):
            await self.cors(scope, receive, send)
        else:
            await self.app(scope, receive, send)
