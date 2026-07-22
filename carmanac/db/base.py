"""SQLAlchemy declarative base and the mixins shared across the schema.

Per the Architecture Invariants in CLAUDE.md, every fact-bearing table carries
provenance (`source_id`, `scraped_at`, `confidence_score`) plus a
`superseded_by` self-reference. A fact is never destructively overwritten: a
better fact supersedes the old one and the old row stays for audit.

These are declared as mixins rather than repeated per model so the invariant is
enforced by construction - you get provenance by inheriting it, and a table
that lacks it is visibly missing a base class.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, MetaData, Numeric, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from sqlalchemy.types import TIMESTAMP

# Deterministic constraint and index names. Without an explicit convention,
# Alembic autogenerate emits server-assigned names that differ between
# environments, which makes migration diffs unstable and hard to review. The
# `ix` pattern deliberately mirrors the `idx_<table>_<column>` names already
# used in docs/schema_phase1.sql so the two stay readable side by side.
NAMING_CONVENTION = {
    "ix": "idx_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base. All models inherit from this."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Row lifecycle timestamps. Distinct from `scraped_at`, which is when the
    *source* was read; these are when *our* row was written."""

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
    """`source_id` / `scraped_at` / `confidence_score` - required on every
    fact-bearing row.

    Each column is built inside `declared_attr` so every subclass gets its own
    Column and CheckConstraint objects rather than sharing one instance.
    """

    @declared_attr
    @classmethod
    def source_id(cls) -> Mapped[int | None]:
        # Indexed because CLAUDE.md requires every FK column to be indexed, and
        # because "show me everything this source told us" is a core query for
        # the reconciliation and review workflows.
        return mapped_column(ForeignKey("sources.id"), index=True)

    @declared_attr
    @classmethod
    def scraped_at(cls) -> Mapped[datetime | None]:
        return mapped_column(TIMESTAMP(timezone=True))

    @declared_attr
    @classmethod
    def confidence_score(cls) -> Mapped[Decimal | None]:
        # The 0-1 range CHECK lives in provenance_table_args(), not inline
        # here - see that function for why it has to be table-level.
        return mapped_column(Numeric(3, 2))


def provenance_table_args() -> tuple[CheckConstraint, ...]:
    """Table-level constraints that must accompany `ProvenanceMixin`.

    Deliberately table-level rather than attached inline to the column.
    SQLAlchemy renders an inline column CheckConstraint fine via
    `metadata.create_all()`, but Alembic autogenerate only inspects
    `Table.constraints` - so an inline constraint is silently dropped from
    generated migrations and would never reach the real database.

    Every model using ProvenanceMixin must spread this into its table args:

        __table_args__ = (*provenance_table_args(), UniqueConstraint(...))
    """
    return (CheckConstraint("confidence_score BETWEEN 0 AND 1", name="confidence_score_range"),)


class SupersededByMixin:
    """Self-referencing `superseded_by` pointer.

    The FK target is derived from the subclass's own `__tablename__`, so each
    table points at itself without restating the table name.
    """

    @declared_attr
    @classmethod
    def superseded_by(cls) -> Mapped[int | None]:
        return mapped_column(ForeignKey(f"{cls.__tablename__}.id"), index=True)
