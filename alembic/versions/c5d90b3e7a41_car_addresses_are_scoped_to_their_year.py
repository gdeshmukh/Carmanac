"""car addresses are scoped to their model year

The route is /<company>/<model>/<year>/<car>, so a car's slug is only the
tail - what separates it from its siblings in that model year. Tails repeat
across the fleet by design (`fwd` fits 3,161 cars), so global uniqueness is
not merely unnecessary, it is impossible: 23,523 configurations compose 2,588
distinct tails.

What the address actually promises is that one URL resolves to one row, and
the URL carries the period. So the constraint moves to (period, slug).

Revision ID: c5d90b3e7a41
Revises: f3c81a4d6b27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d90b3e7a41"
down_revision: str | Sequence[str] | None = "f3c81a4d6b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_configurations_slug", "configurations", type_="unique")
    op.create_unique_constraint(
        "uq_configurations_period_slug", "configurations", ["catalogue_period_id", "slug"]
    )


def downgrade() -> None:
    # Refuse rather than mangle: restoring global uniqueness over tails would
    # fail on the first duplicate, and there are 22,751 of them. The addresses
    # have to be recomposed under the old grammar first.
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT count(*) FROM (SELECT slug FROM configurations "
            "WHERE slug IS NOT NULL GROUP BY slug HAVING count(*) > 1) d"
        )
    ).scalar_one()
    if duplicates:
        raise RuntimeError(
            f"{duplicates} car addresses are only unique within their model year; "
            "recompose them globally before restoring the global constraint"
        )
    op.drop_constraint("uq_configurations_period_slug", "configurations", type_="unique")
    op.create_unique_constraint("uq_configurations_slug", "configurations", ["slug"])
