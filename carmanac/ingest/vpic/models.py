"""Land vPIC passenger-vehicle MODELS in `raw_scrape.raw_records`.

The first model-level data in the project - the cars themselves, at nameplate
granularity. For every landed vPIC make, `GetModelsForMakeIdYear/makeId/{id}/
vehicletype/{car|mpv}` returns that make's models of that type; the union is
the make's passenger nameplates (same two-type scope as the makes fetch,
measured on Honda 2026-07-29: 14 car + 10 MPV -> 21 distinct, Accord in both).

**All 247 landed makes are fetched, not just the 134 matched ones.** Landing
is generous by design (raw is cheap, Tier 1 is a re-fetchable cache), and
models under UNMATCHED makes are evidence the match queue is waiting on -
what "BBC" sells decides which BBC it is. Reconciliation stays strict and
happens elsewhere, behind its own ADR.

**One record per model.** vPIC ModelIds are globally unique, so `external_id`
is `model:<ModelId>` - the `model:` prefix keeps MakeId 440 and ModelId 440
from colliding in `external_ids` (decided 2026-07-29, the re-key). The
payload carries the model's name (vPIC returns proper mixed case here -
"Accord", "FCX Clarity" - unlike the SHOUTING make names), its make's id and
name, and the sorted vehicle-type list (the F4 canonicalization lesson).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from carmanac.db.models import RawRecord
from carmanac.ingest.landing import (
    LandResult,
    content_hash,
    get_source,
    upsert_raw_records,
)
from carmanac.ingest.vpic.client import VpicClient
from carmanac.ingest.vpic.land import PASSENGER_VEHICLE_TYPES, VPIC_SOURCE_NAME

log = logging.getLogger(__name__)


def landed_make_ids(session: Session) -> list[int]:
    """MakeIds of every landed vPIC make record, ascending.

    Selected by the external id's KIND PREFIX, like every consumer of this
    source's mixed record kinds. Not by payload shape: model payloads carry
    `make_id` too, so a shape test reads them as makes as well (harmless here -
    their make ids are a subset - but the same defect that made the match pass
    mis-attach model ids to companies, so both now key on the prefix).
    """
    source = get_source(session, VPIC_SOURCE_NAME)
    make_ids = {
        record.payload["make_id"]
        for record in session.scalars(
            select(RawRecord).where(
                RawRecord.source_id == source.id,
                RawRecord.external_id.like("make:%"),
            )
        )
        if isinstance(record.payload, dict) and "make_id" in record.payload
    }
    return sorted(make_ids)


def _merge_models(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One payload per ModelId with the sorted set of vehicle types."""
    models: dict[int, dict[str, Any]] = {}
    for row in rows:
        model_id = row["Model_ID"]
        entry = models.setdefault(
            model_id,
            {
                "model_id": model_id,
                "model_name": row["Model_Name"],
                "make_id": row["Make_ID"],
                "make_name": row["Make_Name"],
                "vehicle_types": [],
            },
        )
        type_name = row["VehicleTypeName"]
        if type_name not in entry["vehicle_types"]:
            entry["vehicle_types"].append(type_name)
    for entry in models.values():
        entry["vehicle_types"].sort()
    return sorted(models.values(), key=lambda e: e["model_id"])


def land_passenger_models(session: Session, client: VpicClient | None = None) -> LandResult:
    """Fetch every landed make's passenger models and land the raw records.

    Commits on success. Landing only - turning these into `models` rows is
    the reconciler's job, behind its own ADR.
    """
    source = get_source(session, VPIC_SOURCE_NAME)
    make_ids = landed_make_ids(session)
    owns_client = client is None
    client = client or VpicClient()

    rows: list[dict[str, Any]] = []
    try:
        for i, make_id in enumerate(make_ids, 1):
            for vt in PASSENGER_VEHICLE_TYPES:
                rows.extend(
                    client.get_results(f"GetModelsForMakeIdYear/makeId/{make_id}/vehicletype/{vt}")
                )
            if i % 25 == 0:
                log.info("  ... %d/%d makes fetched", i, len(make_ids))
    finally:
        if owns_client:
            client.close()

    merged = _merge_models(rows)
    log.info(
        "Fetched %d model/type rows across %d makes -> %d distinct models",
        len(rows),
        len(make_ids),
        len(merged),
    )

    records = [
        {
            "source_id": source.id,
            "url": client.base_url,
            "external_id": f"model:{payload['model_id']}",
            "http_status": 200,
            "content_hash": content_hash(payload),
            "payload": payload,
        }
        for payload in merged
    ]
    inserted = upsert_raw_records(session, records)

    session.commit()
    result = LandResult(fetched=len(merged), inserted=inserted)
    log.info("Landed %d new raw record(s); %d unchanged", result.inserted, result.unchanged)
    return result


if __name__ == "__main__":
    from carmanac.runner import run

    run(land_passenger_models)
