"""Engines and transmissions - first-class entities, plus their join tables.

These are not attributes of a configuration. Cross-make reuse makes that
modeling non-negotiable (CLAUDE.md): the BMW B58 appears in the Toyota Supra,
GM LS engines end up in everything, the ZF 8HP is fitted by half the industry.
`manufacturer_make_id` therefore points at the *engine's* maker, which
deliberately may differ from the car's make.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from carmanac.db.base import (
    Base,
    ProvenanceMixin,
    SupersededByMixin,
    TimestampMixin,
    provenance_table_args,
)


class Engine(Base, ProvenanceMixin, SupersededByMixin, TimestampMixin):
    """An engine entity, e.g. 'B58', 'LS3', '2JZ-GTE'."""

    __tablename__ = "engines"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Maker of the engine - may differ from the car's make. This is the whole
    # point of engines being first-class.
    manufacturer_make_id: Mapped[int | None] = mapped_column(ForeignKey("makes.id"), index=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    family_code: Mapped[str | None] = mapped_column(Text)
    displacement_cc: Mapped[int | None] = mapped_column(Integer)
    cylinders: Mapped[int | None] = mapped_column(SmallInteger)
    configuration: Mapped[str | None] = mapped_column(Text)  # 'inline', 'v', 'flat'
    aspiration: Mapped[str | None] = mapped_column(Text)  # 'na', 'turbo', 'twin-turbo', ...
    fuel_type_id: Mapped[int | None] = mapped_column(ForeignKey("fuel_types.id"), index=True)
    wikidata_qid: Mapped[str | None] = mapped_column(Text, unique=True)

    __table_args__ = provenance_table_args()


class Transmission(Base, ProvenanceMixin, SupersededByMixin, TimestampMixin):
    """A transmission entity, e.g. 'ZF 8HP', 'Getrag 420G'."""

    __tablename__ = "transmissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    manufacturer_make_id: Mapped[int | None] = mapped_column(ForeignKey("makes.id"), index=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    transmission_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("transmission_types.id"), index=True
    )
    gear_count: Mapped[int | None] = mapped_column(SmallInteger)
    wikidata_qid: Mapped[str | None] = mapped_column(Text, unique=True)

    __table_args__ = provenance_table_args()


class ConfigurationEngine(Base, ProvenanceMixin):
    """Many-to-many: a configuration may offer several engines across trims.

    Carries provenance because the *association itself* is a scraped fact -
    "this source says this car came with this engine" is exactly the kind of
    claim that gets contradicted between sources.
    """

    __tablename__ = "configuration_engines"

    # Composite PK. `configuration_id` needs no separate index: it is the
    # leading column of the PK index, so lookups by configuration are covered.
    configuration_id: Mapped[int] = mapped_column(ForeignKey("configurations.id"), primary_key=True)
    engine_id: Mapped[int] = mapped_column(ForeignKey("engines.id"), primary_key=True, index=True)

    __table_args__ = provenance_table_args()


class ConfigurationTransmission(Base, ProvenanceMixin):
    """Many-to-many: configuration <-> transmission."""

    __tablename__ = "configuration_transmissions"

    configuration_id: Mapped[int] = mapped_column(ForeignKey("configurations.id"), primary_key=True)
    transmission_id: Mapped[int] = mapped_column(
        ForeignKey("transmissions.id"), primary_key=True, index=True
    )

    __table_args__ = provenance_table_args()
