"""Land Wikidata SPARQL results in `raw_scrape.raw_records`.

This is step one of two. It fetches and stores; it does **not** create `makes`,
resolve entities, or write `field_provenance`. That is the reconciler's job, and
keeping the two apart is deliberate:

    fetch + land   ->  raw_scrape.raw_records   (this module)
    reconcile      ->  makes / field_provenance (next, per the reconciler ADR)

The split is what makes the architecture invariant "raw scrape data is never
discarded ... for re-reconciliation when matching logic improves" actually
usable. Because the raw payload is stored before anything interprets it, an
improved matcher can be re-run over records already on disk - no re-scraping,
no dependence on Wikidata looking the same as it did today.

**One record per entity.** A raw record is one manufacturer, keyed by its QID in
`external_id`, with that entity's full SPARQL binding as the JSONB payload. The
binding is stored as-is - keeping `{"type": "literal", "datatype": ...,
"value": ...}` rather than flattening to plain strings - because the datatype
and language tags are part of what the source said, and flattening is a
transformation that belongs downstream.

**Idempotency by hash.** `content_hash` is a SHA-256 over the canonical JSON of
the payload. Re-running an unchanged fetch inserts nothing - the unique
constraint on `(source_id, content_hash)` matches, and the only effect is a
`last_seen_at` bump recording that the source still asserts this exact payload.
A manufacturer whose data genuinely changed hashes differently and lands as a
new row *alongside* the old one - the history is the audit trail, not a problem
to be overwritten. The bump is what makes a reverted payload (A -> B -> A)
representable: the revert re-touches the original row, so "current record" =
max(last_seen_at), not max(fetched_at) (foundation review F3).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from carmanac.db.models import RawRecord, Source
from carmanac.ingest.wikidata.client import SparqlClient
from carmanac.ingest.wikidata.queries import MAKES_QUERY, MULTI_VALUE_VARS

log = logging.getLogger(__name__)

WIKIDATA_SOURCE_NAME = "Wikidata"
_ENTITY_PREFIX = "http://www.wikidata.org/entity/"

# SPARQL does not promise a stable order within a GROUP_CONCAT group - the
# order follows the query plan. Observed live: "KINTO Europe" (Q127773218) came
# back with the same 18 countries in a rotated order after the query was
# widened, hashing differently and re-landing as a spurious "change".
#
# Left alone, every multi-valued entity would re-land whenever the plan shifted
# (a query edit, server load, an index change), inflating the raw store and
# filling the change history with non-changes. Sorting loses nothing: the order
# was never meaningful - Wikidata holds an unordered set, and the delimited
# string is an artifact of our own aggregation.
#
# The variable set comes from queries.py, derived from the query text itself.
# It used to be a hand-maintained parallel list here, which is how the SAMPLE()
# nondeterminism (foundation review F4) slipped past: this file canonicalized
# what was listed, not what the query aggregated.
_MULTI_VALUE_SEPARATOR = "|"

# Postgres caps a statement at 65535 bind parameters. At ~7 columns per row a
# single 6.5k-row INSERT would sit uncomfortably close to that ceiling, so
# batches are chunked well below it.
_CHUNK_SIZE = 500


@dataclass(frozen=True)
class LandResult:
    """What one landing run did. `fetched - inserted` is the unchanged count."""

    fetched: int
    inserted: int

    @property
    def unchanged(self) -> int:
        return self.fetched - self.inserted


def qid_from_uri(uri: str) -> str:
    """`http://www.wikidata.org/entity/Q246` -> `Q246`.

    The QID is the join key (CLAUDE.md), so it is stored bare in `external_id`
    rather than as a full URI - `external_ids` rows written later by the
    reconciler must match on the same literal string.
    """
    return uri.removeprefix(_ENTITY_PREFIX)


def canonicalize(binding: dict[str, Any]) -> dict[str, Any]:
    """Sort the GROUP_CONCAT lists in a binding so equal content compares equal.

    Applied to the payload itself, not merely to the hash input: a stored record
    that reorders between runs would look changed to a human reading it too, and
    the two would not diff cleanly. Canonical on the way in is simpler than
    canonical at every read.
    """
    canonical: dict[str, Any] = {}
    for var, cell in binding.items():
        value = cell.get("value")
        if var in MULTI_VALUE_VARS and isinstance(value, str):
            parts = sorted(value.split(_MULTI_VALUE_SEPARATOR))
            cell = {**cell, "value": _MULTI_VALUE_SEPARATOR.join(parts)}
        canonical[var] = cell
    return canonical


def content_hash(payload: dict[str, Any]) -> str:
    """Stable SHA-256 of a payload.

    `sort_keys` and the compact separators make the hash depend on the *content*
    only - not on key ordering, which SPARQL does not promise to keep stable
    between runs. Without that, an unchanged entity could hash differently on
    every fetch and re-land forever.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_source(session: Session, name: str = WIKIDATA_SOURCE_NAME) -> Source:
    """Fetch the `sources` row by name.

    Deliberately does not create it: sources are reference data describing what
    a source *is* and at which tier it sits. An ingest silently inventing one
    would let a typo produce a second, untiered "Wikdata" that facts then hang
    off. Missing is an error worth failing on.
    """
    source = session.scalar(select(Source).where(Source.name == name))
    if source is None:
        raise LookupError(
            f"No `sources` row named {name!r}. Reference data must exist before "
            f"ingestion; seed it first."
        )
    return source


def _to_row(binding: dict[str, Any], source_id: int, endpoint: str) -> dict[str, Any] | None:
    """Turn one SPARQL binding into a `raw_records` row dict.

    Returns None for a binding with no `item`, which would be an entity we
    cannot key - not expected from our query, but skipping beats inserting an
    unidentifiable record into a permanent store.
    """
    item = binding.get("item", {}).get("value")
    if not item:
        return None

    payload = canonicalize(binding)
    return {
        "source_id": source_id,
        "url": endpoint,
        "external_id": qid_from_uri(item),
        "http_status": 200,
        "content_hash": content_hash(payload),
        "payload": payload,
    }


def land_makes(session: Session, client: SparqlClient | None = None) -> LandResult:
    """Fetch every Wikidata automobile manufacturer and land the raw records.

    Commits on success. Returns counts; nothing downstream of `raw_records` is
    touched.
    """
    source = get_source(session)
    owns_client = client is None
    client = client or SparqlClient()

    try:
        log.info("Querying %s for automobile manufacturers ...", client.endpoint)
        response = client.query(MAKES_QUERY)
    finally:
        if owns_client:
            client.close()

    bindings: list[dict[str, Any]] = response["results"]["bindings"]
    log.info("Fetched %d manufacturer rows", len(bindings))

    rows = [r for b in bindings if (r := _to_row(b, source.id, client.endpoint)) is not None]

    # Two entities can share a hash only if their payloads are byte-identical,
    # which for distinct QIDs cannot happen - but a single response *can* repeat
    # a row. Deduplicating in-batch keeps ON CONFLICT from having to arbitrate
    # a conflict between two rows of the same INSERT, which Postgres rejects
    # outright ("cannot affect row a second time").
    seen: set[str] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in rows:
        if row["content_hash"] not in seen:
            seen.add(row["content_hash"])
            unique_rows.append(row)

    inserted = 0
    for start in range(0, len(unique_rows), _CHUNK_SIZE):
        chunk = unique_rows[start : start + _CHUNK_SIZE]
        stmt = (
            pg_insert(RawRecord)
            .values(chunk)
            # DO UPDATE, not DO NOTHING (foundation review F3). An unchanged
            # payload must still bump `last_seen_at`: it is what makes "the
            # current record per (source, external_id)" answerable, and it is
            # the only thing that makes an A-B-A revert representable - the
            # reverted payload hashes identically to its old row, so without
            # the bump the newest-looking row would stay the intermediate B
            # forever. Still constraint-driven and race-safe under concurrent
            # ingests, unlike a read-then-write check.
            .on_conflict_do_update(
                index_elements=["source_id", "content_hash"],
                set_={"last_seen_at": func.now()},
            )
            # DO UPDATE returns every row (hit or new), so distinguish real
            # inserts via xmax: 0 means the row was created by this statement,
            # nonzero means it was updated. Postgres system-column arcana, but
            # the alternative is a second round trip per chunk.
            .returning(RawRecord.id, literal_column("(xmax = 0)").label("was_inserted"))
        )
        inserted += sum(1 for row in session.execute(stmt) if row.was_inserted)

    session.commit()
    result = LandResult(fetched=len(bindings), inserted=inserted)
    log.info("Landed %d new raw record(s); %d unchanged", result.inserted, result.unchanged)
    return result
