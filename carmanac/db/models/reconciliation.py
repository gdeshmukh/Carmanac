"""Reconciliation state and the review queue (ADR 0007 §8).

Two tables the reconciler owns:

- `reconciled_records` - sidecar marking which raw records have been processed
  and by which reconciler version. A sidecar rather than columns on
  `raw_records` because the landing zone stays untransformed source data
  (ADR 0003); reconciliation state is our bookkeeping, not theirs.
- `reconciliation_flags` - the review queue (review #9, first version). Every
  situation the reconciler cannot resolve mechanically becomes a row here:
  conflicting field values, multi-valued claims, role disagreements, records
  quarantined at admission, entities a source stopped returning. Flags never
  block projection (ADR 0007 §6.4) - pages always show data - and resolving
  one records a human decision that later feeds the matcher's labeled set.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from carmanac.db.base import Base
from carmanac.db.models.provenance import _ARC_COLUMNS

FLAG_KINDS = (
    "field_conflict",
    "multi_value",
    "role_disagreement",
    "admission_review",
    "source_dropped",
    # A single confident lie: the projected value fails a plausibility rule
    # (Mercedes-AMG "founded 1812" - one claim, no disagreement, no
    # multi_value flag to catch it). The value still projects (§6.4: pages
    # always show data, tentatively); the flag records the suspicion until a
    # better source overturns it or review confirms it (Peugeot really was
    # founded 1810 - as a coffee-mill maker).
    "implausible_value",
    # A source record that could not be matched to a company (ADR 0008):
    # zero or several candidates for a vPIC make. Record-scoped like
    # admission_review - there may be no entity to attach to - with
    # trigram-generated candidates in `detail` for the reviewer. Resolving
    # one grows the curated match registry and the matcher's labeled set.
    "match_review",
)

# Kinds that attach to a raw record rather than an entity (the arc is empty).
RECORD_SCOPED_KINDS = ("admission_review", "match_review")

FLAG_STATUSES = ("open", "resolved", "dismissed")


class ReconciledRecord(Base):
    """Reconciliation state for one raw record.

    `reconciler_version` is which policy/code version processed the record, so
    re-reconciliation can target stale records mechanically ("everything not
    yet processed by v3") instead of re-running the world. PK doubles as the
    FK: at most one state row per raw record, upserted on each pass.
    """

    __tablename__ = "reconciled_records"

    raw_record_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("raw_scrape.raw_records.id"), primary_key=True
    )
    reconciled_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    reconciler_version: Mapped[str] = mapped_column(Text, nullable=False)


class ReconciliationFlag(Base):
    """One situation needing human review (ADR 0007 §8).

    Two shapes, encoded in `flag_shape_matches_kind`:

    - entity-scoped kinds (`field_conflict`, `multi_value`, `role_disagreement`,
      `source_dropped`) attach to exactly one entity via the exclusive arc;
    - `admission_review` is record-scoped - the record was quarantined, so no
      entity exists yet. The arc is all-NULL and `raw_record_id` is required.

    `source_id` / `raw_record_id` point at the specific assertion that
    triggered the flag, where one did. `detail` (JSONB) carries the
    kind-specific evidence (the conflicting values, the class set that
    quarantined, ...) so the review UI needs no per-kind schema.
    """

    __tablename__ = "reconciliation_flags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # --- exclusive arc: exactly one set for entity-scoped kinds, none for
    # record-scoped admission_review (see flag_shape_matches_kind) ---
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id"))
    generation_id: Mapped[int | None] = mapped_column(ForeignKey("generations.id"))
    model_year_id: Mapped[int | None] = mapped_column(ForeignKey("model_years.id"))
    configuration_id: Mapped[int | None] = mapped_column(ForeignKey("configurations.id"))
    engine_id: Mapped[int | None] = mapped_column(ForeignKey("engines.id"))
    transmission_id: Mapped[int | None] = mapped_column(ForeignKey("transmissions.id"))

    field_name: Mapped[str | None] = mapped_column(Text)  # set for field-level kinds
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))

    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), index=True)
    raw_record_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("raw_scrape.raw_records.id"), index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "kind IN ({})".format(", ".join(f"'{k}'" for k in FLAG_KINDS)),
            name="kind_valid",
        ),
        CheckConstraint(
            "status IN ({})".format(", ".join(f"'{s}'" for s in FLAG_STATUSES)),
            name="status_valid",
        ),
        # The two shapes documented on the class. num_nonnulls over the arc
        # is the same device as `exactly_one_entity` on field_provenance,
        # conditioned on kind.
        CheckConstraint(
            "(kind IN ({kinds})"
            " AND num_nonnulls({arc}) = 0"
            " AND raw_record_id IS NOT NULL)"
            " OR (kind NOT IN ({kinds})"
            " AND num_nonnulls({arc}) = 1)".format(
                kinds=", ".join(f"'{k}'" for k in RECORD_SCOPED_KINDS),
                arc=", ".join(_ARC_COLUMNS),
            ),
            name="flag_shape_matches_kind",
        ),
        # Partial arc indexes, as on field_provenance: one arc column set per
        # row, so partial indexes stay small and still satisfy "index every FK".
        *(
            Index(f"idx_reconciliation_flags_{c}", c, postgresql_where=text(f"{c} IS NOT NULL"))
            for c in _ARC_COLUMNS
        ),
        # The review queue's read is "open flags, by kind, oldest first";
        # resolved/dismissed history is queried rarely, so the index is partial.
        Index(
            "idx_reconciliation_flags_open",
            "kind",
            "created_at",
            postgresql_where=text("status = 'open'"),
        ),
    )
