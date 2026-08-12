"""Land vPIC MODEL-YEAR associations in `raw_scrape.raw_records`.

Which catalogue years each landed passenger nameplate was filed for - the
feedstock for `model_year` catalogue periods once the generation question
resolves (the writes stay behind that ADR; this is landing only, per the
fetch-wide / write-narrow doctrine).

The endpoint is `GetModelsForMakeIdYear/makeId/{id}/modelyear/{y}` - one
request per (make, year), no bulk variant - so the sweep is long by shape:
all landed makes x the year range at the polite request rate. It is a
one-time backfill; future top-ups only need the newest year per make.

Fetch decisions (probed live 2026-07-30 before building):

- **Untyped responses.** The vehicletype-filtered variant would double the
  request count, and passenger-ness is already established per ModelId by
  the models fetch. Rows whose ModelId is not a landed passenger model
  (motorcycles - BMW's "F 800 GS" - plus anything filed since the models
  fetch) are skipped and counted, never silently dropped.
- **Year range 1981 -> current+1.** vPIC's VIN data starts at 1981 (1980
  returns zero rows) and has no upper bound - 2099 happily returns today's
  lineup, which is "no end date recorded", not a catalogue assertion. The
  bound is policy, applied at fetch time.
- **One record per model** (`modelyears:<ModelId>`), the models-fetch
  convention: the year rows merge into a sorted list, so an appended year
  changes the content hash and lands as a new version. A model with no
  1981+ rows lands nothing - vPIC is silent about it, and silence is not
  a record.
- **Commit per make.** Makes are independent; landing each one as it
  completes makes a crashed 3-hour sweep resumable by re-run (already-
  landed makes re-fetch but re-land as no-ops) and shows real progress.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
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
from carmanac.ingest.vpic.land import VPIC_SOURCE_NAME
from carmanac.ingest.vpic.models import landed_make_ids

log = logging.getLogger(__name__)

FIRST_VPIC_YEAR = 1981


def landed_passenger_model_ids(session: Session) -> set[int]:
    """ModelIds of every landed passenger model record, by kind prefix."""
    source = get_source(session, VPIC_SOURCE_NAME)
    return {
        record.payload["model_id"]
        for record in session.scalars(
            select(RawRecord).where(
                RawRecord.source_id == source.id,
                RawRecord.external_id.like("model:%"),
            )
        )
        if isinstance(record.payload, dict) and "model_id" in record.payload
    }


def _merge_years(rows: list[dict[str, Any]], year_by_row: list[int]) -> list[dict[str, Any]]:
    """One payload per ModelId with the sorted list of years it appeared in."""
    models: dict[int, dict[str, Any]] = {}
    for row, year in zip(rows, year_by_row, strict=True):
        entry = models.setdefault(
            row["Model_ID"],
            {
                "model_id": row["Model_ID"],
                "model_name": row["Model_Name"],
                "make_id": row["Make_ID"],
                "make_name": row["Make_Name"],
                "years": [],
            },
        )
        if year not in entry["years"]:
            entry["years"].append(year)
    for entry in models.values():
        entry["years"].sort()
    return sorted(models.values(), key=lambda e: e["model_id"])


def land_model_years(
    session: Session,
    client: VpicClient | None = None,
    start_year: int = FIRST_VPIC_YEAR,
    end_year: int | None = None,
) -> LandResult:
    """Fetch every landed make's model-year associations and land them.

    Commits per make. Landing only - turning these into catalogue periods
    is a reconciler pass behind the generation ADR.
    """
    source = get_source(session, VPIC_SOURCE_NAME)
    passenger_ids = landed_passenger_model_ids(session)
    make_ids = landed_make_ids(session)
    end_year = end_year or datetime.now(UTC).year + 1
    years = range(start_year, end_year + 1)
    owns_client = client is None
    client = client or VpicClient()

    log.info(
        "model-year sweep: %d makes x %d years (%d-%d) = %d requests",
        len(make_ids),
        len(years),
        start_year,
        end_year,
        len(make_ids) * len(years),
    )

    fetched = inserted = skipped_rows = models_with_years = 0
    try:
        for i, make_id in enumerate(make_ids, 1):
            rows: list[dict[str, Any]] = []
            year_by_row: list[int] = []
            for year in years:
                for row in client.get_results(
                    f"GetModelsForMakeIdYear/makeId/{make_id}/modelyear/{year}"
                ):
                    if row["Model_ID"] not in passenger_ids:
                        skipped_rows += 1
                        continue
                    rows.append(row)
                    year_by_row.append(year)

            merged = _merge_years(rows, year_by_row)
            records = [
                {
                    "source_id": source.id,
                    "url": client.base_url,
                    "external_id": f"modelyears:{payload['model_id']}",
                    "http_status": 200,
                    "content_hash": content_hash(payload),
                    "payload": payload,
                }
                for payload in merged
            ]
            inserted += upsert_raw_records(session, records)
            session.commit()
            fetched += len(merged)
            models_with_years += len(merged)
            if i % 10 == 0 or i == len(make_ids):
                log.info(
                    "  ... %d/%d makes (%d model-year records so far)", i, len(make_ids), fetched
                )
    finally:
        if owns_client:
            client.close()

    log.info(
        "Landed %d model-year record(s) (%d new); skipped %d non-passenger rows; "
        "%d of %d passenger models have 1981+ years",
        fetched,
        inserted,
        skipped_rows,
        models_with_years,
        len(passenger_ids),
    )
    return LandResult(fetched=fetched, inserted=inserted)


if __name__ == "__main__":
    from carmanac.runner import run

    run(land_model_years)
