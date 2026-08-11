"""Address tests (ADR 0019): the grammar, and the promise that an address is
a projection - never identity, never a reason a row fails to exist.

The integration tests run the real recompute over a synthetic spine, because
the properties worth pinning are whole-database ones: convergence, and who
wins a contested name.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from carmanac.db.models import (
    CataloguePeriod,
    Company,
    Configuration,
    ExternalId,
    Generation,
    MarketRegion,
    Model,
    PeriodKind,
    RawRecord,
    Source,
)
from carmanac.reconcile.addressing import (
    configuration_slug,
    generation_display,
    nonconforming_slug,
    recompute_addresses,
    slugify,
    strip_marque,
)


def vpic_source_id(db) -> int:
    """The vPIC source row, created on demand: make names live on its raw
    records, which is where a company's other filed names come from."""
    source = db.scalar(select(Source).where(Source.name == "NHTSA vPIC"))
    if source is None:
        source = Source(name="NHTSA vPIC", tier=1)
        db.add(source)
        db.flush()
    return source.id


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


def test_configuration_slug_is_only_what_separates_siblings():
    """The company, model and year are path segments, so the tail carries
    neither. It never comes back empty: a car with nothing to say for itself
    is `base`, so a year page is always an index and never also a car."""
    assert configuration_slug("70D", "awd") == "70d-awd"
    assert configuration_slug("Convertible", "rwd") == "convertible-rwd"
    assert configuration_slug("AWD", "awd") == "awd", "the trim already says it"
    assert configuration_slug(None, "rwd") == "rwd"
    assert configuration_slug(None, None) == "base"


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
    assert config.slug == "tsi"

    stats = recompute_addresses(db)
    assert (stats.companies_addressed, stats.configurations_addressed) == (0, 0), (
        "a second run over unchanged data must write nothing"
    )


@pytest.mark.integration
def test_renaming_a_company_moves_its_page_and_no_car_slug(db, spine):
    """Scoping the tail to the model year buys this: a company rename moves
    exactly one address instead of every car under it. The cars' URLs still
    change - the company segment is in the path - but nothing stored moves,
    so the rename is one row, not 23,523."""
    recompute_addresses(db)
    db.commit()
    before = db.scalar(select(Configuration)).slug
    spine["real"].name = "Eagle Motors"
    db.commit()
    stats = recompute_addresses(db)
    db.commit()
    assert spine["real"].slug == "eagle-motors"
    assert db.scalar(select(Configuration)).slug == before
    assert stats.configurations_addressed == 0


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


@pytest.mark.integration
def test_a_company_claims_the_addresses_of_its_other_filed_names(db, spine, wikidata_source):
    """Audi AG is filed with vPIC as "AUDI", so `audi` is not a free address
    for a zero-evidence namesake - even though the two names slugify apart
    and never contest each other directly."""
    vpic = vpic_source_id(db)
    db.add(ExternalId(company_id=spine["real"].id, source_id=vpic, external_id="make:7"))
    db.add(
        RawRecord(
            source_id=vpic,
            external_id="make:7",
            content_hash="h",
            payload={"make_name": "EAGLE MOTORS"},
        )
    )
    stub = Company(name="Eagle Motors")
    db.add(stub)
    db.commit()
    recompute_addresses(db)
    db.commit()
    assert stub.slug is None, "the stub may not take a name the filed company answers to"
    assert spine["real"].slug == "eagle", "and the filed company keeps its own"


@pytest.mark.integration
def test_addresses_may_permute_without_colliding(db, spine):
    """A takes B's address while B moves. Both UPDATEs are legal only if every
    mover releases before any mover takes."""
    recompute_addresses(db)
    db.commit()
    real, stub = spine["real"], spine["stub"]
    real.name = "Talon Cars"
    stub.name = "Eagle"
    db.commit()
    recompute_addresses(db)
    db.commit()
    assert (real.slug, stub.slug) == ("talon-cars", "eagle")


@pytest.mark.integration
def test_one_tail_may_repeat_across_years_but_not_inside_one(db, spine):
    """`rwd` names thousands of cars; the URL scopes it. What may not happen
    is two cars answering at one address inside one model year."""
    period = db.scalar(select(CataloguePeriod))
    kind = db.scalar(select(PeriodKind).where(PeriodKind.code == "model_year"))
    market = db.scalar(select(MarketRegion).where(MarketRegion.code == "US"))
    other = CataloguePeriod(
        model_id=period.model_id, period_kind_id=kind.id, start_year=1993, end_year=1993
    )
    db.add(other)
    db.flush()
    db.add(Configuration(catalogue_period_id=other.id, market_region_id=market.id, trim_name="TSi"))
    db.commit()
    recompute_addresses(db)
    db.commit()
    assert [c.slug for c in db.scalars(select(Configuration).order_by(Configuration.id))] == [
        "tsi",
        "tsi",
    ]

    db.add(
        Configuration(
            catalogue_period_id=other.id,
            market_region_id=market.id,
            trim_name="TSi",
            drivetrain_id=None,
            slug="tsi",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
