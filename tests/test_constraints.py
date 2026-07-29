"""Schema-constraint tests: the pre-reconciler review, made permanent.

Each test here re-runs a verification that was done once by hand against the
live database and recorded in PROGRESS.md prose (R2, R3/R4, ADR 0005). The
reconciler will WRITE through these constraints; they are what turns its
re-runs into supersession instead of duplication, so they must hold on every
future schema change, not just on the day they were reviewed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from carmanac.db.models import (
    CataloguePeriod,
    Company,
    CompanyRole,
    CompanyRoleAssignment,
    Configuration,
    DerivationType,
    FieldProvenance,
    Generation,
    Model,
    PeriodKind,
    RawRecord,
    ReconciliationFlag,
    Source,
    VehicleDerivation,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def graph(db, wikidata_source):
    """A minimal five-level entity graph plus a second generation, for the
    derivation tests."""
    company = Company(slug="bmw", name="BMW")
    db.add(company)
    db.flush()
    model = Model(company_id=company.id, slug="3-series", name="3 Series")
    db.add(model)
    db.flush()
    gen_a = Generation(model_id=model.id, slug="e46", name="E46")
    gen_b = Generation(model_id=model.id, slug="g20", name="G20")
    db.add_all([gen_a, gen_b])
    db.flush()
    kind = db.scalar(select(PeriodKind).where(PeriodKind.code == "model_year"))
    assert kind is not None, "period_kinds seed rows missing from migration"
    period = CataloguePeriod(
        generation_id=gen_a.id, period_kind_id=kind.id, start_year=2002, end_year=2002
    )
    db.add(period)
    db.flush()
    return {
        "company": company,
        "model": model,
        "gen_a": gen_a,
        "gen_b": gen_b,
        "period": period,
        "period_kind": kind,
        "source": wikidata_source,
    }


def _market_region_id(db) -> int:
    from sqlalchemy import text

    return db.execute(text("SELECT id FROM market_regions ORDER BY id LIMIT 1")).scalar_one()


# --- R2: field_provenance accepts exactly one live assertion per source -----


def test_second_live_assertion_same_source_rejected(db, graph):
    """The exact defect proven live pre-R2: three contradictory 'BMW' name
    assertions, same source, all accepted. uq_field_provenance_live must
    reject the second while superseded history stays storable."""
    db.add(
        FieldProvenance(
            company_id=graph["company"].id,
            field_name="name",
            observed_value="BMW",
            source_id=graph["source"].id,
        )
    )
    db.commit()

    db.add(
        FieldProvenance(
            company_id=graph["company"].id,
            field_name="name",
            observed_value="Bayerische Motoren Werke",
            source_id=graph["source"].id,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_supersession_allows_history_and_one_live_row(db, graph):
    """Also documents THE SUPERSESSION ORDER, which this test discovered the
    hard way: the naive sequence (insert new, then point old at it) is
    impossible - the live-unique index rejects the second live row before the
    old one can be repointed, and the old one cannot reference an id that does
    not exist yet. The working dance is: (1) retire old by pointing it at
    ITSELF, freeing the live slot; (2) insert new; (3) repoint old at new.
    The reconciler's supersede helper must use this order (or the schema must
    make it unnecessary - flagged for the ADR 0007 amendment)."""
    old = FieldProvenance(
        company_id=graph["company"].id,
        field_name="name",
        observed_value="BMW",
        source_id=graph["source"].id,
    )
    db.add(old)
    db.commit()

    old.superseded_by = old.id  # (1) retire: old is no longer live
    db.flush()
    new = FieldProvenance(
        company_id=graph["company"].id,
        field_name="name",
        observed_value="BMW AG",
        source_id=graph["source"].id,
    )
    db.add(new)  # (2) now the live slot is free
    db.flush()
    old.superseded_by = new.id  # (3) point history at its successor
    db.commit()

    live = db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.company_id == graph["company"].id,
            FieldProvenance.superseded_by.is_(None),
        )
    ).all()
    assert len(live) == 1
    assert live[0].observed_value == "BMW AG"


def test_different_sources_may_disagree(db, graph):
    """Cross-source disagreement is the reconciler's input, not a violation -
    one live assertion PER SOURCE is the rule."""
    other = Source(name="NHTSA vPIC", tier=1)
    db.add(other)
    db.flush()
    db.add_all(
        [
            FieldProvenance(
                company_id=graph["company"].id,
                field_name="name",
                observed_value="BMW",
                source_id=graph["source"].id,
            ),
            FieldProvenance(
                company_id=graph["company"].id,
                field_name="name",
                observed_value="BMW OF NORTH AMERICA",
                source_id=other.id,
            ),
        ]
    )
    db.commit()  # must not raise


# --- R3/R4: configurations natural key, NULLS NOT DISTINCT ------------------


def test_sparse_duplicate_configuration_rejected(db, graph):
    """Two all-NULL-dimension configurations for the same year+market are the
    same configuration. Default UNIQUE semantics would let both insert -
    NULLS NOT DISTINCT is what makes the key bite for exactly the sparse
    records that need dedup most."""
    market = _market_region_id(db)
    db.add(Configuration(catalogue_period_id=graph["period"].id, market_region_id=market, slug="a"))
    db.commit()
    db.add(Configuration(catalogue_period_id=graph["period"].id, market_region_id=market, slug="b"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_distinct_trims_coexist(db, graph):
    market = _market_region_id(db)
    db.add_all(
        [
            Configuration(
                catalogue_period_id=graph["period"].id,
                market_region_id=market,
                slug="330i",
                trim_name="330i",
            ),
            Configuration(
                catalogue_period_id=graph["period"].id,
                market_region_id=market,
                slug="325i",
                trim_name="325i",
            ),
        ]
    )
    db.commit()  # must not raise


# --- ADR 0009: catalogue_periods ---------------------------------------------


def test_duplicate_open_ended_period_rejected(db, graph):
    """NULLS NOT DISTINCT on the period natural key: an open-ended period
    (end_year NULL, still in production) must collide with its duplicate -
    default UNIQUE semantics would let re-runs append forever."""
    db.add(
        CataloguePeriod(
            generation_id=graph["gen_b"].id,
            period_kind_id=graph["period_kind"].id,
            start_year=2019,
        )
    )
    db.commit()
    db.add(
        CataloguePeriod(
            generation_id=graph["gen_b"].id,
            period_kind_id=graph["period_kind"].id,
            start_year=2019,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_period_ending_before_it_starts_rejected(db, graph):
    db.add(
        CataloguePeriod(
            generation_id=graph["gen_b"].id,
            period_kind_id=graph["period_kind"].id,
            start_year=2005,
            end_year=1998,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_both_kinds_coexist_on_one_generation(db, graph):
    """ADR 0009's mixed granularity: US model years and a Euro production
    period describe the same generation side by side."""
    pp = db.scalar(select(PeriodKind).where(PeriodKind.code == "production_period"))
    assert pp is not None, "period_kinds seed rows missing from migration"
    db.add_all(
        [
            CataloguePeriod(
                generation_id=graph["gen_a"].id,
                period_kind_id=graph["period_kind"].id,
                start_year=2003,
                end_year=2003,
            ),
            CataloguePeriod(
                generation_id=graph["gen_a"].id,
                period_kind_id=pp.id,
                start_year=1998,
                end_year=2005,
            ),
        ]
    )
    db.commit()  # must not raise


# --- ADR 0005: vehicle_derivations ------------------------------------------


def _derivation_type(db) -> DerivationType:
    dt = db.scalar(select(DerivationType).where(DerivationType.code == "tuned"))
    assert dt is not None, "derivation_types seed rows missing from migration"
    return dt


def test_self_derivation_rejected(db, graph):
    db.add(
        VehicleDerivation(
            base_generation_id=graph["gen_a"].id,
            company_id=graph["company"].id,
            derivation_type_id=_derivation_type(db).id,
            derived_generation_id=graph["gen_a"].id,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_duplicate_null_derived_claim_rejected(db, graph):
    """The reconciler re-run trap: the common case (derived side NULL) must
    collide with itself, which only NULLS NOT DISTINCT provides."""
    claim = dict(
        base_generation_id=graph["gen_a"].id,
        company_id=graph["company"].id,
        derivation_type_id=_derivation_type(db).id,
    )
    db.add(VehicleDerivation(**claim))
    db.commit()
    db.add(VehicleDerivation(**claim))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_two_product_lines_from_one_base_coexist(db, graph):
    """Singer DLS and Classic Study shape: same base, company and type,
    different derived generations - both are real, both must insert."""
    dt = _derivation_type(db).id
    db.add_all(
        [
            VehicleDerivation(
                base_generation_id=graph["gen_a"].id,
                company_id=graph["company"].id,
                derivation_type_id=dt,
                derived_generation_id=graph["gen_b"].id,
            ),
            VehicleDerivation(
                base_generation_id=graph["gen_a"].id,
                company_id=graph["company"].id,
                derivation_type_id=dt,
            ),
        ]
    )
    db.commit()  # must not raise


# --- ADR 0007 §8 (F7): association tables are per-source assertion stores ---


def test_two_sources_corroborate_one_role(db, graph):
    """The vPIC arbitration scenario: Wikidata and vPIC both asserting
    "manufacturer" must BOTH hold live rows - corroboration is visible, and
    either source can retract independently. Impossible under the old
    one-row-per-fact composite PK."""
    vpic = Source(name="NHTSA vPIC", tier=1)
    db.add(vpic)
    db.flush()
    role_id = db.execute(
        select(CompanyRole.id).where(CompanyRole.code == "manufacturer")
    ).scalar_one()
    db.add_all(
        [
            CompanyRoleAssignment(
                company_id=graph["company"].id,
                company_role_id=role_id,
                source_id=graph["source"].id,
            ),
            CompanyRoleAssignment(
                company_id=graph["company"].id,
                company_role_id=role_id,
                source_id=vpic.id,
            ),
        ]
    )
    db.commit()  # must not raise

    live = db.scalars(
        select(CompanyRoleAssignment).where(
            CompanyRoleAssignment.company_id == graph["company"].id,
            CompanyRoleAssignment.superseded_by.is_(None),
        )
    ).all()
    assert len(live) == 2


def test_same_source_duplicate_role_assertion_rejected(db, graph):
    """One live assertion per (fact, source) - a re-running reconciler must
    supersede, never append."""
    role_id = db.execute(
        select(CompanyRole.id).where(CompanyRole.code == "manufacturer")
    ).scalar_one()
    claim = dict(
        company_id=graph["company"].id,
        company_role_id=role_id,
        source_id=graph["source"].id,
    )
    db.add(CompanyRoleAssignment(**claim))
    db.commit()
    db.add(CompanyRoleAssignment(**claim))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# --- ADR 0007 §8: reconciliation_flags carry their kind's shape --------------


@pytest.fixture()
def raw_record(db, wikidata_source):
    rec = RawRecord(source_id=wikidata_source.id, content_hash="test", payload={"qid": "Q1"})
    db.add(rec)
    db.flush()
    return rec


def test_entity_flag_requires_exactly_one_entity(db, graph):
    """An entity-scoped kind with an empty arc points at nothing - rejected by
    flag_shape_matches_kind, same device as field_provenance's arc CHECK."""
    db.add(ReconciliationFlag(kind="field_conflict", field_name="name"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_admission_review_rejects_entity(db, graph, raw_record):
    """admission_review is record-scoped BY DEFINITION - the record was
    quarantined, so no entity exists to attach to. An arc column set on one is
    a contradiction the CHECK must catch."""
    db.add(
        ReconciliationFlag(
            kind="admission_review",
            company_id=graph["company"].id,
            raw_record_id=raw_record.id,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_admission_review_requires_raw_record(db, graph):
    """Without raw_record_id an admission_review flag references nothing at
    all - there would be no way to know WHAT is quarantined."""
    db.add(ReconciliationFlag(kind="admission_review"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_both_flag_shapes_insert(db, graph, raw_record):
    db.add_all(
        [
            ReconciliationFlag(
                kind="multi_value",
                company_id=graph["company"].id,
                field_name="founded_year",
                detail={"values": ["1909", "1998"]},
                source_id=graph["source"].id,
            ),
            ReconciliationFlag(
                kind="admission_review",
                raw_record_id=raw_record.id,
                detail={"unclassified": ["Q431289"]},
            ),
        ]
    )
    db.commit()  # must not raise


def test_second_source_may_assert_same_derivation(db, graph):
    """Per-source shape applies to derivations too: two sources claiming
    "this company tuned this generation" coexist as two live rows."""
    other = Source(name="NHTSA vPIC", tier=1)
    db.add(other)
    db.flush()
    dt = _derivation_type(db).id
    db.add_all(
        [
            VehicleDerivation(
                base_generation_id=graph["gen_a"].id,
                company_id=graph["company"].id,
                derivation_type_id=dt,
                source_id=graph["source"].id,
            ),
            VehicleDerivation(
                base_generation_id=graph["gen_a"].id,
                company_id=graph["company"].id,
                derivation_type_id=dt,
                source_id=other.id,
            ),
        ]
    )
    db.commit()  # must not raise
