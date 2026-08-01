"""seed sequential, cng, hydrogen, and the powertrain EAV keys

ADR 0015: rows the EPA attach pass consumes, migration-seeded (the
de1fcf30fd16 precedent). `sequential` lands dormant on Gaurav's ruling -
EPA will likely never file one, but the type exists for sources that do;
the deliberately ABSENT row is any automated-manual bucket, because EPA
cannot distinguish single-clutch from dual-clutch and no bucket word may
erase that difference. cng/hydrogen complete the fuel map for EPA's 102
Natural Gas / Hydrogen rows. The two attribute_definitions keys register
the EAV facts the pass writes (charter rule: registration before landing).

Downgrade deletes nothing: seed rows may be referenced and are harmless.

Revision ID: ea565b7ea489
Revises: de1fcf30fd16
Create Date: 2026-07-31

"""

from collections.abc import Sequence

from alembic import op

revision: str = "ea565b7ea489"
down_revision: str | Sequence[str] | None = "de1fcf30fd16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO transmission_types (code, name) VALUES
          ('sequential', 'Sequential')
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO fuel_types (code, name) VALUES
          ('cng',      'Natural gas'),
          ('hydrogen', 'Hydrogen')
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO attribute_definitions (key, label, data_type, validation_regex, description)
        VALUES
          ('aspiration', 'Aspiration', 'text', '^(na|turbo|twin-turbo|supercharged|twincharged)$',
           'Forced-induction kind. EPA asserts only turbo/supercharged (its flags cannot '
           'see twin-turbo, and natural aspiration is an absence, not an assertion); the '
           'wider value set is for sources that can.'),
          ('flex_fuel', 'Flex fuel', 'boolean', NULL,
           'Runs on E85/gasoline blends (EPA FFV designation).')
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    # Deliberately nothing: rows may be referenced, and a seed row's
    # presence is never wrong.
    pass
