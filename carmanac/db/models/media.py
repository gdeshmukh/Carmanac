"""Versioned media files and the sourced claims that attach them to entities.

`media_assets` records one source's observation of a file and its reuse terms.
`media_attachments` records the separate claim that the file serves a specific
role on one entity. Both are facts: each points to the exact raw record and each
keeps same-source history through supersession (ADR 0021).

The database stores locations and metadata, never file bytes. `rendition_url`
is a source-hosted display rendition; `storage_url` is reserved for our copy
when durable media storage is introduced.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from carmanac.db.base import Base, ProvenanceMixin, provenance_table_args
from carmanac.db.models.provenance import _ARC_COLUMNS, _exactly_one_entity

# What kind of file an asset is. Drives which optional columns are meaningful:
# images use width/height, documents use page_count.
MEDIA_KINDS = ("image", "document")

# Where an asset sits on a page. A lookup would be over-engineering for a closed
# presentational set that only the frontend consumes.
MEDIA_ROLES = (
    "hero",
    "gallery",
    "interior",
    "engine_bay",
    "company_logo",
    "owners_manual",
    "brochure",
    "press_kit",
    "spec_sheet",
)


class MediaAsset(Base, ProvenanceMixin):
    """One source's current observation of a displayable file."""

    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    kind: Mapped[str] = mapped_column(Text, nullable=False)  # image | document
    mime_type: Mapped[str | None] = mapped_column(Text)  # 'image/jpeg', 'application/pdf'

    rendition_url: Mapped[str | None] = mapped_column(Text)  # source-hosted display rendition
    storage_url: Mapped[str | None] = mapped_column(Text)  # our copy, when one exists
    source_url: Mapped[str | None] = mapped_column(Text)  # source description / evidence page

    title: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)

    # Reuse metadata is preserved when supplied; collection is not a rights-policy gate.
    license: Mapped[str | None] = mapped_column(Text)  # 'CC-BY-SA-4.0', 'public domain', ...
    license_url: Mapped[str | None] = mapped_column(Text)
    attribution: Mapped[str | None] = mapped_column(Text)  # credit line to display
    rights_notice: Mapped[str | None] = mapped_column(Text)  # trademark / reuse notice

    # --- format specifics ---
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(SmallInteger)
    byte_size: Mapped[int | None] = mapped_column(BigInteger)

    # Same idea as raw_records: identical bytes need storing only once, and
    # re-scraping an unchanged asset should be a no-op.
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)

    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("media_assets.id", name="fk_media_assets_superseded_by"),
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *provenance_table_args(),
        CheckConstraint("kind IN ('image','document')", name="media_kind_valid"),
        CheckConstraint(
            "storage_url IS NOT NULL OR rendition_url IS NOT NULL", name="media_has_rendition"
        ),
        CheckConstraint(
            "source_id IS NOT NULL AND raw_record_id IS NOT NULL "
            "AND scraped_at IS NOT NULL AND source_url IS NOT NULL",
            name="media_asset_provenance_complete",
        ),
        Index("idx_media_assets_content_hash", "content_hash"),
        Index("idx_media_assets_kind", "kind"),
        Index(
            "uq_media_assets_live",
            "source_id",
            "source_url",
            unique=True,
            postgresql_where=text("superseded_by IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
    )


class MediaAttachment(Base, ProvenanceMixin):
    """One source's claim that an asset serves a role on one entity.

    Uses the same exclusive arc as `field_provenance` and `external_ids` - one
    nullable FK per entity type plus a CHECK that exactly one is set - so
    referential integrity survives, unlike a polymorphic entity_type/entity_id
    pair.
    """

    __tablename__ = "media_attachments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    media_asset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_assets.id"), nullable=False, index=True
    )

    # --- exclusive arc: exactly one of these is set ---
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id"))
    generation_id: Mapped[int | None] = mapped_column(ForeignKey("generations.id"))
    catalogue_period_id: Mapped[int | None] = mapped_column(ForeignKey("catalogue_periods.id"))
    configuration_id: Mapped[int | None] = mapped_column(ForeignKey("configurations.id"))
    engine_id: Mapped[int | None] = mapped_column(ForeignKey("engines.id"))
    transmission_id: Mapped[int | None] = mapped_column(ForeignKey("transmissions.id"))

    role: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int | None] = mapped_column(SmallInteger)
    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("media_attachments.id", name="fk_media_attachments_superseded_by"),
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *provenance_table_args(),
        _exactly_one_entity(),
        CheckConstraint(
            "role IN ({})".format(", ".join(f"'{role}'" for role in MEDIA_ROLES)),
            name="media_attachment_role_valid",
        ),
        CheckConstraint(
            "role <> 'company_logo' OR company_id IS NOT NULL",
            name="company_logo_attaches_to_company",
        ),
        CheckConstraint(
            "source_id IS NOT NULL AND raw_record_id IS NOT NULL AND scraped_at IS NOT NULL",
            name="media_attachment_provenance_complete",
        ),
        # One live assertion per source, entity and role. A source changing the
        # file supersedes the old claim instead of leaving two current logos.
        Index(
            "uq_media_attachments_live",
            *_ARC_COLUMNS,
            "role",
            "source_id",
            unique=True,
            postgresql_where=text("superseded_by IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
        # Partial indexes per arc column: each row sets only one, so a plain
        # index on a mostly-NULL column would be mostly wasted.
        *(
            Index(f"idx_media_attachments_{c}", c, postgresql_where=text(f"{c} IS NOT NULL"))
            for c in _ARC_COLUMNS
        ),
    )
