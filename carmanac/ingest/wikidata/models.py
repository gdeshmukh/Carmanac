"""Land the Wikidata models sweep in `raw_scrape.raw_records` (ADR 0012 §1).

One maximal sweep of the three model-shaped classes (car model, automobile
model series, vehicle model - the fetch net, NOT a level taxonomy), landing
everything: fetch wide, write narrow. The enrichment pass decides level per
make later; ~9k entities under makers we don't hold simply wait in raw as the
tabled expansion's stocked warehouse.

Two request shapes (both probed live 2026-07-30, see queries.py): one
unordered QID-list query, then `VALUES`-batched detail queries with one
aggregated row per entity. **Commit per batch**, the model-years fetcher's
convention: batches are independent, so a crashed sweep resumes by re-run
(already-landed batches re-fetch but re-land as no-ops).

**Bare QIDs, a sweep marker.** `external_id` stays the bare QID - QIDs are
globally unique, so the vPIC-style kind prefix solves a collision that cannot
happen here. Kind *selection* cannot rely on payload shape (the vPIC lesson,
learned twice) or on classes (makes-sweep records legitimately carry model
classes - that is why they DENY there), so the landing stamps a
`"sweep": "models"` marker in the payload: OUR fetch metadata, deliberately
stamped, the same species as the vPIC `vehicle_types` merge. Passes partition
on it - the same QID can land from both sweeps with different payload shapes,
and each pass must see only its own sweep's current record.
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
from carmanac.ingest.wikidata.land import (
    WIKIDATA_SOURCE_NAME,
    canonicalize,
    qid_from_uri,
)
from carmanac.ingest.wikidata.queries import (
    MODELS_DETAIL_QUERY,
    MODELS_MULTI_VALUE_VARS,
    MODELS_QID_QUERY,
)

log = logging.getLogger(__name__)

SWEEP_MARKER = "models"

# 300 QIDs per detail request: probed at 2.1s against the endpoint's 60s
# timeout, so a slow day has a wide margin, and ~49 batches keep the whole
# sweep in minutes. The batch is also the commit unit.
BATCH_SIZE = 300


def fetch_qids(client: SparqlClient) -> list[str]:
    """The sweep population: every QID in the three model-shaped classes,
    sorted numerically client-side (ORDER BY ?item cost ~90s server-side)."""
    rows = client.query(MODELS_QID_QUERY)["results"]["bindings"]
    qids = {qid_from_uri(r["item"]["value"]) for r in rows}
    return sorted(qids, key=lambda q: int(q[1:]))


def _to_row(binding: dict[str, Any], source_id: int, endpoint: str) -> dict[str, Any] | None:
    """One SPARQL binding -> one `raw_records` row, marker stamped.

    The marker is added AFTER canonicalization: it is a plain string, not a
    binding cell, and canonicalize() only understands cells.
    """
    item = binding.get("item", {}).get("value")
    if not item:
        return None

    payload = canonicalize(binding, MODELS_MULTI_VALUE_VARS)
    payload["sweep"] = SWEEP_MARKER
    return {
        "source_id": source_id,
        "url": endpoint,
        "external_id": qid_from_uri(item),
        "http_status": 200,
        "content_hash": content_hash(payload),
        "payload": payload,
    }


def land_models(
    session: Session,
    client: SparqlClient | None = None,
    batch_size: int = BATCH_SIZE,
) -> LandResult:
    """Fetch every model-shaped Wikidata entity and land the raw records.

    Commits per batch. Landing only - lines, memberships and generations are
    the enrichment pass's job (carmanac/reconcile/wikidata_models_pass.py).
    """
    source = get_source(session, WIKIDATA_SOURCE_NAME)
    owns_client = client is None
    client = client or SparqlClient()

    fetched = inserted = 0
    try:
        log.info("Querying %s for the model-sweep population ...", client.endpoint)
        qids = fetch_qids(client)
        batches = [qids[i : i + batch_size] for i in range(0, len(qids), batch_size)]
        log.info("%d entities -> %d detail batches of <=%d", len(qids), len(batches), batch_size)

        for i, batch in enumerate(batches, 1):
            values = " ".join(f"wd:{qid}" for qid in batch)
            bindings = client.query(MODELS_DETAIL_QUERY.format(values=values))["results"][
                "bindings"
            ]
            rows = [
                r for b in bindings if (r := _to_row(b, source.id, client.endpoint)) is not None
            ]
            inserted += upsert_raw_records(session, rows)
            session.commit()
            fetched += len(rows)
            if i % 10 == 0 or i == len(batches):
                log.info("  ... batch %d/%d (%d entities landed)", i, len(batches), fetched)
    finally:
        if owns_client:
            client.close()

    result = LandResult(fetched=fetched, inserted=inserted)
    log.info("Landed %d new raw record(s); %d unchanged", result.inserted, result.unchanged)
    return result
