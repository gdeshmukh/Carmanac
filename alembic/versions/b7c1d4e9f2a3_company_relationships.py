"""company_relationships: dated parent eras (ADR 0022 §1)

Hand-written. One source's assertion that one company was the parent of
another for a span of years, in the company_role_assignments shape: live rows
unique per (era, source), supersession for same-source history. "Current
parent" is the open-ended live row - a projection, never a column.

Revision ID: b7c1d4e9f2a3
Revises: 2aa5ce8be2bd
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7c1d4e9f2a3"
down_revision = "2aa5ce8be2bd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_relationships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("parent_company_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("start_year", sa.SmallInteger(), nullable=True),
        sa.Column("end_year", sa.SmallInteger(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("raw_record_id", sa.BigInteger(), nullable=True),
        sa.Column("scraped_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("confidence_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("confidence_score BETWEEN 0 AND 1", name="confidence_score_range"),
        sa.CheckConstraint("company_id <> parent_company_id", name="not_self"),
        sa.CheckConstraint(
            "start_year IS NULL OR end_year IS NULL OR start_year <= end_year",
            name="era_order",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["parent_company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.ForeignKeyConstraint(["raw_record_id"], ["raw_scrape.raw_records.id"]),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["company_relationships.id"],
            name="fk_company_relationships_superseded_by",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "company_id",
        "parent_company_id",
        "source_id",
        "raw_record_id",
        "superseded_by",
    ):
        op.create_index(f"idx_company_relationships_{column}", "company_relationships", [column])
    op.create_index(
        "uq_company_relationships_live",
        "company_relationships",
        ["company_id", "parent_company_id", "kind", "start_year", "end_year", "source_id"],
        unique=True,
        postgresql_where=sa.text("superseded_by IS NULL"),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_table("company_relationships")
