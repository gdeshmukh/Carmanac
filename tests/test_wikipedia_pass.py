"""ADR 0017 tests for the unified Wikipedia pass: the heading grammar, the
labeled-defer span amendment, section minting, the lead-era mint, and the
body veto in placement.

The integration cast reuses the placement spine (one company, one QID-linked
model with two Wikidata-born generations) plus a second, link-less model the
minting tests own outright - enough to exercise mint / reconcile / distinct /
flag, the curated routing, and both veto directions on the AMG GT shape.
"""

# ruff: noqa: F811 - fixtures imported from the sibling module shadow their
# own names when taken as test parameters; that is how pytest fixtures work.

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from carmanac.db.models import (
    CataloguePeriod,
    Configuration,
    ConfigurationEngine,
    Engine,
    ExternalId,
    FieldProvenance,
    Generation,
    GenerationModelLink,
    GenerationSpecs,
    Model,
    ModelSpecs,
    RawRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.ingest.landing import content_hash
from carmanac.reconcile import policy
from carmanac.reconcile.generation_placement_pass import run_generation_placement_pass
from carmanac.reconcile.sources.wikipedia_infobox import parse_span, parse_specs
from carmanac.reconcile.sources.wikipedia_sections import (
    BodySignal,
    door_counts,
    epa_body_signal,
    parse_article,
    parse_heading,
    trim_body_signal,
)
from carmanac.reconcile.sources.wikipedia_tables import parse_engine_tables
from carmanac.reconcile.wikipedia_pass import run_wikipedia_pass
from tests.test_generation_placement import (  # noqa: F401
    spine,
    wikipedia_source,
)

# --- the heading grammar (pure) -----------------------------------------------


def test_heading_grammar_parses_the_convention():
    h = parse_heading("First generation (XW10; 1997)")
    assert (h.ordinal, h.codes, h.heading_years) == (1, ("XW10",), (1997,))

    h = parse_heading("Second generation (GD/GE; 2001)")
    assert (h.ordinal, h.codes) == (2, ("GD", "GE")), "letters-only codes are legal by position"

    h = parse_heading("Second generation (T20, T30; 1960)")
    assert h.codes == ("T20", "T30")

    h = parse_heading("First generation – NA (1989–1997)")
    assert (h.ordinal, h.codes, h.heading_years) == (1, ("NA",), (1989, 1997))

    h = parse_heading("Sixteenth generation (S230; 2022)")
    assert (h.ordinal, h.codes) == (16, ("S230",))

    h = parse_heading("First generation (''Typ'' 4L; 2005)")
    assert h.codes == ("4L",), "italics and the Typ prefix strip"

    h = parse_heading("First generation (1998–2006)")
    assert (h.ordinal, h.codes, h.heading_years) == (1, (), (1998, 2006))

    h = parse_heading('<span class="anchor" id="X290"></span> First generation (X290; 2018)')
    assert (h.ordinal, h.codes) == (1, ("X290",)), "anchor spans strip"

    h = parse_heading("First generation (C190/R190) {{anchor|C190|R190}}")
    assert (h.ordinal, h.codes) == (1, ("C190", "R190"))


def test_heading_grammar_rejects_non_generations():
    assert parse_heading("Second generation models") is None, "sub-parts are not generations"
    assert parse_heading("Next generation") is None
    assert parse_heading("History") is None
    assert parse_heading("First generation facelift and revisions") is None


def test_parse_article_sections_and_main_targets():
    wikitext = (
        "{{Infobox automobile\n| name = Nameplate\n| body_style = 5-door [[liftback]]\n}}\n"
        "Intro prose.\n"
        "== History ==\nprose\n"
        "== First generation (AA10; 1990) ==\n"
        "{{Main|Test Car (AA10)}}\n"
        "{{Infobox automobile\n| production = 1990–1997\n}}\n"
        "== Second generation (1998) ==\n"
        "{{Infobox automobile\n| production = 1998–2004\n| body_style = 2-door [[coupé]]\n}}\n"
        "== See also ==\n"
    )
    article = parse_article("Test Car", wikitext)
    assert [s.ordinal for s in article.sections] == [1, 2]
    assert article.sections[0].main_targets == ("Test Car (AA10)",)
    assert article.sections[0].has_infobox and article.sections[1].has_infobox
    assert "body_style = 5-door" in article.top_wikitext


# --- the labeled-defer amendment (pure) ----------------------------------------


def test_labeled_subranges_defer_to_single_unlabeled_range():
    span, reason = parse_span(
        "{{plainlist|\n* October 2014 – September 2022<ref>x</ref>\n"
        "* 2021–2023 (AMG GT Black Series; 1,700 produced)\n"
        "* 2022 (AMG GT Track Series; Limited 55 units)}}"
    )
    assert (span.start, span.end, reason) == (2014, 2022, None)

    span, reason = parse_span("2015–2023<br />2021–2023 (AMG GT Black Series)")
    assert (span.start, span.end, reason) == (2015, 2023, None)


def test_all_labeled_or_multiple_unlabeled_still_flag():
    span, reason = parse_span("1982–1990 (sedan)\n1985–1993 (convertible)")
    assert span is None and reason == "multiple_ranges", "per-body lists keep flagging"
    span, reason = parse_span("1982–1990\n1991–1999")
    assert span is None and reason == "multiple_ranges", "two bare ranges reduce to nothing"


# --- body signals (pure) --------------------------------------------------------


def test_door_counts_and_contradiction():
    assert door_counts("2-door [[coupé]]<br />2-door [[roadster]]") == frozenset({2})
    assert door_counts("five-door [[liftback]]") == frozenset({5})
    assert door_counts("[[roadster]]") == frozenset(), "no count, no claim"

    two_seater = BodySignal(max_doors=3)
    four_door = BodySignal(min_doors=4)
    assert two_seater.contradicts(frozenset({5}))
    assert not two_seater.contradicts(frozenset({2}))
    assert four_door.contradicts(frozenset({2}))
    assert not four_door.contradicts(frozenset({2, 4})), "a mixed-body generation is compatible"
    assert not four_door.contradicts(frozenset()), "no bodies asserted, no veto"


def test_epa_and_trim_signals():
    assert epa_body_signal("Two Seaters", "0", "0", "0", "0") == BodySignal(max_doors=3)
    assert epa_body_signal("Compact Cars", "0", "96", "0", "0") == BodySignal(min_doors=4)
    assert epa_body_signal("Subcompact Cars", "81", "0", "0", "0") == BodySignal(max_doors=3)
    assert not epa_body_signal("Compact Cars", "0", "0", "0", "0"), "cars class alone says nothing"
    assert not epa_body_signal("Small Station Wagons", "0", "0", "0", "0"), "wagons assert nothing"
    assert not epa_body_signal("Compact Cars", "81", "96", "0", "0"), "self-contradiction: silence"
    assert trim_body_signal("S Coupe") == BodySignal(max_doors=3)
    assert not trim_body_signal("63 4matic Plus"), "no body word, no signal"


# --- integration ----------------------------------------------------------------


def _land_article(db, source: Source, qid: str, title: str, wikitext: str) -> RawRecord:
    payload = {
        "qid": qid,
        "title": title,
        "requested_title": title,
        "revid": 1,
        "wikitext": wikitext,
    }
    rec = RawRecord(
        source_id=source.id,
        external_id=f"article:{qid}",
        content_hash=content_hash(payload),
        payload=payload,
    )
    db.add(rec)
    db.commit()
    return rec


@pytest.fixture()
def linkless_model(db, spine) -> Model:  # noqa: F811
    model = Model(company_id=spine["company"].id, slug="z4", name="Z4")
    db.add(model)
    db.commit()
    return model


@pytest.fixture()
def routed(db, monkeypatch, wikidata_source, spine, linkless_model):  # noqa: F811
    """Route article QIDs to models: Q7 1:1-attached to the link-less model,
    Q8 curated onto it (the SECTION_ARTICLE_MODELS mechanism)."""
    db.add(ExternalId(model_id=linkless_model.id, source_id=wikidata_source.id, external_id="Q7"))
    # The routing keys on the model's own source id, so the fixture needs the
    # filing id a real model carries.
    db.add(
        ExternalId(model_id=linkless_model.id, source_id=wikidata_source.id, external_id="model:7")
    )
    db.commit()
    monkeypatch.setattr(policy, "SECTION_ARTICLE_MODELS", {"Q8": "model:7"})
    return linkless_model


@pytest.mark.integration
def test_sections_mint_generations_with_links_and_facts(
    db,
    wikidata_source,
    wikipedia_source,
    spine,
    routed,  # noqa: F811
):
    _land_article(
        db,
        wikipedia_source,
        "Q7",
        "BMW Z4",
        "== First generation (E85; 2002) ==\n"
        "{{Infobox automobile\n| production = 2002–2008\n}}\n"
        "== Second generation (E89; 2009) ==\n"
        "{{Infobox automobile\n| production = 2009–2016\n}}\n",
    )
    stats = run_wikipedia_pass(db)
    assert stats.generations_created == 2 and stats.flagged_articles == 0

    e85 = db.scalar(
        select(Generation)
        .join(ExternalId, ExternalId.generation_id == Generation.id)
        .where(ExternalId.external_id == "section:Q7#1")
    )
    assert e85.name == "Z4 (E85)" and e85.chassis_codes == ["E85"]
    assert (e85.start_year, e85.end_year) == (2002, 2008)
    assert (
        db.scalars(
            select(GenerationModelLink).where(
                GenerationModelLink.generation_id == e85.id,
                GenerationModelLink.model_id == routed.id,
                GenerationModelLink.superseded_by.is_(None),
            )
        )
        .one()
        .raw_record_id
        is not None
    ), "the link carries provenance to the article"
    assert db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.generation_id == e85.id,
            FieldProvenance.field_name == "start_year",
            FieldProvenance.superseded_by.is_(None),
        )
    ).one()

    # Convergence: run 2 writes nothing.
    stats2 = run_wikipedia_pass(db)
    assert stats2.generations_created == 0 and stats2.assertions_inserted == 0
    assert stats2.links_asserted == 0


@pytest.mark.integration
def test_duplicate_ordinals_flag_and_mint_nothing(
    db,
    wikidata_source,
    wikipedia_source,
    spine,
    routed,  # noqa: F811
):
    _land_article(
        db,
        wikipedia_source,
        "Q7",
        "BMW Z4",
        "== First generation (E85; 2002) ==\n{{Infobox automobile\n| production = 2002–2008\n}}\n"
        "== First generation (E89; 2009) ==\n{{Infobox automobile\n| production = 2009–2016\n}}\n",
    )
    stats = run_wikipedia_pass(db)
    assert stats.generations_created == 0 and stats.flagged_articles == 1
    flag = db.scalars(
        select(ReconciliationFlag).where(
            ReconciliationFlag.kind == "section_generation_review",
            ReconciliationFlag.status == "open",
        )
    ).one()
    assert flag.detail["reason"] == "duplicate_or_noncontiguous_ordinals"
    # Re-run: the open question is not re-asked.
    stats2 = run_wikipedia_pass(db)
    assert stats2.flags_opened == 0


@pytest.mark.integration
def test_existing_inventory_reconciles_or_mints_distinct(
    db,
    wikidata_source,
    wikipedia_source,
    spine,  # noqa: F811
):
    """The 330i has Wikidata-born E46/E90. A section carrying E46 reconciles
    (corroborating link, no new row); a section with disjoint codes would
    need every competitor coded - E46/E90 carry none here, so it flags."""
    _land_article(
        db,
        wikipedia_source,
        "Q9",
        "BMW 330i",
        "== First generation (E46; 1997) ==\n{{Infobox automobile\n| production = 1997–2006\n}}\n"
        "== Second generation (E90; 2005) ==\n{{Infobox automobile\n| production = 2005–2011\n}}\n",
    )
    db.add(ExternalId(model_id=spine["model"].id, source_id=wikidata_source.id, external_id="Q9"))
    # Codes on the existing generations so the sections can reconcile.
    spine["e46"].chassis_codes = ["E46"]
    spine["e90"].chassis_codes = ["E90"]
    db.commit()

    stats = run_wikipedia_pass(db)
    assert stats.generations_created == 0 and stats.sections_reconciled == 2
    assert stats.flagged_articles == 0
    corroborating = db.scalars(
        select(GenerationModelLink).where(
            GenerationModelLink.generation_id == spine["e46"].id,
            GenerationModelLink.model_id == spine["model"].id,
            GenerationModelLink.superseded_by.is_(None),
        )
    ).all()
    assert len(corroborating) == 2, "wikidata's link plus wikipedia's corroboration"
    assert db.get(Generation, spine["e46"].id).start_year is None, (
        "reconciled sections assert no facts - the generation's own article is the source"
    )


@pytest.mark.integration
def test_unreconcilable_sections_flag_instead_of_duplicating(
    db,
    wikidata_source,
    wikipedia_source,
    spine,  # noqa: F811
):
    """Same article, but the existing generations carry no codes: nothing
    reconciles, nothing proves distinct, the article flags whole."""
    _land_article(
        db,
        wikipedia_source,
        "Q9",
        "BMW 330i",
        "== First generation (AB12; 1997) ==\n{{Infobox automobile\n| production = 1997–2006\n}}\n",
    )
    db.add(ExternalId(model_id=spine["model"].id, source_id=wikidata_source.id, external_id="Q9"))
    db.commit()
    stats = run_wikipedia_pass(db)
    assert stats.generations_created == 0 and stats.flagged_articles == 1
    assert db.scalar(select(Generation).where(Generation.slug == "330i-ab12")) is None


@pytest.mark.integration
def test_curated_routing_and_disjoint_codes_mint_the_amg_gt_shape(
    db,
    wikidata_source,
    wikipedia_source,
    spine,
    routed,  # noqa: F811
):
    """The proof-car mechanism end to end: the attached article mints
    C190/C192; the curated sibling article mints X290/C590 beside them
    because codes on both sides are disjoint - never each other's rows."""
    _land_article(
        db,
        wikipedia_source,
        "Q7",
        "Test AMG GT",
        "== First generation (C190/R190) ==\n"
        "{{Infobox automobile\n| production = 2014–2022\n"
        "| body_style = 2-door [[coupé]]<br />2-door [[roadster]]\n}}\n"
        "== Second generation (C192; 2023) ==\n"
        "{{Infobox automobile\n| production = 2023–present\n"
        "| body_style = 2-door [[coupé]]\n}}\n",
    )
    _land_article(
        db,
        wikipedia_source,
        "Q8",
        "Test AMG GT 4-Door",
        "{{Infobox automobile\n| name = Test AMG GT 4-Door\n"
        "| body_style = 5-door [[liftback]]\n| production = 2018–present\n}}\n"
        "== First generation (X290; 2018) ==\n"
        "{{Infobox automobile\n| production = 2018–2026\n}}\n"
        "== Second generation (C590; 2026) ==\n"
        "{{Infobox automobile\n| production = 2026 (to commence)\n}}\n",
    )
    stats = run_wikipedia_pass(db)
    assert stats.generations_created == 4 and stats.flagged_articles == 0
    slugs = set(
        db.scalars(
            select(Generation.slug)
            .join(GenerationModelLink, GenerationModelLink.generation_id == Generation.id)
            .where(
                GenerationModelLink.model_id == routed.id,
                GenerationModelLink.superseded_by.is_(None),
            )
        )
    )
    assert slugs == {"z4-c190-r190", "z4-c192", "z4-x290", "z4-c590"}


@pytest.fixture()
def epa_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == "EPA fueleconomy.gov"))
    if source is None:
        source = Source(name="EPA fueleconomy.gov", tier=1, base_url="https://fueleconomy.gov")
        db.add(source)
        db.commit()
    return source


def _attach_epa_body(db, epa_source, config, vehicle_id: str, vclass: str, pv4: str) -> None:
    payload = {"id": vehicle_id, "VClass": vclass, "pv2": "0", "pv4": pv4, "lv2": "0", "lv4": "0"}
    rec = RawRecord(
        source_id=epa_source.id,
        external_id=f"vehicle:{vehicle_id}",
        content_hash=content_hash(payload),
        payload=payload,
    )
    db.add(rec)
    db.add(
        ExternalId(
            configuration_id=config.id,
            source_id=epa_source.id,
            external_id=f"vehicle:{vehicle_id}",
        )
    )
    db.commit()


@pytest.mark.integration
def test_body_veto_places_both_amg_gt_sides_without_overlap(
    db,
    wikidata_source,
    wikipedia_source,
    epa_source,
    spine,
    routed,  # noqa: F811
):
    """2019 holds C190 coupes beside X290 4-doors. The two-seater signal
    vetoes the 5-door X290; the four-door signal vetoes the all-2-door
    C190; a row with no body signal flags overlap - never a guess."""
    _land_article(
        db,
        wikipedia_source,
        "Q7",
        "Test AMG GT",
        "== First generation (C190; 2014) ==\n"
        "{{Infobox automobile\n| production = 2014–2022\n"
        "| body_style = 2-door [[coupé]]<br />2-door [[roadster]]\n}}\n"
        "== Second generation (C192; 2023) ==\n"
        "{{Infobox automobile\n| production = 2023–present\n| body_style = 2-door coupé\n}}\n",
    )
    _land_article(
        db,
        wikipedia_source,
        "Q8",
        "Test AMG GT 4-Door",
        "{{Infobox automobile\n| body_style = 5-door [[liftback]]\n}}\n"
        "== First generation (X290; 2018) ==\n"
        "{{Infobox automobile\n| production = 2018–2026\n}}\n",
    )
    run_wikipedia_pass(db)

    # Periods hang under the routed model; both cars share the 2019 year.
    market = db.execute(text("SELECT id FROM market_regions ORDER BY id LIMIT 1")).scalar()
    period = CataloguePeriod(
        model_id=routed.id,
        period_kind_id=spine["kind"].id,
        start_year=2019,
        end_year=2019,
    )
    db.add(period)
    db.flush()
    coupe = Configuration(
        catalogue_period_id=period.id,
        market_region_id=market,
        slug="gt-2019-coupe",
        trim_name="S",
    )
    four_door = Configuration(
        catalogue_period_id=period.id,
        market_region_id=market,
        slug="gt-2019-63",
        trim_name="63 4matic Plus",
    )
    signalless = Configuration(
        catalogue_period_id=period.id,
        market_region_id=market,
        slug="gt-2019-mystery",
        trim_name="Mystery",
    )
    db.add_all([coupe, four_door, signalless])
    db.commit()
    _attach_epa_body(db, epa_source, coupe, "101", "Two Seaters", "0")
    _attach_epa_body(db, epa_source, four_door, "102", "Compact Cars", "96")

    stats = run_generation_placement_pass(db)
    db.refresh(coupe)
    db.refresh(four_door)
    db.refresh(signalless)

    c190 = db.scalar(select(Generation).where(Generation.slug == "z4-c190"))
    x290 = db.scalar(select(Generation).where(Generation.slug == "z4-x290"))
    assert coupe.generation_id == c190.id, "the two-seater was never an X290 candidate"
    assert four_door.generation_id == x290.id, "the four-door was never a C190 candidate"
    assert signalless.generation_id is None
    flag = db.scalars(
        select(ReconciliationFlag).where(
            ReconciliationFlag.kind == "generation_overlap",
            ReconciliationFlag.configuration_id == signalless.id,
            ReconciliationFlag.status == "open",
        )
    ).one()
    assert {c["generation"] for c in flag.detail["candidates"]} == {"z4-c190", "z4-x290"}
    assert stats.body_vetoed >= 2

    # Convergence.
    stats2 = run_generation_placement_pass(db)
    assert stats2.placed == 0 and stats2.already_placed == 2


# --- the lead era (no sections, one span) --------------------------------------


@pytest.mark.integration
def test_lead_era_mints_one_dated_generation(
    db,
    wikidata_source,
    wikipedia_source,
    spine,
    routed,  # noqa: F811
):
    _land_article(
        db,
        wikipedia_source,
        "Q7",
        "BMW Z4",
        "{{Infobox automobile\n| production = 2002–2016\n}}\nprose, no generation headings",
    )
    stats = run_wikipedia_pass(db)
    assert (stats.lead_era_minted, stats.generations_created) == (1, 1)
    generation = db.scalars(
        select(Generation)
        .join(ExternalId, ExternalId.generation_id == Generation.id)
        .where(ExternalId.external_id == "section:Q7#0")
    ).one()
    assert (generation.name, generation.slug) == ("Z4", "z4")
    assert (generation.start_year, generation.end_year) == (2002, 2016)
    link = db.scalars(
        select(GenerationModelLink).where(
            GenerationModelLink.generation_id == generation.id,
            GenerationModelLink.superseded_by.is_(None),
        )
    ).one()
    assert link.model_id == routed.id and link.raw_record_id is not None

    stats2 = run_wikipedia_pass(db)
    assert (stats2.lead_era_minted, stats2.assertions_inserted, stats2.flags_opened) == (0, 0, 0)


@pytest.mark.integration
def test_lead_era_unparseable_span_mints_nothing(
    db,
    wikidata_source,
    wikipedia_source,
    spine,
    routed,  # noqa: F811
):
    _land_article(
        db,
        wikipedia_source,
        "Q7",
        "BMW Z4",
        "{{Infobox automobile\n| production = 2002–2008 (roadster)\n2009–2016 (coupe)\n}}",
    )
    stats = run_wikipedia_pass(db)
    assert (stats.lead_era_minted, stats.generations_created) == (0, 0)
    outcome = db.execute(
        text("SELECT outcome FROM match_decisions WHERE external_id = 'article:Q7'")
    ).scalar()
    assert outcome == "lead_era_unparseable"


@pytest.mark.integration
def test_lead_era_defers_to_existing_linked_generations(
    db,
    wikidata_source,
    wikipedia_source,
    spine,
    routed,  # noqa: F811
):
    """A model that already has linked generations never takes the
    whole-nameplate span as an era - the Civic hazard at mint scope."""
    db.add(
        GenerationModelLink(
            generation_id=spine["e46"].id, model_id=routed.id, source_id=wikidata_source.id
        )
    )
    db.commit()
    _land_article(
        db,
        wikipedia_source,
        "Q7",
        "BMW Z4",
        "{{Infobox automobile\n| production = 2002–2016\n}}",
    )
    stats = run_wikipedia_pass(db)
    assert (stats.lead_era_minted, stats.generations_created) == (0, 0)
    outcome = db.execute(
        text("SELECT outcome FROM match_decisions WHERE external_id = 'article:Q7'")
    ).scalar()
    assert outcome == "no_sections"


@pytest.mark.integration
def test_lead_era_slug_collision_flags_and_mints_nothing(
    db,
    wikidata_source,
    wikipedia_source,
    spine,
    routed,  # noqa: F811
):
    db.add(Generation(company_id=spine["company"].id, slug="z4", name="Z4 the elder"))
    db.commit()
    _land_article(
        db,
        wikipedia_source,
        "Q7",
        "BMW Z4",
        "{{Infobox automobile\n| production = 2002–2016\n}}",
    )
    stats = run_wikipedia_pass(db)
    assert (stats.lead_era_minted, stats.flagged_articles) == (0, 1)
    flag = db.scalars(
        select(ReconciliationFlag).where(
            ReconciliationFlag.kind == "section_generation_review",
            ReconciliationFlag.status == "open",
            ReconciliationFlag.model_id == routed.id,
        )
    ).one()
    assert flag.detail["reason"] == "generation_slug_collision"


@pytest.mark.integration
def test_lead_era_generation_places_by_lead_model_years(
    db,
    wikidata_source,
    wikipedia_source,
    spine,
    routed,  # noqa: F811
):
    """Placement's decision-time loader reads the article lead for a
    `section:<QID>#0` generation: model years outrun production past the
    slack, and the configuration still places."""
    _land_article(
        db,
        wikipedia_source,
        "Q7",
        "BMW Z4",
        "{{Infobox automobile\n| production = 2002–2015\n| model_years = 2003–2017\n}}",
    )
    run_wikipedia_pass(db)
    market = db.execute(text("SELECT id FROM market_regions ORDER BY id LIMIT 1")).scalar()
    period = CataloguePeriod(
        model_id=routed.id, period_kind_id=spine["kind"].id, start_year=2017, end_year=2017
    )
    db.add(period)
    db.flush()
    config = Configuration(catalogue_period_id=period.id, market_region_id=market, slug="sdrive")
    db.add(config)
    db.commit()
    run_generation_placement_pass(db)
    db.refresh(config)
    generation = db.scalars(
        select(Generation)
        .join(ExternalId, ExternalId.generation_id == Generation.id)
        .where(ExternalId.external_id == "section:Q7#0")
    ).one()
    assert config.generation_id == generation.id


# --- spec defaults (the honest-grain landing) ----------------------------------


def test_parse_specs_takes_single_values_only():
    specs = parse_specs(
        "{{Infobox automobile\n"
        "| wheelbase = {{convert|110.8|in|mm|0|abbr=on}}\n"
        "| length = {{convert|4959|-|4966|mm|in|1|abbr=on}}\n"
        "| width = 1991–93: {{convert|74.9|in|mm|0|abbr=on}}<br/>1994: {{convert|75|in|mm}}\n"
        "| height = {{convert|1425|mm|in|abbr=on}}<ref>x</ref>\n"
        "| weight = {{convert|3536|lb|kg|abbr=on}} (sedan)\n"
        "| powerout = {{convert|110|kW|hp|abbr=on}}\n"
        "}}"
    )
    assert specs["wheelbase_mm"][1] == 2814, "inches normalize to mm"
    assert specs["height_mm"][1] == 1425
    assert specs["power_hp"][1] == 148, "kW normalizes to mechanical hp"
    assert "length_mm" not in specs, "a range is not a default"
    assert "width_mm" not in specs, "an era-prefixed list is not a default"
    assert "curb_weight_kg" not in specs, "a variant-labelled value is not a default"


@pytest.mark.integration
def test_lead_era_article_lands_model_grain_specs(
    db,
    wikidata_source,
    wikipedia_source,
    spine,
    routed,  # noqa: F811
):
    _land_article(
        db,
        wikipedia_source,
        "Q7",
        "BMW Z4",
        "{{Infobox automobile\n"
        "| production = 2002–2016\n"
        "| wheelbase = {{convert|2470|mm|in|1|abbr=on}}\n"
        "| body_style = 2-door [[roadster]]\n"
        "}}",
    )
    run_wikipedia_pass(db)
    specs = db.get(ModelSpecs, routed.id)
    assert (specs.wheelbase_mm, specs.doors) == (2470, 2)
    assert (
        db.get(
            GenerationSpecs,
            db.scalar(
                select(ExternalId.generation_id).where(ExternalId.external_id == "section:Q7#0")
            ),
        )
        is None
    ), "a nameplate page lands at model grain, not on its era"
    prov = db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.model_id == routed.id,
            FieldProvenance.field_name == "wheelbase_mm",
            FieldProvenance.superseded_by.is_(None),
        )
    ).one()
    assert prov.source_id == wikipedia_source.id

    stats2 = run_wikipedia_pass(db)
    assert stats2.assertions_inserted == 0

    # The leaf inherits: the configuration carries nothing, the view answers
    # from the model default.
    market = db.execute(text("SELECT id FROM market_regions ORDER BY id LIMIT 1")).scalar()
    period = CataloguePeriod(
        model_id=routed.id, period_kind_id=spine["kind"].id, start_year=2010, end_year=2010
    )
    db.add(period)
    db.flush()
    config = Configuration(catalogue_period_id=period.id, market_region_id=market, slug="s")
    db.add(config)
    db.commit()
    row = db.execute(
        text("SELECT wheelbase_mm, doors FROM v_configuration_full WHERE configuration_id = :i"),
        {"i": config.id},
    ).one()
    assert (row.wheelbase_mm, row.doors) == (2470, 2)


@pytest.mark.integration
def test_generation_attached_article_lands_generation_grain_specs(
    db,
    wikidata_source,
    wikipedia_source,
    spine,
):
    _land_article(
        db,
        wikipedia_source,
        "Q1",
        "BMW 3 Series (E46)",
        "{{Infobox automobile\n"
        "| production = 1997–2006\n"
        "| wheelbase = {{convert|2725|mm|in|1|abbr=on}}\n"
        "}}",
    )
    run_wikipedia_pass(db)
    specs = db.get(GenerationSpecs, spine["e46"].id)
    assert specs.wheelbase_mm == 2725
    prov = db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.generation_id == spine["e46"].id,
            FieldProvenance.field_name == "wheelbase_mm",
            FieldProvenance.superseded_by.is_(None),
        )
    ).one()
    assert prov.observed_value.startswith("{{convert|2725")


# --- the engine tables (ADR 0020 amendment) ------------------------------------

_Z4_TABLE = (
    "== Engines ==\n"
    '{| class="wikitable"\n'
    "! Model !! Engine !! Displacement !! Power !! Torque !! Years\n"
    "|-\n"
    "| 2.5i || rowspan=2 | [[BMW M54]] || {{convert|2494|cc|L|abbr=on}} "
    "|| {{convert|192|PS|kW hp|abbr=on}} || {{convert|245|N.m|lbft|abbr=on}} || 2002–2005\n"
    "|-\n"
    "| 3.0i || {{convert|2979|cc|L|abbr=on}} || {{convert|231|PS|kW hp|abbr=on}} "
    "|| {{convert|300|N.m|lbft|abbr=on}} || 2002–2005\n"
    "|}\n"
)


def test_parse_engine_tables_reads_rows_spans_and_units():
    rows = parse_engine_tables(_Z4_TABLE)
    assert len(rows) == 2
    first, second = rows
    assert first.engines == (("bmw m54", None),)
    assert second.engines == (("bmw m54", None),), "the rowspan carries the engine down"
    assert (first.displacement_cc, first.years) == (2494, (2002, 2005))
    assert first.power_hp == 189, "PS normalizes to mechanical hp"
    assert first.torque_nm == 245


def test_parse_engine_tables_ignores_sales_tables_and_banner_rows():
    rows = parse_engine_tables(
        '{| class="wikitable"\n! Year !! Europe !! Brazil\n|-\n| 2007 || 1 || 2\n|}\n'
        '{| class="wikitable"\n! Engine !! Power !! Years\n'
        "|-\n! colspan=3 | Diesel engines\n"
        "|-\n| [[Alpina B3|B3]] || {{convert|90|kW|PS hp|abbr=on}} || 1998–2003\n|}\n"
    )
    assert len(rows) == 1
    assert rows[0].fuel == "diesel", "the banner row is fuel context, not data"


def _z4_config(db, spine, routed, year: int, cc: int | None, slug: str) -> Configuration:
    market = db.execute(text("SELECT id FROM market_regions ORDER BY id LIMIT 1")).scalar()
    period = db.scalar(
        select(CataloguePeriod).where(
            CataloguePeriod.model_id == routed.id, CataloguePeriod.start_year == year
        )
    )
    if period is None:
        period = CataloguePeriod(
            model_id=routed.id, period_kind_id=spine["kind"].id, start_year=year, end_year=year
        )
        db.add(period)
        db.flush()
    config = Configuration(
        catalogue_period_id=period.id,
        market_region_id=market,
        slug=slug,
        engine_displacement_cc=cc,
    )
    db.add(config)
    db.commit()
    return config


@pytest.mark.integration
def test_engine_table_links_and_lands_power_on_physical_keys(
    db,
    wikidata_source,
    wikipedia_source,
    spine,
    routed,  # noqa: F811
):
    """The 2.5i config matches one row on years+displacement: the family
    mints through the prefix rung (BMW M54 under BMW), the link lands with
    the article as evidence, and the row's power/torque land standardless
    with the observed string kept."""
    config = _z4_config(db, spine, routed, 2003, 2500, "25i")
    away = _z4_config(db, spine, routed, 2015, 2500, "later")  # outside every row's years
    _land_article(db, wikipedia_source, "Q7", "BMW Z4", _Z4_TABLE)
    stats = run_wikipedia_pass(db)
    assert stats.engines_minted == 1 and stats.powertrain_links == 1

    engine = db.scalars(select(Engine)).one()
    assert (engine.slug, engine.name) == ("bmw-m54", "BMW M54")
    assert engine.manufacturer_company_id == spine["company"].id
    link = db.scalars(
        select(ConfigurationEngine).where(ConfigurationEngine.superseded_by.is_(None))
    ).one()
    assert (link.configuration_id, link.engine_id) == (config.id, engine.id)
    assert link.raw_record_id is not None

    db.refresh(config)
    assert (config.power_hp, config.torque_nm) == (189, 245)
    prov = db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.configuration_id == config.id,
            FieldProvenance.field_name == "power_hp",
            FieldProvenance.superseded_by.is_(None),
        )
    ).one()
    assert "PS" in prov.observed_value, "the observed string keeps the source's own units"
    db.refresh(away)
    assert away.power_hp is None, "a config outside the rows' years takes nothing"

    rerun = run_wikipedia_pass(db)
    assert (rerun.engines_minted, rerun.powertrain_links, rerun.assertions_inserted) == (0, 0, 0)


@pytest.mark.integration
def test_ambiguous_rows_link_nothing_and_queue(
    db,
    wikidata_source,
    wikipedia_source,
    spine,
    routed,  # noqa: F811
):
    """Two same-displacement rows naming different engines in the config's
    years: nothing links, nothing lands, the decision log holds the case."""
    config = _z4_config(db, spine, routed, 2003, 2500, "25i")
    _land_article(
        db,
        wikipedia_source,
        "Q7",
        "BMW Z4",
        '{| class="wikitable"\n! Engine !! Displacement !! Years\n'
        "|-\n| [[BMW M54]] || 2494 cc || 2002–2005\n"
        "|-\n| [[BMW N52]] || 2497 cc || 2002–2005\n|}\n",
    )
    stats = run_wikipedia_pass(db)
    assert stats.powertrain_links == 0 and stats.powertrain_ambiguous == 1
    outcome = db.execute(
        text("SELECT outcome FROM match_decisions WHERE external_id = :k"),
        {"k": f"configuration:{config.id}"},
    ).scalar()
    assert outcome == "engine_ambiguous"
