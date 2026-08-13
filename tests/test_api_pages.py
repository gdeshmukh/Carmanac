"""The read pages (F2): every address in the ADR 0019 route map answers, and
a dead address is a 404.

Deliberately status-only (ruled 2026-08-13): the templates are scaffolding
and their markup will churn as the site builds out, so nothing here asserts
rendered HTML. The seeded shapes are the degraded ones - a slugless car, an
unplaced car, an empty model, an empty year - because a 200 over degraded
data is what catches a template that forgot to guard a NULL.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from carmanac.api.app import create_app
from carmanac.api.routes import get_session
from carmanac.db.models import (
    CataloguePeriod,
    Company,
    Configuration,
    Generation,
    GenerationModelLink,
    Model,
    PeriodKind,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def seeded(db):
    """One company, an M3 with a placed car and a slugless unplaced one, an
    empty Z1 with an empty 1990 year, a dated E46, and a generation whose
    address is a bare number."""
    company = Company(slug="bmw", name="BMW")
    db.add(company)
    db.flush()
    m3 = Model(company_id=company.id, slug="m3", name="M3")
    empty = Model(company_id=company.id, slug="z1", name="Z1")
    db.add_all([m3, empty])
    db.flush()
    e46 = Generation(
        company_id=company.id,
        slug="e46",
        name="E46",
        chassis_codes=["E46"],
        start_year=2000,
        end_year=2006,
    )
    numeric = Generation(company_id=company.id, slug="2004")
    db.add_all([e46, numeric])
    db.flush()
    db.add(GenerationModelLink(generation_id=e46.id, model_id=m3.id))
    kind = db.scalar(select(PeriodKind).where(PeriodKind.code == "model_year"))
    period = CataloguePeriod(model_id=m3.id, period_kind_id=kind.id, start_year=2004, end_year=2004)
    empty_period = CataloguePeriod(
        model_id=empty.id, period_kind_id=kind.id, start_year=1990, end_year=1990
    )
    db.add_all([period, empty_period])
    db.flush()
    market_id = db.execute(text("SELECT id FROM market_regions ORDER BY id LIMIT 1")).scalar_one()
    db.add_all(
        [
            Configuration(
                catalogue_period_id=period.id,
                market_region_id=market_id,
                slug="base",
                trim_name="Base",
                generation_id=e46.id,
            ),
            Configuration(
                catalogue_period_id=period.id, market_region_id=market_id, trim_name="Competition"
            ),
        ]
    )
    db.flush()


@pytest.fixture()
def client(db, seeded):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/bmw",
        "/bmw/m3",
        "/bmw/m3/2004",  # lists the slugless car - NULL address renders, never crashes
        "/bmw/m3/2004/base",
        "/bmw/z1",  # empty model
        "/bmw/z1/1990",  # year with no cars
        "/bmw/generations",
        "/bmw/generations/e46",
        # Numeric generation slug: a 200 proves the generations route beat
        # /{model}/{year:int}, which would 404 on model "generations".
        "/bmw/generations/2004",
    ],
)
def test_the_route_map_answers(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize(
    "path",
    ["/nope", "/bmw/nope", "/bmw/m3/1899", "/bmw/m3/2004/nope", "/bmw/generations/nope"],
)
def test_dead_addresses_404(client, path):
    assert client.get(path).status_code == 404
