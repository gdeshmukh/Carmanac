"""implausible_value flag kind

Adds `implausible_value` to the reconciliation_flags kind CHECK. Hand-written:
Alembic autogenerate cannot see CHECK-constraint changes (the same blind spot
as inline CHECKs, triggers, and primary keys - see the 2026-07-22 and
d212a042caa7 lessons), so this constraint swap would otherwise silently never
reach the database.

Why the kind exists: a single wrong claim projects with no flag at all -
multi_value only fires on disagreement, and Mercedes-AMG's lone "founded
1812" claim sailed through. Plausibility rules at projection open this flag
while the value still projects tentatively (ADR 0007 §6.4).

Revision ID: f3c645b9cb6f
Revises: 111a7cd329b8
Create Date: 2026-07-29 12:04:08.155399

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3c645b9cb6f"
down_revision: str | Sequence[str] | None = "111a7cd329b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_KINDS = "'field_conflict', 'multi_value', 'role_disagreement', 'admission_review', 'source_dropped'"
_NEW_KINDS = _OLD_KINDS + ", 'implausible_value'"
_CONSTRAINT = "ck_reconciliation_flags_kind_valid"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "reconciliation_flags")
    op.create_check_constraint(
        "kind_valid", "reconciliation_flags", f"kind IN ({_NEW_KINDS})"
    )


def downgrade() -> None:
    # Refuses (via CHECK re-add failing) if implausible_value rows exist -
    # delete or resolve them first; a silent DELETE here would discard review
    # state the operator may not know about.
    op.drop_constraint(_CONSTRAINT, "reconciliation_flags")
    op.create_check_constraint(
        "kind_valid", "reconciliation_flags", f"kind IN ({_OLD_KINDS})"
    )
