"""sources: the LLM read, and its review flag kind (ADR 0017, amended 2026-09-17)

Hand-written. Reference data for the source whose records are a model's
reading of one landed page - tier 3, derived from a tier 2 page, every fact
gated on a quote from it - and `llm_placement_review` in the flag kind
CHECK, one flag per leaf a read places (CHECK changes are an autogenerate
blind spot).

Revision ID: 5c1e9f3a7b2d
Revises: b7c1d4e9f2a3
Create Date: 2026-09-17
"""

from __future__ import annotations

from alembic import op

revision = "5c1e9f3a7b2d"
down_revision = "b7c1d4e9f2a3"
branch_labels = None
depends_on = None

_OLD_KINDS = (
    "'field_conflict', 'multi_value', 'role_disagreement', 'admission_review', "
    "'source_dropped', 'implausible_value', 'match_review', 'generation_overlap', "
    "'section_generation_review'"
)
_NEW_KINDS = _OLD_KINDS + ", 'llm_placement_review'"
_CONSTRAINT = "ck_reconciliation_flags_kind_valid"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "reconciliation_flags")
    op.create_check_constraint("kind_valid", "reconciliation_flags", f"kind IN ({_NEW_KINDS})")
    op.execute(
        """
        INSERT INTO sources (name, tier, base_url, description)
        SELECT 'LLM read', 3, 'https://openrouter.ai',
               'A model''s reading of one landed page: its generations, the cars in each and the configurations they are, every item quoted from the page'
        WHERE NOT EXISTS (SELECT 1 FROM sources WHERE name = 'LLM read')
        """
    )


def downgrade() -> None:
    # Refuses (via CHECK re-add failing) while review rows exist.
    op.drop_constraint(_CONSTRAINT, "reconciliation_flags")
    op.create_check_constraint("kind_valid", "reconciliation_flags", f"kind IN ({_OLD_KINDS})")
    op.execute("DELETE FROM sources WHERE name = 'LLM read'")
