"""generations company-anchored; generation_model_links

ADR 0016: a generation is a company-anchored index entity, not a child of
one model - one E46 covers 325i/330i/M3, so `generations.model_id` becomes
`generations.company_id` and which models a generation spans moves to
`generation_model_links`, a per-source assertion table in the
`model_line_members` shape. The existing parent FKs seed the link table as
sourceless rows; the Wikidata models pass re-asserts them with full
provenance on its next run.

Hand-written: column swaps and constraint renames are autogenerate blind
spots (the ADR 0009 lesson), and this one backfills through a join.

Revision ID: c113fff36784
Revises: ea565b7ea489
Create Date: 2026-08-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c113fff36784"
down_revision: str | Sequence[str] | None = "ea565b7ea489"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # --- generation_model_links ----------------------------------------------
    op.create_table(
        "generation_model_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("generation_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("raw_record_id", sa.BigInteger(), nullable=True),
        sa.Column("scraped_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("confidence_score", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.CheckConstraint(
            "confidence_score BETWEEN 0 AND 1",
            name=op.f("ck_generation_model_links_confidence_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["generations.id"],
            name=op.f("fk_generation_model_links_generation_id_generations"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["models.id"], name=op.f("fk_generation_model_links_model_id_models")
        ),
        sa.ForeignKeyConstraint(
            ["raw_record_id"],
            ["raw_scrape.raw_records.id"],
            name=op.f("fk_generation_model_links_raw_record_id_raw_records"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name=op.f("fk_generation_model_links_source_id_sources")
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["generation_model_links.id"],
            name="fk_generation_model_links_superseded_by",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_model_links")),
    )
    op.create_index(
        op.f("idx_generation_model_links_generation_id"),
        "generation_model_links",
        ["generation_id"],
        unique=False,
    )
    op.create_index(
        op.f("idx_generation_model_links_model_id"),
        "generation_model_links",
        ["model_id"],
        unique=False,
    )
    op.create_index(
        op.f("idx_generation_model_links_raw_record_id"),
        "generation_model_links",
        ["raw_record_id"],
        unique=False,
    )
    op.create_index(
        op.f("idx_generation_model_links_source_id"),
        "generation_model_links",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        op.f("idx_generation_model_links_superseded_by"),
        "generation_model_links",
        ["superseded_by"],
        unique=False,
    )
    op.create_index(
        "uq_generation_model_links_live",
        "generation_model_links",
        ["generation_id", "model_id", "source_id"],
        unique=True,
        postgresql_where=sa.text("superseded_by IS NULL"),
        postgresql_nulls_not_distinct=True,
    )

    # --- generations: model_id -> company_id ---------------------------------
    op.add_column("generations", sa.Column("company_id", sa.Integer(), nullable=True))
    bind.execute(
        sa.text(
            "UPDATE generations g SET company_id = m.company_id "
            "FROM models m WHERE m.id = g.model_id"
        )
    )
    orphans = bind.execute(
        sa.text("SELECT count(*) FROM generations WHERE company_id IS NULL")
    ).scalar()
    if orphans:
        raise RuntimeError(
            f"{orphans} generations resolve no company through their model; "
            "stop and look, don't force"
        )
    op.alter_column("generations", "company_id", nullable=False)

    # Constraint before drop: if company-scope slugs collide after all, this
    # aborts while model_id still exists and nothing is lost.
    op.create_unique_constraint(
        "uq_generations_company_id_slug", "generations", ["company_id", "slug"]
    )
    op.create_foreign_key(
        "fk_generations_company_id_companies", "generations", "companies", ["company_id"], ["id"]
    )
    op.create_index("idx_generations_company_id", "generations", ["company_id"])

    # Seed the links from the FKs being retired. Sourceless: what asserted the
    # link is the pass's business to re-record with raw-record lineage.
    bind.execute(
        sa.text(
            "INSERT INTO generation_model_links (generation_id, model_id) "
            "SELECT id, model_id FROM generations"
        )
    )

    op.drop_constraint("uq_generations_model_id_slug", "generations")
    op.drop_constraint("fk_generations_model_id_models", "generations")
    op.drop_index("idx_generations_model_id", table_name="generations")
    op.drop_column("generations", "model_id")


def downgrade() -> None:
    # Refuse rather than discard (the 76cb287dd71c posture). Two things cannot
    # round-trip into a single parent FK: a generation linked to several
    # models, and sourced link rows - provenance-carrying facts a later pass
    # asserted. Sourceless seed rows are this migration's own artifacts and
    # may go.
    bind = op.get_bind()
    sourced = bind.execute(
        sa.text("SELECT count(*) FROM generation_model_links WHERE source_id IS NOT NULL")
    ).scalar()
    unparentable = bind.execute(
        sa.text(
            "SELECT count(*) FROM generations g WHERE ("
            "SELECT count(DISTINCT model_id) FROM generation_model_links "
            "WHERE generation_id = g.id AND superseded_by IS NULL) != 1"
        )
    ).scalar()
    if sourced or unparentable:
        raise RuntimeError(
            f"cannot downgrade: {sourced} sourced link assertions would be "
            f"discarded and {unparentable} generations do not resolve to "
            "exactly one model; resolve them first"
        )

    op.add_column("generations", sa.Column("model_id", sa.Integer(), nullable=True))
    bind.execute(
        sa.text(
            "UPDATE generations g SET model_id = ("
            "SELECT model_id FROM generation_model_links "
            "WHERE generation_id = g.id AND superseded_by IS NULL LIMIT 1)"
        )
    )
    op.alter_column("generations", "model_id", nullable=False)
    op.create_unique_constraint("uq_generations_model_id_slug", "generations", ["model_id", "slug"])
    op.create_foreign_key(
        "fk_generations_model_id_models", "generations", "models", ["model_id"], ["id"]
    )
    op.create_index("idx_generations_model_id", "generations", ["model_id"])

    op.drop_index("idx_generations_company_id", table_name="generations")
    op.drop_constraint("fk_generations_company_id_companies", "generations")
    op.drop_constraint("uq_generations_company_id_slug", "generations")
    op.drop_column("generations", "company_id")

    op.drop_index("uq_generation_model_links_live", table_name="generation_model_links")
    op.drop_index(
        op.f("idx_generation_model_links_superseded_by"), table_name="generation_model_links"
    )
    op.drop_index(op.f("idx_generation_model_links_source_id"), table_name="generation_model_links")
    op.drop_index(
        op.f("idx_generation_model_links_raw_record_id"), table_name="generation_model_links"
    )
    op.drop_index(op.f("idx_generation_model_links_model_id"), table_name="generation_model_links")
    op.drop_index(
        op.f("idx_generation_model_links_generation_id"), table_name="generation_model_links"
    )
    op.drop_table("generation_model_links")
