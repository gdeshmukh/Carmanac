"""Models-sweep landing tests (ADR 0012 §1), through the REAL land_models code.

The SPARQL client is faked (canned QID list + canned detail bindings, zero
network); everything from batching through canonicalization and the ON
CONFLICT upsert runs for real against the migrated test database. The landing
properties under permanent test:

- bare-QID external ids with the `"sweep": "models"` payload marker stamped
- batching: one detail query per batch, commit per batch (resumability)
- idempotent re-land; rotated multi-value cells hash unchanged (the models
  query's OWN alias set, not the makes query's)
- a models-sweep record and a makes-sweep record for the SAME QID coexist -
  the partition the sweep marker exists for
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from carmanac.db.models import RawRecord
from carmanac.ingest.wikidata.land import land_makes
from carmanac.ingest.wikidata.models import land_models

pytestmark = pytest.mark.integration

_ENTITY = "http://www.wikidata.org/entity/"


class FakeSweepClient:
    """Answers the sweep's two query shapes: the QID-list query with `qids`,
    each detail query with the canned bindings for the QIDs it asks about."""

    def __init__(self, bindings: list[dict[str, Any]]) -> None:
        self.bindings = {b["item"]["value"].rsplit("/", 1)[-1]: b for b in bindings}
        self.endpoint = "fake://sparql"
        self.detail_queries = 0

    def query(self, sparql: str) -> dict[str, Any]:
        if "VALUES ?cls" in sparql:
            rows = [{"item": {"type": "uri", "value": f"{_ENTITY}{q}"}} for q in self.bindings]
            return {"results": {"bindings": rows}}
        self.detail_queries += 1
        asked = [tok.removeprefix("wd:") for tok in sparql.split() if tok.startswith("wd:Q")]
        return {"results": {"bindings": [self.bindings[q] for q in asked if q in self.bindings]}}

    def close(self) -> None:  # pragma: no cover - only owned clients are closed
        raise AssertionError("land_models must not close a client it was handed")


def entity(qid: str, label: str, makers: str = "", **extra: str) -> dict[str, Any]:
    b: dict[str, Any] = {
        "item": {"type": "uri", "value": f"{_ENTITY}{qid}"},
        "itemLabel": {"type": "literal", "value": label},
        "makers": {"type": "literal", "value": makers},
    }
    for var, value in extra.items():
        b[var] = {"type": "literal", "value": value}
    return b


def rows_for(db, qid: str) -> list[RawRecord]:
    return list(
        db.scalars(select(RawRecord).where(RawRecord.external_id == qid).order_by(RawRecord.id))
    )


def test_first_land_stamps_marker_and_bare_qid(db, wikidata_source):
    result = land_models(
        db, client=FakeSweepClient([entity("Q100", "BMW M3", makers=f"{_ENTITY}Q26678")])
    )
    assert (result.fetched, result.inserted) == (1, 1)
    (row,) = rows_for(db, "Q100")
    assert row.payload["sweep"] == "models", "kind selection needs the stamped marker"
    assert row.payload["itemLabel"]["value"] == "BMW M3"


def test_batches_are_split_and_each_commits(db, wikidata_source):
    client = FakeSweepClient([entity("Q1", "A"), entity("Q2", "B"), entity("Q3", "C")])
    result = land_models(db, client=client, batch_size=2)
    assert client.detail_queries == 2, "3 entities at batch_size=2 is two detail requests"
    assert (result.fetched, result.inserted) == (3, 3)


def test_reland_unchanged_is_noop(db, wikidata_source):
    bindings = [entity("Q100", "BMW M3", makers=f"{_ENTITY}Q26678")]
    land_models(db, client=FakeSweepClient(bindings))
    result = land_models(db, client=FakeSweepClient(bindings))
    assert (result.inserted, result.unchanged) == (0, 1)
    assert len(rows_for(db, "Q100")) == 1


def test_rotated_multi_value_cell_is_unchanged(db, wikidata_source):
    """Canonicalization must use the MODELS query's alias set: `makers` is not
    a makes-query variable, so the default set would let a rotated cell hash
    as a spurious change."""
    land_models(
        db, client=FakeSweepClient([entity("Q1", "Alpina B3", makers="wd:Q26678|wd:Q692895")])
    )
    result = land_models(
        db, client=FakeSweepClient([entity("Q1", "Alpina B3", makers="wd:Q692895|wd:Q26678")])
    )
    assert result.inserted == 0
    assert len(rows_for(db, "Q1")) == 1


def test_same_qid_from_both_sweeps_coexists(db, wikidata_source):
    """One QID landed by the makes sweep AND the models sweep keeps two raw
    rows - different payload shapes, one marked. Each pass's current-record
    selection partitions on the marker; losing either row would shadow the
    other sweep's view of the entity."""
    from tests.test_landing import FakeSparqlClient, binding

    land_makes(db, client=FakeSparqlClient([binding("Q466066", "BMW 3 Series")]))
    land_models(db, client=FakeSweepClient([entity("Q466066", "BMW 3 Series")]))

    rows = rows_for(db, "Q466066")
    assert len(rows) == 2
    markers = {row.payload.get("sweep") for row in rows}
    assert markers == {None, "models"}
