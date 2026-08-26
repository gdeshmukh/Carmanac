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
from sqlalchemy import func, select

from carmanac.db.models import (
    Company,
    ExternalId,
    FieldProvenance,
    Generation,
    GenerationModelLink,
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
from tests.test_reconcile import _land as _land_wd
from tests.test_vpic_models_pass import _land_model, _matched_make, vpic_source  # noqa: F401
from tests.test_wikidata_models_landing import _ENTITY
from tests.test_wikipedia_pass import _land_article

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
    parts_of: list[str] | None = None,
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
        "partsOf": uris(parts_of),
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
    assert generation.company_id == model.company_id
    link = db.scalars(select(GenerationModelLink)).one()
    assert (link.generation_id, link.model_id) == (generation.id, model.id)
    assert link.source_id == wikidata_source.id and link.raw_record_id is not None
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


# --- the line destination rule (ADR 0011 §2, amended) ------------------------


def _stranded_line_fixture(db, wikidata_source, vpic_source):  # noqa: F811
    """The stranded shape: Wikidata's maker is a model-less holding company
    while vPIC filed the models under the carmaker - Mercedes-Benz Group
    holding the C-Class series entity, Mercedes-Benz holding the models."""
    mb = _matched_make(db, wikidata_source, vpic_source, "Q36008", "Mercedes-Benz", 449)
    _land_model(db, vpic_source, 7, "C-Class", 449, "MERCEDES-BENZ")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)
    _land_wd(db, wikidata_source, "Q36009", label="Mercedes-Benz Group")
    run_companies_pass(db, wikidata)
    _land_sweep(
        db,
        wikidata_source,
        "Q1000",
        "Mercedes-Benz C-Class",
        classes=["Q59773381"],
        makers=["Q36009"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q1001",
        "Mercedes-Benz C300",
        makers=["Q36009"],
        series_of=["Q1000"],
    )
    return mb


def test_stranded_line_files_under_the_carmaker(db, wikidata_source, vpic_source):  # noqa: F811
    """A series whose maker holds zero models files under the model-holding
    company its own name wears: C-Class lands under Mercedes-Benz, prefix-
    stripped, not under Mercedes-Benz Group - and the re-run re-derives it
    exactly there."""
    mb = _stranded_line_fixture(db, wikidata_source, vpic_source)
    stats = run_wikidata_models_pass(db)

    assert stats.lines_created == 1 and stats.flags_opened == 0
    line = db.scalars(select(ModelLine)).one()
    assert (line.company_id, line.slug, line.name) == (mb.id, "c-class", "C-Class")

    stats = run_wikidata_models_pass(db)
    assert stats.lines_created == 0 and stats.lines_matched == 1
    assert db.scalars(select(ModelLine)).one().company_id == mb.id


def test_held_line_stays_under_its_maker(db, wikidata_source, vpic_source):  # noqa: F811
    """A WIKIDATA_LINE_HOLDS entry pins the row where it sits: the pass files
    it under the maker exactly as before the destination rule, unstripped and
    unflagged, until the hold's owner resolves it."""
    _stranded_line_fixture(db, wikidata_source, vpic_source)
    policy.WIKIDATA_LINE_HOLDS[("mercedes-benz-group", "Mercedes-Benz C-Class")] = (
        "generation-grain"
    )
    try:
        stats = run_wikidata_models_pass(db)
    finally:
        del policy.WIKIDATA_LINE_HOLDS[("mercedes-benz-group", "Mercedes-Benz C-Class")]

    assert stats.lines_created == 1 and stats.flags_opened == 0
    line = db.scalars(select(ModelLine)).one()
    group = db.scalars(select(Company).where(Company.slug == "mercedes-benz-group")).one()
    assert (line.company_id, line.slug, line.name) == (
        group.id,
        "mercedes-benz-c-class",
        "Mercedes-Benz C-Class",
    )


def test_line_brand_namesake_flags_and_stays(db, wikidata_source, vpic_source):  # noqa: F811
    """Two companies named Mercury: the vote never picks between namesakes.
    The row keeps filing under its model-less maker - staying put is not a
    guess - and the open question rides a match_review flag, asked once."""
    _land_wd(db, wikidata_source, "Q2000", label="Ford Motor Company")
    _land_wd(db, wikidata_source, "Q2001", label="Mercury")
    _land_wd(db, wikidata_source, "Q2002", label="Mercury")
    run_companies_pass(db, wikidata)
    _land_sweep(
        db,
        wikidata_source,
        "Q1100",
        "Mercury Marquis",
        classes=["Q59773381"],
        makers=["Q2000"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q1101",
        "Mercury Marquis Brougham",
        makers=["Q2000"],
        series_of=["Q1100"],
    )
    stats = run_wikidata_models_pass(db)

    assert stats.lines_created == 1 and stats.flags_opened == 1
    line = db.scalars(select(ModelLine)).one()
    fomoco = db.scalars(select(Company).where(Company.name == "Ford Motor Company")).one()
    assert (line.company_id, line.name) == (fomoco.id, "Mercury Marquis")
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "match_review")
    ).one()
    assert flag.detail["reason"] == "line_brand_ambiguous"
    assert [c["company"] for c in flag.detail["candidates"]] == ["Mercury", "Mercury"]

    stats = run_wikidata_models_pass(db)
    assert stats.lines_matched == 1 and stats.flags_opened == 0


def test_line_brand_model_less_destination_flags(db, wikidata_source, vpic_source):  # noqa: F811
    """The name's brand exists but holds no models: relocating there would
    re-strand the row, so it stays under its maker with the question open."""
    _land_wd(db, wikidata_source, "Q3000", label="Auto Union")
    _land_wd(db, wikidata_source, "Q3001", label="DKW")
    run_companies_pass(db, wikidata)
    _land_sweep(
        db,
        wikidata_source,
        "Q1200",
        "DKW Meisterklasse",
        classes=["Q59773381"],
        makers=["Q3000"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q1201",
        "DKW F89",
        makers=["Q3000"],
        series_of=["Q1200"],
    )
    stats = run_wikidata_models_pass(db)

    assert stats.lines_created == 1 and stats.flags_opened == 1
    line = db.scalars(select(ModelLine)).one()
    auto_union = db.scalars(select(Company).where(Company.name == "Auto Union")).one()
    assert line.company_id == auto_union.id
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "match_review")
    ).one()
    assert flag.detail["reason"] == "line_brand_model_less"


def test_vote_flip_awaits_relocation_never_duplicates(db, wikidata_source, vpic_source):  # noqa: F811
    """A flagged vote that later turns clean must not mint a duplicate at the
    destination: derivation never abandons an existing maker-side row. The
    open flag flips to line_awaits_relocation and the row stays put until the
    relocation script's reviewed run."""
    _land_wd(db, wikidata_source, "Q3000", label="Auto Union")
    _land_wd(db, wikidata_source, "Q3001", label="DKW")
    run_companies_pass(db, wikidata)
    _land_sweep(
        db,
        wikidata_source,
        "Q1200",
        "DKW Meisterklasse",
        classes=["Q59773381"],
        makers=["Q3000"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q1201",
        "DKW F89",
        makers=["Q3000"],
        series_of=["Q1200"],
    )
    stats = run_wikidata_models_pass(db)
    assert stats.lines_created == 1 and stats.flags_opened == 1

    dkw = db.scalars(select(Company).where(Company.name == "DKW")).one()
    db.add(Model(company_id=dkw.id, slug="f89", name="F89"))
    db.commit()
    stats = run_wikidata_models_pass(db)

    assert stats.lines_created == 0 and stats.flags_dismissed == 0
    line = db.scalars(select(ModelLine)).one()
    auto_union = db.scalars(select(Company).where(Company.name == "Auto Union")).one()
    assert line.company_id == auto_union.id
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "match_review")
    ).one()
    assert flag.status == "open"
    assert flag.detail["reason"] == "line_awaits_relocation"

    stats = run_wikidata_models_pass(db)
    assert stats.lines_created == 0 and stats.flags_opened == 0


def test_line_wearing_its_model_less_maker_stays(db, wikidata_source, vpic_source):  # noqa: F811
    """The maker-in-wearers arm: a line whose name states its own model-less
    maker stays with it - TVR's lines are TVR's before any US filing."""
    _land_wd(db, wikidata_source, "Q4000", label="TVR")
    run_companies_pass(db, wikidata)
    _land_sweep(
        db,
        wikidata_source,
        "Q1400",
        "TVR Tuscan",
        classes=["Q59773381"],
        makers=["Q4000"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q1401",
        "TVR Tuscan Speed Six",
        makers=["Q4000"],
        series_of=["Q1400"],
    )
    stats = run_wikidata_models_pass(db)

    assert stats.lines_created == 1 and stats.flags_opened == 0
    line = db.scalars(select(ModelLine)).one()
    tvr = db.scalars(select(Company).where(Company.name == "TVR")).one()
    assert (line.company_id, line.slug, line.name) == (tvr.id, "tuscan", "Tuscan")


def test_model_holding_maker_keeps_foreign_badged_line(db, wikidata_source, vpic_source):  # noqa: F811
    """A maker that holds models keeps its foreign-badged lines: Lexus GS
    under Toyota is the maker's own assertion, and namesake companies would
    poison any vote there. No move, no flag."""
    toyota = _matched_make(db, wikidata_source, vpic_source, "Q53268", "Toyota", 448)
    _land_model(db, vpic_source, 1, "4Runner", 448, "TOYOTA")
    lexus = _matched_make(db, wikidata_source, vpic_source, "Q35919", "Lexus", 453)
    _land_model(db, vpic_source, 2, "GS", 453, "LEXUS")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)
    _land_sweep(
        db,
        wikidata_source,
        "Q1300",
        "Lexus GS",
        classes=["Q59773381"],
        makers=["Q53268"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q1301",
        "Lexus GS 300",
        makers=["Q53268"],
        series_of=["Q1300"],
    )
    stats = run_wikidata_models_pass(db)

    assert stats.flags_opened == 0
    line = db.scalars(select(ModelLine)).one()
    assert (line.company_id, line.name) == (toyota.id, "Lexus GS")
    assert lexus.id != toyota.id


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
    # Keyed on the model's own source id, not its address: a page rename must
    # not re-arm a match a human rejected.
    monkeypatch.setattr(policy, "WIKIDATA_MODEL_NEGATIVES", frozenset({("Q879", "model:1")}))

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


def test_label_brand_respects_word_boundaries(db, wikidata_source, vpic_source):  # noqa: F811
    """The Ranger/Range Rover shape: normalization strips spacing, so a raw
    startswith would read the held brand 'Ranger' out of 'Range Rover (1st
    generation)' and raise a false cross-badge. A brand prefix must end on a
    word boundary of the label."""
    _matched_make(db, wikidata_source, vpic_source, "Q35907", "Land Rover", 444)
    _matched_make(db, wikidata_source, vpic_source, "Q2130910", "Ranger", 999)
    _land_model(db, vpic_source, 31, "Range Rover", 444, "LAND ROVER")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    _land_sweep(
        db,
        wikidata_source,
        "Q5257063",
        "Range Rover (1st generation)",
        makers=["Q35907"],
        aliases=["Range Rover"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q7292685",
        "Land Rover Range Rover (P38A)",
        makers=["Q35907"],
        aliases=["Range Rover"],
    )
    stats = run_wikidata_models_pass(db)

    assert stats.models_matched == 0 and stats.market_name_flagged == 2
    by_qid = {
        f.detail["qid"]: f.detail
        for f in db.scalars(
            select(ReconciliationFlag).where(ReconciliationFlag.kind == "match_review")
        )
    }
    assert by_qid["Q5257063"]["cross_badge"] is False
    assert "label_brand" not in by_qid["Q5257063"], (
        "'Range Rover (1st generation)' wears no held brand - 'Ranger' is a "
        "substring across a word boundary, not a prefix the label wears"
    )
    assert by_qid["Q7292685"]["cross_badge"] is False
    assert by_qid["Q7292685"]["label_brand"] == "land-rover"


def test_open_flag_detail_refreshes_when_answer_changes(db, wikidata_source, vpic_source):  # noqa: F811
    """An open flag is the CURRENT question: when the computation behind a
    same-reason flag changes between runs (a brand-artifact merge flips the
    cross-badge verdict), the detail refreshes rather than preserving the
    stale answer. Closes stay immutable."""
    _matched_make(db, wikidata_source, vpic_source, "Q35907", "Land Rover", 444)
    _land_model(db, vpic_source, 31, "Range Rover", 444, "LAND ROVER")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)

    rec = _land_sweep(
        db,
        wikidata_source,
        "Q5257063",
        "Range Rover (1st generation)",
        makers=["Q35907"],
        aliases=["Range Rover"],
    )
    _land_sweep(
        db,
        wikidata_source,
        "Q7292685",
        "Land Rover Range Rover (P38A)",
        makers=["Q35907"],
        aliases=["Range Rover"],
    )
    stale = ReconciliationFlag(
        kind="match_review",
        raw_record_id=rec.id,
        source_id=wikidata_source.id,
        detail={
            "reason": "market_name_or_rebadge",
            "qid": "Q5257063",
            "cross_badge": True,
            "label_brand": "range-rover",
        },
    )
    db.add(stale)
    db.commit()

    run_wikidata_models_pass(db)
    db.refresh(stale)
    assert stale.status == "open"
    assert stale.detail["cross_badge"] is False
    assert "label_brand" not in stale.detail


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


# --- shared claims: the label-duplicate cluster (live finding, 2026-07-30) ---------


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
    assert generation.company_id == model.company_id
    link = db.scalars(select(GenerationModelLink)).one()
    assert (link.generation_id, link.model_id) == (generation.id, model.id)
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


# --- rung 6, mint (ADR 0012 §7) -----------------------------------------------


def test_mint_creates_nameplates_under_registry_company(
    db,
    wikidata_source,
    vpic_source,  # noqa: F811
    monkeypatch,
):
    """Under a registry company, entities that fell through every rung mint
    nameplate rows: name is the prefix-stripped label, asserted with
    provenance, the QID attaches, and a re-run refreshes instead of
    re-minting."""
    citroen = _matched_make(db, wikidata_source, vpic_source, "Q6746", "Citroën", 900)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q6746", "citroen")

    _land_sweep(
        db, wikidata_source, "Q1000", "Citroën 2CV", description="economy car", makers=["Q6746"]
    )
    _land_sweep(db, wikidata_source, "Q1001", "Citroën BX", makers=["Q6746"])

    stats = run_wikidata_models_pass(db)
    assert stats.models_minted == 2 and stats.mint_contested == 0

    models = {m.slug: m for m in db.scalars(select(Model).where(Model.company_id == citroen.id))}
    assert set(models) == {"2cv", "bx"}
    assert models["2cv"].name == "2CV" and models["2cv"].summary == "economy car"
    assert db.scalars(
        select(ExternalId).where(
            ExternalId.model_id == models["2cv"].id, ExternalId.external_id == "Q1000"
        )
    ).one()
    assert db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.model_id == models["2cv"].id,
            FieldProvenance.field_name == "name",
        )
    ).one()
    assert _decision(db, "Q1000").outcome == "model_minted"

    rerun = run_wikidata_models_pass(db)
    assert rerun.models_minted == 0 and rerun.models_refreshed == 2
    assert db.scalar(select(func.count(Model.id)).where(Model.company_id == citroen.id)) == 2


def test_mint_label_duplicates_flag_as_a_group_and_none_mints(
    db,
    wikidata_source,
    vpic_source,  # noqa: F811
    monkeypatch,
):
    """Two different-era cars sharing a nameplate label: minting either would
    enthrone an arbitrary era at the plain address, so the whole group flags
    and waits for one naming ruling - never first-wins, never a suffix."""
    citroen = _matched_make(db, wikidata_source, vpic_source, "Q6746", "Citroën", 900)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q6746", "citroen")

    _land_sweep(
        db, wikidata_source, "Q1100", "Citroën C8", description="1929 saloon", makers=["Q6746"]
    )
    _land_sweep(
        db, wikidata_source, "Q1101", "Citroën C8", description="executive car", makers=["Q6746"]
    )

    stats = run_wikidata_models_pass(db)
    assert stats.models_minted == 0 and stats.mint_contested == 2
    assert db.scalar(select(func.count(Model.id)).where(Model.company_id == citroen.id)) == 0
    flags = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "match_review")
    ).all()
    duplicate_flags = [f for f in flags if f.detail.get("reason") == "mint_label_duplicates"]
    assert len(duplicate_flags) == 2
    assert duplicate_flags[0].detail["duplicates"] == ["Q1100", "Q1101"]
    assert _decision(db, "Q1100").outcome == "flagged_mint_duplicates"


def test_mint_conditions_hold_entities_out(db, wikidata_source, vpic_source, monkeypatch):  # noqa: F811
    """The under-admission gates: a label wearing another held marque, an
    excluded word, and membership evidence each keep an entity waiting."""
    abarth = _matched_make(db, wikidata_source, vpic_source, "Q26823", "Abarth", 901)
    _matched_make(db, wikidata_source, vpic_source, "Q27597", "Fiat", 902)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q26823", "abarth")

    _land_sweep(db, wikidata_source, "Q1200", "Fiat 850", makers=["Q26823"])
    _land_sweep(
        db,
        wikidata_source,
        "Q1201",
        "Abarth 2000",
        description="sports prototype",
        makers=["Q26823"],
    )
    _land_sweep(db, wikidata_source, "Q1202", "Abarth 500", makers=["Q26823"], series_of=["Q77777"])

    stats = run_wikidata_models_pass(db)
    assert stats.models_minted == 0 and stats.mint_contested == 0
    assert db.scalar(select(func.count(Model.id)).where(Model.company_id == abarth.id)) == 0
    assert stats.waits_unmatched == 3


def test_mint_occupied_slug_flags_instead_of_minting(db, wikidata_source, vpic_source, monkeypatch):  # noqa: F811
    """The accent-divergence collision: vPIC files 'Mehari', the label says
    'Méhari' - different normalized names (no rung-3 match), one slug. The
    entity flags with the occupant as candidate; a human rules match or duplicate."""
    citroen = _matched_make(db, wikidata_source, vpic_source, "Q6746", "Citroën", 900)
    _land_model(db, vpic_source, 77, "Mehari", 900, "CITROËN")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q6746", "citroen")

    _land_sweep(db, wikidata_source, "Q1300", "Citroën Méhari", makers=["Q6746"])

    stats = run_wikidata_models_pass(db)
    assert stats.models_minted == 0 and stats.mint_contested == 1
    assert db.scalar(select(func.count(Model.id)).where(Model.company_id == citroen.id)) == 1
    assert _decision(db, "Q1300").outcome == "flagged_mint_occupied"
    assert _decision(db, "Q1300").detail["model"] == "citroen/mehari"


def test_mint_strips_the_marque_word_of_a_longer_company_name(
    db,
    wikidata_source,
    vpic_source,  # noqa: F811
    monkeypatch,
):
    """'Škoda 100' under the company filed as 'Škoda Auto': the recorded-name
    strip cannot fire, the shared-token fallback cuts the marque word."""
    skoda = _matched_make(db, wikidata_source, vpic_source, "Q29637", "Škoda Auto", 903)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q29637", "skoda-auto")

    _land_sweep(db, wikidata_source, "Q1400", "Škoda 100", makers=["Q29637"])

    stats = run_wikidata_models_pass(db)
    assert stats.models_minted == 1
    model = db.scalars(select(Model).where(Model.company_id == skoda.id)).one()
    assert (model.name, model.slug) == ("100", "100")


def test_mint_era_siblings_contest_as_one_group(db, wikidata_source, vpic_source, monkeypatch):  # noqa: F811
    """A nameplate and its roman- or parenthetical-dressed siblings are one
    naming ruling: Wikidata files generations as sibling model entities, and
    minting them flat would put three Dokkers beside each other."""
    dacia = _matched_make(db, wikidata_source, vpic_source, "Q27460", "Dacia", 904)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q27460", "dacia")

    _land_sweep(db, wikidata_source, "Q1500", "Dacia Lodgy", makers=["Q27460"])
    _land_sweep(db, wikidata_source, "Q1501", "Dacia Lodgy I", makers=["Q27460"])
    _land_sweep(db, wikidata_source, "Q1502", "Dacia Lodgy II", makers=["Q27460"])
    _land_sweep(db, wikidata_source, "Q1503", "Dacia Bigster", makers=["Q27460"])

    stats = run_wikidata_models_pass(db)
    assert (stats.models_minted, stats.mint_contested) == (1, 3)
    model = db.scalars(select(Model).where(Model.company_id == dacia.id)).one()
    assert model.name == "Bigster"
    assert _decision(db, "Q1500").outcome == "flagged_mint_duplicates"
    assert _decision(db, "Q1500").detail["duplicates"] == ["Q1500", "Q1501", "Q1502"]


def test_mint_paren_sibling_of_an_existing_model_contests(
    db,
    wikidata_source,
    vpic_source,  # noqa: F811
    monkeypatch,
):
    """'A110 (2017)' beside a HELD A110 is the same question as beside a
    candidate one - the era-stripped base is occupied, so it flags."""
    alpine = _matched_make(db, wikidata_source, vpic_source, "Q26944", "Alpine", 905)
    _land_model(db, vpic_source, 88, "A110", 905, "ALPINE")
    from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

    run_vpic_models_pass(db)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q26944", "alpine")

    _land_sweep(db, wikidata_source, "Q1600", "Alpine A110 (2017)", makers=["Q26944"])

    stats = run_wikidata_models_pass(db)
    assert (stats.models_minted, stats.mint_contested) == (0, 1)
    assert db.scalar(select(func.count(Model.id)).where(Model.company_id == alpine.id)) == 1
    assert _decision(db, "Q1600").outcome == "flagged_mint_duplicates"


def test_mint_holds_out_multi_maker_entities(db, wikidata_source, vpic_source, monkeypatch):  # noqa: F811
    """The C1 shape: a JV car asserts two makers, and only one is held. The
    sole-maker condition reads the ENTITY's claim, not our resolution of it -
    a rebadge judgment is never a mint."""
    citroen = _matched_make(db, wikidata_source, vpic_source, "Q6746", "Citroën", 900)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q6746", "citroen")

    _land_sweep(db, wikidata_source, "Q1700", "Citroën C1", makers=["Q6746", "Q77770"])

    stats = run_wikidata_models_pass(db)
    assert (stats.models_minted, stats.waits_unmatched) == (0, 1)
    assert db.scalar(select(func.count(Model.id)).where(Model.company_id == citroen.id)) == 0


def test_mint_holds_out_part_of_membership(db, wikidata_source, vpic_source, monkeypatch):  # noqa: F811
    """P361 is membership evidence exactly like P179: an entity that is part
    of something we do not hold may be a generation of it, so it waits."""
    _matched_make(db, wikidata_source, vpic_source, "Q6746", "Citroën", 900)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q6746", "citroen")

    _land_sweep(
        db, wikidata_source, "Q1710", "Citroën DS3 Racing X", makers=["Q6746"], parts_of=["Q77771"]
    )

    stats = run_wikidata_models_pass(db)
    assert (stats.models_minted, stats.waits_unmatched) == (0, 1)


def test_mint_label_sanity_gates(db, wikidata_source, vpic_source, monkeypatch):  # noqa: F811
    """The bare-QID fallback label and a label that IS the company name both
    wait: neither names a car."""
    citroen = _matched_make(db, wikidata_source, vpic_source, "Q6746", "Citroën", 900)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q6746", "citroen")

    _land_sweep(db, wikidata_source, "Q1720", makers=["Q6746"])  # label defaults to the QID
    _land_sweep(db, wikidata_source, "Q1721", "Citroën", makers=["Q6746"])

    stats = run_wikidata_models_pass(db)
    assert stats.models_minted == 0
    assert db.scalar(select(func.count(Model.id)).where(Model.company_id == citroen.id)) == 0


def test_mint_exclude_words_match_either_field_any_case(
    db,
    wikidata_source,
    vpic_source,  # noqa: F811
    monkeypatch,
):
    """The exclude gate reads label and description together, case-blind."""
    _matched_make(db, wikidata_source, vpic_source, "Q6746", "Citroën", 900)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q6746", "citroen")

    _land_sweep(db, wikidata_source, "Q1730", "Citroën Sport Concept", makers=["Q6746"])
    _land_sweep(
        db,
        wikidata_source,
        "Q1731",
        "Citroën ZX Grand Raid",
        description="RALLY raider",
        makers=["Q6746"],
    )

    stats = run_wikidata_models_pass(db)
    assert (stats.models_minted, stats.waits_unmatched) == (0, 2)


def test_mint_nonconforming_slug_flags(db, wikidata_source, vpic_source, monkeypatch):  # noqa: F811
    """A label with no ASCII form slugifies to nothing: the drift guard flags
    for a curated romanization instead of minting an unaddressable row."""
    citroen = _matched_make(db, wikidata_source, vpic_source, "Q6746", "Citroën", 900)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q6746", "citroen")

    _land_sweep(db, wikidata_source, "Q1740", "Citroën 雪铁龙", makers=["Q6746"])

    stats = run_wikidata_models_pass(db)
    assert (stats.models_minted, stats.mint_contested) == (0, 1)
    assert db.scalar(select(func.count(Model.id)).where(Model.company_id == citroen.id)) == 0
    assert _decision(db, "Q1740").outcome == "flagged_mint_nonconforming"


def test_mint_contested_rerun_is_stable(db, wikidata_source, vpic_source, monkeypatch):  # noqa: F811
    """Contested stays contested, identically: the same duplicates re-contest on
    every run, the open flag refreshes rather than duplicates, and nothing
    ever mints behind the ruling's back."""
    citroen = _matched_make(db, wikidata_source, vpic_source, "Q6746", "Citroën", 900)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q6746", "citroen")

    _land_sweep(
        db, wikidata_source, "Q1750", "Citroën C8", description="1929 saloon", makers=["Q6746"]
    )
    _land_sweep(
        db, wikidata_source, "Q1751", "Citroën C8", description="executive car", makers=["Q6746"]
    )

    first = run_wikidata_models_pass(db)
    second = run_wikidata_models_pass(db)
    assert (first.mint_contested, second.mint_contested) == (2, 2)
    assert (first.flags_opened, second.flags_opened) == (2, 0)
    assert db.scalar(select(func.count(Model.id)).where(Model.company_id == citroen.id)) == 0
    flags = db.scalars(select(ReconciliationFlag).where(ReconciliationFlag.status == "open")).all()
    assert len([f for f in flags if (f.detail or {}).get("reason") == "mint_label_duplicates"]) == 2
    assert _decision(db, "Q1750").outcome == "flagged_mint_duplicates"


# --- rung 7, duplicate rulings (ADR 0012 §7) ----------------------------------------


def _wikipedia_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == "Wikipedia (English)"))
    if source is None:
        source = Source(name="Wikipedia (English)", tier=2, base_url="https://en.wikipedia.org")
        db.add(source)
        db.commit()
    return source


def test_duplicate_ruling_resolves_model_and_dated_era(
    db,
    wikidata_source,
    vpic_source,  # noqa: F811
    monkeypatch,
):
    """The ruled shape: one nameplate model (the plain-titled entity), the
    era entity a generation under it named by its article's parenthetical,
    both flags dismissed with recorded resolutions, and a re-run stable."""
    citroen = _matched_make(db, wikidata_source, vpic_source, "Q6746", "Citroën", 900)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q6746", "citroen")
    _land_sweep(
        db, wikidata_source, "Q1100", "Citroën C6", description="executive car", makers=["Q6746"]
    )
    _land_sweep(
        db, wikidata_source, "Q1101", "Citroën C6", description="1929 saloon", makers=["Q6746"]
    )
    run_wikidata_models_pass(db)  # contests the pair, opens the duplicate flags

    wikipedia = _wikipedia_source(db)
    _land_article(
        db,
        wikipedia,
        "Q1101",
        "Citroën C6 (1928–1932)",
        "{{Infobox automobile\n| production = 1928–1932\n}}",
    )
    monkeypatch.setitem(policy.WIKIDATA_DUPLICATE_NAMEPLATES, "Q1100", "model:citroen/c6")
    monkeypatch.setitem(policy.WIKIDATA_DUPLICATE_NAMEPLATES, "Q1101", "era:citroen/c6")

    stats = run_wikidata_models_pass(db)
    assert stats.duplicates_resolved == 2 and stats.mint_contested == 0

    model = db.scalars(select(Model).where(Model.company_id == citroen.id)).one()
    assert (model.slug, model.name) == ("c6", "C6")
    assert db.scalars(
        select(ExternalId).where(ExternalId.model_id == model.id, ExternalId.external_id == "Q1100")
    ).one(), "the plain-titled entity attaches at model grain"

    generation = db.scalars(
        select(Generation)
        .join(ExternalId, ExternalId.generation_id == Generation.id)
        .where(ExternalId.external_id == "Q1101")
    ).one()
    assert generation.name == "C6 (1928–1932)", "the era wears its article's parenthetical"
    assert generation.slug is None, "a span may separate eras but may not wear an address"
    assert db.scalars(
        select(GenerationModelLink).where(
            GenerationModelLink.generation_id == generation.id,
            GenerationModelLink.model_id == model.id,
            GenerationModelLink.superseded_by.is_(None),
        )
    ).one()
    open_duplicates = db.scalars(
        select(ReconciliationFlag).where(
            ReconciliationFlag.kind == "match_review", ReconciliationFlag.status == "open"
        )
    ).all()
    assert not [f for f in open_duplicates if f.detail.get("reason") == "mint_label_duplicates"]
    dismissed = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.status == "dismissed")
    ).all()
    assert any(f.detail.get("resolution") == "duplicate_model:citroen/c6" for f in dismissed)
    assert any(f.detail.get("resolution") == "duplicate_era:citroen/c6" for f in dismissed)

    rerun = run_wikidata_models_pass(db)
    assert rerun.duplicates_resolved == 0 and rerun.assertions_inserted == 0
    assert rerun.flags_opened == 0 and rerun.generations_refreshed >= 1
    db.refresh(generation)
    assert generation.name == "C6 (1928–1932)", "the refresh keeps the ruled era name"


def test_duplicate_era_without_evidence_stays_flagged(
    db,
    wikidata_source,
    vpic_source,  # noqa: F811
    monkeypatch,
):
    _matched_make(db, wikidata_source, vpic_source, "Q6746", "Citroën", 900)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q6746", "citroen")
    _land_sweep(db, wikidata_source, "Q1200", "Citroën Pony", makers=["Q6746"])
    _land_sweep(db, wikidata_source, "Q1201", "Citroën Pony II", makers=["Q6746"])
    run_wikidata_models_pass(db)
    monkeypatch.setitem(policy.WIKIDATA_DUPLICATE_NAMEPLATES, "Q1201", "era:citroen/pony")

    stats = run_wikidata_models_pass(db)
    assert stats.duplicates_resolved == 0
    assert _decision(db, "Q1201").outcome == "duplicate_era_awaits_span"
    assert stats.models_minted == 0, "a half-ruled group never mints its leftover"
    assert _decision(db, "Q1200").outcome == "flagged_mint_duplicates"
    assert _decision(db, "Q1200").detail["duplicates"] == ["Q1200", "Q1201"]
    open_flags = db.scalars(
        select(ReconciliationFlag).where(
            ReconciliationFlag.kind == "match_review", ReconciliationFlag.status == "open"
        )
    ).all()
    assert [f for f in open_flags if f.detail.get("reason") == "mint_label_duplicates"], (
        "identity without time resolves nothing"
    )


def test_unruled_claimant_of_a_ruled_base_flags_never_attaches(
    db,
    wikidata_source,
    vpic_source,  # noqa: F811
    monkeypatch,
):
    """An address the registry rules over takes no QID by label match: the
    plain-labeled entity outside the registry is the group's open grain
    question, not the nameplate's anchor."""
    citroen = _matched_make(db, wikidata_source, vpic_source, "Q6746", "Citro\u00ebn", 900)
    monkeypatch.setitem(policy.WIKIDATA_MINT_COMPANIES, "Q6746", "citroen")
    db.add(Model(company_id=citroen.id, slug="pony", name="Pony"))
    db.commit()
    monkeypatch.setitem(policy.WIKIDATA_DUPLICATE_NAMEPLATES, "Q1201", "era:citroen/pony")
    _land_sweep(db, wikidata_source, "Q1200", "Citro\u00ebn Pony", makers=["Q6746"])

    first = run_wikidata_models_pass(db)
    second = run_wikidata_models_pass(db)
    assert (first.models_matched, second.models_matched) == (0, 0)
    assert (first.flags_opened, second.flags_opened) == (1, 0)
    assert _decision(db, "Q1200").outcome == "flagged_duplicate_ruled_base"
    assert _decision(db, "Q1200").detail["duplicates"] == ["Q1201"]
    assert db.scalar(select(ExternalId).where(ExternalId.external_id == "Q1200")) is None, (
        "the bare model keeps no anchor until a human rules"
    )
