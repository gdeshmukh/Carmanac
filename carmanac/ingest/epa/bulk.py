"""Land the EPA fueleconomy.gov bulk vehicle data in `raw_scrape.raw_records`.

One download, every EPA-rated vehicle since 1984: `vehicles.csv` is the
published bulk export behind fueleconomy.gov, one row per EPA vehicle id at
per-variant granularity - "C300 4MATIC", "330i", engine size, transmission,
mpg figures. Exactly the configuration-level feedstock the write passes
will consume once the generation ADR lands; until then this is landing
only (fetch-wide / write-narrow).

One raw record per CSV row, `vehicle:<id>` (the kind-prefix convention),
payload = the row as published, untransformed - blank strings and all.
EPA regenerates the file continuously; a changed row hashes differently
and lands beside its old version as history, per the landing contract.
"""

from __future__ import annotations

import csv
import io
import logging

from sqlalchemy.orm import Session

from carmanac.ingest.http import PoliteClient
from carmanac.ingest.landing import (
    LandResult,
    content_hash,
    get_source,
    upsert_raw_records,
)

log = logging.getLogger(__name__)

EPA_SOURCE_NAME = "EPA fueleconomy.gov"
VEHICLES_CSV_PATH = "/feg/epadata/vehicles.csv"


def land_vehicles(
    session: Session,
    csv_text: str | None = None,
    client: PoliteClient | None = None,
) -> LandResult:
    """Download (or accept) vehicles.csv and land one record per row.

    Commits on success. `csv_text` is injectable for tests; the real path
    downloads from the source's registered base URL.
    """
    source = get_source(session, EPA_SOURCE_NAME)
    url = f"{source.base_url.rstrip('/')}{VEHICLES_CSV_PATH}"

    if csv_text is None:
        owns_client = client is None
        client = client or PoliteClient()
        try:
            log.info("downloading %s", url)
            csv_text = client.request("GET", url).text
        finally:
            if owns_client:
                client.close()

    rows = list(csv.DictReader(io.StringIO(csv_text)))
    if rows and "id" not in rows[0]:
        raise ValueError(f"vehicles.csv has no `id` column (columns: {sorted(rows[0])[:10]}...)")
    log.info("parsed %d vehicle rows", len(rows))

    records = [
        {
            "source_id": source.id,
            "url": url,
            "external_id": f"vehicle:{row['id']}",
            "http_status": 200,
            "content_hash": content_hash(row),
            "payload": row,
        }
        for row in rows
    ]
    inserted = upsert_raw_records(session, records)
    session.commit()
    result = LandResult(fetched=len(rows), inserted=inserted)
    log.info("Landed %d new raw record(s); %d unchanged", result.inserted, result.unchanged)
    return result


if __name__ == "__main__":
    from carmanac.runner import run

    run(land_vehicles)
