"""slugs are addresses, not identity

A slug is the public address of a page and nothing else. Nothing joins on it,
no registry keys on it, and no row may fail to exist because its address is
taken - which is what NOT NULL on these five spine columns enforced by
accident. A company or a configuration is created because a source asserted
it; the address arrives when one is available, and stays NULL when the name is
contested. `engines` and `transmissions` hold no rows yet and follow when the
ADR that starts minting them lands.

The unique constraints stay: an address that resolves to two rows is useless.
Postgres allows unlimited NULLs under a UNIQUE constraint, so unaddressed rows
coexist freely.

Revision ID: f3c81a4d6b27
Revises: b7d1a2c4e9f0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3c81a4d6b27"
down_revision: str | Sequence[str] | None = "b7d1a2c4e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ADDRESSED = ("companies", "models", "model_lines", "generations", "configurations")


def upgrade() -> None:
    for table in _ADDRESSED:
        op.alter_column(table, "slug", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Refuse rather than discard: a NULL slug is a real state (an admitted row
    # whose address a human has not settled), and inventing one to satisfy the
    # constraint would mint an address nobody chose.
    bind = op.get_bind()
    for table in _ADDRESSED:
        unaddressed = bind.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE slug IS NULL")  # noqa: S608
        ).scalar_one()
        if unaddressed:
            raise RuntimeError(
                f"{unaddressed} {table} rows have no slug; assign addresses "
                "before restoring NOT NULL"
            )
    for table in reversed(_ADDRESSED):
        op.alter_column(table, "slug", existing_type=sa.Text(), nullable=False)
