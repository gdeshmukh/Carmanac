"""ADR 0017 (amended 2026-09-17) tests for the LLM read as a source: the
page text and the gate, the script's landing, and the pass that mints,
dates, places, flags, defers and withdraws from what a read stated."""

# ruff: noqa: F811 - fixtures imported from the sibling module shadow their
# own names when taken as test parameters; that is how pytest fixtures work.

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from carmanac.db.models import (
    ExternalId,
    FieldProvenance,
    Generation,
    GenerationModelLink,
    RawRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.ingest.http import IngestHTTPError
from carmanac.ingest.landing import content_hash
from carmanac.ingest.llm_read import SOURCE_NAME, ask_openrouter, read_model, settings
from carmanac.reconcile import llm_read_pass
from carmanac.reconcile.generation_placement_pass import run_generation_placement_pass
from carmanac.reconcile.llm_read_pass import FLAG_KIND, run_llm_read_pass
from carmanac.reconcile.sources.llm_read import (
    PROMPT_VERSION,
    Leaf,
    build_messages,
    page_text,
    verify,
)
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
    "The E90 ran from 2005 to 2011 and represents the fifth generation.\n"
)
SECTIONED = (
    "The 3 Series is a car.\n== E36 (1990–2000) ==\nThe 325i was sold from 1991 to 1999.\n"
    "== E46 (1997–2006) ==\nThe M3 came in 2000.\nThe 330i was sold from 2001 to 2005 as a sedan.\n"
    "=== Coupé ===\nThe 328Ci coupé was sold from 1999 to 2000.\n"
    "== E90 (2005–2011) ==\nThe Targas came in 2006.\n"
    "== Fifth generation (F30; 2012) ==\n{{Infobox automobile\n| production = 2012–2019\n}}\n"
    'The <span id="f30">F30</span> sedan came first.\n'
    "=== Body ===\nThe Targa 4S was one body, the Turbo another; "
    "the 3.0&nbsp;L engine served both.\n"
    "== Spans ==\nGeneration V: 2012–2019 (F30)\n"
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
    assert ("G20", None, "quote does not state the name or codes and start") in reasons
    assert (None, 3, "outside the car's years") in reasons
    assert (None, 9, "not offered") in reasons


def test_verify_refuses_an_open_end_the_quote_does_not_state_and_a_leaf_two_generations_claim():
    text = page_text(WIKITEXT)
    answer = _answer([1])
    answer["generations"][0]["end_year"] = None
    assert verify(answer, text, _offered({1: 2003})).dropped == [
        {"generation": "E46", "reason": "open end without 'present'"}
    ]
    answer["generations"] = [
        _generation("E90", 2005, None, E90_QUOTE + " and represents the fifth")
    ]
    assert verify(answer, text, {}).dropped == [
        {"generation": "E90", "reason": "open end without 'present'"}
    ]

    answer = _answer([1, 2])
    answer["generations"][0]["cars"].append(_car("sedan", CAR_QUOTE + " as a sedan", [1]))
    answer["generations"].append(
        _generation("E90", 2005, 2011, E90_QUOTE, [_car("E90", E90_QUOTE, [2])])
    )
    out = verify(answer, text, _offered({1: 2003, 2: 2005}, trim="330i"))
    e46, e90 = out.generations
    assert [(c.name, c.leaves) for c in e46.cars] == [("330i", ((1, "exact"),)), ("sedan", ())], (
        "one generation claiming a leaf twice keeps it once"
    )
    assert e90.cars[0].leaves == () and {"leaf": 2, "reason": "claimed twice"} in out.dropped

    malformed = verify("not json", text, {})
    assert malformed.malformed and malformed.dropped == [{"reason": "malformed answer"}]


def test_verify_bounds_a_generation_by_its_own_section_and_names_by_whole_words():
    text = page_text(SECTIONED)
    # The read skips the E46: its section must not fall to the E36 by default.
    answer = {
        "generations": [
            _generation("E36", 1990, 2000, "E36 (1990–2000)", [_car("330i", CAR_QUOTE, [1])]),
            _generation(
                "E90", 2005, 2011, "E90 (2005–2011)", [_car("Targa", "The Targas came", [3])]
            ),
        ]
    }
    out = verify(answer, text, _offered({1: 2003, 3: 2006}))
    assert [(g.name, [c.leaves for c in g.cars]) for g in out.generations] == [
        ("E36", []),
        ("E90", [((3, "closest"),)]),
    ], "the plural states the Targa"
    assert {
        "generation": "E36",
        "car": "330i",
        "reason": "quoted outside the section",
    } in out.dropped

    answer = {
        "generations": [
            _generation("M3", 2001, None, "The M3 came in 2000", codes=[]),
            _generation(
                "E46",
                1997,
                2006,
                "E46 (1997–2006)",
                [
                    _car("328Ci", "The 328Ci coupé was sold from 1999 to 2000", [2]),
                    _car("330", CAR_QUOTE, [1]),
                    _car(" ", CAR_QUOTE, [1]),
                    _car("330i", CAR_QUOTE, [1]),
                ],
            ),
        ]
    }
    out = verify(answer, text, _offered({1: 2003, 2: 2000}, trim="330i"))
    (e46,) = out.generations
    assert [(c.name, c.leaves) for c in e46.cars] == [
        ("328Ci", ((2, "closest"),)),
        ("330i", ((1, "exact"),)),
    ], "a subsection stays inside its parent; the dropped M3 entry cuts nothing"
    reasons = {(d.get("car"), d["reason"]) for d in out.dropped}
    assert {("330", "car not quoted"), (" ", "no name")} <= reasons


def test_verify_judges_a_generation_s_quotes_one_passage_at_a_time():
    text = page_text(SECTIONED)
    heading, years = "Fifth generation (F30; 2012)", "production = 2012–2019"
    f30 = _generation("F30", 2012, 2019, None, [_car("F30", "The F30 sedan ... came first", [1])])
    f30["quotes"], f30["codes"] = [heading, years], ["F30", "F31"]
    e36 = _generation("E36", 1990, 2000, None)
    e36["quotes"] = ["E36 (1990–2000)", years]
    out = verify({"generations": [f30, e36]}, text, _offered({1: 2014}))
    assert [(g.name, g.codes, g.quote, [c.leaves for c in g.cars]) for g in out.generations] == [
        ("F30", ("F30",), f"{heading} | {years}", [((1, "closest"),)]),
        ("E36", ("E36",), "E36 (1990–2000)", []),
    ], "a spare quote from another section is set aside, not fatal; only stated codes stay"

    # A passage that names the generation counts from anywhere on the page.
    f30["quotes"] = [heading, "Generation V: 2012–2019 (F30)"]
    assert [g.end_year for g in verify({"generations": [f30]}, text, {}).generations] == [2019]
    f30["quotes"] = [heading]
    assert verify({"generations": [f30]}, text, {}).dropped == [
        {"generation": "F30", "reason": "quote does not state the end"}
    ]

    # A quoted heading anchors the section even when it is not the first quote.
    f30["quotes"] = ["The Targa 4S was one body", heading, years]
    assert [g.end_year for g in verify({"generations": [f30]}, text, {}).generations] == [2019]

    # An ellipsis joins passages; it never manufactures a phrase or a name.
    f30["quotes"] = [heading, years]
    f30["cars"] = [
        _car("Targa Turbo", "Targa ... Turbo", [1]),
        _car("Targa 4", "The Targa 4S was one body", [1]),
        _car("Turbo", "the Turbo another; the 3.0 L engine", [1]),
    ]
    out = verify({"generations": [f30]}, text, _offered({1: 2014}))
    assert [(c.name, c.leaves) for c in out.generations[0].cars] == [("Turbo", ((1, "closest"),))]
    assert {d["car"] for d in out.dropped} == {"Targa Turbo", "Targa 4"}


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


def _generation(name, start, end, quote, cars=(), codes=None) -> dict:
    codes = [name] if codes is None else codes
    return {
        "name": name,
        "codes": codes,
        "start_year": start,
        "end_year": end,
        "quote": quote,
        "cars": list(cars),
    }


def _car(name, quote, leaves: list[int]) -> dict:
    return {"name": name, "quote": quote, "leaves": [{"id": i, "match": "exact"} for i in leaves]}


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


def _land_read(
    db, source, page, answer: dict | str, leaf_ids: list[int], llm=None, version=PROMPT_VERSION
) -> RawRecord:
    payload = {
        "qid": "Q9",
        "title": "BMW 330i",
        "page_record_id": page.id,
        "revid": 1,
        "prompt_version": version,
        "llm": llm or settings.llm_model,
        "leaf_ids": leaf_ids,
        "answer": answer if isinstance(answer, str) else json.dumps(answer),
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
def test_the_script_asks_once_per_question_and_lands_the_answer_untouched(
    db, llm_source, spine, wikipedia_source, article
):
    asked: list[list[dict]] = []
    ids = [leaf.id for leaf in article["leaves"]]
    raw = "```json\n" + json.dumps(_answer(ids[:1], ids[2:]), indent=1) + "\n```"

    def fake(messages, llm):
        asked.append(messages)
        return raw

    result = read_model(db, "bmw/330i", llm="test", ask=fake)
    assert (result.read, result.generations, result.leaves, result.dropped) == (True, 2, 2, 0)
    assert (
        "E46 | - | ?–?" in asked[0][1]["content"] and f"{ids[0]} | 2003" in asked[0][1]["content"]
    )
    record = db.scalars(select(RawRecord).where(RawRecord.source_id == llm_source.id)).one()
    assert record.external_id == "read:Q9" and record.payload["leaf_ids"] == ids
    assert record.payload["answer"] == raw, "fences and all"

    assert read_model(db, "bmw/330i", llm="test", ask=fake).read is False and len(asked) == 1
    assert read_model(db, "bmw/330i", llm="test", ask=fake, force=True).read and len(asked) == 2
    assert read_model(db, "bmw/330i", llm="other", ask=fake).read and len(asked) == 3
    _configuration(db, spine, 2008, "sedan")
    assert read_model(db, "bmw/330i", llm="test", ask=fake).read and len(asked) == 4, (
        "a new candidate is a new question"
    )
    _land_article(db, wikipedia_source, "Q9", "BMW 330i", WIKITEXT + "A later revision.\n")
    assert read_model(db, "bmw/330i", llm="test", ask=fake).read and len(asked) == 5, (
        "so is a new revision of the page"
    )


def test_the_script_refuses_an_answer_the_model_did_not_finish(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test")
    for choice in (
        {"finish_reason": "length", "message": {"content": '{"generations": ['}},
        {"finish_reason": "stop", "message": {"content": None, "refusal": "no"}},
    ):
        monkeypatch.setattr(
            "carmanac.ingest.http.PoliteClient.request",
            lambda self, method, url, body=choice, **kwargs: _Response({"choices": [body]}),
        )
        with pytest.raises(IngestHTTPError):
            ask_openrouter([], "test")


class _Response:
    def __init__(self, body: dict):
        self.body = body

    def json(self) -> dict:
        return self.body


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
    _land_read(db, llm_source, page, _answer([c2003.id]), [c.id for c in article["leaves"]])
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
def test_the_pass_leaves_the_world_alone_for_a_stale_or_malformed_read(
    db, llm_source, spine, article
):
    page, (c2003, _c2005, c2012) = article["page"], article["leaves"]
    ids = [c.id for c in article["leaves"]]
    _land_read(db, llm_source, page, _answer([c2003.id], [c2012.id]), ids)
    assert run_llm_read_pass(db).placed == 2

    _land_read(db, llm_source, page, '{"generations": [', ids)
    stats = run_llm_read_pass(db)
    assert (stats.skipped, stats.withdrawn, stats.flags_dismissed) == (1, 0, 0)
    _land_read(db, llm_source, page, _answer([]), ids, version="1")
    _land_read(db, llm_source, page, _answer([]), ids, llm="another model")
    stats = run_llm_read_pass(db)
    assert (stats.skipped, stats.withdrawn, stats.flags_dismissed) == (3, 0, 0), (
        "a stale prompt, another model, and the cut-short answer all state nothing"
    )
    db.refresh(c2003), db.refresh(c2012)
    assert c2003.generation_id == spine["e46"].id and c2012.generation_id is not None


@pytest.mark.integration
def test_a_minted_generation_follows_the_read_that_states_it(db, llm_source, spine, article):
    page, (c2003, _c2005, c2012) = article["page"], article["leaves"]
    ids = [c.id for c in article["leaves"]]
    _land_read(db, llm_source, page, _answer([c2003.id], [c2012.id]), ids)
    run_llm_read_pass(db)
    f30 = db.scalar(
        select(Generation)
        .join(ExternalId, ExternalId.generation_id == Generation.id)
        .where(ExternalId.external_id == "read:Q9#f30")
    )

    # Renamed but matched by code, and the E46 stated twice: one row each.
    answer = _answer([c2003.id], [c2012.id])
    answer["generations"][1]["name"] = "F30 series"
    answer["generations"].append(_generation("E46 facelift", 1997, 2006, E46_QUOTE, codes=["E46"]))
    _land_read(db, llm_source, page, answer, ids)
    stats = run_llm_read_pass(db)
    assert (stats.generations_minted, stats.generations_retired, stats.withdrawn) == (0, 0, 0)
    again = run_llm_read_pass(db)
    assert (again.assertions_inserted, again.assertions_superseded, again.flags_opened) == (
        0,
        0,
        0,
    )
    db.refresh(f30), db.refresh(c2012)
    assert (f30.start_year, f30.chassis_codes, c2012.generation_id) == (2012, ["F30"], f30.id)
    assert db.scalar(select(func.count()).select_from(Generation)) == 3, "E46, E90, F30"

    # Dropped: its facts and its link retire, so it holds nothing up.
    answer = _answer([c2003.id])
    answer["generations"].append(_generation("E90", 2005, 2011, E90_QUOTE, codes=[]))
    _land_read(db, llm_source, page, answer, ids)
    stats = run_llm_read_pass(db)
    assert (stats.generations_retired, stats.links_retired, stats.withdrawn) == (1, 1, 1)
    c2008 = _configuration(db, spine, 2008, "sedan")
    placement = run_generation_placement_pass(db)
    db.refresh(c2008), db.refresh(f30)
    assert c2008.generation_id == spine["e90"].id and placement.undated_competitor == 0
    assert (f30.start_year, f30.chassis_codes) == (None, None)
    assert (
        db.scalar(
            select(func.count())
            .select_from(GenerationModelLink)
            .where(
                GenerationModelLink.generation_id == f30.id,
                GenerationModelLink.superseded_by.is_(None),
            )
        )
        == 0
    )
    assert run_llm_read_pass(db).links_retired == 0

    # A held generation the read alone dated loses that span when a read stops stating it.
    db.refresh(spine["e90"])
    assert (spine["e90"].start_year, spine["e90"].end_year) == (2005, 2011)
    _land_read(db, llm_source, page, _answer([c2003.id]), ids)
    assert run_llm_read_pass(db).generations_retired == 1
    db.refresh(spine["e90"])
    assert (spine["e90"].start_year, spine["e90"].end_year) == (None, None)


@pytest.mark.integration
def test_a_review_flag_resolved_by_hand_stays_resolved(db, llm_source, spine, article):
    page, (_c2003, c2005, _c2012) = article["page"], article["leaves"]
    _land_read(db, llm_source, page, _answer([c2005.id]), [c.id for c in article["leaves"]])
    run_llm_read_pass(db)
    review = db.scalars(
        select(ReconciliationFlag).where(
            ReconciliationFlag.configuration_id == c2005.id, ReconciliationFlag.status == "open"
        )
    ).one()
    review.status = "resolved"
    db.commit()
    assert run_llm_read_pass(db).flags_opened == 0, "a person's word holds"
    assert (
        db.scalar(
            select(func.count())
            .select_from(ReconciliationFlag)
            .where(ReconciliationFlag.configuration_id == c2005.id)
        )
        == 1
    )


@pytest.mark.integration
def test_a_correction_outranks_the_read_and_a_held_placement_is_only_contradicted(
    db, monkeypatch, llm_source, spine, article, wikidata_source
):
    page, (c2003, c2005, _c2012) = article["page"], article["leaves"]
    _land_read(
        db, llm_source, page, _answer([c2003.id, c2005.id]), [c.id for c in article["leaves"]]
    )
    monkeypatch.setattr(llm_read_pass, "PLACEMENT_CORRECTIONS", {"bmw/330i/2005/sedan": "e90"})
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
