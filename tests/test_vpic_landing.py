"""vPIC landing tests: payload canonicalization and landing idempotency.

Mirrors the Wikidata landing tests' approach: a fake client returns fixed
API results, the real landing path runs against the real database, and the
promises that matter - one record per make, sorted vehicle types (the F4
canonicalization lesson), hash-idempotent re-runs, `last_seen_at` bumps -
are checked against the real constraints.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from carmanac.db.models import RawRecord, Source
from carmanac.ingest.vpic.land import land_passenger_makes

pytestmark = pytest.mark.integration


class FakeVpicClient:
    """Returns canned per-endpoint results; records nothing, fetches nothing."""

    base_url = "https://vpic.test/api/vehicles"

    def __init__(self, per_path: dict[str, list[dict]]):
        self.per_path = per_path

    def get_results(self, path: str, **params: str) -> list[dict]:
        return self.per_path[path]

    def close(self) -> None:  # matches the real client's interface
        pass


def _client(car: list[dict], mpv: list[dict]) -> FakeVpicClient:
    return FakeVpicClient(
        {
            "GetMakesForVehicleType/car": car,
            "GetMakesForVehicleType/multipurpose passenger vehicle (mpv)": mpv,
        }
    )


@pytest.fixture()
def vpic_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == "NHTSA vPIC"))
    if source is None:
        source = Source(name="NHTSA vPIC", tier=1, base_url="https://vpic.nhtsa.dot.gov")
        db.add(source)
        db.commit()
    return source


def _row(make_id: int, name: str, type_name: str) -> dict:
    return {"MakeId": make_id, "MakeName": name, "VehicleTypeId": 0, "VehicleTypeName": type_name}


def test_merges_types_per_make_sorted(db, vpic_source):
    """A make in both endpoints lands ONCE, with a sorted type list - the
    fetch order of the endpoints must not leak into the payload (F4)."""
    client = _client(
        car=[_row(441, "TESLA", "Passenger Car")],
        mpv=[_row(441, "TESLA", "Multipurpose Passenger Vehicle (MPV)")],
    )
    result = land_passenger_makes(db, client)
    assert (result.fetched, result.inserted) == (1, 1)

    record = db.scalars(select(RawRecord)).one()
    assert record.external_id == "441"
    assert record.payload["vehicle_types"] == [
        "Multipurpose Passenger Vehicle (MPV)",
        "Passenger Car",
    ]


def test_reland_is_idempotent_and_bumps_last_seen(db, vpic_source):
    client = _client(car=[_row(440, "ASTON MARTIN", "Passenger Car")], mpv=[])
    land_passenger_makes(db, client)
    first = db.scalars(select(RawRecord)).one()
    seen_before = first.last_seen_at

    result = land_passenger_makes(db, client)
    assert result.inserted == 0
    db.expire_all()
    record = db.scalars(select(RawRecord)).one()  # still exactly one row
    assert record.last_seen_at > seen_before


def test_changed_payload_lands_alongside_history(db, vpic_source):
    """vPIC renaming a make is a new record next to the old one - raw history
    is the change log, never overwritten."""
    land_passenger_makes(db, _client(car=[_row(500, "DATSUN", "Passenger Car")], mpv=[]))
    land_passenger_makes(db, _client(car=[_row(500, "NISSAN", "Passenger Car")], mpv=[]))

    records = db.scalars(select(RawRecord).order_by(RawRecord.id)).all()
    assert [r.payload["make_name"] for r in records] == ["DATSUN", "NISSAN"]
    assert all(r.external_id == "500" for r in records)
