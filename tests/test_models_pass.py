"""vPIC models pass tests (ADR 0010): the matched-make gate, the identity
ladder, slug collisions, name provenance, and idempotency.

Same synthetic-payload approach as the other reconciler tests: real raw
records for both sources, the real passes, the real constraints.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from carmanac.db.models import (
    Company,
    ExternalId,
    FieldProvenance,
    Model,
    RawRecord,
    ReconciledRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.ingest.landing import content_hash
from carmanac.reconcile import policy
from carmanac.reconcile.engine import run_companies_pass
from carmanac.reconcile.matching import run_vpic_match_pass
from carmanac.reconcile.models_pass import run_vpic_models_pass
from carmanac.reconcile.sources import wikidata
from tests.test_matching import _land_vpic
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


def _land_model(
    db, source: Source, model_id: int, model_name: str, make_id: int, make_name: str
) -> RawRecord:
    payload = {
        "model_id": model_id,
        "model_name": model_name,
        "make_id": make_id,
        "make_name": make_name,
        "vehicle_types": ["Passenger Car"],
    }
    rec = RawRecord(
        source_id=source.id,
        external_id=f"model:{model_id}",
        content_hash=content_hash(payload),
        payload=payload,
    )
    db.add(rec)
    db.commit()
    return rec


def _matched_make(db, wikidata_source, vpic_source, qid: str, label: str, make_id: int) -> Company:
    """A company both sources agree on: the precondition for §1."""
    _land_wd(db, wikidata_source, qid, label=label)
    run_companies_pass(db, wikidata)
    _land_vpic(db, vpic_source, make_id, label.upper())
    run_vpic_match_pass(db)
    return db.scalars(select(Company).where(Company.name == label)).one()


def _second_santana_makeid(db, vpic_source) -> None:
    """The live shape behind §2.3: LAND ROVER SANTANA (13766) is a second
    MakeId pinned to the same company as SANTANA (13765)."""
    _land_vpic(db, vpic_source, 13766, "LAND ROVER SANTANA")
    policy.VPIC_MATCHES["13766"] = "Q265465"
    try:
        run_vpic_match_pass(db)
    finally:
        del policy.VPIC_MATCHES["13766"]


def test_creates_models_under_matched_make(db, wikidata_source, vpic_source):
    """The happy path: a nameplate row, its `model:<id>` external id, and a
    vPIC-sourced `name` assertion under the model arc."""
    honda = _matched_make(db, wikidata_source, vpic_source, "Q9584", "Honda", 474)
    _land_model(db, vpic_source, 1861, "Accord", 474, "HONDA")
    _land_model(db, vpic_source, 1862, "FCX Clarity", 474, "HONDA")

    stats = run_vpic_models_pass(db)
    assert stats.models_created == 2 and stats.flags_opened == 0

    models = db.scalars(select(Model).order_by(Model.slug)).all()
    assert [(m.company_id, m.slug, m.name) for m in models] == [
        (honda.id, "accord", "Accord"),
        (honda.id, "fcx-clarity", "FCX Clarity"),  # mixed case stored as asserted
    ]
    ids = {
        e.external_id: e.model_id
        for e in db.scalars(select(ExternalId).where(ExternalId.model_id.isnot(None)))
    }
    assert set(ids) == {"model:1861", "model:1862"}

    assertions = db.scalars(
        select(FieldProvenance).where(FieldProvenance.model_id.isnot(None))
    ).all()
    assert {(a.field_name, a.observed_value, a.source_id) for a in assertions} == {
        ("name", "Accord", vpic_source.id),
        ("name", "FCX Clarity", vpic_source.id),
    }
    assert all(a.superseded_by is None and a.raw_record_id is not None for a in assertions)


def test_unmatched_make_creates_nothing_and_flags_nothing(db, wikidata_source, vpic_source):
    """§1: one open make question must not fan out into model-shaped copies.
    The record is still marked reconciled-seen, so a later run picks it up."""
    _land_vpic(db, vpic_source, 9999, "MYSTERY MAKE")  # landed, never matched
    record = _land_model(db, vpic_source, 5000, "Enigma", 9999, "MYSTERY MAKE")

    stats = run_vpic_models_pass(db)
    assert stats.skipped_unmatched_make == 1 and stats.models_created == 0
    assert db.scalar(select(func.count()).select_from(Model)) == 0
    assert db.scalar(select(func.count()).select_from(ReconciliationFlag)) == 0
    assert db.get(ReconciledRecord, record.id) is not None


def test_matched_make_later_picks_up_its_models(db, wikidata_source, vpic_source):
    """The mechanical conversion §1 promises: the make matches after the first
    models run, and the next run creates its nameplates with no extra work."""
    _land_vpic(db, vpic_source, 473, "MAZDA")
    _land_model(db, vpic_source, 2000, "RX-7", 473, "MAZDA")
    assert run_vpic_models_pass(db).skipped_unmatched_make == 1

    _land_wd(db, wikidata_source, "Q35996", label="Mazda")
    run_companies_pass(db, wikidata)
    run_vpic_match_pass(db)

    stats = run_vpic_models_pass(db)
    assert stats.models_created == 1
    model = db.scalars(select(Model)).one()
    assert (model.slug, model.name) == ("rx-7", "RX-7")


def test_slug_collision_flags_rather_than_suffixes(db, wikidata_source, vpic_source):
    """§2.3, the Santana shape: two MakeIds resolving to one company deliver
    the same nameplate twice. The lower ModelId keeps the slug; the higher one
    is flagged for merge-or-suffix and creates NO row - an auto-suffixed
    `110-wb-2` would manufacture duplicate identity."""
    santana = _matched_make(db, wikidata_source, vpic_source, "Q265465", "Santana Motor", 13765)
    _second_santana_makeid(db, vpic_source)

    _land_model(db, vpic_source, 36863, '110" WB', 13765, "SANTANA")
    _land_model(db, vpic_source, 36864, '110" WB', 13766, "LAND ROVER SANTANA")

    stats = run_vpic_models_pass(db)
    assert stats.models_created == 1 and stats.flags_opened == 1

    model = db.scalars(select(Model)).one()
    assert (model.company_id, model.slug) == (santana.id, "110-wb")
    ids = {
        e.external_id for e in db.scalars(select(ExternalId).where(ExternalId.model_id.isnot(None)))
    }
    assert ids == {"model:36863"}  # the lower ModelId won

    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "match_review")
    ).one()
    assert flag.status == "open"
    assert flag.model_id is None and flag.raw_record_id is not None  # record-scoped
    assert flag.detail["reason"] == "slug_collision"
    assert flag.detail["slug"] == "110-wb"
    assert flag.detail["existing_model"]["id"] == model.id

    # Re-running must not ask the same question twice.
    assert run_vpic_models_pass(db).flags_opened == 0
    assert db.scalar(select(func.count()).select_from(ReconciliationFlag)) == 1


def test_same_slug_under_two_companies_is_not_a_collision(db, wikidata_source, vpic_source):
    """The natural key is (company, slug): every make may have its own '3'."""
    _matched_make(db, wikidata_source, vpic_source, "Q26678", "BMW", 452)
    _matched_make(db, wikidata_source, vpic_source, "Q35996", "Mazda", 473)
    _land_model(db, vpic_source, 3001, "3", 452, "BMW")
    _land_model(db, vpic_source, 3002, "3", 473, "MAZDA")

    stats = run_vpic_models_pass(db)
    assert stats.models_created == 2 and stats.flags_opened == 0
    assert {m.slug for m in db.scalars(select(Model))} == {"3"}


def test_collision_flag_dismissed_when_a_human_merges(db, wikidata_source, vpic_source):
    """Resolving the flag means attaching the ModelId to the existing row. The
    next run climbs rung 1, refreshes, and dismisses the stale question."""
    santana = _matched_make(db, wikidata_source, vpic_source, "Q265465", "Santana Motor", 13765)
    _second_santana_makeid(db, vpic_source)
    _land_model(db, vpic_source, 36863, '110" WB', 13765, "SANTANA")
    _land_model(db, vpic_source, 36864, '110" WB', 13766, "LAND ROVER SANTANA")
    run_vpic_models_pass(db)

    model = db.scalars(select(Model)).one()
    db.add(ExternalId(model_id=model.id, source_id=vpic_source.id, external_id="model:36864"))
    db.commit()

    stats = run_vpic_models_pass(db)
    assert stats.flags_dismissed == 1 and stats.models_created == 0
    assert stats.models_matched == 2  # both ModelIds now resolve to the one row
    flag = db.scalars(select(ReconciliationFlag)).one()
    assert flag.status == "dismissed" and flag.resolved_at is not None
    assert db.scalar(select(func.count()).select_from(Model)) == 1
    assert santana.id == model.company_id


def test_renamed_model_supersedes_rather_than_appends(db, wikidata_source, vpic_source):
    """§3: `name` is a reconciled fact. vPIC renaming a ModelId supersedes its
    live assertion and re-projects; the slug (public identity) does not move."""
    _matched_make(db, wikidata_source, vpic_source, "Q9584", "Honda", 474)
    _land_model(db, vpic_source, 1861, "Accord", 474, "HONDA")
    run_vpic_models_pass(db)

    _land_model(db, vpic_source, 1861, "Accord Hybrid", 474, "HONDA")  # new payload, same ModelId
    stats = run_vpic_models_pass(db)
    assert stats.assertions_superseded == 1 and stats.models_created == 0

    model = db.scalars(select(Model)).one()
    assert (model.name, model.slug) == ("Accord Hybrid", "accord")
    rows = db.scalars(
        select(FieldProvenance)
        .where(FieldProvenance.model_id == model.id)
        .order_by(FieldProvenance.id)
    ).all()
    assert [(r.observed_value, r.superseded_by is None) for r in rows] == [
        ("Accord", False),
        ("Accord Hybrid", True),
    ]


def test_models_pass_idempotent(db, wikidata_source, vpic_source):
    _matched_make(db, wikidata_source, vpic_source, "Q9584", "Honda", 474)
    _land_model(db, vpic_source, 1861, "Accord", 474, "HONDA")
    _land_model(db, vpic_source, 1862, "Civic", 474, "HONDA")
    run_vpic_models_pass(db)

    def counts() -> tuple:
        return (
            db.scalar(select(func.count()).select_from(Model)),
            db.scalar(select(func.count()).select_from(ExternalId)),
            db.scalar(select(func.count()).select_from(FieldProvenance)),
            db.scalar(select(func.count()).select_from(ReconciliationFlag)),
        )

    before = counts()
    stats = run_vpic_models_pass(db)
    assert stats.models_matched == 2 and stats.models_created == 0
    assert stats.assertions_inserted == 0 and stats.flags_opened == 0
    assert counts() == before
