"""Seed one real vehicle end to end, to make the schema tangible.

Threads a single car - a 2002 BMW 330i (E46, US market) - through all five
levels of the hierarchy, then attaches an engine, a transmission, and two EAV
attributes. The point is to see, in one place, how a real vehicle decomposes:

    makes        BMW
      models       3 Series
        generations  E46            <- chassis codes live here
          model_years  2002
            configurations  330i (US, sedan, RWD)   <- specs live here
                              |- engines         M54B30
                              |- transmissions   Getrag 220 (5MT)
                              `- attributes      EAV long-tail

Also seeds the lookup/dimension tables, since nothing above can be inserted
without them.

Idempotent: re-running updates nothing and inserts nothing new. Run with

    .venv/bin/python scripts/seed_demo.py
"""

from __future__ import annotations

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
    FuelType,
    Generation,
    Make,
    MarketRegion,
    Model,
    ModelYear,
    Source,
    Transmission,
    TransmissionType,
)
from carmanac.db.session import SessionLocal

# Every fact this script writes is attributed to Wikidata at high confidence.
# Real ingestion will vary confidence by source tier and field.
SCRAPED_AT = datetime(2026, 7, 22, tzinfo=UTC)
CONFIDENCE = Decimal("0.95")

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

# Long-tail specs that fail the >=80%-fill bar for a column, so they are EAV.
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


def provenance(source: Source) -> dict[str, Any]:
    """The provenance triple required on every fact-bearing row."""
    return {
        "source_id": source.id,
        "scraped_at": SCRAPED_AT,
        "confidence_score": CONFIDENCE,
    }


def main() -> None:
    created_count = 0

    with SessionLocal() as session:
        # --- reference data ------------------------------------------------
        for model_cls, rows in LOOKUPS.items():
            for row in rows:
                _, created = get_or_create(
                    session, model_cls, defaults={"name": row["name"]}, code=row["code"]
                )
                created_count += created

        source, created = get_or_create(
            session,
            Source,
            defaults={
                "tier": 1,
                "base_url": "https://www.wikidata.org",
                "description": "Structured open knowledge base; universal QID join key.",
            },
            name="Wikidata",
        )
        created_count += created

        for attr in ATTRIBUTES:
            # `key` is the natural-key filter, so it must not also appear in
            # defaults - it would arrive twice at the constructor.
            _, created = get_or_create(
                session,
                AttributeDefinition,
                defaults={k: v for k, v in attr.items() if k != "key"},
                key=attr["key"],
            )
            created_count += created

        prov = provenance(source)
        rwd = session.scalar(select(Drivetrain).filter_by(code="rwd"))
        sedan = session.scalar(select(BodyStyle).filter_by(code="sedan"))
        gasoline = session.scalar(select(FuelType).filter_by(code="gasoline"))
        manual = session.scalar(select(TransmissionType).filter_by(code="manual"))
        us = session.scalar(select(MarketRegion).filter_by(code="US"))

        # --- the five-level chain ------------------------------------------
        make, created = get_or_create(
            session,
            Make,
            defaults={
                "name": "BMW",
                "wikidata_qid": "Q26678",
                "country_code": "DE",
                "founded_year": 1916,
                **prov,
            },
            slug="bmw",
        )
        created_count += created

        model, created = get_or_create(
            session,
            Model,
            defaults={"name": "3 Series", "wikidata_qid": "Q194352", **prov},
            make_id=make.id,
            slug="3-series",
        )
        created_count += created

        generation, created = get_or_create(
            session,
            Generation,
            defaults={
                "name": "E46",
                # One generation, several body-specific codes - exactly why
                # this is an array rather than a scalar column.
                "chassis_codes": ["E46"],
                "start_year": 1998,
                "end_year": 2006,
                "wikidata_qid": "Q1122106",
                **prov,
            },
            model_id=model.id,
            slug="e46",
        )
        created_count += created

        model_year, created = get_or_create(
            session, ModelYear, defaults={**prov}, generation_id=generation.id, year=2002
        )
        created_count += created

        configuration, created = get_or_create(
            session,
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
                **prov,
            },
            model_year_id=model_year.id,
            slug="330i-us-sedan",
        )
        created_count += created

        # --- powertrain as first-class entities ----------------------------
        engine, created = get_or_create(
            session,
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
                **prov,
            },
            slug="bmw-m54b30",
        )
        created_count += created

        transmission, created = get_or_create(
            session,
            Transmission,
            defaults={
                "manufacturer_make_id": None,  # Getrag is not itself a `makes` row
                "name": "Getrag 220",
                "transmission_type_id": manual.id,
                "gear_count": 5,
                **prov,
            },
            slug="getrag-220",
        )
        created_count += created

        _, created = get_or_create(
            session,
            ConfigurationEngine,
            defaults={**prov},
            configuration_id=configuration.id,
            engine_id=engine.id,
        )
        created_count += created

        _, created = get_or_create(
            session,
            ConfigurationTransmission,
            defaults={**prov},
            configuration_id=configuration.id,
            transmission_id=transmission.id,
        )
        created_count += created

        # --- EAV long-tail --------------------------------------------------
        valves = session.scalar(select(AttributeDefinition).filter_by(key="valves_per_cylinder"))
        compression = session.scalar(select(AttributeDefinition).filter_by(key="compression_ratio"))

        _, created = get_or_create(
            session,
            ConfigurationAttribute,
            defaults={"value_integer": 4, **prov},
            configuration_id=configuration.id,
            attribute_id=valves.id,
        )
        created_count += created

        _, created = get_or_create(
            session,
            ConfigurationAttribute,
            defaults={"value_numeric": Decimal("10.2"), **prov},
            configuration_id=configuration.id,
            attribute_id=compression.id,
        )
        created_count += created

        session.commit()

    print(f"Seed complete. {created_count} new row(s) inserted.")


if __name__ == "__main__":
    main()
