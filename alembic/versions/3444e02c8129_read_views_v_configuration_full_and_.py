"""read views: v_configuration_full and coverage

Hand-written: views are invisible to autogenerate. These are the first
pieces of the read surface (F2) — plain views, no storage, recomputed per
query. `v_configuration_full` is the one-name answer to "a full car row":
the spine joined company → model → period → configuration with the lookups
resolved and the ADR 0019 address composed. The coverage views are the
shape questions asked while filling the database — which models have cars,
which are empty shells, how much is placed.

The address column composes only for `model_year` periods with every slug
present; anything else is NULL, matching the route grammar's current scope
(non-model-year periods have no segment grammar yet) and the rule that an
unaddressed row is honest, not broken.

Revision ID: 3444e02c8129
Revises: c5d90b3e7a41
Create Date: 2026-08-12
"""

from __future__ import annotations

from alembic import op

revision = "3444e02c8129"
down_revision = "c5d90b3e7a41"
branch_labels = None
depends_on = None

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

MODEL_COVERAGE = """
CREATE VIEW v_model_coverage AS
SELECT
    m.id            AS model_id,
    co.slug         AS company_slug,
    m.slug          AS model_slug,
    m.name          AS model_name,
    count(DISTINCT cp.id)                                   AS periods,
    min(cp.start_year)                                      AS first_year,
    max(coalesce(cp.end_year, cp.start_year))               AS last_year,
    count(DISTINCT cp.id) FILTER (WHERE c.id IS NOT NULL)   AS periods_with_cars,
    count(c.id)                                             AS configurations,
    count(c.id) FILTER (WHERE c.generation_id IS NOT NULL)  AS placed
FROM models m
JOIN companies co              ON co.id = m.company_id
LEFT JOIN catalogue_periods cp ON cp.model_id = m.id
LEFT JOIN configurations c     ON c.catalogue_period_id = cp.id
GROUP BY m.id, co.slug, m.slug, m.name
"""

COMPANY_COVERAGE = """
CREATE VIEW v_company_coverage AS
SELECT
    co.id           AS company_id,
    co.slug         AS company_slug,
    co.name         AS company_name,
    count(DISTINCT m.id)                                    AS models,
    count(DISTINCT m.id) FILTER (WHERE c.id IS NOT NULL)    AS models_with_cars,
    count(c.id)                                             AS configurations,
    count(c.id) FILTER (WHERE c.generation_id IS NOT NULL)  AS placed
FROM companies co
LEFT JOIN models m             ON m.company_id = co.id
LEFT JOIN catalogue_periods cp ON cp.model_id = m.id
LEFT JOIN configurations c     ON c.catalogue_period_id = cp.id
GROUP BY co.id, co.slug, co.name
"""


def upgrade() -> None:
    op.execute(CONFIGURATION_FULL)
    op.execute(MODEL_COVERAGE)
    op.execute(COMPANY_COVERAGE)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_company_coverage")
    op.execute("DROP VIEW IF EXISTS v_model_coverage")
    op.execute("DROP VIEW IF EXISTS v_configuration_full")
