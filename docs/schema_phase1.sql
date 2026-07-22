-- ============================================================================
-- Global Vehicle Database — Phase 1 Schema (reference DDL)
-- ============================================================================
-- This file is the human-readable reference for the Phase 1 schema. It is NOT
-- the migration. Per CLAUDE.md, all schema changes land via Alembic
-- (auto-generated, hand-reviewed) — never raw ALTER TABLE in production.
-- This DDL exists so the Alembic baseline can be diffed against an intended
-- target, and so reviewers have one place to read the whole schema.
--
-- Scope decision (2026-06-15): Phase 1 is LEAN. Open Questions in PROGRESS.md
-- (parent_company_id, coachbuilders, concept/race flags, slug strategy) are
-- DEFERRED to their own ADRs and are NOT in this DDL. See docs/decisions/.
--
-- Conventions (from CLAUDE.md):
--   snake_case, plural table names, `id` PK, FK columns `<singular>_id`,
--   every fact-bearing table carries source_id / scraped_at / confidence_score,
--   every FK column is indexed.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector, for later semantic search
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- trigram, for name/slug fuzzy matching

CREATE SCHEMA IF NOT EXISTS raw_scrape;     -- untransformed source records, kept forever

-- ============================================================================
-- ENUM-LIKE LOOKUP / DIMENSION TABLES
-- Kept as tables (not native ENUMs) so values can be added without a migration
-- and can themselves carry provenance / aliases.
-- ============================================================================

CREATE TABLE market_regions (
    id              SERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,          -- e.g. 'US', 'EU', 'JDM', 'GLOBAL'
    name            TEXT NOT NULL,
    description     TEXT
);

CREATE TABLE body_styles (
    id              SERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,          -- e.g. 'sedan', 'wagon', 'coupe'
    name            TEXT NOT NULL,
    description     TEXT
);

CREATE TABLE drivetrains (
    id              SERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,          -- 'fwd', 'rwd', 'awd', '4wd'
    name            TEXT NOT NULL
);

CREATE TABLE fuel_types (
    id              SERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,          -- 'gasoline', 'diesel', 'bev', 'phev', 'hev'
    name            TEXT NOT NULL
);

CREATE TABLE transmission_types (
    id              SERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,          -- 'manual', 'automatic', 'dct', 'cvt'
    name            TEXT NOT NULL
);

-- ============================================================================
-- SOURCES — referenced by every fact-bearing row. Never hard-code URLs in code.
-- ============================================================================

CREATE TABLE sources (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,                 -- 'Wikidata', 'NHTSA vPIC', 'EPA fueleconomy.gov'
    tier            SMALLINT NOT NULL CHECK (tier BETWEEN 1 AND 4),
    base_url        TEXT,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- CORE ENTITY HIERARCHY: makes -> models -> generations -> model_years -> configurations
-- ============================================================================

CREATE TABLE makes (
    id              SERIAL PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    wikidata_qid    TEXT UNIQUE,                   -- universal join key where present
    country_code    TEXT,                          -- ISO 3166-1 alpha-2 of HQ, nullable
    founded_year    SMALLINT,
    defunct_year    SMALLINT,                      -- null = still active
    -- provenance
    source_id           INTEGER REFERENCES sources(id),
    scraped_at          TIMESTAMPTZ,
    confidence_score    NUMERIC(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    superseded_by       INTEGER REFERENCES makes(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_makes_source_id ON makes(source_id);
CREATE INDEX idx_makes_superseded_by ON makes(superseded_by);
CREATE INDEX idx_makes_name_trgm ON makes USING gin (name gin_trgm_ops);

CREATE TABLE models (
    id              SERIAL PRIMARY KEY,
    make_id         INTEGER NOT NULL REFERENCES makes(id),
    slug            TEXT NOT NULL,                 -- unique within make, see constraint
    name            TEXT NOT NULL,                 -- nameplate, e.g. '3 Series', 'Corolla'
    wikidata_qid    TEXT UNIQUE,
    -- provenance
    source_id           INTEGER REFERENCES sources(id),
    scraped_at          TIMESTAMPTZ,
    confidence_score    NUMERIC(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    superseded_by       INTEGER REFERENCES models(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (make_id, slug)
);
CREATE INDEX idx_models_make_id ON models(make_id);
CREATE INDEX idx_models_source_id ON models(source_id);
CREATE INDEX idx_models_superseded_by ON models(superseded_by);
CREATE INDEX idx_models_name_trgm ON models USING gin (name gin_trgm_ops);

CREATE TABLE generations (
    id              SERIAL PRIMARY KEY,
    model_id        INTEGER NOT NULL REFERENCES models(id),
    slug            TEXT NOT NULL,                 -- unique within model
    name            TEXT,                          -- e.g. 'E46', 'G80', 'XV70'
    chassis_codes   TEXT[],                        -- generations often span several codes
    start_year      SMALLINT,
    end_year        SMALLINT,                      -- null = still in production
    wikidata_qid    TEXT UNIQUE,
    -- provenance
    source_id           INTEGER REFERENCES sources(id),
    scraped_at          TIMESTAMPTZ,
    confidence_score    NUMERIC(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    superseded_by       INTEGER REFERENCES generations(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (model_id, slug)
);
CREATE INDEX idx_generations_model_id ON generations(model_id);
CREATE INDEX idx_generations_source_id ON generations(source_id);
CREATE INDEX idx_generations_superseded_by ON generations(superseded_by);

CREATE TABLE model_years (
    id              SERIAL PRIMARY KEY,
    generation_id   INTEGER NOT NULL REFERENCES generations(id),
    year            SMALLINT NOT NULL,
    -- provenance
    source_id           INTEGER REFERENCES sources(id),
    scraped_at          TIMESTAMPTZ,
    confidence_score    NUMERIC(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    superseded_by       INTEGER REFERENCES model_years(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (generation_id, year)
);
CREATE INDEX idx_model_years_generation_id ON model_years(generation_id);
CREATE INDEX idx_model_years_source_id ON model_years(source_id);
CREATE INDEX idx_model_years_superseded_by ON model_years(superseded_by);

-- ----------------------------------------------------------------------------
-- configurations — the atomic unit. (model_year + trim + market + drivetrain combo)
-- ~20 universal core spec columns live here; long-tail lives in configuration_attributes.
--
-- Core column selection (2026-06-15) is grounded in what the three Tier 1
-- sources reliably populate:
--   - NHTSA vPIC:        trim/series, body style, doors, drive type, engine basics
--   - EPA fueleconomy:   fuel type, displacement, cylinders, transmission, mpg/mpge, range
--   - Wikidata:          curb weight, seating, dimensions (sparser but present)
-- Anything below ~80% expected fill across configurations stays in EAV, per CLAUDE.md.
-- ----------------------------------------------------------------------------

CREATE TABLE configurations (
    id              SERIAL PRIMARY KEY,
    model_year_id   INTEGER NOT NULL REFERENCES model_years(id),
    market_region_id INTEGER REFERENCES market_regions(id),
    slug            TEXT NOT NULL,
    wikidata_qid    TEXT UNIQUE,

    -- identity / classification
    trim_name           TEXT,                      -- 'M340i', 'LE', 'GTI Autobahn'
    body_style_id       INTEGER REFERENCES body_styles(id),
    drivetrain_id       INTEGER REFERENCES drivetrains(id),
    doors               SMALLINT,
    seating_capacity    SMALLINT,

    -- powertrain summary (denormalized convenience; authoritative detail in
    -- engines/transmissions via the join tables below)
    fuel_type_id            INTEGER REFERENCES fuel_types(id),
    engine_displacement_cc  INTEGER,
    cylinders               SMALLINT,
    transmission_type_id    INTEGER REFERENCES transmission_types(id),

    -- performance / economy core
    power_hp                INTEGER,
    torque_nm               INTEGER,
    mpg_city                NUMERIC(5,1),
    mpg_highway             NUMERIC(5,1),
    mpg_combined            NUMERIC(5,1),
    mpge_combined           NUMERIC(5,1),          -- electrified
    electric_range_km       INTEGER,               -- BEV/PHEV

    -- physical core
    curb_weight_kg          INTEGER,
    length_mm               INTEGER,
    width_mm                INTEGER,
    height_mm               INTEGER,
    wheelbase_mm            INTEGER,

    -- launch pricing (only MSRP-at-launch is in scope per CLAUDE.md)
    msrp_launch_amount      NUMERIC(12,2),
    msrp_launch_currency    TEXT,                  -- ISO 4217

    -- provenance
    source_id           INTEGER REFERENCES sources(id),
    scraped_at          TIMESTAMPTZ,
    confidence_score    NUMERIC(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    superseded_by       INTEGER REFERENCES configurations(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (model_year_id, slug)
);
CREATE INDEX idx_configurations_model_year_id ON configurations(model_year_id);
CREATE INDEX idx_configurations_market_region_id ON configurations(market_region_id);
CREATE INDEX idx_configurations_body_style_id ON configurations(body_style_id);
CREATE INDEX idx_configurations_drivetrain_id ON configurations(drivetrain_id);
CREATE INDEX idx_configurations_fuel_type_id ON configurations(fuel_type_id);
CREATE INDEX idx_configurations_transmission_type_id ON configurations(transmission_type_id);
CREATE INDEX idx_configurations_source_id ON configurations(source_id);
CREATE INDEX idx_configurations_superseded_by ON configurations(superseded_by);

-- ============================================================================
-- EAV — long-tail / sparse attributes
-- ============================================================================

CREATE TABLE attribute_definitions (
    id              SERIAL PRIMARY KEY,
    key             TEXT NOT NULL UNIQUE,          -- canonical English key, e.g. 'turbo_count'
    label           TEXT NOT NULL,                 -- display label
    data_type       TEXT NOT NULL CHECK (data_type IN ('text','integer','numeric','boolean')),
    unit            TEXT,                          -- 'mm', 'kW', 'L', null
    validation_regex TEXT,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE configuration_attributes (
    id              BIGSERIAL PRIMARY KEY,
    configuration_id      INTEGER NOT NULL REFERENCES configurations(id),
    attribute_id    INTEGER NOT NULL REFERENCES attribute_definitions(id),
    -- one typed column populated per row, matching attribute_definitions.data_type
    value_text      TEXT,
    value_integer   BIGINT,
    value_numeric   NUMERIC,
    value_boolean   BOOLEAN,
    -- provenance
    source_id           INTEGER REFERENCES sources(id),
    scraped_at          TIMESTAMPTZ,
    confidence_score    NUMERIC(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    superseded_by       BIGINT REFERENCES configuration_attributes(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_configuration_attributes_configuration_id ON configuration_attributes(configuration_id);
CREATE INDEX idx_configuration_attributes_attribute_id ON configuration_attributes(attribute_id);
CREATE INDEX idx_configuration_attributes_source_id ON configuration_attributes(source_id);
CREATE INDEX idx_configuration_attributes_superseded_by ON configuration_attributes(superseded_by);
-- one live (non-superseded) value per (configuration, attribute)
CREATE UNIQUE INDEX uq_configuration_attribute_live
    ON configuration_attributes(configuration_id, attribute_id)
    WHERE superseded_by IS NULL;

-- ============================================================================
-- ENGINES & TRANSMISSIONS — first-class entities (cross-make reuse)
-- ============================================================================

CREATE TABLE engines (
    id              SERIAL PRIMARY KEY,
    manufacturer_make_id INTEGER REFERENCES makes(id),  -- maker of engine; may != car's make
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT,                          -- 'B58', 'LS3', '2JZ-GTE'
    family_code     TEXT,
    displacement_cc INTEGER,
    cylinders       SMALLINT,
    configuration   TEXT,                          -- 'inline', 'v', 'flat'
    aspiration      TEXT,                          -- 'na', 'turbo', 'twin-turbo', 'supercharged'
    fuel_type_id    INTEGER REFERENCES fuel_types(id),
    wikidata_qid    TEXT UNIQUE,
    -- provenance
    source_id           INTEGER REFERENCES sources(id),
    scraped_at          TIMESTAMPTZ,
    confidence_score    NUMERIC(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    superseded_by       INTEGER REFERENCES engines(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_engines_manufacturer_make_id ON engines(manufacturer_make_id);
CREATE INDEX idx_engines_fuel_type_id ON engines(fuel_type_id);
CREATE INDEX idx_engines_source_id ON engines(source_id);
CREATE INDEX idx_engines_superseded_by ON engines(superseded_by);

CREATE TABLE transmissions (
    id              SERIAL PRIMARY KEY,
    manufacturer_make_id INTEGER REFERENCES makes(id),
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT,                          -- 'ZF 8HP', 'Getrag 420G'
    transmission_type_id INTEGER REFERENCES transmission_types(id),
    gear_count      SMALLINT,
    wikidata_qid    TEXT UNIQUE,
    -- provenance
    source_id           INTEGER REFERENCES sources(id),
    scraped_at          TIMESTAMPTZ,
    confidence_score    NUMERIC(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    superseded_by       INTEGER REFERENCES transmissions(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_transmissions_manufacturer_make_id ON transmissions(manufacturer_make_id);
CREATE INDEX idx_transmissions_transmission_type_id ON transmissions(transmission_type_id);
CREATE INDEX idx_transmissions_source_id ON transmissions(source_id);
CREATE INDEX idx_transmissions_superseded_by ON transmissions(superseded_by);

-- many-to-many: a configuration can offer multiple engines/transmissions
CREATE TABLE configuration_engines (
    configuration_id      INTEGER NOT NULL REFERENCES configurations(id),
    engine_id       INTEGER NOT NULL REFERENCES engines(id),
    source_id           INTEGER REFERENCES sources(id),
    scraped_at          TIMESTAMPTZ,
    confidence_score    NUMERIC(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    PRIMARY KEY (configuration_id, engine_id)
);
CREATE INDEX idx_configuration_engines_engine_id ON configuration_engines(engine_id);
CREATE INDEX idx_configuration_engines_source_id ON configuration_engines(source_id);

CREATE TABLE configuration_transmissions (
    configuration_id      INTEGER NOT NULL REFERENCES configurations(id),
    transmission_id INTEGER NOT NULL REFERENCES transmissions(id),
    source_id           INTEGER REFERENCES sources(id),
    scraped_at          TIMESTAMPTZ,
    confidence_score    NUMERIC(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    PRIMARY KEY (configuration_id, transmission_id)
);
CREATE INDEX idx_configuration_transmissions_transmission_id ON configuration_transmissions(transmission_id);
CREATE INDEX idx_configuration_transmissions_source_id ON configuration_transmissions(source_id);
