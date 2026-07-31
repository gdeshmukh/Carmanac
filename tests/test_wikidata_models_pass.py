"""Wikidata models-sweep pass tests (ADR 0012): the ladder, lines,
memberships, direct-case generations, the tabled expansion's waits, and the
labeled-set capture (decision log, resolution reasons, negative registry).

Same synthetic-payload approach as the other reconciler tests: real raw
records for both sources and both sweeps, the real passes, the real
constraints. The fixture cast mirrors the live shapes the ADR names:

- Toyota / `4Runner`   - the prefix-stripped direct match ("Toyota 4Runner")
- Mercedes / `C-Class` - a series-CLASSED entity that IS the as-filed model
  (level is per make), whose members become generations under it
- BMW / `M3`           - no as-filed "3 Series": the series entity becomes a
  LINE, the matched member a membership, and the generation-shaped member
  waits for the year pass
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from carmanac.db.models import (
    ExternalId,
    FieldProvenance,
    Generation,
    MatchDecision,
    Model,
    ModelLine,
    ModelLineMember,
    RawRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.ingest.landing import content_hash
from carmanac.reconcile import policy
from carmanac.reconcile.engine import run_companies_pass
from carmanac.reconcile.sources import wikidata
from carmanac.reconcile.wikidata_models_pass import run_wikidata_models_pass
from tests.test_vpic_models_pass import _land_model, _matched_make, vpic_source  # noqa: F401
from tests.test_wikidata_models_landing import _ENTITY

pytestmark = pytest.mark.integration


def _land_sweep(
    db,
    source: Source,
    qid: str,
    label: str | None = None,
    description: str = "",
    aliases: list[str] | None = None,
    classes: list[str] | None = None,
    makers: list[str] | None = None,
    series_of: list[str] | None = None,
    follows: list[str] | None = None,
    inceptions: list[str] | None = None,
    discontinueds: list[str] | None = None,
) -> RawRecord:
    """One models-sweep record, shaped exactly as the fetcher lands it:
    binding cells, GROUP_CONCAT'd multi-values, the stamped marker."""

    def cell(value: str) -> dict:
        return {"type": "literal", "value": value}

    def uris(qids: list[str] | None) -> dict:
        return cell("|".join(f"{_ENTITY}{q}" for q in sorted(qids or [])))

    payload = {
        "item": {"type": "uri", "value": f"{_ENTITY}{qid}"},
        "itemLabel": cell(label if label is not None else qid),
        "itemDescription": cell(description),
        "aliases": cell("|".join(sorted(aliases or []))),
        "classes": uris(classes),
        "makers": uris(makers),
        "seriesOf": uris(series_of),
        "partsOf": cell(""),
        "followsIds": uris(follows),
        "followedByIds": cell(""),
        "inceptions": cell("|".join(sorted(inceptions or []))),
        "dissolutions": cell(""),
        "prodStarts": cell(""),
        "prodEnds": cell(""),
        "discontinueds": cell("|".join(sorted(discontinueds or []))),
        "sweep": "models",
    }
    rec = RawRecord(
        source_id=source.id,
        external_id=qid,
        content_hash=content_hash(payload),
        payload=payload,
    )
    db.add(rec)
    db.commit()
    return rec


def _decision(db, qid: str) -> MatchDecision:
    return db.scalars(select(MatchDecision).where(MatchDecision.external_id == qid)).one()


# --- rung 3: model correspondence -------------------------------------------


def test_prefix_stripped_match_enriches_model(db, wikidata_source, vpic_source):  # noqa: F811
    """'Toyota 4Runner' <-> as-filed `4Runner`: still mechanical, never fuzzy.
    The match writes the QID onto the model, asserts summary (name stays
    vPIC's - the label must never rename the filing), and logs the decision."""
    toyota = _matched_make(db, wikidata_source, vpic_source, "Q53268", "Toyota", 448)
    _land_model(db, vpic_source, 1, "4Runner", 448, "TOYOTA")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(
        db,
        wikidata_source,
        "Q879",
        "Toyota 4Runner",
        description="mid-size SUV",
        makers=["Q53268"],
    )
    stats = run_wikidata_models_pass(db)
    assert stats.models_matched == 1 and stats.flags_opened == 0

    model = db.scalars(select(Model).where(Model.company_id == toyota.id)).one()
    assert model.name == "4Runner", "the label must not rename the as-filed model"
    assert model.summary == "mid-size SUV"
    assert db.scalars(
        select(ExternalId).where(ExternalId.model_id == model.id, ExternalId.external_id == "Q879")
    ).one()
    assert db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.model_id == model.id,
            FieldProvenance.field_name == "summary",
            FieldProvenance.superseded_by.is_(None),
        )
    ).one()

    decision = _decision(db, "Q879")
    assert (decision.rung, decision.method, decision.outcome) == (
        "3",
        "prefix_stripped_label",
        "matched",
    )


def test_series_classed_entity_is_a_model_when_the_filing_says_so(
    db,
    wikidata_source,
    vpic_source,  # noqa: F811
):
    """The C-Class shape: class does not encode level. A series-classed entity
    matching Mercedes' as-filed row is a MODEL correspondence, and its P179
    members become generations UNDER it (the direct case)."""
    _matched_make(db, wikidata_source, vpic_source, "Q36008", "Mercedes-Benz", 449)
    _land_model(db, vpic_source, 2, "C-Class", 449, "MERCEDES-BENZ")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(
        db,
        wikidata_source,
        "Q100",
        "Mercedes-Benz C-Class",
        classes=["Q59773381"],
        makers=["Q36008"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q200",
        "Mercedes-Benz W205",
        description="fourth generation",
        makers=["Q36008"],
        series_of=["Q100"],
        inceptions=["2014-01-01T00:00:00Z"],
        discontinueds=["2021-01-01T00:00:00Z"],
    )
    stats = run_wikidata_models_pass(db)
    assert stats.models_matched == 1
    assert stats.generations_created == 1
    assert stats.lines_created == 0, "a matched model is never also a line"

    model = db.scalars(select(Model)).one()
    generation = db.scalars(select(Generation)).one()
    assert generation.model_id == model.id
    assert (generation.slug, generation.name) == ("w205", "W205")
    assert generation.chassis_codes == ["W205"]
    assert (generation.start_year, generation.end_year) == (2014, 2021)
    assert db.scalars(
        select(ExternalId).where(
            ExternalId.generation_id == generation.id, ExternalId.external_id == "Q200"
        )
    ).one()
    assert _decision(db, "Q200").outcome == "generation_created"


# --- rungs 4-5: lines, memberships, the waiting generations ------------------


def _bmw_line_fixture(db, wikidata_source, vpic_source):  # noqa: F811
    """BMW with as-filed `M3`; the sweep delivers the series entity, a
    matched member (BMW M3), and a generation-shaped member (BMW E30)."""
    bmw = _matched_make(db, wikidata_source, vpic_source, "Q26678", "BMW", 452)
    _land_model(db, vpic_source, 3, "M3", 452, "BMW")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(
        db,
        wikidata_source,
        "Q466066",
        "BMW 3 Series",
        classes=["Q59773381"],
        makers=["Q26678"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q300",
        "BMW M3",
        makers=["Q26678"],
        series_of=["Q466066"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q838837",
        "BMW E30",
        makers=["Q26678"],
        series_of=["Q466066"],
        follows=["Q730915"],
    )
    return bmw


def test_line_membership_and_waiting_generation(db, wikidata_source, vpic_source):  # noqa: F811
    """The BMW shape end to end: series -> line (never a model row), matched
    member -> membership with raw-record provenance, generation entity of a
    line -> waits for the year pass (no row, no flag)."""
    bmw = _bmw_line_fixture(db, wikidata_source, vpic_source)
    stats = run_wikidata_models_pass(db)

    assert stats.lines_created == 1
    line = db.scalars(select(ModelLine)).one()
    assert (line.company_id, line.slug, line.name) == (bmw.id, "3-series", "3 Series")
    assert db.scalar(select(ExternalId).where(ExternalId.external_id == "Q466066")) is None, (
        "lines hold no external ids - the series QID stays on the raw record"
    )

    assert stats.models_matched == 1  # BMW M3 -> as-filed M3
    member = db.scalars(select(ModelLineMember)).one()
    m3 = db.scalars(select(Model)).one()
    assert (member.model_line_id, member.model_id) == (line.id, m3.id)
    assert member.source_id == wikidata_source.id
    assert member.raw_record_id is not None, "membership is a fact; facts carry provenance"

    assert stats.generations_created == 0 and stats.line_generations_waiting == 1
    assert db.scalars(select(Generation)).all() == []
    assert stats.flags_opened == 0
    assert _decision(db, "Q838837").outcome == "line_generation_waits"


def test_idempotent_rerun_and_stable_decisions(db, wikidata_source, vpic_source):  # noqa: F811
    _bmw_line_fixture(db, wikidata_source, vpic_source)
    run_wikidata_models_pass(db)
    decisions_before = db.scalar(select(MatchDecision.id).order_by(MatchDecision.id.desc()))

    stats = run_wikidata_models_pass(db)
    assert stats.models_matched == 0 and stats.models_refreshed == 1
    assert stats.lines_created == 0 and stats.lines_matched == 1
    assert stats.memberships_inserted == 0
    assert stats.assertions_inserted == 0 and stats.flags_opened == 0
    assert db.scalar(select(MatchDecision.id).order_by(MatchDecision.id.desc())) == (
        decisions_before
    ), "decisions upsert - a re-run must not append rows"


# --- waits and flags (rungs 2 and 6) -----------------------------------------


def test_unheld_maker_and_expansion_wait_silently(db, wikidata_source, vpic_source):  # noqa: F811
    """The §2.2 gate and the §3 tabled expansion: no held maker -> waits; a
    held company's unmatched entity with no near-miss -> waits. No rows, no
    flags - but every wait is a logged decision."""
    _matched_make(db, wikidata_source, vpic_source, "Q53268", "Toyota", 448)

    _land_sweep(db, wikidata_source, "Q400", "Lada Niva", makers=["Q999999"])
    _land_sweep(db, wikidata_source, "Q500", "Toyota Century", makers=["Q53268"])
    stats = run_wikidata_models_pass(db)

    assert stats.waits_no_held_maker == 1 and stats.waits_unmatched == 1
    assert stats.flags_opened == 0
    assert db.scalars(select(Model)).all() == [], "v1 never creates models"
    assert _decision(db, "Q400").outcome == "waits_no_held_maker"
    assert _decision(db, "Q500").outcome == "waits_unmatched"


def test_near_miss_flags_with_candidates(db, wikidata_source, vpic_source):  # noqa: F811
    """Rung 6: a resolvable company plus trigram near-misses is a review
    question, not a silent wait - the boundary between a matching question
    and an expansion question."""
    _matched_make(db, wikidata_source, vpic_source, "Q53268", "Toyota", 448)
    _land_model(db, vpic_source, 1, "4Runner", 448, "TOYOTA")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(db, wikidata_source, "Q600", "Toyota 4Runner II", makers=["Q53268"])
    stats = run_wikidata_models_pass(db)

    assert stats.flags_opened == 1
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "match_review")
    ).one()
    assert flag.detail["reason"] == "no_model_match"
    assert flag.detail["candidates"][0]["slug"] == "4runner"
    assert _decision(db, "Q600").outcome == "flagged_candidates"


def test_negative_registry_blocks_the_match(db, wikidata_source, vpic_source, monkeypatch):  # noqa: F811
    """A recorded human 'not this one' must hold across re-runs: the unique
    exact hit is excluded and the entity waits instead of silently
    re-matching."""
    _matched_make(db, wikidata_source, vpic_source, "Q53268", "Toyota", 448)
    _land_model(db, vpic_source, 1, "4Runner", 448, "TOYOTA")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)
    monkeypatch.setattr(policy, "WIKIDATA_MODEL_NEGATIVES", frozenset({("Q879", "toyota/4runner")}))

    _land_sweep(db, wikidata_source, "Q879", "Toyota 4Runner", makers=["Q53268"])
    stats = run_wikidata_models_pass(db)

    assert stats.models_matched == 0
    assert db.scalar(select(ExternalId).where(ExternalId.external_id == "Q879")) is None


def test_flag_close_records_resolution(db, wikidata_source, vpic_source):  # noqa: F811
    """The review's resolution discipline: a flag dismissed because its
    entity now matches must say why."""
    _matched_make(db, wikidata_source, vpic_source, "Q53268", "Toyota", 448)
    _land_sweep(db, wikidata_source, "Q600", "Toyota 4Runner II", makers=["Q53268"])
    _land_model(db, vpic_source, 1, "4Runner", 448, "TOYOTA")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)
    run_wikidata_models_pass(db)  # opens the near-miss flag

    # A later payload for the same entity now exactly names the model.
    _land_sweep(db, wikidata_source, "Q600", "Toyota 4Runner", makers=["Q53268"])
    stats = run_wikidata_models_pass(db)

    assert stats.models_matched == 1 and stats.flags_dismissed == 1
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "match_review")
    ).one()
    assert flag.status == "dismissed"
    assert flag.detail["resolution"] == "matched:toyota/4runner"


def test_ambiguous_hits_flag_never_guess(db, wikidata_source, vpic_source):  # noqa: F811
    """Two as-filed models hit by one entity's names -> a candidates flag,
    never a pick (the 308 GTB/GTS shape)."""
    _matched_make(db, wikidata_source, vpic_source, "Q27586", "Ferrari", 475)
    _land_model(db, vpic_source, 10, "308 GTB", 475, "FERRARI")
    _land_model(db, vpic_source, 11, "308GTB", 475, "FERRARI")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(db, wikidata_source, "Q700", "Ferrari 308 GTB", makers=["Q27586"])
    stats = run_wikidata_models_pass(db)

    assert stats.models_matched == 0 and stats.flags_opened == 1
    assert _decision(db, "Q700").outcome == "flagged_ambiguous"


# --- name-form evidence ranks (ADR 0013) --------------------------------------


def _matched_make_named(db, wikidata_source, vpic_source, qid, wd_name, make_id, make_name):  # noqa: F811
    """A matched make whose Wikidata company name and vPIC make name differ
    (the 'Audi AG' vs AUDI shape), pinned through the curated registry."""
    from carmanac.db.models import Company
    from carmanac.reconcile.matching import run_vpic_match_pass
    from tests.test_matching import _land_vpic
    from tests.test_reconcile import _land as _land_wd

    _land_wd(db, wikidata_source, qid, label=wd_name)
    run_companies_pass(db, wikidata)
    _land_vpic(db, vpic_source, make_id, make_name)
    policy.VPIC_MATCHES[str(make_id)] = qid
    try:
        run_vpic_match_pass(db)
    finally:
        del policy.VPIC_MATCHES[str(make_id)]
    return db.scalars(select(Company).where(Company.name == wd_name)).one()


def test_make_name_prefix_strips_corporate_name(db, wikidata_source, vpic_source):  # noqa: F811
    """ADR 0013 §1: 'Audi A3' under company 'Audi AG' is a LABEL hit because
    the vPIC make name AUDI is a recorded prefix - not an alias-only artifact."""
    _matched_make_named(db, wikidata_source, vpic_source, "Q23317", "Audi AG", 582, "AUDI")
    _land_model(db, vpic_source, 20, "A3", 582, "AUDI")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(db, wikidata_source, "Q161880", "Audi A3", makers=["Q23317"])
    stats = run_wikidata_models_pass(db)

    assert stats.models_matched == 1 and stats.flags_opened == 0
    decision = _decision(db, "Q161880")
    assert (decision.method, decision.outcome) == ("prefix_stripped_label", "matched")


def test_uncontested_same_brand_alias_attaches(db, wikidata_source, vpic_source):  # noqa: F811
    """The Echo/LeCar species: the alias IS the as-filed US name of the same
    car. Uncontested and same-brand, it attaches - with the method logged so
    every alias-carried attachment stays one audit query."""
    _matched_make(db, wikidata_source, vpic_source, "Q53268", "Toyota", 448)
    _land_model(db, vpic_source, 21, "Echo", 448, "TOYOTA")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(
        db,
        wikidata_source,
        "Q106612214",
        "Toyota Yaris (XP10)",
        makers=["Q53268"],
        aliases=["Toyota Echo", "Toyota Platz"],
    )
    stats = run_wikidata_models_pass(db)

    assert stats.models_matched == 1 and stats.flags_opened == 0
    decision = _decision(db, "Q106612214")
    assert (decision.method, decision.outcome) == ("prefix_stripped_alias", "matched")

    rerun = run_wikidata_models_pass(db)
    assert rerun.models_refreshed == 1
    assert _decision(db, "Q106612214").method == "prefix_stripped_alias", (
        "a refresh must preserve HOW the match was made (ADR 0013 §4)"
    )


def test_cross_badge_alias_never_attaches(db, wikidata_source, vpic_source):  # noqa: F811
    """The Trailseeker shape: an alias-only hit whose label wears a DIFFERENT
    held brand never attaches, even uncontested - it flags as a rebadge."""
    _matched_make(db, wikidata_source, vpic_source, "Q53268", "Toyota", 448)
    _matched_make(db, wikidata_source, vpic_source, "Q172741", "Subaru", 523)
    _land_model(db, vpic_source, 22, "bZ Woodland", 448, "TOYOTA")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(
        db,
        wikidata_source,
        "Q133885141",
        "Subaru Trailseeker",
        makers=["Q53268"],
        aliases=["Toyota bZ Woodland"],
    )
    stats = run_wikidata_models_pass(db)

    assert stats.models_matched == 0 and stats.market_name_flagged == 1
    assert db.scalar(select(ExternalId).where(ExternalId.external_id == "Q133885141")) is None
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "match_review")
    ).one()
    assert flag.detail["reason"] == "market_name_or_rebadge"
    assert flag.detail["cross_badge"] is True
    assert flag.detail["label_brand"] == "subaru"
    assert _decision(db, "Q133885141").outcome == "flagged_market_name_or_rebadge"


def test_label_claimant_beats_alias_claimant(db, wikidata_source, vpic_source):  # noqa: F811
    """The Highlander/Kluger shape: the label claimant is the 1:1
    correspondence; the alias claimant flags as a market name instead of
    forming a cluster."""
    _matched_make(db, wikidata_source, vpic_source, "Q53268", "Toyota", 448)
    _land_model(db, vpic_source, 23, "Highlander", 448, "TOYOTA")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(db, wikidata_source, "Q1421661", "Toyota Highlander", makers=["Q53268"])
    _land_sweep(
        db,
        wikidata_source,
        "Q2447150",
        "Toyota Kluger",
        makers=["Q53268"],
        aliases=["Toyota Highlander"],
    )
    stats = run_wikidata_models_pass(db)

    assert stats.models_matched == 1 and stats.market_name_flagged == 1
    attached = db.scalars(
        select(ExternalId).where(ExternalId.external_id.in_(["Q1421661", "Q2447150"]))
    ).one()
    assert attached.external_id == "Q1421661"
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "match_review")
    ).one()
    assert flag.detail["reason"] == "market_name_or_rebadge"
    assert flag.detail["cross_badge"] is False
    assert flag.detail["co_claimants"] == ["Q1421661"]
    assert _decision(db, "Q1421661").outcome == "matched"


def test_zero_label_claimants_all_flag(db, wikidata_source, vpic_source):  # noqa: F811
    """The Feroza/Rugger shape: two entities are each 'aka Rocky' by alias
    and neither wears the name as its label - nobody attaches, both flag,
    a human picks via the registry."""
    _matched_make(db, wikidata_source, vpic_source, "Q27511", "Daihatsu", 460)
    _land_model(db, vpic_source, 24, "Rocky", 460, "DAIHATSU")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(
        db,
        wikidata_source,
        "Q262713",
        "Daihatsu Feroza",
        makers=["Q27511"],
        aliases=["Daihatsu Rocky"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q11012341",
        "Daihatsu Rugger",
        makers=["Q27511"],
        aliases=["Daihatsu Rocky"],
    )
    stats = run_wikidata_models_pass(db)

    assert stats.models_matched == 0 and stats.market_name_flagged == 2
    assert (
        db.scalar(select(ExternalId).where(ExternalId.external_id.in_(["Q262713", "Q11012341"])))
        is None
    )
    assert _decision(db, "Q262713").detail["co_claimants"] == ["Q11012341"]


# --- shared claims: the label-twin cluster (live finding, 2026-07-30) ---------


def test_shared_claims_flag_never_attach(db, wikidata_source, vpic_source):  # noqa: F811
    """The BMW X5 shape found live: several entities carrying the bare
    nameplate label (the nameplate + its generation entities, chains only, no
    P179) all exact-match one as-filed model. Correspondence is not 1:1 and
    the nameplate is not mechanically identifiable -> nobody attaches, ONE
    flag carries the cluster, and re-runs must not churn assertions."""
    _matched_make(db, wikidata_source, vpic_source, "Q26678", "BMW", 452)
    _land_model(db, vpic_source, 5, "X5", 452, "BMW")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(db, wikidata_source, "Q1000", "BMW X5", description="SUV", makers=["Q26678"])
    _land_sweep(
        db,
        wikidata_source,
        "Q2000",
        "BMW X5",
        description="car model",
        makers=["Q26678"],
        follows=["Q3000"],
    )
    stats = run_wikidata_models_pass(db)

    assert stats.models_matched == 0 and stats.flags_opened == 1
    assert (
        db.scalar(select(ExternalId).where(ExternalId.external_id.in_(["Q1000", "Q2000"]))) is None
    )
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "match_review")
    ).one()
    assert flag.detail["reason"] == "shared_model_match"
    assert [c["qid"] for c in flag.detail["claimants"]] == ["Q1000", "Q2000"]
    assert _decision(db, "Q1000").outcome == "flagged_shared_match"
    assert _decision(db, "Q2000").outcome == "flagged_shared_match"

    rerun = run_wikidata_models_pass(db)
    assert rerun.assertions_inserted == 0 and rerun.assertions_superseded == 0
    assert rerun.flags_opened == 0, "the cluster flag is asked once"


def test_claimant_with_p179_to_claimant_defers_to_generation(
    db,
    wikidata_source,
    vpic_source,  # noqa: F811
):
    """A claimant whose P179 points at another claimant is that claimant's
    generation wearing the nameplate label (Wikidata labels W205 'Mercedes-
    Benz C-Class'): it leaves the cluster, the real nameplate attaches 1:1,
    and the deferred entity becomes a direct-case generation under it."""
    _matched_make(db, wikidata_source, vpic_source, "Q36008", "Mercedes-Benz", 449)
    _land_model(db, vpic_source, 2, "C-Class", 449, "MERCEDES-BENZ")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(
        db,
        wikidata_source,
        "Q100",
        "Mercedes-Benz C-Class",
        classes=["Q59773381"],
        makers=["Q36008"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q200",
        "Mercedes-Benz C-Class",
        description="fourth generation",
        makers=["Q36008"],
        series_of=["Q100"],
        aliases=["Mercedes-Benz C-Class (W205)"],
    )
    stats = run_wikidata_models_pass(db)

    assert stats.models_matched == 1 and stats.flags_opened == 0
    model = db.scalars(select(Model)).one()
    wd_qid = db.scalars(
        select(ExternalId).where(ExternalId.model_id == model.id, ExternalId.external_id.like("Q%"))
    ).one()
    assert wd_qid.external_id == "Q100", (
        "the nameplate entity, not its generation, is the model's QID"
    )
    generation = db.scalars(select(Generation)).one()
    assert generation.model_id == model.id
    assert generation.chassis_codes == ["W205"], "code extracted from the alias parenthetical"
    assert _decision(db, "Q200").outcome == "generation_created"


# --- the sweep partition ------------------------------------------------------


def test_companies_pass_never_sees_sweep_records(db, wikidata_source):
    """The partition the marker exists for: model-sweep records carry model
    classes, which DENY in the companies pass - without the partition a
    model entity would shadow (or leak into) the makes-side view."""
    from tests.test_reconcile import _land as _land_wd

    _land_wd(db, wikidata_source, "Q26678", label="BMW")
    _land_sweep(db, wikidata_source, "Q26678", "BMW", classes=["Q3231690"])
    _land_sweep(db, wikidata_source, "Q300", "BMW M3", classes=["Q3231690"])

    stats = run_companies_pass(db, wikidata)
    assert stats.processed == 1, "only the makes-sweep record is a companies-pass unit"
    assert db.scalars(select(ExternalId)).one().external_id == "Q26678"


def test_company_mapped_qid_waits(db, wikidata_source, vpic_source):  # noqa: F811
    """A sweep entity whose QID already corresponds to a held COMPANY cannot
    also map to a model (ADR 0011 §4's 1:1 rule)."""
    _matched_make(db, wikidata_source, vpic_source, "Q26678", "BMW", 452)
    _land_sweep(db, wikidata_source, "Q26678", "BMW", classes=["Q3231690"])

    stats = run_wikidata_models_pass(db)
    assert stats.company_entities == 1
    assert _decision(db, "Q26678").outcome == "company_entity"
