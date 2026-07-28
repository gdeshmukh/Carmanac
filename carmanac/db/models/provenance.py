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
    "company_id",
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
        # Re-scraping is idempotent *structurally*, not by convention: an
        # unchanged payload hashes the same and matches here, so a re-run
        # cannot duplicate it even if the application-side check is skipped or
        # two ingests race (the landing upsert just bumps `last_seen_at`). A
        # payload that genuinely CHANGED hashes differently and is inserted as
        # a new row - which is the point, since raw records are never discarded
        # and the history is the change log.
        #
        # `source_id` is part of the key so two sources may legitimately hold
        # byte-identical payloads. `external_id` is deliberately NOT: the
        # payload already contains the entity's identifier, so an equal hash
        # means an equal record.
        UniqueConstraint("source_id", "content_hash", name="uq_raw_records_source_id_content_hash"),
        {"schema": "raw_scrape"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    url: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)  # the source's id for this record
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    # When this exact payload was most recently returned by the source.
    # `fetched_at` is the immutable first observation; this is bumped by the
    # landing upsert on every re-observation. The distinction is load-bearing
    # for reverts (foundation review F3): a source going A -> B -> A re-touches
    # the original A row rather than landing a third, so "the source's current
    # record for an entity" is max(last_seen_at), never max(fetched_at) - by
    # fetched_at the stale intermediate B looks newest. A last_seen_at that
    # stops advancing while ingests keep running is also the "source no longer
    # returns this entity" signal that retraction handling needs.
    last_seen_at: Mapped[datetime] = mapped_column(
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
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
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
        # AT MOST ONE LIVE ASSERTION per (entity, field, source). Without this
        # the table accepted unlimited contradictory live rows - verified: three
        # rows saying companies.name was 'BMW', 'Bayerische Motoren Werke' and
        # 'BMW AG', same source, none superseded, all accepted. A re-running
        # reconciler would then append rather than supersede, and "what Wikidata
        # currently says" would stop having one answer.
        #
        # Partial (WHERE superseded_by IS NULL) because superseded history is
        # retained by design, so only live rows may be unique. Deliberately a
        # DIFFERENT shape from `uq_configuration_attribute_live`, which omits
        # source_id: EAV rows are projected winners (one displayed value per
        # attribute), while these rows are per-source assertions (ADR 0007
        # SS8) - one live row per source, disagreement included.
        #
        # NULLS NOT DISTINCT: exactly one arc column is set per row and the
        # other six are NULL, so without it every row would look distinct on
        # those columns and the constraint would never fire.
        Index(
            "uq_field_provenance_live",
            *_ARC_COLUMNS,
            "field_name",
            "source_id",
            unique=True,
            postgresql_where=text("superseded_by IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
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
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
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
