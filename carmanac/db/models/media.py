"""Images and documents attached to entities.

Two tables rather than one column, because media is many-to-many with almost
everything: a car page wants several photos, one photo may legitimately
illustrate a generation *and* a specific configuration, and an owner's manual
often covers a whole model year rather than one trim.

    media_assets       the file itself, once, with its licence
    media_attachments  which entities it belongs to, and in what role

**Licensing is a first-class column, not an afterthought.** Most car photography
is not freely reusable, and a database that stores an image without recording
what it is allowed to do with it cannot safely publish any of it. `license` and
`attribution` are therefore required on any asset intended for display.

**Files live in object storage, not in Postgres.** `storage_url` is the pointer;
`source_url` records where it came from. This mirrors the `storage_url` escape
hatch already reserved on `raw_scrape.raw_records` (ADR 0003) - the database
holds metadata and provenance, the bytes live where bytes belong.
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
    UniqueConstraint,
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
    "logo",
    "owners_manual",
    "brochure",
    "press_kit",
    "spec_sheet",
)


class MediaAsset(Base, ProvenanceMixin):
    """One file, stored once regardless of how many entities reference it.

    Fact-bearing: an asset is something a source published, so it carries the
    provenance quartet and links back to the scrape that found it.
    """

    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    kind: Mapped[str] = mapped_column(Text, nullable=False)  # image | document
    mime_type: Mapped[str | None] = mapped_column(Text)  # 'image/jpeg', 'application/pdf'

    storage_url: Mapped[str | None] = mapped_column(Text)  # our copy, in object storage
    source_url: Mapped[str | None] = mapped_column(Text)  # where we found it

    title: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)

    # --- licensing: required before anything may be displayed ---
    license: Mapped[str | None] = mapped_column(Text)  # 'CC-BY-SA-4.0', 'press-use', ...
    attribution: Mapped[str | None] = mapped_column(Text)  # credit line to display

    # --- format specifics ---
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(SmallInteger)
    byte_size: Mapped[int | None] = mapped_column(BigInteger)

    # Same idea as raw_records: identical bytes need storing only once, and
    # re-scraping an unchanged asset should be a no-op.
    content_hash: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *provenance_table_args(),
        CheckConstraint("kind IN ('image','document')", name="media_kind_valid"),
        # An asset with neither a stored copy nor a source is unusable.
        CheckConstraint(
            "storage_url IS NOT NULL OR source_url IS NOT NULL", name="media_has_location"
        ),
        Index("idx_media_assets_content_hash", "content_hash"),
        Index("idx_media_assets_kind", "kind"),
    )


class MediaAttachment(Base):
    """Which entities an asset belongs to, and how it should be presented.

    Uses the same exclusive arc as `field_provenance` and `external_ids` - one
    nullable FK per entity type plus a CHECK that exactly one is set - so
    referential integrity survives, unlike a polymorphic entity_type/entity_id
    pair.

    Not fact-bearing itself: the *asset* carries provenance. This row only says
    where it is shown.
    """

    __tablename__ = "media_attachments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    media_asset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # --- exclusive arc: exactly one of these is set ---
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id"))
    generation_id: Mapped[int | None] = mapped_column(ForeignKey("generations.id"))
    catalogue_period_id: Mapped[int | None] = mapped_column(ForeignKey("catalogue_periods.id"))
    configuration_id: Mapped[int | None] = mapped_column(ForeignKey("configurations.id"))
    engine_id: Mapped[int | None] = mapped_column(ForeignKey("engines.id"))
    transmission_id: Mapped[int | None] = mapped_column(ForeignKey("transmissions.id"))

    role: Mapped[str | None] = mapped_column(Text)  # hero | gallery | owners_manual | ...
    sort_order: Mapped[int | None] = mapped_column(SmallInteger)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        _exactly_one_entity(),
        # The same asset should not attach to the same entity twice in the same
        # role. NULLS NOT DISTINCT because six of the seven arc columns are NULL
        # on every row.
        UniqueConstraint(
            "media_asset_id",
            *_ARC_COLUMNS,
            "role",
            name="uq_media_attachments_asset_entity_role",
            postgresql_nulls_not_distinct=True,
        ),
        # Partial indexes per arc column: each row sets only one, so a plain
        # index on a mostly-NULL column would be mostly wasted.
        *(
            Index(f"idx_media_attachments_{c}", c, postgresql_where=text(f"{c} IS NOT NULL"))
            for c in _ARC_COLUMNS
        ),
    )
