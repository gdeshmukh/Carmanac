"""The read pages (F2): the ADR 0019 route map rendered from the read views.

The app never commits, so these run on the test session's uncommitted state -
`dependency_overrides` hands the handlers the same session the fixture wrote
through, which is also what keeps the dev database untouched.
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
    """One company, two models - an M3 with a 2004 year holding a placed car
    and a slugless one, and an empty Z1. Plus a generation addressed as a bare
    number, which is what pins the route order."""
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
        name="M3 (E46)",
        chassis_codes=["E46"],
        start_year=2000,
        end_year=2006,
    )
    numeric = Generation(company_id=company.id, slug="2004", name="Numeric (2004)")
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
                power_hp=333,
            ),
            Configuration(
                catalogue_period_id=period.id,
                market_region_id=market_id,
                trim_name="Competition",
            ),
        ]
    )
    db.flush()
    return company


@pytest.fixture()
def client(db, seeded):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def test_root_lists_addressed_cars(client):
    body = client.get("/").text
    assert '<a href="/bmw/m3/2004/base">' in body
    assert "Competition" not in body  # unaddressed: no address to link to


def test_company_page_lists_models_and_generations(client):
    body = client.get("/bmw").text
    assert '<a href="/bmw/m3">M3</a>' in body
    assert '<a href="/bmw/z1">Z1</a>' in body  # the empty model is still a page
    assert '<a href="/bmw/generations/e46">M3 (E46)</a>' in body


def test_model_page_shows_the_year_spine(client):
    assert '<a href="/bmw/m3/2004">2004</a>' in client.get("/bmw/m3").text


def test_empty_model_renders_its_spine_honestly(client):
    """A model with a year and no cars is a page, not a 404 - 621 live models
    are this shape."""
    body = client.get("/bmw/z1").text
    assert '<a href="/bmw/z1/1990">1990</a>' in body
    assert client.get("/bmw/z1/1990").status_code == 200


def test_year_page_lists_the_unaddressed_car_without_a_link(client):
    """An unaddressed row is the queue rendering as itself (ADR 0019)."""
    body = client.get("/bmw/m3/2004").text
    assert '<a href="/bmw/m3/2004/base">Base</a>' in body
    assert 'Competition <span class="unaddressed">(no address)</span>' in body


def test_configuration_page_renders_placement_and_honest_nulls(client):
    body = client.get("/bmw/m3/2004/base").text
    assert "333 hp" in body
    assert '<a href="/bmw/generations/e46">M3 (E46)</a>' in body
    assert "E46" in body
    assert '<span class="null">&mdash;</span>' in body  # torque, weight: no source has said


def test_unplaced_car_says_so(db, client, seeded):
    """Placement is evidence-gated (ADR 0014); an unplaced car must not
    silently render as placed."""
    db.execute(text("UPDATE configurations SET slug = 'competition' WHERE slug IS NULL"))
    body = client.get("/bmw/m3/2004/competition").text
    assert "Not placed" in body


def test_generation_page_lists_its_models_and_placed_cars(client):
    body = client.get("/bmw/generations/e46").text
    assert '<a href="/bmw/m3">M3</a>' in body
    assert '<a href="/bmw/m3/2004/base">Base</a>' in body
    assert "2000–2006" in body


def test_open_ended_generation_span_reads_as_present(db, client):
    """`generations.end_year` NULL means still in production, so the span must
    not render as a missing value."""
    db.execute(text("UPDATE generations SET end_year = NULL WHERE slug = 'e46'"))
    assert "2000–present" in client.get("/bmw/generations/e46").text


def test_generations_literal_wins_over_the_model_route(client):
    """`/bmw/generations/2004` is a generation, never model 'generations' in
    year 2004 - route registration order is what guarantees it."""
    body = client.get("/bmw/generations/2004").text
    assert "Numeric (2004)" in body


@pytest.mark.parametrize(
    "path",
    ["/nope", "/bmw/nope", "/bmw/m3/1899", "/bmw/m3/2004/nope", "/bmw/generations/nope"],
)
def test_unknown_addresses_404(client, path):
    assert client.get(path).status_code == 404
