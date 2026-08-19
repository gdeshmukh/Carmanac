"""vPIC MODELS landing tests: per-model merge, the model: external-id
namespace, make selection by payload shape, and landing idempotency.

Same approach as the makes landing tests: a fake client, the real landing
path, the real constraints.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from carmanac.db.models import RawRecord, Source
from carmanac.ingest.landing import content_hash
from carmanac.ingest.vpic.models import land_passenger_models, landed_make_ids

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


def _land_make(db, source, make_id: int, name: str) -> RawRecord:
    payload = {"make_id": make_id, "make_name": name, "vehicle_types": ["Passenger Car"]}
    rec = RawRecord(
        source_id=source.id,
        external_id=f"make:{make_id}",
        content_hash=content_hash(payload),
        payload=payload,
    )
    db.add(rec)
    db.commit()
    return rec


def _model_row(make_id: int, make: str, model_id: int, model: str, type_name: str) -> dict:
    return {
        "Make_ID": make_id,
        "Make_Name": make,
        "Model_ID": model_id,
        "Model_Name": model,
        "VehicleTypeId": 0,
        "VehicleTypeName": type_name,
    }


def _paths(make_id: int) -> tuple[str, str, str]:
    return (
        f"GetModelsForMakeIdYear/makeId/{make_id}/vehicletype/car",
        f"GetModelsForMakeIdYear/makeId/{make_id}/vehicletype/multipurpose passenger vehicle (mpv)",
        f"GetModelsForMakeIdYear/makeId/{make_id}/vehicletype/truck",
    )


def test_merges_types_per_model_with_prefixed_id(db, vpic_source):
    """The Accord shape: one model under both vehicle types lands ONCE, with a
    sorted type list and a model:-prefixed external id (MakeId 474 and a
    hypothetical ModelId 474 must never collide in external_ids)."""
    _land_make(db, vpic_source, 474, "HONDA")
    car, mpv, _truck = _paths(474)
    client = FakeVpicClient(
        {
            car: [
                _model_row(474, "HONDA", 1861, "Accord", "Passenger Car"),
                _model_row(474, "HONDA", 1863, "Civic", "Passenger Car"),
            ],
            mpv: [
                _model_row(474, "HONDA", 1861, "Accord", "Multipurpose Passenger Vehicle (MPV)"),
                _model_row(474, "HONDA", 1864, "Pilot", "Multipurpose Passenger Vehicle (MPV)"),
            ],
        }
    )
    result = land_passenger_models(db, client)
    assert (result.fetched, result.inserted) == (3, 3)

    records = {
        r.external_id: r
        for r in db.scalars(select(RawRecord).where(RawRecord.external_id.like("model:%")))
    }
    assert set(records) == {"model:1861", "model:1863", "model:1864"}
    accord = records["model:1861"].payload
    assert accord["model_name"] == "Accord" and accord["make_id"] == 474
    assert accord["vehicle_types"] == [
        "Multipurpose Passenger Vehicle (MPV)",
        "Passenger Car",
    ]


def test_fetches_every_landed_make_not_just_matched(db, vpic_source):
    """Models under unmatched makes are the evidence the match queue waits on
    (what 'BBC' sells decides which BBC it is) - the fetch takes its make list
    from the LANDED records, by payload shape, ignoring the seed demo's
    synthetic vPIC record."""
    _land_make(db, vpic_source, 440, "ASTON MARTIN")
    _land_make(db, vpic_source, 8605, "BBC")
    db.add(  # the seed demo's configuration-level record: no make_id, skipped
        RawRecord(
            source_id=vpic_source.id,
            external_id="vpic-x",
            content_hash="deadbeef",
            payload={"id": "vpic-x", "body_style": "sedan"},
        )
    )
    db.commit()
    assert landed_make_ids(db) == [440, 8605]

    client = FakeVpicClient({})
    land_passenger_models(db, client)
    assert client.calls == [*_paths(440), *_paths(8605)]


def test_reland_is_idempotent(db, vpic_source):
    _land_make(db, vpic_source, 474, "HONDA")
    car, *_ = _paths(474)
    client = FakeVpicClient({car: [_model_row(474, "HONDA", 1861, "Accord", "Passenger Car")]})
    land_passenger_models(db, client)
    result = land_passenger_models(db, client)
    assert (result.inserted, result.unchanged) == (0, 1)
    assert (
        db.scalar(select(RawRecord.external_id).where(RawRecord.external_id.like("model:%")))
        == "model:1861"
    )
