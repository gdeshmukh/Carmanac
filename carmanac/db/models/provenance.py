"""Provenance and identity-mapping infrastructure (ADR 0002, ADR 0003).

Three tables that make the schema ready for multi-source reconciliation:

- `raw_scrape.raw_records` - the permanent landing zone. Every scrape lands here
  untransformed and is never discarded; facts point back at the row they came
  from so they can be re-derived when matching logic improves.
- `field_provenance` - field-level provenance for the entity tables' columns.
  Records every source's assertion about a `(entity, field)`; the entity column
  holds the reconciled winner. This is what makes supersession actually work.
- `external_ids` - maps `(source, external_id)` to an entity. Replaces the
  per-table `wikidata_qid` columns so any source's identifiers have a home.

`field_provenance` and `external_ids` use an EXCLUSIVE ARC: one nullable FK per
entity type plus a CHECK that exactly one is set. That keeps real referential
integrity (a polymorphic entity_type/entity_id pair would give that up) while
staying a single table.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from carmanac.db.base import Base

# The seven entity tables an assertion or external id can attach to. Used to
# build the exclusive-arc CHECK and the per-column partial indexes.
_ARC_COLUMNS = (
    "make_id",
    "model_id",
    "generation_id",
    "model_year_id",
    "configuration_id",
    "engine_id",
    "transmission_id",
)


def _exactly_one_entity() -> CheckConstraint:
    """CHECK that exactly one of the exclusive-arc FK columns is set."""
    cols = ", ".join(_ARC_COLUMNS)
    return CheckConstraint(f"num_nonnulls({cols}) = 1", name="exactly_one_entity")


class RawRecord(Base):
    """One untransformed source record. Lives in the `raw_scrape` schema and is
    kept permanently (architecture invariant: raw data is never discarded).

    `content_hash` makes re-scraping idempotent - an unchanged payload need not
    be re-parsed. `payload` (JSONB) suits the Tier 1 JSON/CSV sources;
    `storage_url` is the future pointer for bulk Tier 3/4 HTML kept in object
    storage instead (ADR 0003), so adopting that later needs no schema change.
    """

    __tablename__ = "raw_records"
    __table_args__ = (
        Index("idx_raw_records_source_id_external_id", "source_id", "external_id"),
        Index("idx_raw_records_content_hash", "content_hash"),
        {"schema": "raw_scrape"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    url: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)  # the source's id for this record
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)  # dedup / change detection
    payload: Mapped[dict | None] = mapped_column(JSONB)
    storage_url: Mapped[str | None] = mapped_column(Text)  # future object-storage pointer


class FieldProvenance(Base):
    """One source's assertion about one field of one entity.

    The entity's column holds the reconciled winning value; this table holds the
    full set of assertions behind it, with supersession for same-source history.
    `observed_value` is stored as text for audit ("EPA said mpg_combined=26.0"),
    independent of the column's typed value.
    """

    __tablename__ = "field_provenance"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # --- exclusive arc: exactly one of these is set ---
    make_id: Mapped[int | None] = mapped_column(ForeignKey("makes.id"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id"))
    generation_id: Mapped[int | None] = mapped_column(ForeignKey("generations.id"))
    model_year_id: Mapped[int | None] = mapped_column(ForeignKey("model_years.id"))
    configuration_id: Mapped[int | None] = mapped_column(ForeignKey("configurations.id"))
    engine_id: Mapped[int | None] = mapped_column(ForeignKey("engines.id"))
    transmission_id: Mapped[int | None] = mapped_column(ForeignKey("transmissions.id"))

    field_name: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. 'power_hp'
    observed_value: Mapped[str | None] = mapped_column(Text)  # what the source claimed, as text

    # --- provenance (source_id NOT NULL: an assertion must have a source) ---
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    raw_record_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("raw_scrape.raw_records.id"), index=True
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    scraped_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("field_provenance.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        _exactly_one_entity(),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        # Entity FKs are indexed as PARTIAL indexes (WHERE col IS NOT NULL):
        # each row sets only one arc column, so a partial index is far smaller
        # than a plain one on a mostly-null column. This is the high-volume
        # table, so the leanness matters. Satisfies "index every FK" (CLAUDE.md).
        *(
            Index(f"idx_field_provenance_{c}", c, postgresql_where=text(f"{c} IS NOT NULL"))
            for c in _ARC_COLUMNS
        ),
        # Common lookup: "everything this source asserted about this field".
        Index("idx_field_provenance_source_id_field_name", "source_id", "field_name"),
    )


class ExternalId(Base):
    """Maps a source's identifier to one entity (ADR 0003).

    Replaces the per-table `wikidata_qid` columns. A Wikidata QID is now a row
    here with the Wikidata source; the QID remains the universal join key, just
    stored in a form that extends to NHTSA ids, EPA ids, marque-wiki slugs, etc.
    """

    __tablename__ = "external_ids"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # --- exclusive arc: exactly one of these is set ---
    make_id: Mapped[int | None] = mapped_column(ForeignKey("makes.id"), index=True)
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id"), index=True)
    generation_id: Mapped[int | None] = mapped_column(ForeignKey("generations.id"), index=True)
    model_year_id: Mapped[int | None] = mapped_column(ForeignKey("model_years.id"), index=True)
    configuration_id: Mapped[int | None] = mapped_column(
        ForeignKey("configurations.id"), index=True
    )
    engine_id: Mapped[int | None] = mapped_column(ForeignKey("engines.id"), index=True)
    transmission_id: Mapped[int | None] = mapped_column(ForeignKey("transmissions.id"), index=True)

    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        _exactly_one_entity(),
        # One source's identifier maps to exactly one entity.
        UniqueConstraint("source_id", "external_id", name="uq_external_ids_source_id_external_id"),
    )
