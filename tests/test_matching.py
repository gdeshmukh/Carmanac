"""vPIC match pass tests (ADR 0008): the ladder, corroboration-admission,
flag lifecycle, and idempotency.

Uses the same synthetic-payload approach as the other reconciler tests: real
raw records for both sources, the real pass, the real constraints.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from carmanac.db.models import (
    Company,
    CompanyRoleAssignment,
    ExternalId,
    RawRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.ingest.landing import content_hash
from carmanac.reconcile import policy
from carmanac.reconcile.engine import run_companies_pass
from carmanac.reconcile.matching import normalize_name, run_vpic_match_pass
from carmanac.reconcile.sources import wikidata
from tests.test_reconcile import _land as _land_wd

pytestmark = pytest.mark.integration


@pytest.fixture()
def vpic_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == "NHTSA vPIC"))
    if source is None:
        source = Source(name="NHTSA vPIC", tier=1, base_url="https://vpic.nhtsa.dot.gov")
        db.add(source)
        db.commit()
    return source


def _land_vpic(db, source, make_id: int, name: str, types=("Passenger Car",)) -> RawRecord:
    payload = {"make_id": make_id, "make_name": name, "vehicle_types": sorted(types)}
    rec = RawRecord(
        source_id=source.id,
        external_id=f"make:{make_id}",
        content_hash=content_hash(payload),
        payload=payload,
    )
    db.add(rec)
    db.commit()
    return rec


def test_normalize_name():
    assert normalize_name("ASTON MARTIN") == normalize_name("Aston Martin")
    assert normalize_name("Lynk & Co") == normalize_name("LYNK & CO")
    assert normalize_name("BMW") != normalize_name("BMW M")


def test_exact_match_attaches_id_and_vpic_role(db, wikidata_source, vpic_source):
    """vPIC 'ASTON MARTIN' + Wikidata-admitted 'Aston Martin' -> one company,
    two external-id namespaces, a second (vPIC-sourced) role assertion."""
    _land_wd(db, wikidata_source, "Q27074", label="Aston Martin")
    run_companies_pass(db, wikidata)
    _land_vpic(db, vpic_source, 440, "ASTON MARTIN")

    stats = run_vpic_match_pass(db)
    assert stats.matched_existing == 1 and stats.flagged == 0

    company = db.scalars(select(Company)).one()
    ids = {(e.source_id, e.external_id) for e in db.scalars(select(ExternalId))}
    assert ids == {(wikidata_source.id, "Q27074"), (vpic_source.id, "make:440")}
    roles = db.scalars(
        select(CompanyRoleAssignment).where(
            CompanyRoleAssignment.company_id == company.id,
            CompanyRoleAssignment.superseded_by.is_(None),
        )
    ).all()
    assert {r.source_id for r in roles} == {wikidata_source.id, vpic_source.id}


def test_corroboration_admits_quarantined_and_resolves_flag(db, wikidata_source, vpic_source):
    """The queue-shrinking path: a boilerplate-classed (quarantined) Wikidata
    entity that vPIC lists is admitted on vPIC's evidence; its admission flag
    resolves as corroborated_by_vpic, not by a human."""
    _land_wd(
        db, wikidata_source, "Q6742", label="Peugeot Motors of America", classes=("Q4830453",)
    )  # boilerplate-only -> quarantine
    # not pinned under this label/QID? Q6742 IS pinned - use an unpinned QID:
    _land_wd(db, wikidata_source, "Q999001", label="Mystery Motors", classes=("Q4830453",))
    run_companies_pass(db, wikidata)
    open_admission = db.scalars(
        select(ReconciliationFlag).where(
            ReconciliationFlag.kind == "admission_review",
            ReconciliationFlag.status == "open",
        )
    ).all()
    assert len(open_admission) == 1  # Mystery Motors (Peugeot admits via pin)

    _land_vpic(db, vpic_source, 600, "MYSTERY MOTORS")
    stats = run_vpic_match_pass(db)
    assert stats.corroborated_created == 1

    company = db.scalars(select(Company).where(Company.name == "Mystery Motors")).one()
    assert company.summary is None  # created through the full engine path
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "admission_review")
    ).one()
    assert flag.status == "resolved"
    assert flag.detail["resolution"] == "corroborated_by_vpic"
    # vPIC asserted the role; Wikidata did not (no target class).
    roles = db.scalars(select(CompanyRoleAssignment)).all()
    assert [r.source_id for r in roles] == [vpic_source.id]

    # And the wikidata pass keeps updating it despite the quarantine verdict
    # (cross-source admission): re-run must be a no-op, not a re-quarantine.
    wd_stats = run_companies_pass(db, wikidata)
    assert wd_stats.flags_opened == 0


def test_no_match_flags_with_candidates(db, wikidata_source, vpic_source):
    _land_wd(db, wikidata_source, "Q100", label="Aston Martin")
    run_companies_pass(db, wikidata)
    _land_vpic(db, vpic_source, 700, "ASTON MARTN LAGONDA")  # near-miss, no exact

    stats = run_vpic_match_pass(db)
    assert stats.flagged == 1
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "match_review")
    ).one()
    assert flag.status == "open" and flag.company_id is None
    assert any(c["slug"] == "aston-martin" for c in flag.detail["candidates"])

    run_vpic_match_pass(db)  # open flag not duplicated
    assert db.scalar(select(func.count()).select_from(ReconciliationFlag)) == 1


def test_ambiguous_match_flags_not_guesses(db, wikidata_source, vpic_source, monkeypatch):
    # The second Eagle needs a curated pin to mint at all (ADR 0019 §2);
    # with both live, the make name is ambiguous and must flag, not guess.
    monkeypatch.setitem(policy.COMPANY_SLUG_OVERRIDES, "Q200", "eagle-talon")
    _land_wd(db, wikidata_source, "Q100", label="Eagle", classes=("Q786820",))
    _land_wd(db, wikidata_source, "Q200", label="Eagle", classes=("Q786820",))
    run_companies_pass(db, wikidata)
    _land_vpic(db, vpic_source, 800, "EAGLE")

    stats = run_vpic_match_pass(db)
    assert stats.matched_existing == 0 and stats.flagged == 1
    flag = db.scalars(select(ReconciliationFlag)).one()
    assert flag.detail["reason"] == "ambiguous"
    assert len(flag.detail["candidates"]) == 2


def test_registry_match_wins(db, wikidata_source, vpic_source):
    """Rung 1: a curated MakeId->QID judgment outranks name mismatch."""
    _land_wd(db, wikidata_source, "Q35996", label="Mazda")
    run_companies_pass(db, wikidata)
    _land_vpic(db, vpic_source, 900, "TOYO KOGYO")  # no name overlap

    policy.VPIC_MATCHES["900"] = "Q35996"
    try:
        stats = run_vpic_match_pass(db)
    finally:
        del policy.VPIC_MATCHES["900"]
    assert stats.matched_existing == 1
    ids = {e.external_id for e in db.scalars(select(ExternalId))}
    assert ids == {"Q35996", "make:900"}


def test_registry_preempts_wrong_unique_exact_name(db, wikidata_source, vpic_source):
    """The AUDI shape: after the brand-artifact merges, the only exact-name
    'AUDI' company is SAIC's Chinese AUDI marque - a real, separate make -
    while the right target is labeled 'Audi AG'. The curated registry entry
    (rung 1) must win before the exact-name rung guesses wrong."""
    _land_wd(db, wikidata_source, "Q23317", label="Audi AG")
    _land_wd(db, wikidata_source, "Q136087723", label="AUDI", classes=("Q10429667",))
    run_companies_pass(db, wikidata)
    _land_vpic(db, vpic_source, 582, "AUDI")

    stats = run_vpic_match_pass(db)
    assert stats.matched_existing == 1 and stats.flagged == 0
    audi_ag = db.scalars(select(Company).where(Company.name == "Audi AG")).one()
    vpic_row = db.scalars(select(ExternalId).where(ExternalId.external_id == "make:582")).one()
    assert vpic_row.company_id == audi_ag.id


def test_model_records_are_not_read_as_makes(db, wikidata_source, vpic_source):
    """Record kinds are told apart by the external-id PREFIX, not payload shape.

    A model payload carries `make_id`/`make_name` too, so the shape test this
    pass shipped with read every landed model record as a make and attached
    `model:<id>` to a company - 2,018 wrong company external ids waiting on the
    next run. The prefix is the durable marker.
    """
    _land_wd(db, wikidata_source, "Q35996", label="Mazda")
    run_companies_pass(db, wikidata)
    _land_vpic(db, vpic_source, 473, "MAZDA")
    db.add(
        RawRecord(
            source_id=vpic_source.id,
            external_id="model:2000",
            content_hash="modelhash",
            payload={
                "model_id": 2000,
                "model_name": "RX-7",
                "make_id": 473,
                "make_name": "MAZDA",
                "vehicle_types": ["Passenger Car"],
            },
        )
    )
    db.commit()

    stats = run_vpic_match_pass(db)
    assert stats.processed == 1  # the make only
    ids = {e.external_id for e in db.scalars(select(ExternalId))}
    assert ids == {"Q35996", "make:473"}


def test_match_pass_idempotent(db, wikidata_source, vpic_source):
    _land_wd(db, wikidata_source, "Q27074", label="Aston Martin")
    run_companies_pass(db, wikidata)
    _land_vpic(db, vpic_source, 440, "ASTON MARTIN")
    run_vpic_match_pass(db)

    def counts() -> tuple:
        return (
            db.scalar(select(func.count()).select_from(ExternalId)),
            db.scalar(select(func.count()).select_from(CompanyRoleAssignment)),
            db.scalar(select(func.count()).select_from(ReconciliationFlag)),
        )

    before = counts()
    stats = run_vpic_match_pass(db)
    assert stats.already_matched == 1 and stats.matched_existing == 0
    assert counts() == before
