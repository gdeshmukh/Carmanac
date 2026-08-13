"""The pages: thin handlers that resolve an address against the queries,
404 when nothing answers, and render a template otherwise.

The route map is ADR 0019's. Routes are matched in registration order, which
is how the reserved literal works: `/{company}/generations/{generation}` is
registered before `/{company}/{model}/{year}`, so `/porsche/generations/964`
reaches the generation handler instead of parsing as model "generations",
year 964. The `:int` converter means a non-numeric third segment matches no
route at all and 404s.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy.orm import Session

from carmanac.api import queries
from carmanac.db.session import SessionLocal

router = APIRouter()

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def dash(value: object, unit: str = "") -> Markup:
    """Honest NULL: a visible em dash, never a hidden field. The unit only
    prints beside a real value ("2979 cc", not "- cc")."""
    if value is None:
        return Markup('<span class="null">&mdash;</span>')
    return escape(f"{value} {unit}".strip())


templates.env.filters["dash"] = dash


def get_session() -> Iterator[Session]:
    """Per-request session, closed without commit: an uncommitted session
    rolls back, so pages cannot write even by accident."""
    with SessionLocal() as session:
        yield session


Db = Annotated[Session, Depends(get_session)]


def crumbs(path: str) -> list[tuple[str, str | None]]:
    """One crumb per path segment, each linking to the index above it - the
    ADR 0019 truncation property rendered as navigation. A crumb has no link
    when it is the page you are on, or when it is the bare `generations`
    literal, which is a reserved segment rather than a route."""
    segments = [s for s in path.strip("/").split("/") if s]
    trail: list[tuple[str, str | None]] = []
    for i, segment in enumerate(segments):
        last = i == len(segments) - 1
        href = None if last or segment == "generations" else "/" + "/".join(segments[: i + 1])
        trail.append((segment, href))
    return trail


def _render(request: Request, name: str, context: dict[str, Any] | None) -> Response:
    if context is None:
        raise HTTPException(status_code=404)
    context["crumbs"] = crumbs(request.url.path)
    return templates.TemplateResponse(request, name, context)


@router.get("/")
def root(request: Request, db: Db) -> Response:
    return _render(request, "index.html", queries.root_index(db))


@router.get("/{company_slug}/generations/{generation_slug}")
def generation(request: Request, db: Db, company_slug: str, generation_slug: str) -> Response:
    return _render(
        request, "generation.html", queries.generation_page(db, company_slug, generation_slug)
    )


@router.get("/{company_slug}")
def company(request: Request, db: Db, company_slug: str) -> Response:
    return _render(request, "company.html", queries.company_page(db, company_slug))


@router.get("/{company_slug}/{model_slug}")
def model(request: Request, db: Db, company_slug: str, model_slug: str) -> Response:
    return _render(request, "model.html", queries.model_page(db, company_slug, model_slug))


@router.get("/{company_slug}/{model_slug}/{year:int}")
def year(request: Request, db: Db, company_slug: str, model_slug: str, year: int) -> Response:
    return _render(request, "year.html", queries.year_page(db, company_slug, model_slug, year))


@router.get("/{company_slug}/{model_slug}/{year:int}/{car_slug}")
def configuration(
    request: Request, db: Db, company_slug: str, model_slug: str, year: int, car_slug: str
) -> Response:
    return _render(
        request,
        "configuration.html",
        queries.configuration_page(db, company_slug, model_slug, year, car_slug),
    )
