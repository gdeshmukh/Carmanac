"""Corporate-structure landing and the company_relationships pass (ADR 0022)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from carmanac.db.models import Company, CompanyRelationship, ExternalId, MatchDecision, RawRecord
from carmanac.ingest.wikidata.relations import RELATIONS_QUERY, SWEEP_MARKER, land_relations
from carmanac.reconcile import policy
from carmanac.reconcile.company_relations_pass import run_company_relations_pass

pytestmark = pytest.mark.integration

_ENTITY = "http://www.wikidata.org/entity/"
_RANK = "http://wikiba.se/ontology#"


class FakeSparqlClient:
    def __init__(self, bindings: list[dict[str, Any]]) -> None:
        self.bindings = bindings
        self.endpoint = "fake://wikidata"

    def query(self, sparql: str) -> dict[str, Any]:
        asked = {token.removeprefix("wd:") for token in sparql.split() if token.startswith("wd:Q")}
        rows = [row for row in self.bindings if row["item"]["value"].rsplit("/", 1)[-1] in asked]
        return {"results": {"bindings": rows}}

    def close(self) -> None:
        raise AssertionError("an injected client must not be closed")


def _binding(
    qid: str,
    edge: str,
    target: str,
    *,
    label: str | None = None,
    rank: str = "NormalRank",
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    row = {
        "item": {"type": "uri", "value": f"{_ENTITY}{qid}"},
        "edge": {"type": "literal", "value": edge},
        "target": {"type": "uri", "value": f"{_ENTITY}{target}"},
        "targetLabel": {"type": "literal", "value": label or target},
        "rank": {"type": "uri", "value": f"{_RANK}{rank}"},
    }
    for key, value in (("start", start), ("end", end)):
        if value:
            row[key] = {"type": "literal", "value": value}
    return row


@pytest.fixture()
def graph(db, wikidata_source):
    """Opel (Q1) and General Motors (Q2), both held; Stellantis (Q3) not."""
    opel = Company(name="Opel", slug="opel")
    gm = Company(name="General Motors", slug="general-motors")
    db.add_all([opel, gm])
    db.flush()
    db.add_all(
        [
            ExternalId(company_id=opel.id, source_id=wikidata_source.id, external_id="Q1"),
            ExternalId(company_id=gm.id, source_id=wikidata_source.id, external_id="Q2"),
        ]
    )
    db.commit()
    return opel, gm


def _land(db, bindings):
    return land_relations(db, client=FakeSparqlClient(bindings))


def _live(db):
    return list(
        db.scalars(select(CompanyRelationship).where(CompanyRelationship.superseded_by.is_(None)))
    )


def test_query_is_keyed_by_held_qids_and_lands_three_edges():
    assert "VALUES ?item" in RELATIONS_QUERY
    for edge in ("parents", "subsidiaries", "owners"):
        assert f'"{edge}"' in RELATIONS_QUERY


def test_landing_stamps_the_marker_and_keeps_qualifiers(db, wikidata_source, graph):
    result = _land(
        db,
        [
            _binding(
                "Q1",
                "parents",
                "Q2",
                label="General Motors",
                start="1931-01-01T00:00:00Z",
                end="2017-01-01T00:00:00Z",
            ),
            _binding("Q1", "parents", "Q3", label="Stellantis", start="2021-01-01T00:00:00Z"),
            _binding("Q1", "owners", "Q9", label="Some Bank"),
        ],
    )
    assert result.fetched == 2 and result.inserted == 2
    record = db.scalar(
        select(RawRecord).where(
            RawRecord.external_id == "Q1", RawRecord.source_id == wikidata_source.id
        )
    )
    assert record.payload["sweep"] == SWEEP_MARKER
    assert [p["qid"] for p in record.payload["parents"]] == ["Q2", "Q3"]
    assert record.payload["parents"][0]["starts"] == ["1931-01-01T00:00:00Z"]
    assert record.payload["owners"][0]["label"] == "Some Bank"
    again = _land(
        db,
        [
            _binding(
                "Q1",
                "parents",
                "Q2",
                label="General Motors",
                start="1931-01-01T00:00:00Z",
                end="2017-01-01T00:00:00Z",
            ),
            _binding("Q1", "parents", "Q3", label="Stellantis", start="2021-01-01T00:00:00Z"),
            _binding("Q1", "owners", "Q9", label="Some Bank"),
        ],
    )
    assert again.inserted == 0  # same content, same hash: a no-op


def test_parent_claim_asserts_a_dated_era_and_unheld_parent_waits(db, wikidata_source, graph):
    opel, gm = graph
    _land(
        db,
        [
            _binding(
                "Q1", "parents", "Q2", start="1931-01-01T00:00:00Z", end="2017-01-01T00:00:00Z"
            ),
            _binding("Q1", "parents", "Q3", label="Stellantis", start="2021-01-01T00:00:00Z"),
        ],
    )
    stats = run_company_relations_pass(db)
    db.commit()
    assert stats.eras_inserted == 1 and stats.waits_parent_not_held == 1
    (row,) = _live(db)
    assert (row.company_id, row.parent_company_id) == (opel.id, gm.id)
    assert (row.start_year, row.end_year) == (1931, 2017)
    assert row.kind == "parent_organization" and row.source_id == wikidata_source.id
    assert row.raw_record_id is not None
    decision = db.scalar(select(MatchDecision).where(MatchDecision.external_id == "Q1"))
    assert decision.detail["not_held"] == ["Stellantis"]

    again = run_company_relations_pass(db)
    assert again.eras_inserted == 0 and again.eras_retired == 0
    assert len(_live(db)) == 1


def test_subsidiary_claim_on_the_parent_is_the_same_era(db, wikidata_source, graph):
    opel, gm = graph
    _land(
        db,
        [
            _binding(
                "Q1", "parents", "Q2", start="1931-01-01T00:00:00Z", end="2017-01-01T00:00:00Z"
            ),
            _binding(
                "Q2", "subsidiaries", "Q1", start="1931-01-01T00:00:00Z", end="2017-01-01T00:00:00Z"
            ),
        ],
    )
    stats = run_company_relations_pass(db)
    assert stats.eras_asserted == 1 and stats.eras_inserted == 1
    (row,) = _live(db)
    assert (row.company_id, row.parent_company_id) == (opel.id, gm.id)


def test_owned_by_asserts_nothing(db, wikidata_source, graph):
    _land(db, [_binding("Q1", "owners", "Q2")])
    stats = run_company_relations_pass(db)
    assert stats.eras_asserted == 0 and _live(db) == []


def test_deprecated_statements_are_ignored(db, wikidata_source, graph):
    _land(db, [_binding("Q1", "parents", "Q2", rank="DeprecatedRank")])
    assert run_company_relations_pass(db).eras_asserted == 0


def test_self_edge_after_a_merge_is_skipped(db, wikidata_source, graph, monkeypatch):
    opel, _ = graph
    db.add(ExternalId(company_id=opel.id, source_id=wikidata_source.id, external_id="Q7"))
    db.commit()
    monkeypatch.setattr(policy, "IDENTITY_MERGES", {"Q7": "Q1"})
    _land(db, [_binding("Q1", "owners", "Q7"), _binding("Q1", "parents", "Q7")])
    stats = run_company_relations_pass(db)
    assert stats.self_edges == 1 and stats.eras_asserted == 0


def test_a_withdrawn_era_is_retired_not_deleted(db, wikidata_source, graph):
    _land(db, [_binding("Q1", "parents", "Q2", start="1931-01-01T00:00:00Z")])
    run_company_relations_pass(db)
    db.commit()
    _land(
        db,
        [_binding("Q1", "parents", "Q2", start="1931-01-01T00:00:00Z", end="2017-01-01T00:00:00Z")],
    )
    stats = run_company_relations_pass(db)
    db.commit()
    assert stats.eras_inserted == 1 and stats.eras_retired == 1
    rows = list(db.scalars(select(CompanyRelationship)))
    assert len(rows) == 2
    retired = next(r for r in rows if r.superseded_by is not None)
    assert retired.end_year is None and retired.superseded_by == retired.id


def test_an_era_ending_before_it_starts_is_never_asserted(db, wikidata_source, graph):
    _land(
        db,
        [_binding("Q1", "parents", "Q2", start="1896-01-01T00:00:00Z", end="1891-01-01T00:00:00Z")],
    )
    stats = run_company_relations_pass(db)
    assert stats.implausible_eras == 1 and stats.eras_asserted == 0
    decision = db.scalar(select(MatchDecision).where(MatchDecision.external_id == "Q1"))
    assert decision.detail["implausible"] == ["Q2"]


def test_an_undated_claim_beside_a_dated_one_is_not_a_second_era(db, wikidata_source, graph):
    """Both ends state the ownership, only one carries the qualifiers: the
    undated row is the same claim with its dates dropped, not a second era."""
    _land(
        db,
        [
            _binding(
                "Q1", "parents", "Q2", start="1931-01-01T00:00:00Z", end="2017-01-01T00:00:00Z"
            ),
            _binding("Q2", "subsidiaries", "Q1"),
        ],
    )
    stats = run_company_relations_pass(db)
    assert stats.eras_asserted == 1
    (row,) = _live(db)
    assert (row.start_year, row.end_year) == (1931, 2017)


def test_an_undated_claim_alone_still_lands(db, wikidata_source, graph):
    _land(db, [_binding("Q1", "parents", "Q2")])
    stats = run_company_relations_pass(db)
    assert stats.eras_asserted == 1
    (row,) = _live(db)
    assert (row.start_year, row.end_year) == (None, None)
