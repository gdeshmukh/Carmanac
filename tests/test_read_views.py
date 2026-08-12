"""The read views (F2): the spine joined into one row, and the coverage
shapes beside it.

Views are plain (no storage), so these tests are about the join and the
address composition being right, not about refresh semantics.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from carmanac.db.models import (
    CataloguePeriod,
    Company,
    Configuration,
    Generation,
    Model,
    PeriodKind,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def seeded(db):
    """One company, two models — one with a 2004 model year holding two cars
    (one placed, one slugless), one an empty shell."""
    company = Company(slug="bmw", name="BMW")
    db.add(company)
    db.flush()
    m3 = Model(company_id=company.id, slug="m3", name="M3")
    empty = Model(company_id=company.id, slug="z1", name="Z1")
    db.add_all([m3, empty])
    db.flush()
    e46 = Generation(company_id=company.id, slug="e46", name="E46", chassis_codes=["E46"])
    db.add(e46)
    db.flush()
    kind = db.scalar(select(PeriodKind).where(PeriodKind.code == "model_year"))
    period = CataloguePeriod(model_id=m3.id, period_kind_id=kind.id, start_year=2004, end_year=2004)
    db.add(period)
    db.flush()
    market_id = db.execute(text("SELECT id FROM market_regions ORDER BY id LIMIT 1")).scalar_one()
    placed = Configuration(
        catalogue_period_id=period.id,
        market_region_id=market_id,
        slug="base",
        trim_name="Base",
        generation_id=e46.id,
    )
    slugless = Configuration(
        catalogue_period_id=period.id, market_region_id=market_id, trim_name="Competition"
    )
    db.add_all([placed, slugless])
    db.flush()
    return {"company": company, "m3": m3, "empty": empty, "placed": placed}


def test_full_view_joins_the_spine_and_composes_the_address(db, seeded):
    row = (
        db.execute(text("SELECT * FROM v_configuration_full WHERE car_slug = 'base'"))
        .mappings()
        .one()
    )
    assert (row["company_slug"], row["model_slug"], row["start_year"]) == ("bmw", "m3", 2004)
    assert row["address"] == "/bmw/m3/2004/base"
    assert row["trim_name"] == "Base"
    assert row["market"] is not None  # lookup resolved to its code
    assert (row["generation_slug"], row["chassis_codes"]) == ("e46", ["E46"])


def test_full_view_leaves_the_unaddressed_honest(db, seeded):
    """A slugless car appears in the view with a NULL address — unaddressed
    rows are the queue, not missing data (ADR 0019)."""
    row = db.execute(
        text(
            "SELECT address, generation_slug FROM v_configuration_full "
            "WHERE trim_name = 'Competition'"
        )
    ).one()
    assert row.address is None
    assert row.generation_slug is None  # unplaced renders as honest NULL


def test_model_coverage_separates_filled_from_empty(db, seeded):
    rows = {
        r.model_slug: r
        for r in db.execute(text("SELECT * FROM v_model_coverage WHERE company_slug = 'bmw'"))
    }
    m3, empty = rows["m3"], rows["z1"]
    assert (m3.periods, m3.periods_with_cars, m3.configurations, m3.placed) == (1, 1, 2, 1)
    assert (m3.first_year, m3.last_year) == (2004, 2004)
    assert (empty.periods, empty.configurations, empty.placed) == (0, 0, 0)


def test_company_coverage_counts_models_with_cars(db, seeded):
    row = db.execute(text("SELECT * FROM v_company_coverage WHERE company_slug = 'bmw'")).one()
    assert (row.models, row.models_with_cars, row.configurations, row.placed) == (2, 1, 2, 1)
