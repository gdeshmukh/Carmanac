"""match_review flag kind

Adds `match_review` (ADR 0008: a source record with zero or several match
candidates against `companies`) to the reconciliation_flags kind CHECK, and
widens the shape CHECK: match_review is the second RECORD-scoped kind - like
admission_review, there may be no entity to attach to, so the arc is empty
and `raw_record_id` is required.

Hand-written: CHECK edits are invisible to autogenerate (the established
blind-spot list: inline CHECKs, triggers, PKs, and constraint changes).

Revision ID: 613bdd40c0bc
Revises: f3c645b9cb6f
Create Date: 2026-07-29 12:28:13.049494

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "613bdd40c0bc"
down_revision: str | Sequence[str] | None = "f3c645b9cb6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ARC = (
    "company_id, model_id, generation_id, model_year_id, "
    "configuration_id, engine_id, transmission_id"
)

_OLD_KINDS = (
    "'field_conflict', 'multi_value', 'role_disagreement', 'admission_review', "
    "'source_dropped', 'implausible_value'"
)
_NEW_KINDS = _OLD_KINDS + ", 'match_review'"

_OLD_SHAPE = (
    f"(kind = 'admission_review' AND num_nonnulls({_ARC}) = 0 AND raw_record_id IS NOT NULL)"
    f" OR (kind <> 'admission_review' AND num_nonnulls({_ARC}) = 1)"
)
_NEW_SHAPE = (
    f"(kind IN ('admission_review', 'match_review')"
    f" AND num_nonnulls({_ARC}) = 0 AND raw_record_id IS NOT NULL)"
    f" OR (kind NOT IN ('admission_review', 'match_review')"
    f" AND num_nonnulls({_ARC}) = 1)"
)


def upgrade() -> None:
    op.drop_constraint("ck_reconciliation_flags_kind_valid", "reconciliation_flags")
    op.create_check_constraint(
        "kind_valid", "reconciliation_flags", f"kind IN ({_NEW_KINDS})"
    )
    op.drop_constraint("ck_reconciliation_flags_flag_shape_matches_kind", "reconciliation_flags")
    op.create_check_constraint(
        "flag_shape_matches_kind", "reconciliation_flags", _NEW_SHAPE
    )


def downgrade() -> None:
    # Re-adding the narrower CHECKs fails if match_review rows exist - delete
    # or resolve them first; silently discarding review state is worse.
    op.drop_constraint("ck_reconciliation_flags_flag_shape_matches_kind", "reconciliation_flags")
    op.create_check_constraint(
        "flag_shape_matches_kind", "reconciliation_flags", _OLD_SHAPE
    )
    op.drop_constraint("ck_reconciliation_flags_kind_valid", "reconciliation_flags")
    op.create_check_constraint(
        "kind_valid", "reconciliation_flags", f"kind IN ({_OLD_KINDS})"
    )
