"""EAV storage for long-tail / sparse specs.

The hybrid storage rule from CLAUDE.md: ~20 universal specs are columns on
`configurations`; anything that fewer than ~80% of configurations would have a
value for lives here instead. Turbo count, valve count, brake rotor diameter,
market-specific equipment - all EAV.

No attribute may land in `configuration_attributes` until its key is registered
in `attribute_definitions`. That registry is what keeps EAV from decaying into
an untyped free-for-all.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from carmanac.db.base import Base, ProvenanceMixin, provenance_table_args

# The legal EAV value types. Each maps to one typed value column on
# `configuration_attributes`.
ATTRIBUTE_DATA_TYPES = ("text", "integer", "numeric", "boolean")


class AttributeDefinition(Base):
    """Registry of legal EAV keys.

    Keys are canonical English (localized display labels are a deferred
    concern - see PROGRESS.md Open Questions). Reference data, so no provenance
    columns.
    """

    __tablename__ = "attribute_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # e.g. 'turbo_count'
    label: Mapped[str] = mapped_column(Text, nullable=False)
    data_type: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text)  # 'mm', 'kW', 'L', or null
    validation_regex: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Table-level, not inline on the column: Alembic autogenerate only
        # renders constraints it finds in Table.constraints.
        CheckConstraint(
            "data_type IN ('text','integer','numeric','boolean')", name="data_type_valid"
        ),
    )


class ConfigurationAttribute(Base, ProvenanceMixin):
    """One long-tail attribute value for one configuration.

    Exactly one typed value column is populated per row, matching the
    registered `data_type` of the attribute.

    BIGSERIAL rather than SERIAL: this is the table that grows fastest -
    configurations x attributes - and is the one most likely to exhaust a
    32-bit key at global scope.
    """

    __tablename__ = "configuration_attributes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    configuration_id: Mapped[int] = mapped_column(
        ForeignKey("configurations.id"), nullable=False, index=True
    )
    attribute_id: Mapped[int] = mapped_column(
        ForeignKey("attribute_definitions.id"), nullable=False, index=True
    )

    value_text: Mapped[str | None] = mapped_column(Text)
    value_integer: Mapped[int | None] = mapped_column(BigInteger)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric)
    value_boolean: Mapped[bool | None] = mapped_column(Boolean)

    # Declared here rather than via SupersededByMixin because the PK is
    # BIGSERIAL, so the self-reference must be BIGINT to match.
    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("configuration_attributes.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *provenance_table_args(),
        # Partial unique index: at most ONE live value per
        # (configuration, attribute). Superseded history is retained rather
        # than deleted, so the uniqueness must exclude superseded rows.
        Index(
            "uq_configuration_attribute_live",
            "configuration_id",
            "attribute_id",
            unique=True,
            postgresql_where=text("superseded_by IS NULL"),
        ),
    )
