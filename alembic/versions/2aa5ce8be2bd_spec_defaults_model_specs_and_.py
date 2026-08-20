"""spec defaults: model_specs and generation_specs

Hand-written (a view change rides along, invisible to autogenerate). Two 1:1
sibling tables hold physical spec values at the grain a source honestly
states them - the nameplate as a whole, or one era. Configurations inherit
them at read time: `v_configuration_full` resolves each spec column
configuration-first, then generation, then model. A configuration with no
generation inherits straight from its model - generations stay outside the
four-level hierarchy. Provenance rides the existing model_id/generation_id
arcs in field_provenance; no bookkeeping change.

Revision ID: 2aa5ce8be2bd
Revises: 1046d362efc2
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2aa5ce8be2bd"
down_revision = "1046d362efc2"
branch_labels = None
depends_on = None

SPEC_COLUMNS = [
    ("length_mm", sa.Integer),
    ("width_mm", sa.Integer),
    ("height_mm", sa.Integer),
    ("wheelbase_mm", sa.Integer),
    ("curb_weight_kg", sa.Integer),
    ("doors", sa.SmallInteger),
    ("seating_capacity", sa.SmallInteger),
    ("power_hp", sa.Integer),
    ("torque_nm", sa.Integer),
]

CONFIGURATION_FULL = """
CREATE VIEW v_configuration_full AS
SELECT
    c.id                    AS configuration_id,
    co.id                   AS company_id,
    co.slug                 AS company_slug,
    co.name                 AS company_name,
    m.id                    AS model_id,
    m.slug                  AS model_slug,
    m.name                  AS model_name,
    cp.id                   AS catalogue_period_id,
    pk.code                 AS period_kind,
    cp.start_year,
    cp.end_year,
    c.slug                  AS car_slug,
    CASE
        WHEN pk.code = 'model_year'
             AND co.slug IS NOT NULL AND m.slug IS NOT NULL AND c.slug IS NOT NULL
        THEN '/' || co.slug || '/' || m.slug || '/' || cp.start_year::text || '/' || c.slug
    END                     AS address,
    c.trim_name,
    mr.code                 AS market,
    dt.code                 AS drivetrain,
    bs.name                 AS body_style,
    COALESCE(c.doors, gs.doors, ms.doors)                                  AS doors,
    COALESCE(c.seating_capacity, gs.seating_capacity, ms.seating_capacity) AS seating_capacity,
    g.id                    AS generation_id,
    g.slug                  AS generation_slug,
    g.name                  AS generation_name,
    g.chassis_codes,
    ft.name                 AS fuel_type,
    tt.name                 AS transmission_type,
    c.engine_displacement_cc,
    c.cylinders,
    COALESCE(c.power_hp, gs.power_hp, ms.power_hp)                         AS power_hp,
    COALESCE(c.torque_nm, gs.torque_nm, ms.torque_nm)                      AS torque_nm,
    c.mpg_city,
    c.mpg_highway,
    c.mpg_combined,
    c.mpge_combined,
    c.electric_range_km,
    COALESCE(c.curb_weight_kg, gs.curb_weight_kg, ms.curb_weight_kg)       AS curb_weight_kg,
    COALESCE(c.length_mm, gs.length_mm, ms.length_mm)                      AS length_mm,
    COALESCE(c.width_mm, gs.width_mm, ms.width_mm)                         AS width_mm,
    COALESCE(c.height_mm, gs.height_mm, ms.height_mm)                      AS height_mm,
    COALESCE(c.wheelbase_mm, gs.wheelbase_mm, ms.wheelbase_mm)             AS wheelbase_mm
FROM configurations c
JOIN catalogue_periods cp        ON cp.id = c.catalogue_period_id
JOIN period_kinds pk             ON pk.id = cp.period_kind_id
JOIN models m                    ON m.id = cp.model_id
JOIN companies co                ON co.id = m.company_id
JOIN market_regions mr           ON mr.id = c.market_region_id
LEFT JOIN drivetrains dt         ON dt.id = c.drivetrain_id
LEFT JOIN body_styles bs         ON bs.id = c.body_style_id
LEFT JOIN fuel_types ft          ON ft.id = c.fuel_type_id
LEFT JOIN transmission_types tt  ON tt.id = c.transmission_type_id
LEFT JOIN generations g          ON g.id = c.generation_id
LEFT JOIN generation_specs gs    ON gs.generation_id = c.generation_id
LEFT JOIN model_specs ms         ON ms.model_id = m.id
"""

# The pre-specs shape, restored on downgrade (from revision 3444e02c8129).
CONFIGURATION_FULL_PREVIOUS = """
CREATE VIEW v_configuration_full AS
SELECT
    c.id                    AS configuration_id,
    co.id                   AS company_id,
    co.slug                 AS company_slug,
    co.name                 AS company_name,
    m.id                    AS model_id,
    m.slug                  AS model_slug,
    m.name                  AS model_name,
    cp.id                   AS catalogue_period_id,
    pk.code                 AS period_kind,
    cp.start_year,
    cp.end_year,
    c.slug                  AS car_slug,
    CASE
        WHEN pk.code = 'model_year'
             AND co.slug IS NOT NULL AND m.slug IS NOT NULL AND c.slug IS NOT NULL
        THEN '/' || co.slug || '/' || m.slug || '/' || cp.start_year::text || '/' || c.slug
    END                     AS address,
    c.trim_name,
    mr.code                 AS market,
    dt.code                 AS drivetrain,
    bs.name                 AS body_style,
    c.doors,
    c.seating_capacity,
    g.id                    AS generation_id,
    g.slug                  AS generation_slug,
    g.name                  AS generation_name,
    g.chassis_codes,
    ft.name                 AS fuel_type,
    tt.name                 AS transmission_type,
    c.engine_displacement_cc,
    c.cylinders,
    c.power_hp,
    c.torque_nm,
    c.mpg_city,
    c.mpg_highway,
    c.mpg_combined,
    c.mpge_combined,
    c.electric_range_km,
    c.curb_weight_kg,
    c.length_mm,
    c.width_mm,
    c.height_mm,
    c.wheelbase_mm
FROM configurations c
JOIN catalogue_periods cp        ON cp.id = c.catalogue_period_id
JOIN period_kinds pk             ON pk.id = cp.period_kind_id
JOIN models m                    ON m.id = cp.model_id
JOIN companies co                ON co.id = m.company_id
JOIN market_regions mr           ON mr.id = c.market_region_id
LEFT JOIN drivetrains dt         ON dt.id = c.drivetrain_id
LEFT JOIN body_styles bs         ON bs.id = c.body_style_id
LEFT JOIN fuel_types ft          ON ft.id = c.fuel_type_id
LEFT JOIN transmission_types tt  ON tt.id = c.transmission_type_id
LEFT JOIN generations g          ON g.id = c.generation_id
"""


def _spec_table(name: str, fk_column: str, fk_target: str) -> None:
    op.create_table(
        name,
        sa.Column(fk_column, sa.Integer(), nullable=False),
        *[sa.Column(col, kind(), nullable=True) for col, kind in SPEC_COLUMNS],
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(fk_column, name=op.f(f"pk_{name}")),
        sa.ForeignKeyConstraint(
            [fk_column], [fk_target], name=op.f(f"fk_{name}_{fk_column}_{fk_target.split('.')[0]}")
        ),
    )
    op.execute(
        f"CREATE TRIGGER trg_{name}_set_updated_at BEFORE UPDATE ON {name} "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def upgrade() -> None:
    _spec_table("model_specs", "model_id", "models.id")
    _spec_table("generation_specs", "generation_id", "generations.id")
    op.execute("DROP VIEW IF EXISTS v_configuration_full")
    op.execute(CONFIGURATION_FULL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_configuration_full")
    op.execute(CONFIGURATION_FULL_PREVIOUS)
    op.drop_table("generation_specs")
    op.drop_table("model_specs")
