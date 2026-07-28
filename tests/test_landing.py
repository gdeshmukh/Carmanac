"""Landing-path integration tests, through the REAL land_makes code.

The SPARQL client is faked (canned bindings, zero network); everything from
canonicalization through the ON CONFLICT upsert runs for real against the
migrated test database. This is where the properties PROGRESS.md used to
record as one-shot prose verifications become permanent:

- re-landing an unchanged payload inserts nothing and bumps last_seen_at
- a changed payload lands a NEW row alongside the old (history, not overwrite)
- an A-B-A revert re-touches the original row, so max(last_seen_at) is the
  revert - the foundation review F3 scenario, end to end
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from sqlalchemy import select

from carmanac.db.models import RawRecord
from carmanac.ingest.wikidata.land import LandResult, get_source, land_makes

pytestmark = pytest.mark.integration

_ENTITY = "http://www.wikidata.org/entity/"


class FakeSparqlClient:
    """Stands in for SparqlClient: same .endpoint/.query()/.close() surface,
    returns canned bindings instead of touching the network."""

    def __init__(self, bindings: list[dict[str, Any]]) -> None:
        self.bindings = bindings
        self.endpoint = "fake://sparql"

    def query(self, sparql: str) -> dict[str, Any]:
        return {"results": {"bindings": self.bindings}}

    def close(self) -> None:  # pragma: no cover - land_makes only closes owned clients
        raise AssertionError("land_makes must not close a client it was handed")


def binding(qid: str, label: str, countries: str = "Germany", **extra: str) -> dict[str, Any]:
    b: dict[str, Any] = {
        "item": {"type": "uri", "value": f"{_ENTITY}{qid}"},
        "itemLabel": {"type": "literal", "value": label},
        "countries": {"type": "literal", "value": countries},
    }
    for var, value in extra.items():
        b[var] = {"type": "literal", "value": value}
    return b


def land(db, bindings: list[dict[str, Any]]) -> LandResult:
    return land_makes(db, client=FakeSparqlClient(bindings))


def rows_for(db, qid: str) -> list[RawRecord]:
    return list(
        db.scalars(select(RawRecord).where(RawRecord.external_id == qid).order_by(RawRecord.id))
    )


def test_first_land_inserts_everything(db, wikidata_source):
    result = land(db, [binding("Q1", "BMW"), binding("Q2", "Pontiac", countries="")])
    assert (result.fetched, result.inserted, result.unchanged) == (2, 2, 0)
    assert len(rows_for(db, "Q1")) == 1
    assert rows_for(db, "Q1")[0].payload["itemLabel"]["value"] == "BMW"


def test_reland_unchanged_inserts_nothing_and_bumps_last_seen(db, wikidata_source):
    """The idempotency claim, plus F3's re-observation semantics - together,
    because 'inserted 0' alone is exactly the check that failed to catch the
    SAMPLE bug: it says nothing about whether the row was re-observed."""
    land(db, [binding("Q1", "BMW")])
    first = rows_for(db, "Q1")[0]
    first_fetched, first_seen = first.fetched_at, first.last_seen_at

    time.sleep(0.01)
    result = land(db, [binding("Q1", "BMW")])

    assert (result.inserted, result.unchanged) == (0, 1)
    rows = rows_for(db, "Q1")
    assert len(rows) == 1, "unchanged payload must not duplicate"
    db.refresh(rows[0])
    assert rows[0].fetched_at == first_fetched, "first-observation timestamp is immutable"
    assert rows[0].last_seen_at > first_seen, "re-observation must bump last_seen_at"


def test_changed_payload_lands_new_row_alongside_old(db, wikidata_source):
    land(db, [binding("Q1", "BMW")])
    land(db, [binding("Q1", "BMW AG")])
    rows = rows_for(db, "Q1")
    assert len(rows) == 2, "history is the change log, never overwritten"
    assert {r.payload["itemLabel"]["value"] for r in rows} == {"BMW", "BMW AG"}


def test_aba_revert_retouches_original_row(db, wikidata_source):
    """Foundation review F3, end to end. A source going A -> B -> A must leave
    exactly two rows, with 'the current record' - max(last_seen_at) - being
    the original A row again. Before the fix, the revert was silently dropped
    and B stayed newest by every visible ordering."""
    a = binding("Q1", "BMW", countries="Germany")
    b = binding("Q1", "BMW", countries="Germany|Austria")

    land(db, [a])
    land(db, [b])
    time.sleep(0.01)
    result = land(db, [a])  # the revert

    assert result.inserted == 0, "reverted payload matches its old hash"
    rows = rows_for(db, "Q1")
    assert len(rows) == 2, "revert must not create a third row"
    for row in rows:
        db.refresh(row)
    current = max(rows, key=lambda r: r.last_seen_at)
    assert current.payload["countries"]["value"] == "Germany", (
        "after the revert, the ORIGINAL payload must be the current record"
    )


def test_rotated_multi_value_payload_is_unchanged(db, wikidata_source):
    """The GROUP_CONCAT regression through the whole pipeline: a re-fetch that
    returns the same countries in a different order is the same payload."""
    land(db, [binding("Q1", "KINTO", countries="Austria|Germany|France")])
    result = land(db, [binding("Q1", "KINTO", countries="France|Austria|Germany")])
    assert result.inserted == 0
    assert len(rows_for(db, "Q1")) == 1


def test_in_batch_duplicate_binding_lands_once(db, wikidata_source):
    """A single SPARQL response can repeat a row; the in-batch dedup must keep
    ON CONFLICT from arbitrating two identical rows in one INSERT (which
    Postgres rejects outright)."""
    dup = binding("Q1", "BMW")
    result = land(db, [dup, dup])
    assert result.fetched == 2
    assert result.inserted == 1
    assert len(rows_for(db, "Q1")) == 1


def test_binding_without_item_is_skipped(db, wikidata_source):
    result = land(db, [{"itemLabel": {"value": "orphan"}}, binding("Q1", "BMW")])
    assert result.inserted == 1


def test_missing_source_row_fails_loudly(db):
    """get_source refuses to invent a sources row - a typo'd name must not
    silently create a second, untiered source for facts to hang off."""
    with pytest.raises(LookupError):
        get_source(db, "Wikdata")
