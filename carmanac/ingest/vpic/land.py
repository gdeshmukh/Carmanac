"""Land vPIC passenger-vehicle makes in `raw_scrape.raw_records`.

Cars-first at the source (charter): rather than GetAllMakes' 12,308 makes of
every vehicle type - trailers, ironworks, one-off custom shops - this asks
vPIC which makes have PASSENGER vehicles: `GetMakesForVehicleType/car` plus
`.../multipurpose passenger vehicle (mpv)` (SUVs and vans; measured
2026-07-29: 195 + 111 makes, 247 distinct). Landing generously-but-scoped and
admitting strictly stays the reconciler's polarity; this simply doesn't haul
in vehicle types the charter rules out of scope.

**One record per make.** `external_id` is `make:<MakeId>` — vPIC's stable key,
kind-prefixed because vPIC's MakeId and ModelId are separate integer
namespaces while `external_ids` is unique on `(source_id, external_id)`:
bare integers would let MakeId 440 and ModelId 440 collide (decided
2026-07-29, ahead of the models fetch). The payload is the make's name plus
the sorted list of passenger vehicle types it appears under.
Sorting is the same canonicalization lesson the Wikidata GROUP_CONCAT
instability taught (F4): the type list's order is an artifact of our fetch
sequence, and an unsorted payload would re-land forever as the artifact
shifted. Hash/idempotency/revert semantics live in `carmanac.ingest.landing`.
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
from carmanac.ingest.vpic.client import VpicClient

log = logging.getLogger(__name__)

VPIC_SOURCE_NAME = "NHTSA vPIC"

# The passenger scope, per the charter's out-of-scope list (no trucks >class 3,
# no buses, no motorcycles). vPIC's own vehicle-type names.
PASSENGER_VEHICLE_TYPES: tuple[str, ...] = (
    "car",
    "multipurpose passenger vehicle (mpv)",
)


def _merge_makes(
    per_type: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """One payload per MakeId with the sorted set of vehicle types it holds."""
    makes: dict[int, dict[str, Any]] = {}
    for results in per_type.values():
        for row in results:
            make_id = row["MakeId"]
            entry = makes.setdefault(
                make_id,
                {"make_id": make_id, "make_name": row["MakeName"], "vehicle_types": []},
            )
            type_name = row["VehicleTypeName"]
            if type_name not in entry["vehicle_types"]:
                entry["vehicle_types"].append(type_name)
    for entry in makes.values():
        entry["vehicle_types"].sort()
    return sorted(makes.values(), key=lambda e: e["make_id"])


def land_passenger_makes(session: Session, client: VpicClient | None = None) -> LandResult:
    """Fetch every vPIC passenger-vehicle make and land the raw records.

    Commits on success. Returns counts; nothing downstream of `raw_records`
    is touched - matching these makes to companies is the reconciler's job,
    behind its own ADR.
    """
    source = get_source(session, VPIC_SOURCE_NAME)
    owns_client = client is None
    client = client or VpicClient()

    try:
        per_type = {
            vt: client.get_results(f"GetMakesForVehicleType/{vt}") for vt in PASSENGER_VEHICLE_TYPES
        }
    finally:
        if owns_client:
            client.close()

    merged = _merge_makes(per_type)
    fetched = sum(len(r) for r in per_type.values())
    log.info("Fetched %d make/type rows -> %d distinct makes", fetched, len(merged))

    rows = [
        {
            "source_id": source.id,
            "url": client.base_url,
            "external_id": f"make:{payload['make_id']}",
            "http_status": 200,
            "content_hash": content_hash(payload),
            "payload": payload,
        }
        for payload in merged
    ]
    inserted = upsert_raw_records(session, rows)

    session.commit()
    result = LandResult(fetched=len(merged), inserted=inserted)
    log.info("Landed %d new raw record(s); %d unchanged", result.inserted, result.unchanged)
    return result
