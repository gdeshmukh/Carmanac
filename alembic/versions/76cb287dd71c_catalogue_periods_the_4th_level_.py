"""catalogue periods - the 4th level generalizes (ADR 0009)

Revision ID: 76cb287dd71c
Revises: 613bdd40c0bc
Create Date: 2026-07-29 14:13:35.595665

Hand-written. Autogenerate renders a table rename as DROP + CREATE (discarding
rows and dependent FKs) and cannot see triggers - the same two blind spots the
ADR 0006 rename migration documented. What this does, per ADR 0009:

- `period_kinds` lookup, seeded: model_year / production_period / phase.
- `model_years` -> `catalogue_periods`, with every name that embeds the old
  table or column renamed alongside (PK, indexes, FK constraint names, the
  `updated_at` trigger, the id sequence - the ADR 0006 rename left
  `makes_id_seq` behind on `companies`; not repeated here, not fixed here).
- `year` -> (`start_year`, `end_year`, `period_kind_id`); existing rows
  backfill as model_year periods (start = end = year).
- The (generation, year) unique becomes (generation, kind, start, end)
  NULLS NOT DISTINCT - an open-ended period (end NULL = still in production)
  must still collide with its duplicate.
- CHECK end_year >= start_year (NULL end exempt).
- The five referencing tables' `model_year_id` columns rename to
  `catalogue_period_id`; their FK-constraint and index names follow. Column
  renames rewrite constraint/index EXPRESSIONS automatically (the exclusive-
  arc CHECKs, the configurations natural key, uq_media_attachments arc
  columns); only the NAMES need explicit renames.

Downgrade restores the old shape faithfully for model_year rows
(year = start_year). True period/phase rows cannot round-trip into a single
`year` - none exist before this migration, so the downgrade guards against
data loss by refusing if any non-model_year row is present.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "76cb287dd71c"
down_revision: str | Sequence[str] | None = "613bdd40c0bc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, old FK-constraint name) for the five tables holding the arc/FK column.
_REFERENCING = (
    "configurations",
    "external_ids",
    "field_provenance",
    "media_attachments",
    "reconciliation_flags",
)


def upgrade() -> None:
    # ---- period_kinds lookup, seeded --------------------------------------
    op.create_table(
        "period_kinds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_period_kinds")),
        sa.UniqueConstraint("code", name=op.f("uq_period_kinds_code")),
    )
    op.execute(
        """
        INSERT INTO period_kinds (code, name, description) VALUES
        ('model_year', 'Model year',
         'US-style single catalogue year (start = end). The shape vPIC and EPA assert.'),
        ('production_period', 'Production period',
         'A source-asserted manufacturing/catalogue range ("built 1998-2005"). end NULL = still in production.'),
        ('phase', 'Phase',
         'A facelift phase within a generation (zenki/chuki/kouki, Phase 1/2), when a source distinguishes it.')
        """
    )

    # ---- the rename, with every embedded name following --------------------
    op.rename_table("model_years", "catalogue_periods")
    op.execute("ALTER SEQUENCE model_years_id_seq RENAME TO catalogue_periods_id_seq")
    op.execute("ALTER INDEX pk_model_years RENAME TO pk_catalogue_periods")
    op.execute(
        "ALTER INDEX idx_model_years_generation_id RENAME TO idx_catalogue_periods_generation_id"
    )
    op.execute(
        "ALTER TABLE catalogue_periods RENAME CONSTRAINT "
        "fk_model_years_generation_id_generations TO fk_catalogue_periods_generation_id_generations"
    )
    op.execute(
        "ALTER TRIGGER trg_model_years_set_updated_at ON catalogue_periods "
        "RENAME TO trg_catalogue_periods_set_updated_at"
    )

    # ---- year -> (start_year, end_year, period_kind_id) --------------------
    op.add_column("catalogue_periods", sa.Column("start_year", sa.SmallInteger(), nullable=True))
    op.add_column("catalogue_periods", sa.Column("end_year", sa.SmallInteger(), nullable=True))
    op.add_column("catalogue_periods", sa.Column("period_kind_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE catalogue_periods
        SET start_year = year, end_year = year,
            period_kind_id = (SELECT id FROM period_kinds WHERE code = 'model_year')
        """
    )
    op.alter_column("catalogue_periods", "start_year", nullable=False)
    op.alter_column("catalogue_periods", "period_kind_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_catalogue_periods_period_kind_id_period_kinds"),
        "catalogue_periods",
        "period_kinds",
        ["period_kind_id"],
        ["id"],
    )
    op.create_index(
        op.f("idx_catalogue_periods_period_kind_id"), "catalogue_periods", ["period_kind_id"]
    )

    op.drop_constraint("uq_model_years_generation_id_year", "catalogue_periods")
    op.drop_column("catalogue_periods", "year")
    op.create_unique_constraint(
        "uq_catalogue_periods_natural_key",
        "catalogue_periods",
        ["generation_id", "period_kind_id", "start_year", "end_year"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_check_constraint(
        "end_not_before_start",
        "catalogue_periods",
        "end_year IS NULL OR end_year >= start_year",
    )

    # ---- the five referencing tables ---------------------------------------
    for table in _REFERENCING:
        op.alter_column(table, "model_year_id", new_column_name="catalogue_period_id")
        op.execute(
            f"ALTER TABLE {table} RENAME CONSTRAINT "
            f"fk_{table}_model_year_id_model_years TO "
            f"fk_{table}_catalogue_period_id_catalogue_periods"
        )
        op.execute(
            f"ALTER INDEX idx_{table}_model_year_id RENAME TO idx_{table}_catalogue_period_id"
        )

    # A table-column rename rewrites index DEFINITIONS but not the attribute
    # names stored inside existing index relations (found by post-migration
    # catalog sweep: pg_attribute still said model_year_id inside eight
    # indexes). Inert - planning and pg_dump use the definitions - but the
    # catalog should not lie. Index relations accept ALTER TABLE ... RENAME
    # COLUMN.
    for index in (
        "uq_configurations_natural_key",
        "uq_field_provenance_live",
        "uq_media_attachments_asset_entity_role",
        *(f"idx_{table}_catalogue_period_id" for table in _REFERENCING),
    ):
        op.execute(f"ALTER TABLE {index} RENAME COLUMN model_year_id TO catalogue_period_id")


def downgrade() -> None:
    # Refuse rather than silently flatten real periods into fake years.
    non_model_year = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT count(*) FROM catalogue_periods cp
                JOIN period_kinds pk ON pk.id = cp.period_kind_id
                WHERE pk.code <> 'model_year'
                """
            )
        )
        .scalar_one()
    )
    if non_model_year:
        raise RuntimeError(
            f"{non_model_year} non-model_year period rows exist; downgrading would "
            "flatten them into single years. Resolve them first."
        )

    for index in (
        "uq_configurations_natural_key",
        "uq_field_provenance_live",
        "uq_media_attachments_asset_entity_role",
        *(f"idx_{table}_catalogue_period_id" for table in _REFERENCING),
    ):
        op.execute(f"ALTER TABLE {index} RENAME COLUMN catalogue_period_id TO model_year_id")

    for table in _REFERENCING:
        op.execute(
            f"ALTER INDEX idx_{table}_catalogue_period_id RENAME TO idx_{table}_model_year_id"
        )
        op.execute(
            f"ALTER TABLE {table} RENAME CONSTRAINT "
            f"fk_{table}_catalogue_period_id_catalogue_periods TO "
            f"fk_{table}_model_year_id_model_years"
        )
        op.alter_column(table, "catalogue_period_id", new_column_name="model_year_id")

    op.drop_constraint("ck_catalogue_periods_end_not_before_start", "catalogue_periods")
    op.drop_constraint("uq_catalogue_periods_natural_key", "catalogue_periods")
    op.add_column("catalogue_periods", sa.Column("year", sa.SmallInteger(), nullable=True))
    op.execute("UPDATE catalogue_periods SET year = start_year")
    op.alter_column("catalogue_periods", "year", nullable=False)
    op.drop_index(op.f("idx_catalogue_periods_period_kind_id"), table_name="catalogue_periods")
    op.drop_constraint(
        op.f("fk_catalogue_periods_period_kind_id_period_kinds"), "catalogue_periods"
    )
    op.drop_column("catalogue_periods", "period_kind_id")
    op.drop_column("catalogue_periods", "end_year")
    op.drop_column("catalogue_periods", "start_year")
    op.create_unique_constraint(
        "uq_model_years_generation_id_year", "catalogue_periods", ["generation_id", "year"]
    )

    op.execute(
        "ALTER TRIGGER trg_catalogue_periods_set_updated_at ON catalogue_periods "
        "RENAME TO trg_model_years_set_updated_at"
    )
    op.execute(
        "ALTER TABLE catalogue_periods RENAME CONSTRAINT "
        "fk_catalogue_periods_generation_id_generations TO fk_model_years_generation_id_generations"
    )
    op.execute(
        "ALTER INDEX idx_catalogue_periods_generation_id RENAME TO idx_model_years_generation_id"
    )
    op.execute("ALTER INDEX pk_catalogue_periods RENAME TO pk_model_years")
    op.execute("ALTER SEQUENCE catalogue_periods_id_seq RENAME TO model_years_id_seq")
    op.rename_table("catalogue_periods", "model_years")

    op.drop_table("period_kinds")
