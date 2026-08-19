"""Media provenance and company logos.

The media tables are empty before this migration. It completes their deferred
fact shape before the first logo lands: assets and attachments both gain exact
raw provenance and live-row supersession, and the generic `logo` role becomes
the company-only `company_logo` role.

Revision ID: 1046d362efc2
Revises: 3444e02c8129
Create Date: 2026-08-19 11:42:54.077859

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "1046d362efc2"
down_revision: str | Sequence[str] | None = "3444e02c8129"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("rendition_url", sa.Text(), nullable=True))
    op.add_column("media_assets", sa.Column("license_url", sa.Text(), nullable=True))
    op.add_column("media_assets", sa.Column("rights_notice", sa.Text(), nullable=True))
    op.add_column("media_assets", sa.Column("superseded_by", sa.BigInteger(), nullable=True))
    op.alter_column("media_assets", "content_hash", existing_type=sa.TEXT(), nullable=False)
    op.drop_constraint("ck_media_assets_media_has_location", "media_assets")
    op.create_check_constraint(
        "media_has_rendition",
        "media_assets",
        "storage_url IS NOT NULL OR rendition_url IS NOT NULL",
    )
    op.create_check_constraint(
        "media_asset_provenance_complete",
        "media_assets",
        "source_id IS NOT NULL AND raw_record_id IS NOT NULL "
        "AND scraped_at IS NOT NULL AND source_url IS NOT NULL",
    )
    op.create_index("idx_media_assets_superseded_by", "media_assets", ["superseded_by"])
    op.create_index(
        "uq_media_assets_live",
        "media_assets",
        ["source_id", "source_url"],
        unique=True,
        postgresql_where=sa.text("superseded_by IS NULL"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_foreign_key(
        "fk_media_assets_superseded_by",
        "media_assets",
        "media_assets",
        ["superseded_by"],
        ["id"],
    )

    op.drop_constraint(
        "fk_media_attachments_media_asset_id_media_assets",
        "media_attachments",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_media_attachments_media_asset_id_media_assets",
        "media_attachments",
        "media_assets",
        ["media_asset_id"],
        ["id"],
    )
    op.add_column("media_attachments", sa.Column("superseded_by", sa.BigInteger(), nullable=True))
    op.add_column("media_attachments", sa.Column("source_id", sa.Integer(), nullable=True))
    op.add_column("media_attachments", sa.Column("raw_record_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "media_attachments", sa.Column("scraped_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column(
        "media_attachments",
        sa.Column("confidence_score", sa.Numeric(precision=3, scale=2), nullable=True),
    )
    op.execute(
        "UPDATE media_attachments SET role = 'company_logo' "
        "WHERE role = 'logo' AND company_id IS NOT NULL"
    )
    op.alter_column("media_attachments", "role", existing_type=sa.TEXT(), nullable=False)
    op.drop_constraint(
        "uq_media_attachments_asset_entity_role", "media_attachments", type_="unique"
    )
    op.create_check_constraint(
        "confidence_score_range",
        "media_attachments",
        "confidence_score BETWEEN 0 AND 1",
    )
    op.create_check_constraint(
        "media_attachment_role_valid",
        "media_attachments",
        "role IN ('hero', 'gallery', 'interior', 'engine_bay', 'company_logo', "
        "'owners_manual', 'brochure', 'press_kit', 'spec_sheet')",
    )
    op.create_check_constraint(
        "company_logo_attaches_to_company",
        "media_attachments",
        "role <> 'company_logo' OR company_id IS NOT NULL",
    )
    op.create_check_constraint(
        "media_attachment_provenance_complete",
        "media_attachments",
        "source_id IS NOT NULL AND raw_record_id IS NOT NULL AND scraped_at IS NOT NULL",
    )
    op.create_index("idx_media_attachments_raw_record_id", "media_attachments", ["raw_record_id"])
    op.create_index("idx_media_attachments_source_id", "media_attachments", ["source_id"])
    op.create_index("idx_media_attachments_superseded_by", "media_attachments", ["superseded_by"])
    op.create_index(
        "uq_media_attachments_live",
        "media_attachments",
        [
            "company_id",
            "model_id",
            "generation_id",
            "catalogue_period_id",
            "configuration_id",
            "engine_id",
            "transmission_id",
            "role",
            "source_id",
        ],
        unique=True,
        postgresql_where=sa.text("superseded_by IS NULL"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_foreign_key(
        "fk_media_attachments_source_id_sources",
        "media_attachments",
        "sources",
        ["source_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_media_attachments_raw_record_id_raw_records",
        "media_attachments",
        "raw_records",
        ["raw_record_id"],
        ["id"],
        referent_schema="raw_scrape",
    )
    op.create_foreign_key(
        "fk_media_attachments_superseded_by",
        "media_attachments",
        "media_attachments",
        ["superseded_by"],
        ["id"],
    )

    op.execute(
        """
        INSERT INTO sources (name, tier, base_url, description)
        SELECT 'Wikimedia Commons', 1, 'https://commons.wikimedia.org',
               'Structured file metadata, licensing and attribution'
        WHERE NOT EXISTS (SELECT 1 FROM sources WHERE name = 'Wikimedia Commons')
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_media_attachments_superseded_by", "media_attachments", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_media_attachments_raw_record_id_raw_records",
        "media_attachments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_media_attachments_source_id_sources", "media_attachments", type_="foreignkey"
    )
    op.drop_constraint(
        "ck_media_attachments_media_attachment_provenance_complete", "media_attachments"
    )
    op.drop_constraint("ck_media_attachments_company_logo_attaches_to_company", "media_attachments")
    op.drop_constraint("ck_media_attachments_media_attachment_role_valid", "media_attachments")
    op.drop_constraint("ck_media_attachments_confidence_score_range", "media_attachments")
    op.drop_index("uq_media_attachments_live", table_name="media_attachments")
    op.drop_index("idx_media_attachments_superseded_by", table_name="media_attachments")
    op.drop_index("idx_media_attachments_source_id", table_name="media_attachments")
    op.drop_index("idx_media_attachments_raw_record_id", table_name="media_attachments")
    op.create_unique_constraint(
        "uq_media_attachments_asset_entity_role",
        "media_attachments",
        [
            "media_asset_id",
            "company_id",
            "model_id",
            "generation_id",
            "catalogue_period_id",
            "configuration_id",
            "engine_id",
            "transmission_id",
            "role",
        ],
        postgresql_nulls_not_distinct=True,
    )
    op.execute(
        "UPDATE media_attachments SET role = 'logo' "
        "WHERE role = 'company_logo' AND company_id IS NOT NULL"
    )
    op.alter_column("media_attachments", "role", existing_type=sa.TEXT(), nullable=True)
    op.drop_column("media_attachments", "confidence_score")
    op.drop_column("media_attachments", "scraped_at")
    op.drop_column("media_attachments", "raw_record_id")
    op.drop_column("media_attachments", "source_id")
    op.drop_column("media_attachments", "superseded_by")
    op.drop_constraint(
        "fk_media_attachments_media_asset_id_media_assets",
        "media_attachments",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_media_attachments_media_asset_id_media_assets",
        "media_attachments",
        "media_assets",
        ["media_asset_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("fk_media_assets_superseded_by", "media_assets", type_="foreignkey")
    op.drop_constraint("ck_media_assets_media_asset_provenance_complete", "media_assets")
    op.drop_constraint("ck_media_assets_media_has_rendition", "media_assets")
    op.create_check_constraint(
        "media_has_location",
        "media_assets",
        "storage_url IS NOT NULL OR source_url IS NOT NULL",
    )
    op.drop_index("uq_media_assets_live", table_name="media_assets")
    op.drop_index("idx_media_assets_superseded_by", table_name="media_assets")
    op.alter_column("media_assets", "content_hash", existing_type=sa.TEXT(), nullable=True)
    op.drop_column("media_assets", "superseded_by")
    op.drop_column("media_assets", "rights_notice")
    op.drop_column("media_assets", "license_url")
    op.drop_column("media_assets", "rendition_url")
