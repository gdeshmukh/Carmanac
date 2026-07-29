"""Land Wikidata SPARQL results in `raw_scrape.raw_records`.

This is step one of two. It fetches and stores; it does **not** create
companies, resolve entities, or write `field_provenance`. That is the
reconciler's job, and keeping the two apart is deliberate:

    fetch + land   ->  raw_scrape.raw_records       (this module)
    reconcile      ->  companies / field_provenance (carmanac/reconcile/)

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

Idempotency-by-hash and the `last_seen_at` revert semantics live in
`carmanac.ingest.landing`, shared with every source.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from carmanac.ingest.landing import (
    LandResult,
    content_hash,
    get_source,
    upsert_raw_records,
)
from carmanac.ingest.wikidata.client import SparqlClient
from carmanac.ingest.wikidata.queries import MAKES_QUERY, MULTI_VALUE_VARS

__all__ = ["LandResult", "canonicalize", "content_hash", "get_source", "land_makes"]

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


def qid_from_uri(uri: str) -> str:
    """`http://www.wikidata.org/entity/Q246` -> `Q246`.

    The QID is the join key (charter), so it is stored bare in `external_id`
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
    source = get_source(session, WIKIDATA_SOURCE_NAME)
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
    inserted = upsert_raw_records(session, rows)

    session.commit()
    result = LandResult(fetched=len(bindings), inserted=inserted)
    log.info("Landed %d new raw record(s); %d unchanged", result.inserted, result.unchanged)
    return result
