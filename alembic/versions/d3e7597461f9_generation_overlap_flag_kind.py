"""generation_overlap flag kind

Adds `generation_overlap` to the reconciliation_flags kind CHECK
(hand-written - CHECK changes are an autogenerate blind spot, the
f3c645b9cb6f lesson).

Why the kind exists (ADR 0017 §3): a configuration whose period sits inside
TWO linked generations' spans must not be placed by the year alone - the
2019 AMG GT holds C190 coupes beside X290 4-doors. The flag carries the
candidates; placement stays NULL until finer evidence (body style, chassis
code) discriminates.

Revision ID: d3e7597461f9
Revises: c113fff36784
Create Date: 2026-08-06

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d3e7597461f9"
down_revision: str | Sequence[str] | None = "c113fff36784"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_KINDS = (
    "'field_conflict', 'multi_value', 'role_disagreement', 'admission_review', "
    "'source_dropped', 'implausible_value', 'match_review'"
)
_NEW_KINDS = _OLD_KINDS + ", 'generation_overlap'"
_CONSTRAINT = "ck_reconciliation_flags_kind_valid"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "reconciliation_flags")
    op.create_check_constraint("kind_valid", "reconciliation_flags", f"kind IN ({_NEW_KINDS})")


def downgrade() -> None:
    # Refuses (via CHECK re-add failing) if generation_overlap rows exist -
    # the f3c645b9cb6f posture: review state is never silently discarded.
    op.drop_constraint(_CONSTRAINT, "reconciliation_flags")
    op.create_check_constraint("kind_valid", "reconciliation_flags", f"kind IN ({_OLD_KINDS})")
