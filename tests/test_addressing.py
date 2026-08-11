"""Address tests (ADR 0019): the grammar, and the promise that an address is
a projection - never identity, never a reason a row fails to exist.

The integration tests run the real recompute over a synthetic spine, because
the properties worth pinning are whole-database ones: convergence, and who
wins a contested name.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from carmanac.db.models import (
    CataloguePeriod,
    Company,
    Configuration,
    ExternalId,
    Generation,
    MarketRegion,
    Model,
    PeriodKind,
)
from carmanac.reconcile.addressing import (
    configuration_slug,
    generation_display,
    nonconforming_slug,
    recompute_addresses,
    slugify,
    strip_marque,
)

# --- grammar -----------------------------------------------------------------


def test_slugify_folds_the_dash_family_before_stripping_ascii():
    """NFKD cannot decompose an en dash, so it used to vanish with the rest of
    the non-ASCII and take the word boundary with it."""
    assert slugify("Renault–Nissan–Mitsubishi Alliance") == ("renault-nissan-mitsubishi-alliance")
    assert slugify("Citroën") == "citroen"
    assert slugify("トヨタ") == "", "no ASCII form yields no address, never a fallback id"


def test_nonconforming_slug_covers_the_source_artifact_classes():
    assert nonconforming_slug("e46") is None
    assert nonconforming_slug("civic-fifth-generation") is None
    assert nonconforming_slug("2000-2007-subaru-impreza") == "year_range"
    assert nonconforming_slug("de-ville-1961-64") == "year_range", "mid-slug, not just leading"
    assert nonconforming_slug("category-honda-hr-v") == "source_artifact_title"
    assert nonconforming_slug("civic-11th-generation") == "numeral_ordinal"


def test_strip_marque_never_strips_down_to_bare_digits():
    """`Mazda3` is a nameplate, not a 3 - stripping it would address the car
    at `3-bk` under a company already named Mazda."""
    assert strip_marque("Mercedes-AMG GT", ("mercedesbenz", "mercedesamg")) == "GT"
    assert strip_marque("Mazda3", ("mazda",)) == "Mazda3"
    assert strip_marque("Polestar 2", ("polestar",)) == "Polestar 2"


def test_generation_display_prefers_codes_then_ordinal_then_nothing():
    assert generation_display("Camry", ["XV10"], None) == "Camry (XV10)"
    assert generation_display("Civic", None, 11) == "Civic (eleventh generation)"
    assert generation_display("Impreza", None, None) == "", "a span alone is not a name"


def test_configuration_slug_needs_its_parents_to_have_addresses():
    assert configuration_slug("tesla", "model-s", 2015, "70D", "awd") == (
        "tesla-model-s-2015-70d-awd"
    )
    assert configuration_slug(None, "model-s", 2015, None, None) is None


# --- the projection ----------------------------------------------------------


@pytest.fixture()
def spine(db, wikidata_source):
    """Two companies sharing a name, one with a filing and a car."""
    real = Company(name="Eagle")
    stub = Company(name="Eagle")
    db.add_all([real, stub])
    db.flush()
    db.add(ExternalId(company_id=real.id, source_id=wikidata_source.id, external_id="make:1"))
    model = Model(company_id=real.id, name="Talon", slug="talon")
    db.add(model)
    db.flush()
    kind = db.scalar(select(PeriodKind).where(PeriodKind.code == "model_year"))
    period = CataloguePeriod(
        model_id=model.id, period_kind_id=kind.id, start_year=1992, end_year=1992
    )
    db.add(period)
    db.flush()
    market = db.scalar(select(MarketRegion).where(MarketRegion.code == "US"))
    db.add(
        Configuration(
            catalogue_period_id=period.id,
            market_region_id=market.id,
            trim_name="TSi",
        )
    )
    db.commit()
    return {"real": real, "stub": stub, "model": model}


@pytest.mark.integration
def test_evidence_takes_a_contested_address_and_the_loser_waits(db, spine):
    """Arrival order is what the QID suffix was hiding. The company a filing
    authority names and cars hang off takes the bare address; the namesake
    exists, keeps its facts, and simply has none."""
    recompute_addresses(db)
    db.commit()
    assert spine["real"].slug == "eagle"
    assert spine["stub"].slug is None
    assert db.scalar(select(Company).where(Company.id == spine["stub"].id)) is not None


@pytest.mark.integration
def test_a_car_is_addressed_from_its_parents_and_recompute_converges(db, spine):
    recompute_addresses(db)
    db.commit()
    config = db.scalar(select(Configuration))
    assert config.slug == "eagle-talon-1992-tsi"

    stats = recompute_addresses(db)
    assert (stats.companies_addressed, stats.configurations_addressed) == (0, 0), (
        "a second run over unchanged data must write nothing"
    )


@pytest.mark.integration
def test_renaming_a_company_re_addresses_its_cars(db, spine):
    """The trade this ADR accepts: an address follows its data, because it is
    a projection and nothing is published."""
    recompute_addresses(db)
    db.commit()
    spine["real"].name = "Eagle Motors"
    db.commit()
    recompute_addresses(db)
    db.commit()
    assert spine["real"].slug == "eagle-motors"
    assert db.scalar(select(Configuration)).slug == "eagle-motors-talon-1992-tsi"


@pytest.mark.integration
def test_a_generation_with_no_composable_name_has_no_address(db, spine):
    """A production span is not a name. The row exists and keeps its facts;
    the address waits for codes or an ordinal."""
    gen = Generation(company_id=spine["real"].id, name="1992-1994 Talon", slug="1992-1994-talon")
    db.add(gen)
    db.commit()
    recompute_addresses(db)
    db.commit()
    assert gen.slug is None
    assert db.scalar(select(Generation)) is not None
