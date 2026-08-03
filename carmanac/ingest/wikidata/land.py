"""Land Wikidata SPARQL results in `raw_scrape.raw_records`.

Fetch and store only - no companies, no entity resolution, no
`field_provenance`. That separation is what lets an improved matcher re-run
over records already on disk instead of re-scraping.

One raw record per entity, keyed by QID. The SPARQL binding is stored as-is,
keeping `{"type": ..., "datatype": ..., "value": ...}` rather than flattening
to plain strings: the datatype and language tags are part of what the source
said, and flattening is a transformation that belongs downstream.

Idempotency-by-hash and the `last_seen_at` revert semantics are shared across
sources, in `carmanac.ingest.landing`.
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

# GROUP_CONCAT order follows the query plan, not a promise, so the same
# content can hash differently between runs and re-land as a spurious change.
# Sorting loses nothing - Wikidata holds an unordered set, and the delimited
# string is an artifact of our own aggregation.
_MULTI_VALUE_SEPARATOR = "|"


def qid_from_uri(uri: str) -> str:
    """`http://www.wikidata.org/entity/Q246` -> `Q246`.

    The QID is the join key (charter), so it is stored bare in `external_id`
    rather than as a full URI - `external_ids` rows written later by the
    reconciler must match on the same literal string.
    """
    return uri.removeprefix(_ENTITY_PREFIX)


def canonicalize(
    binding: dict[str, Any], multi_vars: frozenset[str] = MULTI_VALUE_VARS
) -> dict[str, Any]:
    """Sort the GROUP_CONCAT lists in a binding so equal content compares equal.

    Applied to the payload itself, not just the hash input: a stored record
    that reorders between runs looks changed to a human reading it too, and the
    two would not diff cleanly.

    `multi_vars` defaults to the makes sweep's aliases; the models sweep passes
    its own.
    """
    canonical: dict[str, Any] = {}
    for var, cell in binding.items():
        value = cell.get("value")
        if var in multi_vars and isinstance(value, str):
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
