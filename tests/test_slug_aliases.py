"""The address contract (ADR 0019): a rename can never drop its old address.

Every property here is enforced by the database triggers in migration
e8f2a91c7d30, so the tests deliberately exercise both the ORM path and raw
SQL - the contract's whole point is that it holds for writers that never
import our code.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from carmanac.db.models import (
    CataloguePeriod,
    Company,
    Configuration,
    Generation,
    MarketRegion,
    Model,
    ModelLine,
    PeriodKind,
    SlugAlias,
)
from carmanac.reconcile.bookkeeping import alias_addresses

pytestmark = pytest.mark.integration


@pytest.fixture()
def companies(db):
    """Two companies, committed so trigger-abort rollbacks keep the baseline."""
    tesla = Company(slug="tesla-q124981765", name="Tesla")
    rivian = Company(slug="rivian", name="Rivian")
    db.add_all([tesla, rivian])
    db.commit()
    return tesla, rivian


def _aliases(db, kind: str) -> list[SlugAlias]:
    return list(db.scalars(select(SlugAlias).where(SlugAlias.entity_kind == kind)))


def test_orm_rename_records_the_old_address(db, companies):
    tesla, _ = companies
    tesla.slug = "tesla"
    db.commit()

    (alias,) = _aliases(db, "company")
    assert alias.slug == "tesla-q124981765"
    assert alias.scope_company_id is None
    assert alias.company_id == tesla.id
    assert alias.reason == "rename"


def test_raw_sql_rename_records_the_old_address(db, companies):
    tesla, _ = companies
    model = Model(company_id=tesla.id, slug="roadster", name="Roadster")
    db.add(model)
    db.commit()

    db.execute(text("UPDATE models SET slug = 'roadster-1' WHERE id = :id"), {"id": model.id})
    db.commit()

    (alias,) = _aliases(db, "model")
    assert (alias.scope_company_id, alias.slug, alias.model_id) == (
        tesla.id,
        "roadster",
        model.id,
    )


def test_retired_address_cannot_be_reminted(db, companies):
    tesla, _ = companies
    tesla.slug = "tesla"
    db.commit()

    db.add(Company(slug="tesla-q124981765", name="Impostor"))
    with pytest.raises(DBAPIError, match="retired address"):
        db.flush()
    db.rollback()


def test_scoped_retirement_only_occupies_its_company(db, companies):
    tesla, rivian = companies
    model = Model(company_id=tesla.id, slug="roadster", name="Roadster")
    db.add(model)
    db.commit()
    model.slug = "roadster-1"
    db.commit()

    # Same slug under the OTHER company is a different address - legal.
    db.add(Model(company_id=rivian.id, slug="roadster", name="Roadster"))
    db.commit()

    db.add(Model(company_id=tesla.id, slug="roadster", name="Roadster again"))
    with pytest.raises(DBAPIError, match="retired address"):
        db.flush()
    db.rollback()


def test_rename_back_discharges_the_self_alias(db, companies):
    tesla, _ = companies
    tesla.slug = "tesla"
    db.commit()
    tesla.slug = "tesla-q124981765"
    db.commit()

    (alias,) = _aliases(db, "company")
    assert alias.slug == "tesla"
    assert alias.company_id == tesla.id


def test_another_rows_retired_address_cannot_be_taken_over(db, companies):
    tesla, rivian = companies
    tesla.slug = "tesla"
    db.commit()

    rivian.slug = "tesla-q124981765"
    with pytest.raises(DBAPIError, match="cannot be taken over"):
        db.flush()
    db.rollback()


def test_reparent_retires_the_pair_address(db, companies):
    tesla, rivian = companies
    generation = Generation(company_id=tesla.id, slug="gen-1", name="Gen 1")
    db.add(generation)
    db.commit()

    db.execute(
        text("UPDATE generations SET company_id = :c WHERE id = :id"),
        {"c": rivian.id, "id": generation.id},
    )
    db.commit()

    (alias,) = _aliases(db, "generation")
    # The address that existed was (tesla, gen-1); scope freezes where it lived.
    assert (alias.scope_company_id, alias.slug, alias.generation_id) == (
        tesla.id,
        "gen-1",
        generation.id,
    )


def test_delete_without_forwarding_alias_raises(db, companies):
    tesla, _ = companies
    db.delete(tesla)
    with pytest.raises(DBAPIError, match="would drop its address"):
        db.flush()
    db.rollback()


def test_merge_shape_alias_then_delete_succeeds(db, companies):
    tesla, rivian = companies
    db.add(
        SlugAlias(
            entity_kind="company",
            slug=tesla.slug,
            company_id=rivian.id,
            reason="merge",
        )
    )
    db.flush()
    db.delete(tesla)
    db.commit()

    assert db.scalar(select(Company).where(Company.slug == "tesla-q124981765")) is None
    (alias,) = _aliases(db, "company")
    assert alias.company_id == rivian.id


def test_aliases_pin_their_target_row(db, companies):
    tesla, rivian = companies
    tesla.slug = "tesla"
    db.commit()

    # Forward tesla's own address, then try to delete: the rename alias still
    # TARGETS the row, and its FK must block until a merge script re-points.
    db.add(SlugAlias(entity_kind="company", slug="tesla", company_id=rivian.id, reason="merge"))
    db.flush()
    db.delete(tesla)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_allow_address_drop_escape(db, companies):
    tesla, _ = companies
    db.execute(text("SET LOCAL carmanac.allow_address_drop = 'on'"))
    db.delete(tesla)
    db.commit()
    assert not _aliases(db, "company")


def test_alias_shape_constraints(db, companies):
    tesla, _ = companies
    # No target at all.
    db.add(SlugAlias(entity_kind="company", slug="x", reason="rename"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    # Target column of the wrong kind.
    model = Model(company_id=tesla.id, slug="m", name="M")
    db.add(model)
    db.flush()
    db.add(SlugAlias(entity_kind="company", slug="x", model_id=model.id, reason="rename"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    # Company-scoped kind without a scope.
    model = Model(company_id=tesla.id, slug="m", name="M")
    db.add(model)
    db.flush()
    db.add(SlugAlias(entity_kind="model", slug="x", model_id=model.id, reason="rename"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_one_promise_per_global_address(db, companies):
    tesla, rivian = companies
    db.add(SlugAlias(entity_kind="company", slug="x", company_id=tesla.id, reason="rename"))
    db.commit()
    # NULLS NOT DISTINCT: a second promise for the same global address must
    # collide despite both scope columns being NULL.
    db.add(SlugAlias(entity_kind="company", slug="x", company_id=rivian.id, reason="rename"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_alias_addresses_helper_maps_scope_and_target(db, companies):
    tesla, _ = companies
    line = ModelLine(company_id=tesla.id, slug="model-s-line", name="Model S line")
    db.add(line)
    db.commit()
    line.slug = "s-line"
    db.commit()

    assert alias_addresses(db, "model_line") == {(tesla.id, "model-s-line"): line.id}
    assert alias_addresses(db, "company") == {}


def _configuration(db, company: Company, slug: str) -> Configuration:
    """A leaf row on the real spine - the kind carrying almost every address."""
    model = Model(company_id=company.id, slug=f"m-{slug}", name="M")
    db.add(model)
    db.flush()
    kind = db.scalar(select(PeriodKind).where(PeriodKind.code == "model_year"))
    period = CataloguePeriod(
        model_id=model.id, period_kind_id=kind.id, start_year=2019, end_year=2019
    )
    db.add(period)
    db.flush()
    market = db.scalar(select(MarketRegion).where(MarketRegion.code == "US"))
    config = Configuration(catalogue_period_id=period.id, market_region_id=market.id, slug=slug)
    db.add(config)
    db.commit()
    return config


def test_configuration_addresses_are_guarded(db, companies):
    """The leaf kind carries 23,523 of the ~23,900 live addresses; without a
    test here the whole kind could fall out of the trigger install unnoticed."""
    tesla, _ = companies
    config = _configuration(db, tesla, "tesla-model-s-2019-awd")

    config.slug = "tesla-model-s-2019-long-range-awd"
    db.commit()
    (alias,) = _aliases(db, "configuration")
    assert (alias.slug, alias.configuration_id) == ("tesla-model-s-2019-awd", config.id)

    # The retired leaf address cannot be re-minted...
    other = _configuration(db, tesla, "placeholder-slug")
    other.slug = "tesla-model-s-2019-awd"
    with pytest.raises(DBAPIError, match="cannot be taken over"):
        db.flush()
    db.rollback()

    # ...and the row cannot be deleted out from under its address.
    db.delete(config)
    with pytest.raises(DBAPIError, match="would drop its address"):
        db.flush()
    db.rollback()


def test_truncate_cannot_silently_discard_history(db, companies):
    """Row triggers do not fire on TRUNCATE, and CASCADE from any address
    table reaches slug_aliases - the one table nothing can recompute."""
    tesla, _ = companies
    tesla.slug = "tesla"
    db.commit()

    with pytest.raises(DBAPIError, match="discard recorded address history"):
        db.execute(text("TRUNCATE configurations CASCADE"))
    db.rollback()
    assert len(_aliases(db, "company")) == 1

    db.execute(text("SET LOCAL carmanac.allow_address_drop = 'on'"))
    db.execute(text("TRUNCATE slug_aliases"))
    db.commit()
    assert not _aliases(db, "company")


def test_rename_onto_a_hand_written_alias_raises(db, companies):
    """An abandoned merge's forwarding alias is a recorded judgment; a rename
    that would silently re-point it must fail instead."""
    tesla, rivian = companies
    db.add(
        SlugAlias(
            entity_kind="company", slug="tesla-q124981765", company_id=rivian.id, reason="merge"
        )
    )
    db.commit()

    tesla.slug = "tesla"
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
