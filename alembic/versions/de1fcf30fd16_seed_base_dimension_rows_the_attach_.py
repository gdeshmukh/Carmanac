"""seed base dimension rows the attach passes consume

The EPA attach pass resolves configurations against the US market region
and the drivetrain / fuel-type / transmission-type dimensions. Those rows
existed in dev only as leftovers of the retired demo seed - the migration
chain never created them, which the test database exposed the first time a
pass depended on one. Lookups a pass consumes are migration-seeded (the
period_kinds precedent, 76cb287dd71c); ON CONFLICT DO NOTHING makes this a
no-op wherever the rows already exist.

Values mirror the live dev rows exactly. Downgrade deletes nothing: the
rows may have configurations pointing at them, and seed rows are harmless.

Revision ID: de1fcf30fd16
Revises: c2d02b91b922
Create Date: 2026-07-31

"""

from collections.abc import Sequence

from alembic import op

revision: str = "de1fcf30fd16"
down_revision: str | Sequence[str] | None = "c2d02b91b922"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO market_regions (code, name) VALUES
          ('US',  'United States'),
          ('EU',  'European Union'),
          ('JDM', 'Japan (domestic)')
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO drivetrains (code, name) VALUES
          ('fwd', 'Front-wheel drive'),
          ('rwd', 'Rear-wheel drive'),
          ('awd', 'All-wheel drive'),
          ('4wd', 'Four-wheel drive')
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO fuel_types (code, name) VALUES
          ('gasoline', 'Gasoline'),
          ('diesel',   'Diesel'),
          ('bev',      'Battery electric'),
          ('phev',     'Plug-in hybrid'),
          ('hev',      'Hybrid')
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO transmission_types (code, name) VALUES
          ('manual',    'Manual'),
          ('automatic', 'Automatic'),
          ('dct',       'Dual-clutch'),
          ('cvt',       'Continuously variable')
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    # Deliberately nothing: configurations may reference these rows, and a
    # seed row's presence is never wrong.
    pass
