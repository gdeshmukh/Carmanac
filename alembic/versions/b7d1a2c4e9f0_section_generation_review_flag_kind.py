"""section_generation_review flag kind

Adds `section_generation_review` to the reconciliation_flags kind CHECK
(hand-written - CHECK changes are an autogenerate blind spot, the
f3c645b9cb6f lesson).

Why the kind exists (ADR 0017 §4): section-minting is all-or-nothing per
article - a duplicate or non-contiguous ordinal, a slug collision, or a
section that reconciles to none of the model's existing generations means
the whole article waits for review, because minting the parseable sections
while skipping one would hide a real competitor from the placement guards.

Revision ID: b7d1a2c4e9f0
Revises: d3e7597461f9
Create Date: 2026-08-07

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7d1a2c4e9f0"
down_revision: str | Sequence[str] | None = "d3e7597461f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_KINDS = (
    "'field_conflict', 'multi_value', 'role_disagreement', 'admission_review', "
    "'source_dropped', 'implausible_value', 'match_review', 'generation_overlap'"
)
_NEW_KINDS = _OLD_KINDS + ", 'section_generation_review'"
_CONSTRAINT = "ck_reconciliation_flags_kind_valid"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "reconciliation_flags")
    op.create_check_constraint("kind_valid", "reconciliation_flags", f"kind IN ({_NEW_KINDS})")


def downgrade() -> None:
    # Refuses (via CHECK re-add failing) if section_generation_review rows
    # exist - review state is never silently discarded.
    op.drop_constraint(_CONSTRAINT, "reconciliation_flags")
    op.create_check_constraint("kind_valid", "reconciliation_flags", f"kind IN ({_OLD_KINDS})")
