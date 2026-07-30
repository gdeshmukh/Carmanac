"""vPIC model-year landing tests: per-model year merge, the passenger
filter, the modelyears: namespace, and idempotency. Fake client, real
landing path, real constraints - the models-landing suite's approach."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from carmanac.db.models import RawRecord, Source
from carmanac.ingest.landing import content_hash
from carmanac.ingest.vpic.years import land_model_years

pytestmark = pytest.mark.integration


class FakeVpicClient:
    base_url = "https://vpic.test/api/vehicles"

    def __init__(self, per_path: dict[str, list[dict]]):
        self.per_path = per_path
        self.calls: list[str] = []

    def get_results(self, path: str, **params: str) -> list[dict]:
        self.calls.append(path)
        return self.per_path.get(path, [])

    def close(self) -> None:
        pass


@pytest.fixture()
def vpic_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == "NHTSA vPIC"))
    if source is None:
        source = Source(name="NHTSA vPIC", tier=1, base_url="https://vpic.nhtsa.dot.gov")
        db.add(source)
        db.commit()
    return source


def _land_make(db, source, make_id: int, name: str) -> None:
    payload = {"make_id": make_id, "make_name": name, "vehicle_types": ["Passenger Car"]}
    db.add(
        RawRecord(
            source_id=source.id,
            external_id=f"make:{make_id}",
            content_hash=content_hash(payload),
            payload=payload,
        )
    )
    db.commit()


def _land_model(db, source, model_id: int, name: str, make_id: int, make_name: str) -> None:
    payload = {
        "model_id": model_id,
        "model_name": name,
        "make_id": make_id,
        "make_name": make_name,
        "vehicle_types": ["Passenger Car"],
    }
    db.add(
        RawRecord(
            source_id=source.id,
            external_id=f"model:{model_id}",
            content_hash=content_hash(payload),
            payload=payload,
        )
    )
    db.commit()


def _year_row(make_id: int, make: str, model_id: int, model: str) -> dict:
    return {"Make_ID": make_id, "Make_Name": make, "Model_ID": model_id, "Model_Name": model}


def _path(make_id: int, year: int) -> str:
    return f"GetModelsForMakeIdYear/makeId/{make_id}/modelyear/{year}"


def test_merges_years_per_model_and_skips_non_passenger(db, vpic_source):
    """Two years of Accord merge into one sorted-years record; the
    motorcycle row (never landed as a passenger model) is skipped."""
    _land_make(db, vpic_source, 474, "HONDA")
    _land_model(db, vpic_source, 1861, "Accord", 474, "HONDA")
    client = FakeVpicClient(
        {
            _path(474, 2002): [
                _year_row(474, "HONDA", 1861, "Accord"),
                _year_row(474, "HONDA", 9001, "Gold Wing"),  # moto: not landed
            ],
            _path(474, 2001): [_year_row(474, "HONDA", 1861, "Accord")],
        }
    )
    result = land_model_years(db, client, start_year=2001, end_year=2002)
    assert (result.fetched, result.inserted) == (1, 1)

    record = db.scalars(select(RawRecord).where(RawRecord.external_id == "modelyears:1861")).one()
    assert record.payload["years"] == [2001, 2002]
    assert record.payload["model_name"] == "Accord"
    assert db.scalar(select(RawRecord).where(RawRecord.external_id == "modelyears:9001")) is None


def test_covers_every_landed_make_and_year(db, vpic_source):
    _land_make(db, vpic_source, 474, "HONDA")
    _land_make(db, vpic_source, 452, "BMW")
    client = FakeVpicClient({})
    land_model_years(db, client, start_year=2001, end_year=2003)
    assert client.calls == [
        _path(452, 2001),
        _path(452, 2002),
        _path(452, 2003),
        _path(474, 2001),
        _path(474, 2002),
        _path(474, 2003),
    ]


def test_reland_unchanged_is_noop_and_new_year_lands_as_history(db, vpic_source):
    _land_make(db, vpic_source, 474, "HONDA")
    _land_model(db, vpic_source, 1861, "Accord", 474, "HONDA")
    rows = {_path(474, 2002): [_year_row(474, "HONDA", 1861, "Accord")]}
    client = FakeVpicClient(rows)
    land_model_years(db, client, start_year=2002, end_year=2002)

    result = land_model_years(db, FakeVpicClient(rows), start_year=2002, end_year=2002)
    assert (result.fetched, result.inserted) == (1, 0)  # unchanged: no-op

    rows[_path(474, 2003)] = [_year_row(474, "HONDA", 1861, "Accord")]
    result = land_model_years(db, FakeVpicClient(rows), start_year=2002, end_year=2003)
    assert (result.fetched, result.inserted) == (1, 1)  # appended year: new version

    versions = db.scalars(
        select(RawRecord).where(RawRecord.external_id == "modelyears:1861").order_by(RawRecord.id)
    ).all()
    assert [v.payload["years"] for v in versions] == [[2002], [2002, 2003]]
