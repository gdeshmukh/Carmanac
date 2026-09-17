"""ADR 0017 (amended 2026-09-17) tests for the LLM read as a source: the
page text and the gate, the script's landing, and the pass that mints,
dates, places, flags, defers and withdraws from what a read stated."""

# ruff: noqa: F811 - fixtures imported from the sibling module shadow their
# own names when taken as test parameters; that is how pytest fixtures work.

from __future__ import annotations

import json

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
from carmanac.ingest.llm_read import SOURCE_NAME, read_model
from carmanac.reconcile import policy
from carmanac.reconcile.generation_placement_pass import run_generation_placement_pass
from carmanac.reconcile.llm_read_pass import FLAG_KIND, run_llm_read_pass
from carmanac.reconcile.sources.llm_read import Leaf, build_messages, page_text, verify
from tests.test_generation_placement import (  # noqa: F401
    _configuration,
    _land_article,
    spine,
    wikipedia_source,
)

WIKITEXT = (
    "The '''E46''' is the fourth generation of the [[BMW 3 Series]]<ref>x</ref>, "
    "produced from 1997 to 2006.\n"
    "The 330i was sold from 2001 to 2005 as a [[sedan (car)|sedan]].\n"
    "The F30 followed in 2012 and remains in production (2012–present).\n"
    "The E90 ran from 2005 to 2011.\n"
)
E90_QUOTE = "The E90 ran from 2005 to 2011"
E46_QUOTE = "The E46 is the fourth generation of the BMW 3 Series, produced from 1997 to 2006"
F30_QUOTE = "The F30 followed in 2012 and remains in production (2012–present)"
CAR_QUOTE = "The 330i was sold from 2001 to 2005"


def _answer(e46_leaves: list[int], f30_leaves: list[int] | None = None) -> dict:
    generations = [
        {
            "name": "E46",
            "codes": ["E46"],
            "start_year": 1997,
            "end_year": 2006,
            "quote": E46_QUOTE,
            "cars": [
                {
                    "name": "330i",
                    "start_year": 2001,
                    "end_year": 2005,
                    "quote": CAR_QUOTE,
                    "leaves": [{"id": i, "match": "exact"} for i in e46_leaves],
                }
            ],
        }
    ]
    if f30_leaves is not None:
        generations.append(
            {
                "name": "F30",
                "codes": ["F30"],
                "start_year": 2012,
                "end_year": None,
                "quote": F30_QUOTE,
                "cars": [
                    {
                        "name": "F30",
                        "quote": F30_QUOTE,
                        "leaves": [{"id": i, "match": "closest"} for i in f30_leaves],
                    }
                ],
            }
        )
    return {"generations": generations}


# --- the text and the gate (pure) --------------------------------------------


def test_page_text_keeps_words_and_drops_markup():
    text = page_text(WIKITEXT)
    assert E46_QUOTE in text and "as a sedan." in text
    assert "<ref>" not in text and "'''" not in text and "[[" not in text


def test_verify_keeps_only_what_the_page_states():
    text = page_text(WIKITEXT)
    answer = _answer([1, 2, 3, 9], [4])
    answer["generations"].append(
        {
            "name": "E90",
            "codes": ["E90"],
            "start_year": 2005,
            "end_year": 2011,
            "quote": "The E90 was sold from 2005 to 2011",
            "cars": [],
        }
    )
    answer["generations"].append(
        {
            "name": "G20",
            "codes": ["G20"],
            "start_year": 2019,
            "end_year": None,
            "quote": E46_QUOTE,
            "cars": [],
        }
    )
    out = verify(answer, text, _offered({1: 2003, 2: 2005, 3: 2006, 4: 2012}, trim="330i"))
    assert [g.name for g in out.generations] == ["E46", "F30"]
    (car,) = out.generations[0].cars
    assert car.leaves == ((1, "exact"), (2, "exact")), "2006 is outside 2001–2005; 9 not offered"
    assert out.generations[1].end_year is None and out.generations[1].cars[0].leaves == (
        (4, "closest"),
    )
    reasons = {(d.get("generation"), d.get("leaf"), d["reason"]) for d in out.dropped}
    assert ("E90", None, "quote not on the page") in reasons
    assert ("G20", None, "quote does not state the codes and start") in reasons
    assert (None, 3, "outside the car's years") in reasons
    assert (None, 9, "not offered") in reasons


def test_verify_refuses_an_open_end_the_quote_does_not_state_and_a_leaf_claimed_twice():
    text = page_text(WIKITEXT)
    answer = _answer([1], [1])
    answer["generations"][0]["end_year"] = None
    out = verify(answer, text, _offered({1: 2003}))
    assert [g.name for g in out.generations] == ["F30"]
    assert out.generations[0].cars[0].leaves == (), "1 was claimed by both cars"
    assert verify("not json", text, {}).dropped == [{"reason": "malformed answer"}]


def test_verify_wants_the_car_quoted_inside_its_generation_and_names_the_match_honestly():
    text = page_text(WIKITEXT)
    answer = _answer([1], [2])
    # The F30's car quotes a sentence that sits in the E46's stretch of the page.
    answer["generations"][1]["cars"][0].update(name="330i", quote=CAR_QUOTE)
    out = verify(answer, text, _offered({1: 2003, 2: 2012}, trim="sedan"))
    assert out.generations[1].cars == ()
    assert {
        "generation": "F30",
        "car": "330i",
        "reason": "quoted outside the section",
    } in out.dropped
    assert out.generations[0].cars[0].leaves == ((1, "closest"),), "the trim is not the car's name"


def _offered(years: dict[int, int], trim: str | None = None) -> dict[int, Leaf]:
    return {i: Leaf(i, year, trim, None, None, None, None, None) for i, year in years.items()}


def test_messages_carry_the_held_generations_and_the_leaf_lines():
    leaf = Leaf(7, 2003, "330i", "Sedan", "Rear-wheel drive", 2979, 6, 225)
    system, user = build_messages("BMW 330i", "text", [("E46", ["E46"], 1997, 2006)], [leaf])
    assert system["role"] == "system" and "verbatim" in system["content"]
    assert "E46 | E46 | 1997–2006" in user["content"]
    assert "7 | 2003 | 330i | Sedan | Rear-wheel drive | 2979 cc 6 cyl 225 hp" in user["content"]
    assert user["content"].endswith("Article text:\ntext")


# --- the script and the pass (integration) ------------------------------------


@pytest.fixture()
def llm_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == SOURCE_NAME))
    if source is None:
        source = Source(name=SOURCE_NAME, tier=3, base_url="https://openrouter.ai")
        db.add(source)
        db.commit()
    return source


@pytest.fixture()
def article(db, wikidata_source, wikipedia_source, spine):
    """The spine's model gains a Wikidata id, a landed article, and three cars."""
    db.add(ExternalId(model_id=spine["model"].id, source_id=wikidata_source.id, external_id="Q9"))
    db.commit()
    page = _land_article(db, wikipedia_source, "Q9", "BMW 330i", WIKITEXT)
    leaves = [_configuration(db, spine, year, "sedan") for year in (2003, 2005, 2012)]
    return {"page": page, "leaves": leaves}


def _land_read(db, source, page, answer: dict, leaf_ids: list[int], llm="test") -> RawRecord:
    payload = {
        "qid": "Q9",
        "title": "BMW 330i",
        "page_record_id": page.id,
        "revid": 1,
        "prompt_version": "2",
        "llm": llm,
        "leaf_ids": leaf_ids,
        "answer": json.dumps(answer),
    }
    record = RawRecord(
        source_id=source.id,
        external_id="read:Q9",
        content_hash=content_hash(payload),
        payload=payload,
    )
    db.add(record)
    db.commit()
    return record


@pytest.mark.integration
def test_the_script_asks_once_per_page_and_lands_the_answer_untouched(db, llm_source, article):
    asked: list[list[dict]] = []
    ids = [leaf.id for leaf in article["leaves"]]

    def fake(messages, llm):
        asked.append(messages)
        return json.dumps(_answer(ids[:1], ids[2:]))

    result = read_model(db, "bmw/330i", llm="test", ask=fake)
    assert (result.read, result.generations, result.leaves, result.dropped) == (True, 2, 2, 0)
    assert (
        "E46 | - | ?–?" in asked[0][1]["content"] and f"{ids[0]} | 2003" in asked[0][1]["content"]
    )
    record = db.scalars(select(RawRecord).where(RawRecord.source_id == llm_source.id)).one()
    assert record.external_id == "read:Q9" and record.payload["leaf_ids"] == ids
    assert json.loads(record.payload["answer"]) == _answer(ids[:1], ids[2:])

    assert read_model(db, "bmw/330i", llm="test", ask=fake).read is False and len(asked) == 1
    assert read_model(db, "bmw/330i", llm="test", ask=fake, force=True).read and len(asked) == 2
    assert read_model(db, "bmw/330i", llm="other", ask=fake).read and len(asked) == 3


@pytest.mark.integration
def test_the_pass_dates_mints_places_flags_and_withdraws(db, llm_source, spine, article):
    page, (c2003, c2005, c2012) = article["page"], article["leaves"]
    _land_read(
        db, llm_source, page, _answer([c2003.id], [c2012.id]), [c.id for c in article["leaves"]]
    )

    stats = run_llm_read_pass(db)
    assert (stats.generations_matched, stats.generations_minted) == (1, 1)
    assert (stats.placed, stats.flags_opened, stats.dropped) == (2, 2, 0)
    db.refresh(spine["e46"])
    assert (spine["e46"].start_year, spine["e46"].end_year) == (1997, 2006), (
        "matched by name, dated"
    )
    f30 = db.scalar(
        select(Generation)
        .join(ExternalId, ExternalId.generation_id == Generation.id)
        .where(ExternalId.external_id == "read:Q9#f30")
    )
    assert (f30.name, f30.chassis_codes, f30.start_year, f30.end_year) == (
        "F30",
        ["F30"],
        2012,
        None,
    )
    assert (
        db.scalars(
            select(GenerationModelLink).where(
                GenerationModelLink.generation_id == f30.id,
                GenerationModelLink.model_id == spine["model"].id,
            )
        )
        .one()
        .raw_record_id
        is not None
    )
    db.refresh(c2003), db.refresh(c2012), db.refresh(c2005)
    assert (c2003.generation_id, c2012.generation_id, c2005.generation_id) == (
        spine["e46"].id,
        f30.id,
        None,
    )
    flags = {
        f.configuration_id: f.detail
        for f in db.scalars(select(ReconciliationFlag).where(ReconciliationFlag.kind == FLAG_KIND))
    }
    assert flags[c2003.id] == {"generation": "e46", "match": "closest", "quote": CAR_QUOTE}
    assert flags[c2012.id]["match"] == "closest"

    again = run_llm_read_pass(db)
    assert (again.placed, again.already_placed, again.assertions_inserted, again.flags_opened) == (
        0,
        2,
        0,
        0,
    )
    assert (again.generations_minted, again.links_asserted, again.withdrawn) == (0, 0, 0)

    # A newer read no longer states the F30 or its leaf.
    _land_read(
        db, llm_source, page, _answer([c2003.id]), [c.id for c in article["leaves"]], llm="test2"
    )
    later = run_llm_read_pass(db)
    assert (later.withdrawn, later.flags_dismissed, later.already_placed) == (1, 1, 1)
    db.refresh(c2012), db.refresh(f30)
    assert c2012.generation_id is None and (f30.start_year, f30.chassis_codes) == (None, None)
    assert db.scalar(select(ExternalId).where(ExternalId.external_id == "read:Q9#f30")) is not None
    assert (
        db.scalars(
            select(FieldProvenance).where(
                FieldProvenance.configuration_id == c2012.id,
                FieldProvenance.superseded_by.is_(None),
            )
        )
        .one()
        .observed_value
        is None
    )


@pytest.mark.integration
def test_placement_pass_defers_to_a_stated_placement_and_flags_what_rests_on_a_read_span(
    db, llm_source, spine, article
):
    page, (c2003, _c2005, _c2012) = article["page"], article["leaves"]
    c2008 = _configuration(db, spine, 2008, "sedan")
    answer = _answer([c2003.id])
    answer["generations"].append(
        {
            "name": "E90",
            "codes": [],
            "start_year": 2005,
            "end_year": 2011,
            "quote": E90_QUOTE,
            "cars": [],
        }
    )
    _land_read(db, llm_source, page, answer, [c.id for c in article["leaves"]])
    run_llm_read_pass(db)
    stats = run_generation_placement_pass(db)
    db.refresh(c2003), db.refresh(c2008)
    assert c2003.generation_id == spine["e46"].id and stats.deferred == 1
    # 2008 by the E90's span, 2012 by its end-year slack: both rest on the read.
    assert c2008.generation_id == spine["e90"].id and stats.read_span == 2, "dated only by the read"
    flag = db.scalars(
        select(ReconciliationFlag).where(
            ReconciliationFlag.configuration_id == c2008.id, ReconciliationFlag.status == "open"
        )
    ).one()
    assert (flag.kind, flag.detail["reason"], flag.detail["span"]) == (
        FLAG_KIND,
        "placed by a span a read stated",
        "2005–2011",
    )
    again = run_generation_placement_pass(db)
    assert (again.flags_opened, again.withdrawn, again.read_span) == (0, 0, 2)


@pytest.mark.integration
def test_a_correction_outranks_the_read_and_a_held_placement_is_only_contradicted(
    db, monkeypatch, llm_source, spine, article, wikidata_source
):
    page, (c2003, c2005, _c2012) = article["page"], article["leaves"]
    _land_read(
        db, llm_source, page, _answer([c2003.id, c2005.id]), [c.id for c in article["leaves"]]
    )
    monkeypatch.setattr(policy, "PLACEMENT_CORRECTIONS", {"bmw/330i/2005/sedan": "e90"})
    # Another source already placed the 2003 car on the E90.
    c2003.generation_id = spine["e90"].id
    db.add(
        FieldProvenance(
            configuration_id=c2003.id,
            field_name="generation_id",
            observed_value=f"generation:{spine['e90'].id}[2005–present]",
            source_id=wikidata_source.id,
        )
    )
    db.commit()

    stats = run_llm_read_pass(db)
    assert (stats.corrected, stats.contradicted, stats.placed) == (1, 1, 0)
    db.refresh(c2003), db.refresh(c2005)
    assert (c2003.generation_id, c2005.generation_id) == (spine["e90"].id, spine["e90"].id)
    flags = {
        f.configuration_id: f.detail
        for f in db.scalars(
            select(ReconciliationFlag).where(
                ReconciliationFlag.kind == FLAG_KIND, ReconciliationFlag.status == "open"
            )
        )
    }
    assert set(flags) == {c2003.id} and flags[c2003.id]["contradicts"] == "e90"

    again = run_llm_read_pass(db)
    assert (again.corrected, again.contradicted, again.flags_opened) == (0, 1, 0)
