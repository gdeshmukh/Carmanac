"""vPIC year pass tests (ADR 0014): model_year periods under matched models,
pure time with no generation links; unmatched models wait; re-runs converge.

Same synthetic-payload approach as the other reconciler tests: real raw
records, the real passes, the real constraints.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from carmanac.db.models import CataloguePeriod, MatchDecision, Model, RawRecord
from carmanac.ingest.landing import content_hash
from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass
from carmanac.reconcile.vpic_years_pass import run_vpic_years_pass
from tests.test_vpic_models_pass import _land_model, _matched_make, vpic_source  # noqa: F401

pytestmark = pytest.mark.integration


def _land_model_years(db, source, model_id: int, years: list[int], make_id: int) -> RawRecord:
    payload = {
        "model_id": model_id,
        "make_id": make_id,
        "make_name": "TOYOTA",
        "model_name": "4Runner",
        "years": years,
    }
    rec = RawRecord(
        source_id=source.id,
        external_id=f"modelyears:{model_id}",
        content_hash=content_hash(payload),
        payload=payload,
    )
    db.add(rec)
    db.commit()
    return rec


def test_periods_created_under_matched_model(db, wikidata_source, vpic_source):  # noqa: F811
    """Each year becomes one model_year period (start = end) under the model
    - and nothing carries a generation: placement is configuration-level."""
    _matched_make(db, wikidata_source, vpic_source, "Q53268", "Toyota", 448)
    _land_model(db, vpic_source, 1, "4Runner", 448, "TOYOTA")
    run_vpic_models_pass(db)
    _land_model_years(db, vpic_source, 1, [1984, 1985, 2001], 448)

    stats = run_vpic_years_pass(db)

    assert stats.processed == 1 and stats.periods_created == 3
    model = db.scalars(select(Model).where(Model.slug == "4runner")).one()
    periods = db.scalars(select(CataloguePeriod).where(CataloguePeriod.model_id == model.id)).all()
    assert sorted(p.start_year for p in periods) == [1984, 1985, 2001]
    assert all(p.end_year == p.start_year for p in periods)
    decision = db.scalars(
        select(MatchDecision).where(MatchDecision.pass_name == "vpic_years")
    ).one()
    assert decision.outcome == "periods_written"
    assert decision.detail == {"years": 3, "created": 3}


def test_rerun_is_a_noop(db, wikidata_source, vpic_source):  # noqa: F811
    _matched_make(db, wikidata_source, vpic_source, "Q53268", "Toyota", 448)
    _land_model(db, vpic_source, 1, "4Runner", 448, "TOYOTA")
    run_vpic_models_pass(db)
    _land_model_years(db, vpic_source, 1, [1984, 1985], 448)
    run_vpic_years_pass(db)

    rerun = run_vpic_years_pass(db)

    assert rerun.periods_created == 0 and rerun.periods_existing == 2
    assert db.scalar(select(CataloguePeriod).where(True).limit(1)) is not None
    assert (
        db.scalars(select(MatchDecision).where(MatchDecision.pass_name == "vpic_years"))
        .one()
        .detail["created"]
        == 0
    )


def test_unmatched_model_waits(db, wikidata_source, vpic_source):  # noqa: F811
    """A year list whose model has no held row creates nothing and flags
    nothing - the make's open question must not fan out (ADR 0010 §1)."""
    _land_model_years(db, vpic_source, 99, [2001], 448)

    stats = run_vpic_years_pass(db)

    assert stats.waits_unmatched_model == 1 and stats.periods_created == 0
    decision = db.scalars(
        select(MatchDecision).where(MatchDecision.pass_name == "vpic_years")
    ).one()
    assert decision.outcome == "waits_unmatched_model"
