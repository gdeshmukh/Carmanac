"""Seed one real vehicle end to end, demonstrating multi-source provenance.

Threads a 2002 BMW 330i (E46, US market) through all five levels of the
hierarchy, then shows the reconciliation-ready model from ADR 0002 / 0003:

    makes        BMW
      models       3 Series
        generations  E46            <- chassis codes live here
          model_years  2002
            configurations  330i (US, sedan, RWD)   <- spec columns = best value
                              |- engines         M54B30
                              |- transmissions   Getrag 220 (5MT)
                              `- attributes      EAV long-tail

Around that, the new infrastructure:

    raw_records      one simulated fetch per source (Wikidata, NHTSA, EPA)
    external_ids     Wikidata QIDs, NHTSA vehicle id  (no more qid columns)
    field_provenance which source asserted each spec field, linked to its scrape

The point: THREE sources contribute different fields to the one configuration,
and each field records who said so - the thing the old one-source-per-row model
could not express.

Idempotent. Run with:  .venv/bin/python scripts/seed_demo.py
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from carmanac.db.models import (
    AttributeDefinition,
    BodyStyle,
    Configuration,
    ConfigurationAttribute,
    ConfigurationEngine,
    ConfigurationTransmission,
    Drivetrain,
    Engine,
    ExternalId,
    FieldProvenance,
    FuelType,
    Generation,
    Make,
    MarketRegion,
    Model,
    ModelYear,
    RawRecord,
    Source,
    Transmission,
    TransmissionType,
)
from carmanac.db.session import SessionLocal

SCRAPED_AT = datetime(2026, 7, 22, tzinfo=UTC)

# Maps an entity instance's type to its exclusive-arc column in field_provenance
# and external_ids (see models/provenance.py).
_ARC_COL_FOR: dict[type, str] = {
    Make: "make_id",
    Model: "model_id",
    Generation: "generation_id",
    ModelYear: "model_year_id",
    Configuration: "configuration_id",
    Engine: "engine_id",
    Transmission: "transmission_id",
}

LOOKUPS: dict[type, list[dict[str, Any]]] = {
    MarketRegion: [
        {"code": "US", "name": "United States"},
        {"code": "EU", "name": "European Union"},
        {"code": "JDM", "name": "Japan (domestic)"},
        {"code": "GLOBAL", "name": "Global"},
    ],
    BodyStyle: [
        {"code": "sedan", "name": "Sedan"},
        {"code": "coupe", "name": "Coupe"},
        {"code": "wagon", "name": "Wagon"},
        {"code": "convertible", "name": "Convertible"},
        {"code": "hatchback", "name": "Hatchback"},
        {"code": "suv", "name": "SUV"},
    ],
    Drivetrain: [
        {"code": "fwd", "name": "Front-wheel drive"},
        {"code": "rwd", "name": "Rear-wheel drive"},
        {"code": "awd", "name": "All-wheel drive"},
        {"code": "4wd", "name": "Four-wheel drive"},
    ],
    FuelType: [
        {"code": "gasoline", "name": "Gasoline"},
        {"code": "diesel", "name": "Diesel"},
        {"code": "bev", "name": "Battery electric"},
        {"code": "phev", "name": "Plug-in hybrid"},
        {"code": "hev", "name": "Hybrid"},
    ],
    TransmissionType: [
        {"code": "manual", "name": "Manual"},
        {"code": "automatic", "name": "Automatic"},
        {"code": "dct", "name": "Dual-clutch"},
        {"code": "cvt", "name": "Continuously variable"},
    ],
}

SOURCES = [
    {"name": "Wikidata", "tier": 1, "base_url": "https://www.wikidata.org"},
    {"name": "NHTSA vPIC", "tier": 1, "base_url": "https://vpic.nhtsa.dot.gov"},
    {"name": "EPA fueleconomy.gov", "tier": 1, "base_url": "https://www.fueleconomy.gov"},
]

ATTRIBUTES = [
    {
        "key": "valves_per_cylinder",
        "label": "Valves per cylinder",
        "data_type": "integer",
        "description": "Intake + exhaust valves per cylinder.",
    },
    {
        "key": "compression_ratio",
        "label": "Compression ratio",
        "data_type": "numeric",
        "unit": ":1",
        "description": "Geometric compression ratio.",
    },
]


def get_or_create(session, model, defaults: dict | None = None, **filters):
    """Fetch by natural key, or insert. Returns (obj, created)."""
    obj = session.scalar(select(model).filter_by(**filters))
    if obj is not None:
        return obj, False
    obj = model(**filters, **(defaults or {}))
    session.add(obj)
    session.flush()  # assign the PK now, children need it
    return obj, True


def get_or_create_raw(session, source: Source, payload: dict) -> tuple[RawRecord, bool]:
    """One simulated fetch. Keyed on content_hash so re-runs don't duplicate."""
    blob = json.dumps(payload, sort_keys=True)
    content_hash = hashlib.sha256(blob.encode()).hexdigest()
    existing = session.scalar(select(RawRecord).filter_by(content_hash=content_hash))
    if existing is not None:
        return existing, False
    rec = RawRecord(
        source_id=source.id,
        url=f"{source.base_url}/demo",
        external_id=payload.get("id"),
        http_status=200,
        content_hash=content_hash,
        payload=payload,
        fetched_at=SCRAPED_AT,
    )
    session.add(rec)
    session.flush()
    return rec, True


def record_external_id(session, entity, source: Source, external_id: str) -> bool:
    """Map (source, external_id) -> entity, unless already mapped."""
    if session.scalar(select(ExternalId).filter_by(source_id=source.id, external_id=external_id)):
        return False
    session.add(
        ExternalId(
            **{_ARC_COL_FOR[type(entity)]: entity.id}, source_id=source.id, external_id=external_id
        )
    )
    return True


def record_field(session, entity, field_name, source, raw, confidence) -> bool:
    """Record that `source` asserted `entity.field_name`. observed_value is read
    from the entity's current column value (which is the reconciled winner)."""
    arc = _ARC_COL_FOR[type(entity)]
    if session.scalar(
        select(FieldProvenance).filter_by(
            **{arc: entity.id}, field_name=field_name, source_id=source.id, superseded_by=None
        )
    ):
        return False
    value = getattr(entity, field_name)
    session.add(
        FieldProvenance(
            **{arc: entity.id},
            field_name=field_name,
            observed_value=None if value is None else str(value),
            source_id=source.id,
            raw_record_id=raw.id if raw else None,
            confidence=Decimal(str(confidence)),
            scraped_at=SCRAPED_AT,
        )
    )
    return True


def main() -> None:
    created = 0

    with SessionLocal() as s:
        # --- reference data ------------------------------------------------
        for model_cls, rows in LOOKUPS.items():
            for row in rows:
                _, c = get_or_create(s, model_cls, defaults={"name": row["name"]}, code=row["code"])
                created += c

        sources: dict[str, Source] = {}
        for spec in SOURCES:
            src, c = get_or_create(
                s,
                Source,
                defaults={"tier": spec["tier"], "base_url": spec["base_url"]},
                name=spec["name"],
            )
            sources[spec["name"]] = src
            created += c
        wikidata, nhtsa, epa = (
            sources["Wikidata"],
            sources["NHTSA vPIC"],
            sources["EPA fueleconomy.gov"],
        )

        for attr in ATTRIBUTES:
            _, c = get_or_create(
                s,
                AttributeDefinition,
                defaults={k: v for k, v in attr.items() if k != "key"},
                key=attr["key"],
            )
            created += c

        # --- one raw record per source (the scrapes these facts came from) -
        raw_wd, c = get_or_create_raw(s, wikidata, {"id": "Q1122106", "label": "BMW 330i (E46)"})
        created += c
        raw_nhtsa, c = get_or_create_raw(
            s, nhtsa, {"id": "vpic-2002-bmw-330i", "BodyClass": "Sedan"}
        )
        created += c
        raw_epa, c = get_or_create_raw(s, epa, {"id": "epa-17681", "comb08": 22})
        created += c

        rwd = s.scalar(select(Drivetrain).filter_by(code="rwd"))
        sedan = s.scalar(select(BodyStyle).filter_by(code="sedan"))
        gasoline = s.scalar(select(FuelType).filter_by(code="gasoline"))
        manual = s.scalar(select(TransmissionType).filter_by(code="manual"))
        us = s.scalar(select(MarketRegion).filter_by(code="US"))

        # --- the five-level chain (identity only - no provenance columns) ---
        make, c = get_or_create(
            s,
            Make,
            defaults={"name": "BMW", "country_code": "DE", "founded_year": 1916},
            slug="bmw",
        )
        created += c
        model, c = get_or_create(
            s, Model, defaults={"name": "3 Series"}, make_id=make.id, slug="3-series"
        )
        created += c
        gen, c = get_or_create(
            s,
            Generation,
            defaults={
                "name": "E46",
                "chassis_codes": ["E46"],
                "start_year": 1998,
                "end_year": 2006,
            },
            model_id=model.id,
            slug="e46",
        )
        created += c
        my, c = get_or_create(s, ModelYear, generation_id=gen.id, year=2002)
        created += c
        cfg, c = get_or_create(
            s,
            Configuration,
            defaults={
                "market_region_id": us.id,
                "trim_name": "330i",
                "body_style_id": sedan.id,
                "drivetrain_id": rwd.id,
                "doors": 4,
                "seating_capacity": 5,
                "fuel_type_id": gasoline.id,
                "engine_displacement_cc": 2979,
                "cylinders": 6,
                "transmission_type_id": manual.id,
                "power_hp": 225,
                "torque_nm": 300,
                "mpg_city": Decimal("19.0"),
                "mpg_highway": Decimal("27.0"),
                "mpg_combined": Decimal("22.0"),
                "curb_weight_kg": 1520,
                "length_mm": 4471,
                "width_mm": 1739,
                "height_mm": 1415,
                "wheelbase_mm": 2725,
                "msrp_launch_amount": Decimal("35400.00"),
                "msrp_launch_currency": "USD",
            },
            model_year_id=my.id,
            slug="330i-us-sedan",
        )
        created += c

        # --- external ids (replaces the old wikidata_qid columns) ----------
        created += record_external_id(s, make, wikidata, "Q26678")
        created += record_external_id(s, model, wikidata, "Q194352")
        created += record_external_id(s, gen, wikidata, "Q1122106")
        created += record_external_id(s, cfg, nhtsa, "vpic-2002-bmw-330i")

        # --- field provenance: THREE sources, one configuration ------------
        # NHTSA supplied the classification/engine-basics fields...
        for field in ("doors", "seating_capacity", "cylinders", "engine_displacement_cc"):
            created += record_field(s, cfg, field, nhtsa, raw_nhtsa, 0.98)
        # ...EPA the economy fields...
        for field in ("mpg_city", "mpg_highway", "mpg_combined"):
            created += record_field(s, cfg, field, epa, raw_epa, 0.99)
        # ...Wikidata the physical + performance fields.
        for field in (
            "power_hp",
            "torque_nm",
            "curb_weight_kg",
            "length_mm",
            "width_mm",
            "height_mm",
            "wheelbase_mm",
        ):
            created += record_field(s, cfg, field, wikidata, raw_wd, 0.90)
        # And a couple of make-level facts from Wikidata.
        for field in ("founded_year", "country_code"):
            created += record_field(s, make, field, wikidata, raw_wd, 0.95)

        # --- powertrain entities (identity) + provenanced associations -----
        engine, c = get_or_create(
            s,
            Engine,
            defaults={
                "manufacturer_make_id": make.id,
                "name": "M54B30",
                "family_code": "M54",
                "displacement_cc": 2979,
                "cylinders": 6,
                "configuration": "inline",
                "aspiration": "na",
                "fuel_type_id": gasoline.id,
            },
            slug="bmw-m54b30",
        )
        created += c
        trans, c = get_or_create(
            s,
            Transmission,
            defaults={"name": "Getrag 220", "transmission_type_id": manual.id, "gear_count": 5},
            slug="getrag-220",
        )
        created += c
        created += record_external_id(s, engine, wikidata, "Q1477973")

        assoc_prov = {
            "source_id": wikidata.id,
            "scraped_at": SCRAPED_AT,
            "confidence_score": Decimal("0.95"),
            "raw_record_id": raw_wd.id,
        }
        _, c = get_or_create(
            s,
            ConfigurationEngine,
            defaults=dict(assoc_prov),
            configuration_id=cfg.id,
            engine_id=engine.id,
        )
        created += c
        _, c = get_or_create(
            s,
            ConfigurationTransmission,
            defaults=dict(assoc_prov),
            configuration_id=cfg.id,
            transmission_id=trans.id,
        )
        created += c

        # --- EAV long-tail (now also linked to its raw record) -------------
        valves = s.scalar(select(AttributeDefinition).filter_by(key="valves_per_cylinder"))
        compression = s.scalar(select(AttributeDefinition).filter_by(key="compression_ratio"))
        eav_prov = {
            "source_id": wikidata.id,
            "scraped_at": SCRAPED_AT,
            "confidence_score": Decimal("0.90"),
            "raw_record_id": raw_wd.id,
        }
        _, c = get_or_create(
            s,
            ConfigurationAttribute,
            defaults={"value_integer": 4, **eav_prov},
            configuration_id=cfg.id,
            attribute_id=valves.id,
        )
        created += c
        _, c = get_or_create(
            s,
            ConfigurationAttribute,
            defaults={"value_numeric": Decimal("10.2"), **eav_prov},
            configuration_id=cfg.id,
            attribute_id=compression.id,
        )
        created += c

        s.commit()

    print(f"Seed complete. {created} new row(s) inserted.")


if __name__ == "__main__":
    main()
