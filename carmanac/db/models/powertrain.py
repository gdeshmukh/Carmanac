"""Engines and transmissions - first-class entities, plus their join tables.

These are not attributes of a configuration. Cross-make reuse makes that
modeling non-negotiable (CLAUDE.md): the BMW B58 appears in the Toyota Supra,
GM LS engines end up in everything, the ZF 8HP is fitted by half the industry.
`manufacturer_make_id` therefore points at the *engine's* maker, which
deliberately may differ from the car's make.

`engines` and `transmissions` are IDENTITY tables (ADR 0002) - no row-level
provenance. The join tables `configuration_engines` / `configuration_transmissions`
DO carry provenance, because the association itself is a scraped claim ("this
source says this car came with this engine") that sources contradict.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, SmallInteger, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from carmanac.db.base import Base, ProvenanceMixin, TimestampMixin, provenance_table_args


class Engine(Base, TimestampMixin):
    """An engine entity, e.g. 'B58', 'LS3', '2JZ-GTE'."""

    __tablename__ = "engines"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Maker of the engine - may differ from the car's company. This is the whole
    # point of engines being first-class.
    manufacturer_company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"), index=True
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    family_code: Mapped[str | None] = mapped_column(Text)
    displacement_cc: Mapped[int | None] = mapped_column(Integer)
    cylinders: Mapped[int | None] = mapped_column(SmallInteger)
    # Was `configuration`, which collided with the `configurations` entity - two
    # meanings of the most load-bearing word in the schema.
    cylinder_layout: Mapped[str | None] = mapped_column(Text)  # 'inline', 'v', 'flat'
    aspiration_id: Mapped[int | None] = mapped_column(ForeignKey("aspirations.id"), index=True)
    fuel_type_id: Mapped[int | None] = mapped_column(ForeignKey("fuel_types.id"), index=True)
    summary: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # Engines are searched by name and family code ("B58", "2JZ"), and
        # matched fuzzily from source text at ingest.
        Index(
            "idx_engines_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class Transmission(Base, TimestampMixin):
    """A transmission entity, e.g. 'ZF 8HP', 'Getrag 420G'."""

    __tablename__ = "transmissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    manufacturer_company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"), index=True
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    transmission_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("transmission_types.id"), index=True
    )
    gear_count: Mapped[int | None] = mapped_column(SmallInteger)
    summary: Mapped[str | None] = mapped_column(Text)


class ConfigurationEngine(Base, ProvenanceMixin):
    """One source's assertion that a configuration came with an engine.

    A PER-SOURCE ASSERTION STORE (ADR 0007 §8, F7), like every association
    fact table: "this source says this car came with this engine" is exactly
    the kind of claim sources contradict, so each source's claim is its own
    row with supersession. The car offers the engine if ANY live row says so.
    """

    __tablename__ = "configuration_engines"

    id: Mapped[int] = mapped_column(primary_key=True)
    configuration_id: Mapped[int] = mapped_column(
        ForeignKey("configurations.id"), nullable=False, index=True
    )
    engine_id: Mapped[int] = mapped_column(ForeignKey("engines.id"), nullable=False, index=True)
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("configuration_engines.id", name="fk_configuration_engines_superseded_by"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *provenance_table_args(),
        Index(
            "uq_configuration_engine_live",
            "configuration_id",
            "engine_id",
            "source_id",
            unique=True,
            postgresql_where=text("superseded_by IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
    )


class ConfigurationTransmission(Base, ProvenanceMixin):
    """Configuration <-> transmission, same per-source assertion shape as
    `ConfigurationEngine`."""

    __tablename__ = "configuration_transmissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    configuration_id: Mapped[int] = mapped_column(
        ForeignKey("configurations.id"), nullable=False, index=True
    )
    transmission_id: Mapped[int] = mapped_column(
        ForeignKey("transmissions.id"), nullable=False, index=True
    )
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey(
            "configuration_transmissions.id", name="fk_configuration_transmissions_superseded_by"
        ),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *provenance_table_args(),
        Index(
            "uq_configuration_transmission_live",
            "configuration_id",
            "transmission_id",
            "source_id",
            unique=True,
            postgresql_where=text("superseded_by IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
    )
