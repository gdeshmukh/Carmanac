"""Read-only queries behind the pages: the three read views, plus the lookups
they don't carry (the year spine, generation rows and links).

Each page function returns the template's whole context as a plain dict, or
None when nothing answers at that address - the handler turns None into 404.

Views are read with `text()` because they have no mapped classes - the view is
the column contract, so `SELECT *` from one is the point. Entity tables go
through the ORM, which already has their classes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import RowMapping, select, text
from sqlalchemy.orm import Session

from carmanac.db.models import Company, Generation, GenerationModelLink, Model

CAR_LIST_LIMIT = 50


def _rows(session: Session, sql: str, **params: Any) -> Sequence[RowMapping]:
    return session.execute(text(sql), params).mappings().all()


def _row(session: Session, sql: str, **params: Any) -> RowMapping | None:
    return session.execute(text(sql), params).mappings().one_or_none()


def root_index(session: Session) -> dict[str, Any]:
    totals = _row(
        session,
        """SELECT count(*) FILTER (WHERE configurations > 0) AS companies_with_cars,
                  coalesce(sum(configurations), 0) AS configurations,
                  coalesce(sum(placed), 0) AS placed
           FROM v_company_coverage""",
    )
    cars = _rows(
        session,
        """SELECT company_name, model_name, start_year, trim_name, address
           FROM v_configuration_full
           WHERE address IS NOT NULL
           ORDER BY start_year DESC, company_slug, model_slug, car_slug
           LIMIT :limit""",
        limit=CAR_LIST_LIMIT,
    )
    return {"totals": totals, "cars": cars}


def company_page(session: Session, company_slug: str) -> dict[str, Any] | None:
    company = _row(
        session, "SELECT * FROM v_company_coverage WHERE company_slug = :slug", slug=company_slug
    )
    if company is None:
        return None
    models = _rows(
        session,
        "SELECT * FROM v_model_coverage WHERE company_slug = :slug ORDER BY model_name",
        slug=company_slug,
    )
    generations = _rows(
        session,
        """SELECT g.name, g.slug, g.chassis_codes, g.start_year, g.end_year,
                  count(c.id) AS placed
           FROM generations g
           LEFT JOIN configurations c ON c.generation_id = g.id
           WHERE g.company_id = :cid
           GROUP BY g.id
           ORDER BY g.start_year NULLS LAST, g.name""",
        cid=company["company_id"],
    )
    return {"company": company, "models": models, "generations": generations}


def model_page(session: Session, company_slug: str, model_slug: str) -> dict[str, Any] | None:
    model = _row(
        session,
        "SELECT * FROM v_model_coverage WHERE company_slug = :c AND model_slug = :m",
        c=company_slug,
        m=model_slug,
    )
    if model is None:
        return None
    # From the tables, not the view: 621 models have a year spine and no cars
    # yet, and those years must still render (an empty year is honest).
    years = _rows(
        session,
        """SELECT pk.code AS kind, cp.start_year, cp.end_year, count(c.id) AS cars
           FROM catalogue_periods cp
           JOIN period_kinds pk ON pk.id = cp.period_kind_id
           LEFT JOIN configurations c ON c.catalogue_period_id = cp.id
           WHERE cp.model_id = :mid
           GROUP BY cp.id, pk.code
           ORDER BY cp.start_year""",
        mid=model["model_id"],
    )
    return {"model": model, "years": years}


def year_page(
    session: Session, company_slug: str, model_slug: str, year: int
) -> dict[str, Any] | None:
    # EXISTS rather than a join: this asks whether the model has that year at
    # all, and returns one row per model no matter how many periods claim it.
    # The names come from the model because an empty year must render too, and
    # then there is no car row to read them from.
    period = _row(
        session,
        """SELECT co.name AS company_name, m.name AS model_name
           FROM models m
           JOIN companies co ON co.id = m.company_id AND co.slug = :c
           WHERE m.slug = :m AND EXISTS (
               SELECT 1 FROM catalogue_periods cp
               JOIN period_kinds pk ON pk.id = cp.period_kind_id AND pk.code = 'model_year'
               WHERE cp.model_id = m.id AND cp.start_year = :y)""",
        c=company_slug,
        m=model_slug,
        y=year,
    )
    if period is None:
        return None
    cars = _rows(
        session,
        """SELECT * FROM v_configuration_full
           WHERE company_slug = :c AND model_slug = :m AND start_year = :y
             AND period_kind = 'model_year'
           ORDER BY car_slug NULLS LAST, trim_name""",
        c=company_slug,
        m=model_slug,
        y=year,
    )
    return {"period": period, "year": year, "cars": cars}


def configuration_page(
    session: Session, company_slug: str, model_slug: str, year: int, car_slug: str
) -> dict[str, Any] | None:
    car = _row(
        session,
        """SELECT * FROM v_configuration_full
           WHERE company_slug = :c AND model_slug = :m AND start_year = :y
             AND car_slug = :car AND period_kind = 'model_year'""",
        c=company_slug,
        m=model_slug,
        y=year,
        car=car_slug,
    )
    return None if car is None else {"car": car}


def generation_page(
    session: Session, company_slug: str, generation_slug: str
) -> dict[str, Any] | None:
    generation = session.scalars(
        select(Generation)
        .join(Company, Company.id == Generation.company_id)
        .where(Company.slug == company_slug, Generation.slug == generation_slug)
    ).one_or_none()
    if generation is None:
        return None
    models = session.execute(
        select(Model.name, Model.slug.label("model_slug"), Company.slug.label("company_slug"))
        .join(GenerationModelLink, GenerationModelLink.model_id == Model.id)
        .join(Company, Company.id == Model.company_id)
        .where(
            GenerationModelLink.generation_id == generation.id,
            GenerationModelLink.superseded_by.is_(None),
        )
        .distinct()
        .order_by(Model.name)
    ).all()
    cars = _rows(
        session,
        """SELECT * FROM v_configuration_full
           WHERE generation_id = :gid
           ORDER BY start_year, model_slug, car_slug NULLS LAST""",
        gid=generation.id,
    )
    years: dict[int, list[RowMapping]] = {}
    for car in cars:
        years.setdefault(car["start_year"], []).append(car)
    return {
        "generation": generation,
        "company_slug": company_slug,
        "models": models,
        "years": years,
    }
