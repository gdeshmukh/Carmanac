"""The read app (F2): entity pages rendered from the read views.

Scaffolding, deliberately: these server-rendered pages prove the read path -
API process -> views -> rendered page - and are the lightest thing that can.
The charter's frontend (Next.js + Vercel) is untouched by this; when it
arrives, this layer keeps the query surface and the templates go. Local dev
only: no docs/openapi surface is claimed, and the app never writes.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException

from carmanac.api.routes import crumbs, router, templates


def create_app() -> FastAPI:
    app = FastAPI(title="Carmanac", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)

    @app.exception_handler(StarletteHTTPException)  # the decorator is the access
    async def not_found(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        if exc.status_code != 404:
            return await http_exception_handler(request, exc)
        # The crumbs still render on a 404, so a dead address offers the
        # truncations above it - the way back to a page that exists.
        return templates.TemplateResponse(
            request,
            "not_found.html",
            {"path": request.url.path, "crumbs": crumbs(request.url.path)},
            status_code=404,
        )

    return app


app = create_app()
