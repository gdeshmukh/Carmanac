"""The five-level entity hierarchy - the spine of the schema.

    makes -> models -> generations -> model_years -> configurations

These are IDENTITY tables (ADR 0002). They hold identity plus their descriptive
and spec columns as the *current best value*. They carry no row-level
provenance and no `superseded_by`: a make either exists or it does not, and
"which source told us BMW was founded in 1916" is a field-level fact recorded in
`field_provenance`, not a property of the identity row. Entities are upserted by
natural key. External identifiers (Wikidata QID, NHTSA id, ...) live in
`external_ids` (ADR 0003), not as columns here.

Every spec-bearing row still foreign-keys back toward `configurations`, because
a configuration (model year x trim x market x drivetrain) is the only level at
which a single spec value is unambiguous.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, Numeric, SmallInteger, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from carmanac.db.base import Base, TimestampMixin


class Make(Base, TimestampMixin):
    """Manufacturer / brand. Top-level entity, has its own page.

    Defunct marques (Pontiac, Saab, Plymouth) stay top-level and simply carry a
    `defunct_year`. Modeling corporate parents is deferred to its own ADR - see
    PROGRESS.md Open Questions.
    """

    __tablename__ = "makes"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    country_code: Mapped[str | None] = mapped_column(Text)  # ISO 3166-1 alpha-2 of HQ
    founded_year: Mapped[int | None] = mapped_column(SmallInteger)
    defunct_year: Mapped[int | None] = mapped_column(SmallInteger)  # null = still active

    models: Mapped[list[Model]] = relationship(back_populates="make")

    # Trigram index: entity resolution matches incoming source names against
    # these fuzzily ("BMW AG" -> "BMW"), so a plain btree is not enough.
    __table_args__ = (
        Index(
            "idx_makes_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class Model(Base, TimestampMixin):
    """A nameplate under a make, e.g. '3 Series', 'Corolla'."""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(primary_key=True)
    make_id: Mapped[int] = mapped_column(ForeignKey("makes.id"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    make: Mapped[Make] = relationship(back_populates="models")
    generations: Mapped[list[Generation]] = relationship(back_populates="model")

    __table_args__ = (
        # Slug is unique *within* a make, so two makes may both have a
        # '3-series' without colliding.
        UniqueConstraint("make_id", "slug", name="uq_models_make_id_slug"),
        Index(
            "idx_models_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class Generation(Base, TimestampMixin):
    """A generation of a model - E46, G80, XV70.

    This is the level enthusiasts actually search by, which is why it carries
    `chassis_codes`.
    """

    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    # One generation routinely spans several codes (E46 sedan/coupe/touring
    # carry different internal codes), hence an array rather than a scalar.
    chassis_codes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    start_year: Mapped[int | None] = mapped_column(SmallInteger)
    end_year: Mapped[int | None] = mapped_column(SmallInteger)  # null = still in production

    model: Mapped[Model] = relationship(back_populates="generations")
    model_years: Mapped[list[ModelYear]] = relationship(back_populates="generation")

    __table_args__ = (UniqueConstraint("model_id", "slug", name="uq_generations_model_id_slug"),)


class ModelYear(Base, TimestampMixin):
    """A single year inside a generation. Thin by design."""

    __tablename__ = "model_years"

    id: Mapped[int] = mapped_column(primary_key=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("generations.id"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    generation: Mapped[Generation] = relationship(back_populates="model_years")
    configurations: Mapped[list[Configuration]] = relationship(back_populates="model_year")

    __table_args__ = (
        UniqueConstraint("generation_id", "year", name="uq_model_years_generation_id_year"),
    )


class Configuration(Base, TimestampMixin):
    """The atomic unit: model_year x trim x market x drivetrain.

    Core spec columns live here as the current best value. A spec earns a column
    only if >=80% of configurations would plausibly have a value (CLAUDE.md);
    everything sparser goes to EAV via `configuration_attributes`. Which source
    each column's value came from is recorded per field in `field_provenance`
    (ADR 0002); the column itself is the reconciled winner.

    Units are metric and explicit in the column name (_cc, _kg, _mm, _nm, _km)
    to remove ambiguity at ingest time. mpg/mpge stay imperial because they are
    EPA-defined metrics, not raw measurements.
    """

    __tablename__ = "configurations"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_year_id: Mapped[int] = mapped_column(
        ForeignKey("model_years.id"), nullable=False, index=True
    )
    market_region_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_regions.id"), index=True
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)

    # --- identity / classification (mostly NHTSA vPIC) ---
    trim_name: Mapped[str | None] = mapped_column(Text)  # 'M340i', 'LE', 'GTI Autobahn'
    body_style_id: Mapped[int | None] = mapped_column(ForeignKey("body_styles.id"), index=True)
    drivetrain_id: Mapped[int | None] = mapped_column(ForeignKey("drivetrains.id"), index=True)
    doors: Mapped[int | None] = mapped_column(SmallInteger)
    seating_capacity: Mapped[int | None] = mapped_column(SmallInteger)

    # --- powertrain summary ---
    # Denormalized for fast list/compare queries. The authoritative powertrain
    # detail lives in engines/transmissions via the join tables; these columns
    # are reconciled against those, never treated as the primary record.
    fuel_type_id: Mapped[int | None] = mapped_column(ForeignKey("fuel_types.id"), index=True)
    engine_displacement_cc: Mapped[int | None] = mapped_column(Integer)
    cylinders: Mapped[int | None] = mapped_column(SmallInteger)
    transmission_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("transmission_types.id"), index=True
    )

    # --- performance / economy (mostly EPA fueleconomy.gov) ---
    power_hp: Mapped[int | None] = mapped_column(Integer)
    torque_nm: Mapped[int | None] = mapped_column(Integer)
    mpg_city: Mapped[float | None] = mapped_column(Numeric(5, 1))
    mpg_highway: Mapped[float | None] = mapped_column(Numeric(5, 1))
    mpg_combined: Mapped[float | None] = mapped_column(Numeric(5, 1))
    mpge_combined: Mapped[float | None] = mapped_column(Numeric(5, 1))  # electrified
    electric_range_km: Mapped[int | None] = mapped_column(Integer)  # BEV/PHEV

    # --- physical (mostly Wikidata) ---
    curb_weight_kg: Mapped[int | None] = mapped_column(Integer)
    length_mm: Mapped[int | None] = mapped_column(Integer)
    width_mm: Mapped[int | None] = mapped_column(Integer)
    height_mm: Mapped[int | None] = mapped_column(Integer)
    wheelbase_mm: Mapped[int | None] = mapped_column(Integer)

    # --- launch pricing (MSRP-at-launch only is in scope) ---
    msrp_launch_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    msrp_launch_currency: Mapped[str | None] = mapped_column(Text)  # ISO 4217

    model_year: Mapped[ModelYear] = relationship(back_populates="configurations")

    __table_args__ = (
        UniqueConstraint("model_year_id", "slug", name="uq_configurations_model_year_id_slug"),
    )
