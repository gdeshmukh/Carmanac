"""Lookup / dimension tables, plus `sources`.

These are modeled as tables rather than native Postgres ENUMs for two reasons
(see docs/schema.md): a new value - a new market, a new body style - can be
added with an INSERT instead of a migration, and each value can later carry its
own aliases or provenance when a source names things differently.

Each lookup has a stable `code` used in logic and slugs, and a human `name`.
These tables are reference data, not scraped facts, so they carry no provenance
columns.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, SmallInteger, Text, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.types import TIMESTAMP

from carmanac.db.base import Base


class _CodeNameLookup(Base):
    """Shared shape for the code/name dimension tables."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    @declared_attr
    @classmethod
    def code(cls) -> Mapped[str]:
        return mapped_column(Text, nullable=False, unique=True)


class MarketRegion(_CodeNameLookup):
    """e.g. 'US', 'EU', 'JDM', 'GLOBAL'."""

    __tablename__ = "market_regions"

    description: Mapped[str | None] = mapped_column(Text)


class BodyStyle(_CodeNameLookup):
    """e.g. 'sedan', 'wagon', 'coupe'."""

    __tablename__ = "body_styles"

    description: Mapped[str | None] = mapped_column(Text)


class Drivetrain(_CodeNameLookup):
    """'fwd', 'rwd', 'awd', '4wd'."""

    __tablename__ = "drivetrains"


class FuelType(_CodeNameLookup):
    """'gasoline', 'diesel', 'bev', 'phev', 'hev'."""

    __tablename__ = "fuel_types"


class TransmissionType(_CodeNameLookup):
    """'manual', 'automatic', 'dct', 'cvt'."""

    __tablename__ = "transmission_types"


class Source(Base):
    """Every data source. Referenced by every fact-bearing row.

    This table is why source URLs are never hard-coded in business logic
    (CLAUDE.md): a fact points at a `sources` row, and the row holds the URL and
    the tier. `tier` matches the 1-4 source tiering - conflicts between sources
    resolve by tier first, then recency.
    """

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Table-level, not inline on the column: Alembic autogenerate only
        # renders constraints it finds in Table.constraints.
        CheckConstraint("tier BETWEEN 1 AND 4", name="tier_range"),
    )
