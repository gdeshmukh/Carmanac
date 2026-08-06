"""ADR 0016 tests: the infobox parser, the time-and-codes pass, and the
placement pass.

Parser tests are pure. The integration cast is one company with two dated
generations and one model linked to both - enough to exercise every
placement outcome: unique overlap places, the AMG GT shape flags, missing
time waits, and moved evidence withdraws. The precedence test proves the
wd-models pass leaves infobox-projected columns alone while keeping its own
label-derived assertions current.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from carmanac.db.models import (
    CataloguePeriod,
    Company,
    Configuration,
    FieldProvenance,
    Generation,
    GenerationModelLink,
    MatchDecision,
    Model,
    PeriodKind,
    RawRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.ingest.landing import content_hash
from carmanac.reconcile.generation_placement_pass import run_generation_placement_pass
from carmanac.reconcile.sources.wikipedia_infobox import parse_infobox, parse_span
from carmanac.reconcile.wikipedia_infobox_pass import run_wikipedia_infobox_pass
from tests.test_vpic_models_pass import vpic_source  # noqa: F401

# --- the parser (pure) --------------------------------------------------------


def test_parse_span_shapes():
    span, reason = parse_span("1982–1994")
    assert (span.start, span.end, reason) == (1982, 1994, None)
    span, reason = parse_span("2014–present<ref>x</ref>")
    assert (span.start, span.end, reason) == (2014, None, None)
    span, reason = parse_span("1984–1991 (North America)")
    assert (span.start, span.end, reason) == (1984, 1991, None)
    span, reason = parse_span("1997")
    assert (span.start, span.end, reason) == (1997, 1997, None)


def test_parse_span_refuses_ambiguity():
    """Flag, never guess: several distinct ranges do not reduce to a hull."""
    span, reason = parse_span("1982–1990 (sedan)\n1985–1993 (convertible)")
    assert span is None and reason == "multiple_ranges"
    span, reason = parse_span("May 1982, June 1984, July 1987")
    assert span is None and reason == "years_without_range"
    span, reason = parse_span("none stated")
    assert span is None and reason is None


def test_parse_infobox_extracts_both_spans():
    wikitext = (
        "{{Infobox automobile\n"
        "| name = BMW 3 Series (E30)\n"
        "| production = 1982–1994<ref name=x/>\n"
        "| model_years = 1984–1991 (North America)\n"
        "}}\n"
    )
    parsed = parse_infobox("BMW 3 Series (E30)", wikitext)
    assert (parsed.production.start, parsed.production.end) == (1982, 1994)
    assert (parsed.model_years.start, parsed.model_years.end) == (1984, 1991)
    assert parsed.failures == ()


# --- integration fixtures -----------------------------------------------------

pytestmark_integration = pytest.mark.integration


@pytest.fixture()
def wikipedia_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == "Wikipedia (English)"))
    if source is None:
        source = Source(name="Wikipedia (English)", tier=2, base_url="https://en.wikipedia.org")
        db.add(source)
        db.commit()
    return source


def _land_infobox(db, source: Source, qid: str, title: str, wikitext: str) -> RawRecord:
    payload = {
        "qid": qid,
        "title": title,
        "requested_title": title,
        "revid": 1,
        "wikitext": wikitext,
    }
    rec = RawRecord(
        source_id=source.id,
        external_id=f"infobox:{qid}",
        content_hash=content_hash(payload),
        payload=payload,
    )
    db.add(rec)
    db.commit()
    return rec


@pytest.fixture()
def spine(db, wikidata_source):
    """Company -> model -> two generations (QID-attached, model-linked) ->
    one period per year of interest, configurations added per test."""
    from carmanac.db.models import ExternalId

    company = Company(slug="bmw", name="BMW")
    db.add(company)
    db.flush()
    model = Model(company_id=company.id, slug="330i", name="330i")
    db.add(model)
    db.flush()
    e46 = Generation(company_id=company.id, slug="e46", name="E46")
    e90 = Generation(company_id=company.id, slug="e90", name="E90")
    db.add_all([e46, e90])
    db.flush()
    db.add_all(
        [
            ExternalId(generation_id=e46.id, source_id=wikidata_source.id, external_id="Q1"),
            ExternalId(generation_id=e90.id, source_id=wikidata_source.id, external_id="Q2"),
            GenerationModelLink(
                generation_id=e46.id, model_id=model.id, source_id=wikidata_source.id
            ),
            GenerationModelLink(
                generation_id=e90.id, model_id=model.id, source_id=wikidata_source.id
            ),
        ]
    )
    kind = db.scalar(select(PeriodKind).where(PeriodKind.code == "model_year"))
    db.commit()
    return {"company": company, "model": model, "e46": e46, "e90": e90, "kind": kind}


def _configuration(db, spine, year: int, slug: str) -> Configuration:
    market = db.execute(text("SELECT id FROM market_regions ORDER BY id LIMIT 1")).scalar()
    period = db.scalar(
        select(CataloguePeriod).where(
            CataloguePeriod.model_id == spine["model"].id,
            CataloguePeriod.start_year == year,
        )
    )
    if period is None:
        period = CataloguePeriod(
            model_id=spine["model"].id,
            period_kind_id=spine["kind"].id,
            start_year=year,
            end_year=year,
        )
        db.add(period)
        db.flush()
    config = Configuration(catalogue_period_id=period.id, market_region_id=market, slug=slug)
    db.add(config)
    db.commit()
    return config


# --- the infobox pass ---------------------------------------------------------


@pytest.mark.integration
def test_infobox_pass_times_and_codes_generation(db, wikidata_source, wikipedia_source, spine):
    _land_infobox(
        db,
        wikipedia_source,
        "Q1",
        "BMW 3 Series (E46)",
        "{{Infobox automobile\n| production = 1997–2006\n}}",
    )
    stats = run_wikipedia_infobox_pass(db)
    assert stats.generations_timed == 1

    db.refresh(spine["e46"])
    assert (spine["e46"].start_year, spine["e46"].end_year) == (1997, 2006)
    assert spine["e46"].chassis_codes == ["E46"], "the title parenthetical asserts the code"
    assert db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.generation_id == spine["e46"].id,
            FieldProvenance.field_name == "start_year",
            FieldProvenance.source_id == wikipedia_source.id,
            FieldProvenance.superseded_by.is_(None),
        )
    ).one()

    # Idempotence: the second run asserts nothing new.
    stats2 = run_wikipedia_infobox_pass(db)
    assert stats2.assertions_inserted == 0 and stats2.flags_opened == 0


@pytest.mark.integration
def test_infobox_pass_flags_unreducible_span(db, wikidata_source, wikipedia_source, spine):
    _land_infobox(
        db,
        wikipedia_source,
        "Q1",
        "BMW 3 Series (E46)",
        "{{Infobox automobile\n| production = 1997–2000 (sedan)\n1999–2006 (coupe)\n}}",
    )
    stats = run_wikipedia_infobox_pass(db)
    assert stats.flags_opened == 1
    db.refresh(spine["e46"])
    assert spine["e46"].start_year is None, "an unreducible span asserts nothing"
    flag = db.scalars(
        select(ReconciliationFlag).where(
            ReconciliationFlag.kind == "implausible_value",
            ReconciliationFlag.generation_id == spine["e46"].id,
        )
    ).one()
    assert flag.detail["reason"] == "multiple_ranges"
    # Re-run: the open flag is not re-asked.
    stats2 = run_wikipedia_infobox_pass(db)
    assert stats2.flags_opened == 0


# --- the placement pass -------------------------------------------------------


def _time_generations(db, wikidata_source, wikipedia_source):
    """E46 1997-2006, E90 2005-2011 - a real one-year overlap at 2005-2006
    once end-slack widens E46 to 2007."""
    _land_infobox(
        db,
        wikipedia_source,
        "Q1",
        "BMW 3 Series (E46)",
        "{{Infobox automobile\n| production = 1997–2006\n}}",
    )
    _land_infobox(
        db,
        wikipedia_source,
        "Q2",
        "BMW 3 Series (E90)",
        "{{Infobox automobile\n| production = 2005–2011\n}}",
    )
    run_wikipedia_infobox_pass(db)


@pytest.mark.integration
def test_unique_overlap_places_with_provenance(db, wikidata_source, wikipedia_source, spine):
    _time_generations(db, wikidata_source, wikipedia_source)
    config = _configuration(db, spine, 2000, "330i-2000")
    stats = run_generation_placement_pass(db)
    assert stats.placed == 1 and stats.overlap_flagged == 0

    db.refresh(config)
    assert config.generation_id == spine["e46"].id
    placement = db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.configuration_id == config.id,
            FieldProvenance.field_name == "generation_id",
            FieldProvenance.superseded_by.is_(None),
        )
    ).one()
    assert placement.raw_record_id is not None, "placement cites the deciding record"
    decision = db.scalars(
        select(MatchDecision).where(MatchDecision.external_id == f"configuration:{config.id}")
    ).one()
    assert decision.outcome == "placed_dated_overlap"

    # Convergence: run 2 changes nothing.
    stats2 = run_generation_placement_pass(db)
    assert stats2.placed == 0 and stats2.already_placed == 1


@pytest.mark.integration
def test_overlap_flags_instead_of_guessing(db, wikidata_source, wikipedia_source, spine):
    """The AMG GT shape: a year inside two spans stays NULL and flags."""
    _time_generations(db, wikidata_source, wikipedia_source)
    config = _configuration(db, spine, 2005, "330i-2005")
    stats = run_generation_placement_pass(db)
    assert stats.overlap_flagged == 1 and stats.placed == 0

    db.refresh(config)
    assert config.generation_id is None
    flag = db.scalars(
        select(ReconciliationFlag).where(
            ReconciliationFlag.kind == "generation_overlap",
            ReconciliationFlag.configuration_id == config.id,
        )
    ).one()
    assert {c["generation"] for c in flag.detail["candidates"]} == {"e46", "e90"}

    stats2 = run_generation_placement_pass(db)
    assert stats2.flags_opened == 0, "the open flag is not re-asked"


@pytest.mark.integration
def test_end_slack_rescues_final_model_year_but_never_forces(
    db, wikidata_source, wikipedia_source, spine
):
    """2007 is outside E46's 1997-2006 production but inside its +1 slack;
    E90 owns it outright too, so slack must NOT place - two candidates flag.
    2012 is E90's final-year rescue with no competitor: it places."""
    _time_generations(db, wikidata_source, wikipedia_source)
    contested = _configuration(db, spine, 2007, "330i-2007")
    rescued = _configuration(db, spine, 2012, "330i-2012")
    stats = run_generation_placement_pass(db)

    db.refresh(contested)
    db.refresh(rescued)
    assert contested.generation_id is None, "slack adds a candidate, never a winner"
    assert rescued.generation_id == spine["e90"].id
    assert stats.overlap_flagged == 1 and stats.placed == 1


@pytest.mark.integration
def test_model_years_span_outranks_production(db, wikidata_source, wikipedia_source, spine):
    """An explicit US model_years field is the same axis as the period and
    wins exact: E46 model-years 1999-2005 excludes a 1997 configuration that
    its production span would have accepted."""
    _land_infobox(
        db,
        wikipedia_source,
        "Q1",
        "BMW 3 Series (E46)",
        "{{Infobox automobile\n| production = 1997–2006\n| model_years = 1999–2005\n}}",
    )
    run_wikipedia_infobox_pass(db)
    config = _configuration(db, spine, 1997, "330i-1997")
    stats = run_generation_placement_pass(db)
    db.refresh(config)
    assert config.generation_id is None and stats.unplaced_no_candidate >= 1


@pytest.mark.integration
def test_moved_evidence_withdraws_placement(db, wikidata_source, wikipedia_source, spine):
    """The sole-placer posture (ADR 0015 precedent): corrected spans that
    orphan a placement supersede it back to NULL, with the trail kept."""
    _time_generations(db, wikidata_source, wikipedia_source)
    config = _configuration(db, spine, 2000, "330i-2000")
    run_generation_placement_pass(db)
    db.refresh(config)
    assert config.generation_id == spine["e46"].id

    # The article is corrected: E46 production actually started 2001. A new
    # revision lands beside the old record and becomes current.
    _land_infobox(
        db,
        wikipedia_source,
        "Q1",
        "BMW 3 Series (E46)",
        "{{Infobox automobile\n| production = 2001–2006\n}}",
    )
    run_wikipedia_infobox_pass(db)
    stats = run_generation_placement_pass(db)

    db.refresh(config)
    assert config.generation_id is None and stats.withdrawn == 1
    superseded = db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.configuration_id == config.id,
            FieldProvenance.field_name == "generation_id",
            FieldProvenance.superseded_by.isnot(None),
        )
    ).all()
    assert superseded, "the withdrawn placement stays in the trail"


@pytest.mark.integration
def test_undated_competitor_bars_placement(db, wikidata_source, wikipedia_source, spine):
    """The Celica lesson: one dated match beside an undated sibling is not
    uniqueness - the configuration waits until the sibling gains a span."""
    _land_infobox(
        db,
        wikipedia_source,
        "Q1",
        "BMW 3 Series (E46)",
        "{{Infobox automobile\n| production = 1997–2006\n}}",
    )
    run_wikipedia_infobox_pass(db)  # e90 stays undated
    config = _configuration(db, spine, 2000, "330i-2000")
    stats = run_generation_placement_pass(db)

    db.refresh(config)
    assert config.generation_id is None and stats.undated_competitor == 1
    decision = db.scalars(
        select(MatchDecision).where(MatchDecision.external_id == f"configuration:{config.id}")
    ).one()
    assert decision.outcome == "waits_undated_competitor"


@pytest.mark.integration
def test_redirected_article_asserts_nothing(db, wikidata_source, wikipedia_source, spine):
    """The Civic Hybrid lesson: a sitelink that redirects to a different
    page changed subject - the whole-nameplate span must not land, and one
    that already landed heals back to NULL."""
    rec = _land_infobox(
        db,
        wikipedia_source,
        "Q1",
        "BMW 3 Series",  # resolved title != requested: a real redirect
        "{{Infobox automobile\n| production = 1975–present\n}}",
    )
    rec.payload = {**rec.payload, "requested_title": "BMW 3 Series (E46)"}
    rec.content_hash = content_hash(rec.payload)
    db.commit()
    stats = run_wikipedia_infobox_pass(db)
    assert stats.redirected == 1 and stats.generations_timed == 0
    db.refresh(spine["e46"])
    assert spine["e46"].start_year is None


# --- precedence (ADR 0016 §4) -------------------------------------------------


@pytest.mark.integration
def test_wd_models_refresh_does_not_stomp_infobox_columns(
    db,
    wikidata_source,
    wikipedia_source,
    vpic_source,  # noqa: F811
    spine,
):
    """The label pass keeps asserting into field_provenance but leaves
    columns the infobox pass owns: a wd-models re-run after infobox timing
    must not drag start_year back to the label-derived value."""
    from carmanac.reconcile.wikidata_models_pass import run_wikidata_models_pass
    from tests.test_wikidata_models_pass import _land_sweep

    # Attach the E46 QID's sweep record so the refresh path runs, carrying a
    # (wrong) Wikidata date claim.
    _land_sweep(
        db,
        wikidata_source,
        "Q1",
        "BMW E46",
        makers=["Q999"],
        inceptions=["1990-01-01T00:00:00Z"],
    )
    _land_infobox(
        db,
        wikipedia_source,
        "Q1",
        "BMW 3 Series (E46)",
        "{{Infobox automobile\n| production = 1997–2006\n}}",
    )
    run_wikipedia_infobox_pass(db)
    db.refresh(spine["e46"])
    assert spine["e46"].start_year == 1997

    run_wikidata_models_pass(db)
    db.refresh(spine["e46"])
    assert spine["e46"].start_year == 1997, "label-derived date must not stomp the infobox"
    wikidata_claim = db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.generation_id == spine["e46"].id,
            FieldProvenance.field_name == "start_year",
            FieldProvenance.source_id == wikidata_source.id,
            FieldProvenance.superseded_by.is_(None),
        )
    ).one()
    assert wikidata_claim.observed_value == "1990", "the outranked assertion stays recorded"
