"""periods under models, placement on configurations

ADR 0014: the hierarchy is a goal per car. `catalogue_periods` re-parents
from generations to models - a period is pure time, recording exactly what
the year-asserting sources say: (model, span). Generation placement moves
to `configurations.generation_id`, NULLABLE and evidence-gated, because
one model year can contain configurations of two generations at once (the
2019 AMG GT: C190 coupes and X290 4-doors side by side).

Hand-written (the ADR 0009 lesson: column swaps and constraint renames are
autogenerate blind spots). The table is empty at migration time - ADR 0014
lands before the first year pass - so the parent swap needs no backfill.

Revision ID: c2d02b91b922
Revises: a1756b039034
Create Date: 2026-07-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d02b91b922"
down_revision: str | Sequence[str] | None = "a1756b039034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- catalogue_periods: generation_id -> model_id ------------------------
    # Empty-table guard: the swap adds model_id NOT NULL without a backfill,
    # which is only legal while no rows exist. If this ever fires, the
    # migration ordering assumption broke - stop and look, don't force.
    rows = op.get_bind().execute(sa.text("SELECT count(*) FROM catalogue_periods")).scalar()
    if rows:
        raise RuntimeError(
            f"catalogue_periods has {rows} rows; this migration assumes the "
            "parent swap happens before any year pass has written periods"
        )

    op.drop_constraint("uq_catalogue_periods_natural_key", "catalogue_periods")
    op.drop_constraint("fk_catalogue_periods_generation_id_generations", "catalogue_periods")
    op.drop_index("idx_catalogue_periods_generation_id", table_name="catalogue_periods")
    op.drop_column("catalogue_periods", "generation_id")

    op.add_column("catalogue_periods", sa.Column("model_id", sa.Integer(), nullable=False))
    op.create_foreign_key(
        "fk_catalogue_periods_model_id_models",
        "catalogue_periods",
        "models",
        ["model_id"],
        ["id"],
    )
    op.create_index("idx_catalogue_periods_model_id", "catalogue_periods", ["model_id"])
    op.create_unique_constraint(
        "uq_catalogue_periods_natural_key",
        "catalogue_periods",
        ["model_id", "period_kind_id", "start_year", "end_year"],
        postgresql_nulls_not_distinct=True,
    )

    # --- configurations: evidence-gated generation placement -----------------
    op.add_column("configurations", sa.Column("generation_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_configurations_generation_id_generations",
        "configurations",
        "generations",
        ["generation_id"],
        ["id"],
    )
    op.create_index("idx_configurations_generation_id", "configurations", ["generation_id"])


def downgrade() -> None:
    # Refuse rather than discard (the 76cb287dd71c posture): model-parented
    # periods cannot round-trip into generation-parented rows, and placement
    # facts on configurations would be silently lost.
    bind = op.get_bind()
    periods = bind.execute(sa.text("SELECT count(*) FROM catalogue_periods")).scalar()
    placed = bind.execute(
        sa.text("SELECT count(*) FROM configurations WHERE generation_id IS NOT NULL")
    ).scalar()
    if periods or placed:
        raise RuntimeError(
            f"cannot downgrade: {periods} model-parented periods and {placed} "
            "placed configurations would be discarded; resolve them first"
        )

    op.drop_index("idx_configurations_generation_id", table_name="configurations")
    op.drop_constraint("fk_configurations_generation_id_generations", "configurations")
    op.drop_column("configurations", "generation_id")

    op.drop_constraint("uq_catalogue_periods_natural_key", "catalogue_periods")
    op.drop_index("idx_catalogue_periods_model_id", table_name="catalogue_periods")
    op.drop_constraint("fk_catalogue_periods_model_id_models", "catalogue_periods")
    op.drop_column("catalogue_periods", "model_id")

    op.add_column("catalogue_periods", sa.Column("generation_id", sa.Integer(), nullable=False))
    op.create_foreign_key(
        "fk_catalogue_periods_generation_id_generations",
        "catalogue_periods",
        "generations",
        ["generation_id"],
        ["id"],
    )
    op.create_index("idx_catalogue_periods_generation_id", "catalogue_periods", ["generation_id"])
    op.create_unique_constraint(
        "uq_catalogue_periods_natural_key",
        "catalogue_periods",
        ["generation_id", "period_kind_id", "start_year", "end_year"],
        postgresql_nulls_not_distinct=True,
    )
