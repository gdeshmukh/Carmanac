"""per-source association assertion stores (F7)

Auto-generated, then substantially hand-rewritten - autogenerate got this one
WRONG in two ways worth recording:

1. It emitted `add_column('id', Integer, nullable=False)` with no sequence or
   default, which fails on tables holding seed rows.
2. It never touched the primary keys at all: the old composite PKs
   (company_id, company_role_id) etc. would have remained, so a second source
   asserting the same fact would still be rejected - silently defeating the
   entire point of the migration. Alembic autogenerate does not compare
   primary keys; this is the same class of blind spot as the inline CHECK
   constraints and triggers.

Hand-written shape per association table: ADD COLUMN id SERIAL (backfills the
seed rows), swap the PK to it, then the assertion-store additions
(superseded_by + self-FK, created_at, per-source live-unique index).

Downgrade restores the composite PKs; it will fail if per-source rows exist
by then (two sources on one fact cannot fit the old key) - which is correct:
that data has no representation in the old schema.

Revision ID: d212a042caa7
Revises: 88079940a9e5
Create Date: 2026-07-28 11:35:56.673910

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd212a042caa7'
down_revision: str | Sequence[str] | None = '88079940a9e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, fact columns) for the three tables whose PK moves to a surrogate.
# vehicle_derivations already has a surrogate id and is handled separately.
_REKEYED = (
    ("company_role_assignments", ("company_id", "company_role_id")),
    ("configuration_engines", ("configuration_id", "engine_id")),
    ("configuration_transmissions", ("configuration_id", "transmission_id")),
)


def _live_index_name(table: str) -> str:
    # uq_company_role_assignment_live etc. - singularized to match the models.
    singular = {
        "company_role_assignments": "uq_company_role_assignment_live",
        "configuration_engines": "uq_configuration_engine_live",
        "configuration_transmissions": "uq_configuration_transmission_live",
    }
    return singular[table]


def upgrade() -> None:
    """Upgrade schema."""
    for table, fact_cols in _REKEYED:
        # SERIAL via raw DDL: it creates the sequence AND backfills existing
        # (seed) rows in one step, which op.add_column cannot.
        op.execute(f"ALTER TABLE {table} ADD COLUMN id SERIAL")
        op.drop_constraint(f"pk_{table}", table, type_="primary")
        op.create_primary_key(f"pk_{table}", table, ["id"])

        op.add_column(table, sa.Column("superseded_by", sa.Integer(), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "created_at",
                sa.TIMESTAMP(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )
        # The old composite PK covered lookups on its leading column; that
        # index is gone with the PK swap, so the leading fact column gets an
        # explicit index (the second fact column has had one all along).
        op.create_index(f"idx_{table}_{fact_cols[0]}", table, [fact_cols[0]], unique=False)
        op.create_index(f"idx_{table}_superseded_by", table, ["superseded_by"], unique=False)
        op.create_index(
            _live_index_name(table),
            table,
            [*fact_cols, "source_id"],
            unique=True,
            postgresql_where=sa.text("superseded_by IS NULL"),
            postgresql_nulls_not_distinct=True,
        )
        op.create_foreign_key(
            f"fk_{table}_superseded_by", table, table, ["superseded_by"], ["id"]
        )

    # vehicle_derivations: already surrogate-keyed; its natural-key unique
    # constraint becomes the per-source live index.
    op.add_column("vehicle_derivations", sa.Column("superseded_by", sa.Integer(), nullable=True))
    op.add_column(
        "vehicle_derivations",
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.drop_constraint("uq_vehicle_derivations_natural_key", "vehicle_derivations", type_="unique")
    op.create_index(
        "idx_vehicle_derivations_superseded_by",
        "vehicle_derivations",
        ["superseded_by"],
        unique=False,
    )
    # base_generation_id led the old natural-key unique index, which covered
    # base-side lookups; it needs an explicit index now that the live index
    # still leads with it - actually retained via the live index below, but
    # only for live rows. Full-column index for history reads:
    op.create_index(
        "idx_vehicle_derivations_base_generation_id",
        "vehicle_derivations",
        ["base_generation_id"],
        unique=False,
    )
    op.create_index(
        "uq_vehicle_derivations_live",
        "vehicle_derivations",
        [
            "base_generation_id",
            "company_id",
            "derivation_type_id",
            "derived_generation_id",
            "source_id",
        ],
        unique=True,
        postgresql_where=sa.text("superseded_by IS NULL"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_foreign_key(
        "fk_vehicle_derivations_superseded_by",
        "vehicle_derivations",
        "vehicle_derivations",
        ["superseded_by"],
        ["id"],
    )


def downgrade() -> None:
    """Downgrade schema. Fails if per-source rows exist - see module docstring."""
    op.drop_constraint(
        "fk_vehicle_derivations_superseded_by",
        "vehicle_derivations",
        type_="foreignkey",
    )
    op.drop_index("uq_vehicle_derivations_live", table_name="vehicle_derivations")
    op.drop_index("idx_vehicle_derivations_base_generation_id", table_name="vehicle_derivations")
    op.drop_index("idx_vehicle_derivations_superseded_by", table_name="vehicle_derivations")
    op.create_unique_constraint(
        "uq_vehicle_derivations_natural_key",
        "vehicle_derivations",
        ["base_generation_id", "company_id", "derivation_type_id", "derived_generation_id"],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_column("vehicle_derivations", "created_at")
    op.drop_column("vehicle_derivations", "superseded_by")

    for table, fact_cols in reversed(_REKEYED):
        op.drop_constraint(f"fk_{table}_superseded_by", table, type_="foreignkey")
        op.drop_index(_live_index_name(table), table_name=table)
        op.drop_index(f"idx_{table}_superseded_by", table_name=table)
        op.drop_index(f"idx_{table}_{fact_cols[0]}", table_name=table)
        op.drop_column(table, "created_at")
        op.drop_column(table, "superseded_by")
        op.drop_constraint(f"pk_{table}", table, type_="primary")
        op.drop_column(table, "id")
        op.create_primary_key(f"pk_{table}", table, list(fact_cols))
