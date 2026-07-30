"""EPA bulk-CSV landing tests: the vehicle: namespace, untransformed row
payloads, the id-column contract, and idempotency."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from carmanac.db.models import RawRecord, Source
from carmanac.ingest.epa.bulk import land_vehicles

pytestmark = pytest.mark.integration

CSV = """id,make,model,year,displ,cylinders,trany,comb08
17681,BMW,330i,2002,3.0,6,Manual 5-spd,22
17682,Mercedes-Benz,C300 4MATIC,2015,2.0,4,Automatic 7-spd,24
"""


@pytest.fixture()
def epa_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == "EPA fueleconomy.gov"))
    if source is None:
        source = Source(name="EPA fueleconomy.gov", tier=1, base_url="https://www.fueleconomy.gov")
        db.add(source)
        db.commit()
    return source


def test_lands_one_prefixed_record_per_row(db, epa_source):
    result = land_vehicles(db, csv_text=CSV)
    assert (result.fetched, result.inserted) == (2, 2)

    records = {
        r.external_id: r
        for r in db.scalars(select(RawRecord).where(RawRecord.external_id.like("vehicle:%")))
    }
    assert set(records) == {"vehicle:17681", "vehicle:17682"}
    c300 = records["vehicle:17682"].payload
    # Untransformed: strings as published, per-variant granularity intact.
    assert c300["model"] == "C300 4MATIC"
    assert c300["displ"] == "2.0"
    assert records["vehicle:17681"].url.endswith("/feg/epadata/vehicles.csv")


def test_reland_is_idempotent_and_changed_row_lands_as_history(db, epa_source):
    land_vehicles(db, csv_text=CSV)
    result = land_vehicles(db, csv_text=CSV)
    assert (result.fetched, result.inserted) == (2, 0)

    changed = CSV.replace("Manual 5-spd", "Manual 6-spd")
    result = land_vehicles(db, csv_text=changed)
    assert result.inserted == 1  # only the changed row lands a new version
    versions = db.scalars(
        select(RawRecord).where(RawRecord.external_id == "vehicle:17681").order_by(RawRecord.id)
    ).all()
    assert [v.payload["trany"] for v in versions] == ["Manual 5-spd", "Manual 6-spd"]


def test_missing_id_column_fails_loudly(db, epa_source):
    with pytest.raises(ValueError, match="no `id` column"):
        land_vehicles(db, csv_text="make,model\nBMW,330i\n")
