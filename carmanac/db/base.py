"""SQLAlchemy declarative base and the mixins shared across the schema.

Provenance attaches to facts, not identity (ADR 0002), and the mixins split
along that line: `TimestampMixin` on every table, `ProvenanceMixin` only on
fact-bearing rows (the EAV table and the association tables).

`field_provenance` (models/provenance.py) covers the entity tables' columns.
It declares its columns explicitly instead of using `ProvenanceMixin` because
its `source_id` is NOT NULL.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, MetaData, Numeric, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from sqlalchemy.types import TIMESTAMP

# Without an explicit convention, Alembic autogenerate emits server-assigned
# constraint names that differ between environments, which makes migration
# diffs unstable and hard to review.
NAMING_CONVENTION = {
    "ix": "idx_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """When OUR row was written - distinct from `scraped_at`, which is when the
    source was read.

    `onupdate` fires on ORM updates only. Bulk ingestion (COPY, INSERT ... ON
    CONFLICT) bypasses the ORM, so the rule also lives in the database as a
    BEFORE UPDATE trigger (migration `a1c4e7b93f20`) - that is what actually
    holds during ingestion. The trigger skips no-op updates, so an idempotent
    re-scrape that changes nothing does not bump the timestamp.
    """

    @declared_attr
    @classmethod
    def created_at(cls) -> Mapped[datetime]:
        return mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    @declared_attr
    @classmethod
    def updated_at(cls) -> Mapped[datetime]:
        return mapped_column(
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        )


class ProvenanceMixin:
    """`source_id` / `scraped_at` / `confidence_score` / `raw_record_id` for
    fact-bearing rows.

    Each column is built inside `declared_attr` so every subclass gets its own
    Column and CheckConstraint objects rather than sharing one instance.
    """

    @declared_attr
    @classmethod
    def source_id(cls) -> Mapped[int | None]:
        # "Show me everything this source told us" is a core review query.
        return mapped_column(ForeignKey("sources.id"), index=True)

    @declared_attr
    @classmethod
    def raw_record_id(cls) -> Mapped[int | None]:
        # Back to the exact scrape (ADR 0003), so facts can be re-derived
        # when matching logic improves.
        from sqlalchemy import BigInteger

        return mapped_column(BigInteger, ForeignKey("raw_scrape.raw_records.id"), index=True)

    @declared_attr
    @classmethod
    def scraped_at(cls) -> Mapped[datetime | None]:
        return mapped_column(TIMESTAMP(timezone=True))

    @declared_attr
    @classmethod
    def confidence_score(cls) -> Mapped[Decimal | None]:
        # The 0-1 range CHECK is table-level, in provenance_table_args().
        return mapped_column(Numeric(3, 2))


def provenance_table_args() -> tuple[CheckConstraint, ...]:
    """Table-level constraints that must accompany `ProvenanceMixin`.

    Table-level rather than inline on the column: Alembic autogenerate only
    inspects `Table.constraints`, so an inline column CheckConstraint renders
    fine under `metadata.create_all()` but is silently dropped from generated
    migrations and never reaches the real database.

        __table_args__ = (*provenance_table_args(), UniqueConstraint(...))
    """
    return (CheckConstraint("confidence_score BETWEEN 0 AND 1", name="confidence_score_range"),)
