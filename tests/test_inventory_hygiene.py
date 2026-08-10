"""ADR 0018 tests: the NOT_A_GENERATION registry gate, the demotion script's
census/apply halves, and the section-main machinery (grain guards, fact
sourcing with per-record provenance, the placement loaders, the lander's
target selection, dash-template spans).

Reuses the placement spine (one company, one QID-linked model with two
Wikidata-born generations) and the sections-pass cast.
"""

# ruff: noqa: F811 - fixtures imported from sibling modules shadow their own
# names when taken as test parameters; that is how pytest fixtures work.

from __future__ import annotations

import pytest
from sqlalchemy import select

from carmanac.db.models import (
    ExternalId,
    FieldProvenance,
    Generation,
    GenerationModelLink,
    RawRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.ingest.landing import content_hash
from carmanac.ingest.wikipedia.section_mains import section_main_targets
from carmanac.reconcile import policy
from carmanac.reconcile.generation_placement_pass import run_generation_placement_pass
from carmanac.reconcile.sources.wikipedia_infobox import parse_span
from carmanac.reconcile.wikidata_models_pass import run_wikidata_models_pass
from carmanac.reconcile.wikipedia_sections_pass import (
    run_wikipedia_sections_pass,
    section_main_asserts,
)
from scripts.decisions.demote_non_generations import apply as demote_apply
from scripts.decisions.demote_non_generations import census as demote_census
from tests.test_generation_placement import (  # noqa: F401
    _configuration,
    spine,
    wikipedia_source,
)
from tests.test_wikidata_models_pass import _land_sweep
from tests.test_wikipedia_sections import _land_article

# --- pure: dash templates and the grain guards ---------------------------------


def test_parse_span_normalizes_dash_templates():
    span, reason = parse_span("October 2010 {{nbndash}} September 2017")
    assert (span.start, span.end, reason) == (2010, 2017, None)
    span, _ = parse_span("2011{{ndash}}2017")
    assert (span.start, span.end) == (2011, 2017)
    span, _ = parse_span("1998 {{snd}} 2004")
    assert (span.start, span.end) == (1998, 2004)


def test_section_main_grain_guards():
    ok = {"requested_title": "Mazda MX-5 (NA)", "title": "Mazda MX-5 (NA)"}
    assert section_main_asserts(ok)
    wobble = {
        "requested_title": "Nissan_Leaf (first generation)",
        "title": "Nissan Leaf (first generation)",
    }
    assert section_main_asserts(wobble), "underscore/case wobble is not a redirect"
    redirected = {"requested_title": "Honda Civic Hybrid", "title": "Honda Civic"}
    assert not section_main_asserts(redirected), "a redirected target asserts nothing"
    bare = {"requested_title": "Kia Sephia", "title": "Kia Sephia"}
    assert not section_main_asserts(bare), "a bare-title target is a nameplate deferral"


# --- the registry gate ----------------------------------------------------------


@pytest.mark.integration
def test_registry_holds_wd_pass_from_resurrecting_links(db, wikidata_source, spine, monkeypatch):
    """The demotion mechanism's load-bearing half: with the links retired,
    a wd-models re-run over the registered QID must not re-assert them."""
    monkeypatch.setattr(policy, "NOT_A_GENERATION", {"Q1": "trim_lineage"})
    # Q1 is the spine's e46, generation-attached; give its model a sweep QID
    # so the entity's P179 resolves to an attached model - the exact shape
    # that re-asserts a link on refresh.
    db.add(ExternalId(model_id=spine["model"].id, source_id=wikidata_source.id, external_id="Q100"))
    db.commit()
    _land_sweep(db, wikidata_source, "Q100", label="BMW 330i", classes=["Q3231690"])
    _land_sweep(db, wikidata_source, "Q1", label="BMW E46", series_of=["Q100"])

    link = db.scalars(
        select(GenerationModelLink).where(
            GenerationModelLink.generation_id == spine["e46"].id,
            GenerationModelLink.superseded_by.is_(None),
        )
    ).one()
    link.superseded_by = link.id
    db.commit()

    run_wikidata_models_pass(db)
    assert (
        db.scalar(
            select(GenerationModelLink.id).where(
                GenerationModelLink.generation_id == spine["e46"].id,
                GenerationModelLink.superseded_by.is_(None),
            )
        )
        is None
    ), "the registry gate must keep the retirement from resurrecting"
    from carmanac.db.models import MatchDecision

    decision = db.scalars(select(MatchDecision).where(MatchDecision.external_id == "Q1")).one()
    assert decision.outcome == "held_not_a_generation"
    assert decision.detail == {"verdict": "trim_lineage"}


@pytest.mark.integration
def test_sections_pass_excludes_demoted_from_reconciliation(
    db, wikidata_source, wikipedia_source, spine, monkeypatch
):
    """A model whose only linked generations are ruled wrong-grain mints
    freely: the demoted rows are not competitors, so sections neither
    reconcile onto them nor flag against them."""
    monkeypatch.setattr(policy, "NOT_A_GENERATION", {"Q1": "trim_lineage", "Q2": "body_style"})
    db.add(ExternalId(model_id=spine["model"].id, source_id=wikidata_source.id, external_id="Q9"))
    db.commit()
    _land_article(
        db,
        wikipedia_source,
        "Q9",
        "BMW 330i",
        "== First generation (AB10; 1999) ==\n{{Infobox automobile\n| production = 1999–2005\n}}\n",
    )
    stats = run_wikipedia_sections_pass(db)
    assert stats.generations_created == 1 and stats.flagged_articles == 0
    assert stats.sections_reconciled == 0, "a demoted row must never absorb a section"


# --- the demotion script --------------------------------------------------------


@pytest.mark.integration
def test_demotion_census_and_apply(db, wikidata_source, spine, monkeypatch):
    monkeypatch.setattr(policy, "NOT_A_GENERATION", {"Q1": "trim_lineage"})
    flag = ReconciliationFlag(
        kind="multi_value",
        generation_id=spine["e46"].id,
        field_name="start_year",
        detail={"claims": [1998, 1999]},
        source_id=wikidata_source.id,
    )
    db.add(flag)
    db.commit()

    entries = demote_census(db)
    ruled = [e for e in entries if e.get("verdict")]
    assert len(ruled) == 1 and ruled[0]["qid"] == "Q1"
    assert len(ruled[0]["links"]) == 1
    assert ruled[0]["placements"] == []
    assert len(ruled[0]["flags"]) == 1

    retired, resolved = demote_apply(db, entries)
    assert (retired, resolved) == (1, 1)
    live = db.scalar(
        select(GenerationModelLink.id).where(
            GenerationModelLink.generation_id == spine["e46"].id,
            GenerationModelLink.superseded_by.is_(None),
        )
    )
    assert live is None, "links retire by self-supersession"
    db.refresh(flag)
    assert flag.status == "resolved"
    assert flag.detail["resolution"] == "not_a_generation:trim_lineage"
    # The row, its external id, and its facts stay.
    assert db.get(Generation, spine["e46"].id) is not None
    assert db.scalar(select(ExternalId.id).where(ExternalId.external_id == "Q1")) is not None


# --- section-main: fact sourcing with per-record provenance ---------------------


def _land_section_main(
    db, source: Source, qid: str, ordinal: int, requested: str, resolved: str, wikitext: str
) -> RawRecord:
    payload = {
        "qid": qid,
        "ordinal": ordinal,
        "title": resolved,
        "requested_title": requested,
        "revid": 1,
        "wikitext": wikitext,
    }
    rec = RawRecord(
        source_id=source.id,
        external_id=f"section-main:{qid}#{ordinal}",
        content_hash=content_hash(payload),
        payload=payload,
    )
    db.add(rec)
    db.commit()
    return rec


MAIN_ARTICLE = (
    "== First generation (NA; 1989) ==\n"
    "{{Main|Test Roadster (NA)}}\n"
    "prose, no infobox\n"
    "== Second generation (NB; 1998) ==\n"
    "{{Main|Test Roadster (NB)}}\n"
    "prose, no infobox\n"
)


@pytest.fixture()
def minted_undated(db, wikidata_source, wikipedia_source, spine):
    """A second model whose article mints two undated section generations -
    the Main-target shape (sections defer content, no section infobox)."""
    from carmanac.db.models import Model

    model = Model(company_id=spine["company"].id, slug="roadster", name="Roadster")
    db.add(model)
    db.flush()
    db.add(ExternalId(model_id=model.id, source_id=wikidata_source.id, external_id="Q60"))
    db.commit()
    _land_article(db, wikipedia_source, "Q60", "Test Roadster", MAIN_ARTICLE)
    stats = run_wikipedia_sections_pass(db)
    assert stats.generations_created == 2
    na = db.scalar(
        select(Generation)
        .join(ExternalId, ExternalId.generation_id == Generation.id)
        .where(ExternalId.external_id == "section:Q60#1")
    )
    assert na.start_year is None, "no section infobox: undated until the target lands"
    return {"model": model, "na": na}


@pytest.mark.integration
def test_section_main_dates_generation_with_target_provenance(
    db, wikidata_source, wikipedia_source, spine, minted_undated
):
    record = _land_section_main(
        db,
        wikipedia_source,
        "Q60",
        1,
        "Test Roadster (NA)",
        "Test Roadster (NA)",
        "{{Infobox automobile\n| production = 1989 {{nbndash}} 1997\n"
        "| model_years = 1990–1997\n}}\n",
    )
    stats = run_wikipedia_sections_pass(db)
    na = db.get(Generation, minted_undated["na"].id)
    assert (na.start_year, na.end_year) == (1989, 1997)
    assert stats.flagged_articles == 0

    start = db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.generation_id == na.id,
            FieldProvenance.field_name == "start_year",
            FieldProvenance.superseded_by.is_(None),
        )
    ).one()
    assert start.raw_record_id == record.id, "provenance points at the section-main record"
    name = db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.generation_id == na.id,
            FieldProvenance.field_name == "name",
            FieldProvenance.superseded_by.is_(None),
        )
    ).one()
    assert name.raw_record_id != record.id, "the name is still the article's assertion"

    stats2 = run_wikipedia_sections_pass(db)
    assert stats2.assertions_inserted == 0 and stats2.assertions_superseded == 0


@pytest.mark.integration
def test_section_main_grain_guards_hold_in_the_pass(
    db, wikidata_source, wikipedia_source, spine, minted_undated
):
    """A redirected target and a bare-title (nameplate) target both assert
    nothing - the Kia Sephia shape: a whole-nameplate span that parses
    cleanly must not land on one generation."""
    _land_section_main(
        db,
        wikipedia_source,
        "Q60",
        1,
        "Test Roadster (NA)",
        "Test Roadster",
        "{{Infobox automobile\n| production = 1989–2005\n}}\n",
    )
    _land_section_main(
        db,
        wikipedia_source,
        "Q60",
        2,
        "Other Nameplate",
        "Other Nameplate",
        "{{Infobox automobile\n| production = 1992–2003\n}}\n",
    )
    run_wikipedia_sections_pass(db)
    na = db.get(Generation, minted_undated["na"].id)
    assert na.start_year is None, "redirected target asserts nothing"
    nb = db.scalar(
        select(Generation)
        .join(ExternalId, ExternalId.generation_id == Generation.id)
        .where(ExternalId.external_id == "section:Q60#2")
    )
    assert nb.start_year is None, "bare-title target asserts nothing"


@pytest.mark.integration
def test_section_main_feeds_placement_and_the_car_places(
    db, wikidata_source, wikipedia_source, spine, minted_undated
):
    """End to end: both targets land, both generations date, and the
    configuration places via the target's model_years with provenance to
    the section-main record."""
    na_record = _land_section_main(
        db,
        wikipedia_source,
        "Q60",
        1,
        "Test Roadster (NA)",
        "Test Roadster (NA)",
        "{{Infobox automobile\n| production = 1989–1997\n| model_years = 1990–1997\n"
        "| body_style = 2-door [[roadster]]\n}}\n",
    )
    _land_section_main(
        db,
        wikipedia_source,
        "Q60",
        2,
        "Test Roadster (NB)",
        "Test Roadster (NB)",
        "{{Infobox automobile\n| production = 1998–2005\n| model_years = 1999–2005\n}}\n",
    )
    run_wikipedia_sections_pass(db)

    roadster_spine = dict(spine, model=minted_undated["model"])
    config = _configuration(db, roadster_spine, 1995, "roadster-1995")
    run_generation_placement_pass(db)
    db.refresh(config)
    assert config.generation_id == minted_undated["na"].id
    placement = db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.configuration_id == config.id,
            FieldProvenance.field_name == "generation_id",
            FieldProvenance.superseded_by.is_(None),
        )
    ).one()
    assert placement.raw_record_id == na_record.id, (
        "the deciding span is the section-main record's model_years"
    )


# --- the lander's target selection ----------------------------------------------


@pytest.mark.integration
def test_lander_targets_only_clean_single_pointers(db, wikidata_source, wikipedia_source, spine):
    """Fragment targets and multi-target sections are skipped; dated
    generations leave the target set once dated (unless refreshing an
    already-landed record)."""
    from carmanac.db.models import Model

    model = Model(company_id=spine["company"].id, slug="mixed", name="Mixed")
    db.add(model)
    db.flush()
    db.add(ExternalId(model_id=model.id, source_id=wikidata_source.id, external_id="Q70"))
    db.commit()
    _land_article(
        db,
        wikipedia_source,
        "Q70",
        "Test Mixed",
        "== First generation (AA10; 1990) ==\n{{Main|Test Mixed (AA10)}}\nprose\n"
        "== Second generation (BB10; 1998) ==\n{{Main|Other Line#BB10}}\nprose\n"
        "== Third generation (CC10; 2005) ==\n{{Main|Test Mixed (CC10)}}{{Main|Elsewhere}}\n"
        "== Fourth generation (DD10; 2012) ==\n"
        "{{Main|Test Mixed (DD10)}}\n{{Infobox automobile\n| production = 2012–2019\n}}\n",
    )
    run_wikipedia_sections_pass(db)
    targets = section_main_targets(db)
    assert [(t.ordinal, t.title) for t in targets if t.qid == "Q70"] == [
        (1, "Test Mixed (AA10)"),
    ], "fragment, multi-target, and already-dated sections are all excluded"
