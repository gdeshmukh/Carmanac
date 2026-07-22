# Project: Global Vehicle Database

## Mission

Build a centralized, relational database covering **every production passenger vehicle ever made, globally** — every make, model, generation, model year, and configuration, across all markets and eras. Continuous ingestion from authoritative and enthusiast sources, reconciled into a single canonical record per configuration. Served via a fast, intuitive web frontend with dedicated pages for makes, models, generations, configurations, and engines.

This is a long-horizon project (months to years) intended as a portfolio centerpiece demonstrating data engineering, entity resolution at scale, relational schema design, and full-stack delivery. Scope is intentionally ambitious — that is the novelty.

## Current Phase

**Phase 0: Foundation setup** — repo scaffolding, schema design, tooling decisions. See `PROGRESS.md` for live status.

Next planned phase: Phase 1 — schema implementation + Tier 1 source ingestion (Wikidata + NHTSA + EPA).

## Architecture Invariants

These are settled decisions. Do not propose changes without flagging them explicitly as a decision to revisit.

- **Postgres is the source of truth.** `pgvector` extension for semantic search.
- **Five-level entity hierarchy**: `makes` → `models` → `generations` → `model_years` → `configurations`. Every spec-bearing row ultimately foreign-keys back to `configurations`.
- **Engines and transmissions are first-class entities** with their own tables. Cross-make reuse (BMW B58 in Toyota Supra, GM LS swaps, etc.) makes this non-negotiable.
- **Hybrid storage model**: ~20 universal core specs live as columns on `configurations`. Long-tail/sparse attributes live in an EAV table (`configuration_attributes`). New attributes are registered in `attribute_definitions` before any data lands.
- **Provenance attaches to facts, not identity** (ADR 0002). Fact-bearing rows carry `source_id` / `scraped_at` / `confidence_score` (EAV `configuration_attributes`, the association tables) or field-level provenance in `field_provenance`. Entity/identity tables (`makes` … `configurations`, `engines`, `transmissions`) carry no provenance — they hold the reconciled current value and are upserted by natural key. Supersession lives with the facts, never on identity rows.
- **Raw scrape data is never discarded.** Separate `raw_scrape` schema holds untransformed source records permanently (`raw_scrape.raw_records`); every fact carries a `raw_record_id` back to the exact scrape, for re-reconciliation when matching logic improves.
- **Wikidata QID is the universal join key** wherever a vehicle entity has one — stored in `external_ids` alongside every other source's identifiers (ADR 0003), not as a per-table column.

## Schema Overview

Core tables (Phase 1 target):

- `makes` — manufacturers/brands. Top-level entity, has its own page.
- `models` — nameplates under a make. FK → `makes`.
- `generations` — generation of a model (E46, G80, etc.). FK → `models`. Holds chassis codes.
- `model_years` — specific year within a generation. FK → `generations`.
- `configurations` — atomic unit (year + trim + market + drivetrain combo). FK → `model_years` + `market_regions`.
- `engines` — engine entities. FK → `makes` (manufacturer of engine, may differ from car's make).
- `transmissions` — transmission entities.
- `configuration_engines`, `configuration_transmissions` — many-to-many join tables.
- `market_regions`, `body_styles`, `drivetrains`, `transmission_types`, `fuel_types` — dimension/lookup tables.
- `sources` — every data source (URL, tier, scraped_at). Referenced by every fact.
- `configuration_attributes` — EAV for long-tail specs.
- `attribute_definitions` — registry of legal EAV keys with units, types, validation.
- `field_provenance` — field-level provenance for entity/spec columns (ADR 0002).
- `external_ids` — `(source, external_id)` → entity mapping, incl. Wikidata QIDs (ADR 0003).
- `raw_scrape.raw_records` — permanent untransformed scrape landing zone (ADR 0003).

Reference DDL lives in `docs/schema_phase1.sql`; rationale in `docs/schema.md`. **Note:** the SQLAlchemy models in `carmanac/db/models/` are now the source of schema truth (the applied Alembic baseline + revision `00531f09d08f`); `docs/schema_phase1.sql` predates ADR 0002/0003 and is stale pending reconciliation (see PROGRESS.md Open Questions). Leaf entity renamed `variants` → `configurations` (ADR 0001).

## Source Tiering

Sources are tiered by authority. Conflicts resolve by tier first, then recency, then flag for review.

- **Tier 1 (authoritative, structured):** NHTSA vPIC API, EPA fueleconomy.gov bulk data, EU type approval data, Japan MLIT, manufacturer press kits/media sites, Wikidata.
- **Tier 2 (structured secondhand):** Wikipedia (multilingual — EN, DE, JA, IT critical), Edmunds, KBB, Car and Driver / MotorTrend archives.
- **Tier 3 (unstructured enthusiast):** Marque-specific wikis (BimmerWiki, Toyota Wiki, etc.), forum spec threads, club archives. Critical for pre-2000, JDM, and Euro-only configurations.
- **Tier 4 (visual/PDF):** OCR'd brochures from archive.org, manufacturer historical PDFs.

## Tech Stack

- **Database**: Postgres + pgvector. Migrations via Alembic — never raw `ALTER TABLE`.
- **Ingestion**: Scrapy for structured sites; Playwright fallback for JS-heavy sources.
- **Orchestration**: Dagster.
- **API**: FastAPI.
- **Frontend**: Next.js + Tailwind + shadcn/ui. Deployed on Vercel.
- **Language**: Python 3.11+ for backend/scrapers, TypeScript for frontend.

## Conventions

- **Python**: ruff for lint/format, type hints required, pydantic for I/O validation.
- **SQL**: `snake_case`, plural table names, `id` as PK, FK columns named `<singular>_id`.
- **Scrapers** live in `scrapers/<source_name>/`. One directory per source.
- **Migrations** via Alembic only. Auto-generated, then hand-reviewed.
- **Commits**: Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`).
- **Decisions**: Significant decisions get an ADR in `docs/decisions/` before implementation.

## URL / Page Structure

The frontend route map mirrors the entity hierarchy (public slug for the leaf is pending the slug-strategy ADR; it need not literally be `configurations`):

- `/makes/<make-slug>` — make page
- `/makes/<make-slug>/<model-slug>` — model page
- `/makes/<make-slug>/<model-slug>/<generation-slug>` — generation page
- `/configurations/<configuration-slug-or-id>` — configuration detail
- `/engines/<engine-slug>` — engine detail + list of configurations using it
- `/compare?configurations=a,b,c` — comparison view

## What Claude Should Always Do

- Read `PROGRESS.md` before suggesting next steps or claiming context.
- Treat Architecture Invariants above as settled — flag explicitly if proposing to revisit one.
- Write an ADR in `docs/decisions/` for any significant new decision before implementation.
- Respect rate limits and identify the scraper bot honestly in user-agent strings.
- Index foreign key columns. Always.
- Include `source_id`, `scraped_at`, `confidence_score` columns on any new *fact-bearing* table (not identity/entity tables — see the provenance invariant and ADR 0002).

## What Claude Should Never Do

- Propose adding a spec as a column on `configurations` for something that should be EAV (rule of thumb: if <80% of configurations would have a value, it's EAV).
- Drop or restructure EAV in favor of new columns without an explicit ask.
- Hard-code source URLs in business logic — sources go in the `sources` table.
- Scrape commercial sites without rate limiting, identification, and respect for robots.txt.
- Throw away raw scrape data after transformation.
- Suggest "all cars" scope-narrowing — the global scope is the explicit point of the project.

## Out-of-Scope (current phase)

- Commercial vehicles (trucks >class 3, buses) — passenger only for now.
- Motorcycles.
- Pricing data beyond original MSRP at launch.
- User accounts / saved lists / any social features.
