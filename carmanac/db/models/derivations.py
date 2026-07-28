"""Vehicle derivations - how built/derived vehicles relate to their base
(ADR 0005, as amended at acceptance).

One fact table, keyed on the *base* generation, records every
company-transforms-a-vehicle relationship: coachbuilt, restomod, tuned,
rebadged. Keying on the base is what makes "show me modified 911s" one query
returning Singer, Gunther Werks and Ruf together, whether or not the result has
its own catalogue entry.

The derived side records CATALOGUE PLACEMENT, not legal status:

- `derived_generation_id` NULL - the build stays under the base make. A
  customer 964 rebuilt by Singer is a Porsche 911; a Duesenberg bodied by
  Murphy is a Duesenberg. The historical norm.
- `derived_generation_id` set - the build is its own model/generation under the
  builder company. The Alpina B3 (G20) is a generation under Alpina; the Singer
  DLS is a generation under Singer that *links to* the 964 rather than
  appearing as a generation of it.

Whose name is in the VIN lives ONLY in `company_role_assignments` (the
`manufacturer` role). The two axes are independent per build - Ruf issues its
own VINs on the CTR while its customer conversions keep their Porsche VIN - so
neither can be derived from the other; agreement between them is a reconciler
check, not a constraint.

Platform sharing (Urus/SQ8/Cayenne on MLB Evo) is deliberately NOT here: no
builder, no donor, no direction. It resolves to a future `platforms` entity
with `evolved_from` lineage (ADR 0005 SS5).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from carmanac.db.base import Base, ProvenanceMixin, provenance_table_args


class VehicleDerivation(Base, ProvenanceMixin):
    """One company-transforms-a-vehicle claim. Fact-bearing: "the DLS is based
    on the 964" is sourced like any other assertion, so it carries provenance
    and traces to its raw record.
    """

    __tablename__ = "vehicle_derivations"

    # Surrogate PK rather than the composite-PK idiom of the other association
    # tables: the natural key below includes a nullable column, which a primary
    # key cannot.
    id: Mapped[int] = mapped_column(primary_key=True)

    # The donor / origin vehicle. Everything keys on the base. Explicitly
    # indexed: the live-unique index below also leads with this column but is
    # PARTIAL (live rows only), so history reads ("every claim ever made about
    # the 964") need a full index of their own.
    base_generation_id: Mapped[int] = mapped_column(
        ForeignKey("generations.id"), nullable=False, index=True
    )

    # Who did the transforming. Points at `companies` regardless of role
    # (ADR 0006) - Singer, Alpina, Zagato and Murphy are all just companies.
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)

    # Catalogue placement - see module docstring. Nullable by design.
    derived_generation_id: Mapped[int | None] = mapped_column(ForeignKey("generations.id"))

    derivation_type_id: Mapped[int] = mapped_column(
        ForeignKey("derivation_types.id"), nullable=False, index=True
    )
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("vehicle_derivations.id", name="fk_vehicle_derivations_superseded_by"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *provenance_table_args(),
        # A generation cannot derive from itself.
        CheckConstraint(
            "derived_generation_id IS NULL OR derived_generation_id <> base_generation_id",
            name="derived_not_base",
        ),
        # One LIVE claim per (base, company, type, derived, source) - the
        # per-source assertion-store shape (ADR 0007 SS8, F7): two sources may
        # both assert "Singer restomods the 964", and each may retract
        # independently via supersession. NULLS NOT DISTINCT is load-bearing
        # twice over: the common case (derived side NULL - Ruf conversions,
        # Murphy bodies) and sourceless seed rows would otherwise never
        # collide, and a re-running reconciler would insert the same claim
        # endlessly.
        Index(
            "uq_vehicle_derivations_live",
            "base_generation_id",
            "company_id",
            "derivation_type_id",
            "derived_generation_id",
            "source_id",
            unique=True,
            postgresql_where=text("superseded_by IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
        # The child->parent read ("what is the DLS built on?") - a lookup by
        # derived generation. Partial: the column is NULL for the historical
        # majority of rows (contract coachbuilding), and a query by value never
        # needs the NULLs. Mirrors the field_provenance arc-column idiom.
        Index(
            "idx_vehicle_derivations_derived_generation_id",
            "derived_generation_id",
            postgresql_where=text("derived_generation_id IS NOT NULL"),
        ),
    )
