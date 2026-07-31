# Carmanac — Project Charter

Mission, architecture invariants, and working rules for the project. This is the
document to read first; [PROGRESS.md](../PROGRESS.md) is the living log of where
work actually stands.

## Mission

Build a centralized, relational database covering **every production passenger
vehicle ever made, globally** — every make, model, generation, model year, and
configuration, across all markets and eras. Continuous ingestion from
authoritative and enthusiast sources, reconciled into a single canonical record
per configuration. Served via a fast, intuitive web frontend with dedicated
pages for makes, models, generations, configurations, and engines.

This is a long-horizon project (months to years) intended as a portfolio
centerpiece demonstrating data engineering, entity resolution at scale,
relational schema design, and full-stack delivery. Scope is intentionally
ambitious — that is the novelty.

**The focal point is the individual car's page.** Configurations — and the
generation and model views that aggregate them — are what users come for;
capturing the cars is the mission. Companies, engines, and the other entities
get pages too, but as supporting cast: the database is organised so the car
pages can exist, not the other way around. Any ingestion or modeling decision
that grows the supporting cast at the expense of reaching the cars is the
wrong trade.

## Current Phase

**Phase 1: schema live, first source landing.** All 29 tables are applied and
reviewed; Wikidata fetch-and-land runs end to end into
`raw_scrape.raw_records`. See `PROGRESS.md` for live status.

Next: the reconciler — turning raw records into `companies` +
`field_provenance`. NHTSA vPIC and EPA follow.

## Architecture Invariants

These are settled decisions. Proposals to change one must be flagged explicitly
as a decision to revisit — never slipped in silently.

- **Postgres is the source of truth.** `pgvector` extension for semantic search.
- **The entity hierarchy is a goal per car, not a form every car must fill**
  (ADR 0014, from the 2026-07-31 fundamentals review). The mandatory,
  source-asserted rails are `companies` → `models` → `catalogue_periods` →
  `configurations`; every spec-bearing row ultimately foreign-keys back to
  `configurations`. **`generations` is the goal level**: generation
  placement is an evidence-gated fact on the configuration
  (`configurations.generation_id`, nullable), written only when a source
  places the individual car (body style, chassis code, dated spans) —
  never inferred from the year alone, because one model year can contain
  two generations at once (the 2019 AMG GT: C190 coupes beside X290
  4-doors). Pages render richly when placed and degrade gracefully to
  model → year when not; nothing lies. A **catalogue period** (ADR 0009,
  re-parented to models by ADR 0014) is pure time: a US model year
  (`start_year = end_year` — the shape vPIC/EPA assert), a production
  period ("built 1998–2005" — Euro/JDM), or a facelift phase
  (zenki/kouki, Phase 1/2). No pass may fabricate per-year rows from a
  period or vice versa, and no pass may fabricate a generation.
  Aggregation pages (lines, chassis-code pages, the generation page) are
  queries over the spine, not entities.
  `companies` holds every organisation
  that appears on or behind a vehicle — BMW, Alpina, Singer, Zagato (ADR 0006).
  **"Make" is a role, not a table**: a company holding manufacturer
  responsibility (its own WMI) is a make, recorded in
  `company_role_assignments`. Modelling makes and builders separately was
  rejected because Alpina is both, and would need two rows and two pages.
- **Engines and transmissions are first-class entities** with their own tables.
  Cross-make reuse (BMW B58 in Toyota Supra, GM LS swaps, etc.) makes this
  non-negotiable.
- **Hybrid storage model**: ~20 universal core specs live as columns on
  `configurations`. Long-tail/sparse attributes live in an EAV table
  (`configuration_attributes`). New attributes are registered in
  `attribute_definitions` before any data lands.
- **Provenance attaches to facts, not identity** (ADR 0002). Fact-bearing rows
  carry `source_id` / `scraped_at` / `confidence_score` (EAV
  `configuration_attributes`, the association tables) or field-level provenance
  in `field_provenance`. Entity/identity tables (`companies` …
  `configurations`, `engines`, `transmissions`) carry no provenance — they hold
  the reconciled current value and are upserted by natural key. Supersession
  lives with the facts, never on identity rows.
- **Raw scrape data is retained by re-fetchability** (ADR 0004). A separate
  `raw_scrape` schema holds untransformed source records
  (`raw_scrape.raw_records`); every fact carries a `raw_record_id` back to the
  exact scrape, for re-reconciliation when matching logic improves. Tier 3/4
  records are **archival — never deleted**, because they may be unrepeatable.
  Tier 1/2 records from stable programmatic sources are a **cache**: prunable
  when correctness calls for it, then re-landed. Artifacts of our own bugs are
  deletable at any tier. **Distrust never justifies deletion** — an unreliable
  source is demoted in reconciliation, not erased, since the evidence is what
  justifies the demotion.
- **Wikidata QID is the universal join key** wherever a vehicle entity has one
  — stored in `external_ids` alongside every other source's identifiers
  (ADR 0003), not as a per-table column.

## Schema Overview

Core tables (Phase 1 target):

- `companies` — manufacturers, coachbuilders, restomodders, tuners. Top-level
  entity, has its own page. Roles via `company_roles` +
  `company_role_assignments`.
- `models` — nameplates under a company. FK → `companies`.
- `generations` — generation of a model (E46, G80, etc.). FK → `models`. Holds
  chassis codes.
- `catalogue_periods` (formerly `model_years`; ADR 0009) — the mandatory 4th
  level under a generation: a US model year, a production period, or a
  facelift phase, with `period_kinds` as the closed set. FK → `generations`.
- `configurations` — atomic unit (period + trim + market + drivetrain
  combo). FK → `catalogue_periods` + `market_regions`.
- `engines` — engine entities. FK → `companies` (maker of the engine, may
  differ from the car's company).
- `transmissions` — transmission entities.
- `configuration_engines`, `configuration_transmissions` — many-to-many join
  tables.
- `market_regions`, `body_styles`, `drivetrains`, `transmission_types`,
  `fuel_types`, `currencies`, `countries`, `aspirations`, `company_roles`,
  `derivation_types` — dimension/lookup tables.
- `vehicle_derivations` — fact table keyed on the *base* generation:
  coachbuilt/restomod/tuned/rebadged relationships (ADR 0005). The nullable
  derived side records catalogue placement (own entry under the builder vs.
  stays under the base make), never legal status — that lives in
  `company_role_assignments`.
- `media_assets`, `media_attachments` — images and documents (owner's manuals,
  brochures), with licence and attribution. Attached to any entity via an
  exclusive arc.
- `sources` — every data source (URL, tier, scraped_at). Referenced by every
  fact.
- `configuration_attributes` — EAV for long-tail specs.
- `attribute_definitions` — registry of legal EAV keys with units, types,
  validation.
- `field_provenance` — field-level provenance for entity/spec columns
  (ADR 0002).
- `external_ids` — `(source, external_id)` → entity mapping, incl. Wikidata
  QIDs (ADR 0003).
- `raw_scrape.raw_records` — permanent untransformed scrape landing zone
  (ADR 0003).

Reference DDL lives in `docs/schema_phase1.sql`; rationale in `docs/schema.md`.
**Note:** the SQLAlchemy models in `carmanac/db/models/` are now the source of
schema truth (current Alembic head `5cbf6be81036`); `docs/schema_phase1.sql`
and `docs/schema.md` predate ADR 0002-0006 and are stale (see PROGRESS.md Open
Questions). Leaf entity renamed `variants` → `configurations` (ADR 0001).

## Source Tiering

Sources are tiered by authority. Conflicts resolve by tier first, then recency,
then flag for review.

- **Tier 1 (authoritative, structured):** NHTSA vPIC API, EPA fueleconomy.gov
  bulk data, EU type approval data, Japan MLIT, manufacturer press kits/media
  sites, Wikidata.
- **Tier 2 (structured secondhand):** Wikipedia (multilingual — EN, DE, JA, IT
  critical), Edmunds, KBB, Car and Driver / MotorTrend archives.
- **Tier 3 (unstructured enthusiast):** Marque-specific wikis (BimmerWiki,
  Toyota Wiki, etc.), forum spec threads, club archives. Critical for pre-2000,
  JDM, and Euro-only configurations.
- **Tier 4 (visual/PDF):** OCR'd brochures from archive.org, manufacturer
  historical PDFs.

## Tech Stack

- **Database**: Postgres + pgvector. Migrations via Alembic — never raw
  `ALTER TABLE`.
- **Ingestion**: Scrapy for structured sites; Playwright fallback for JS-heavy
  sources.
- **Orchestration**: Dagster.
- **API**: FastAPI.
- **Frontend**: Next.js + Tailwind + shadcn/ui. Deployed on Vercel.
- **Language**: Python 3.11+ for backend/scrapers, TypeScript for frontend.

## Conventions

- **Python**: ruff for lint/format, type hints required, pydantic for I/O
  validation.
- **SQL**: `snake_case`, plural table names, `id` as PK, FK columns named
  `<singular>_id`.
- **Scrapers** live in `scrapers/<source_name>/`. One directory per source.
  (API-based ingestion lives in `carmanac/ingest/<source>/` — importable
  Python needing a DB session, unlike Scrapy spiders which run under Scrapy's
  own runner.)
- **Migrations** via Alembic only. Auto-generated, then hand-reviewed.
- **Commits**: Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`).
- **Decisions**: Significant decisions get an ADR in `docs/decisions/` before
  implementation.

## URL / Page Structure

The frontend route map mirrors the entity hierarchy (public slug for the leaf
is pending the slug-strategy ADR; it need not literally be `configurations`):

- `/makes/<company-slug>` — company page (a make is a company holding the
  `manufacturer` role)
- `/makes/<company-slug>/<model-slug>` — model page
- `/makes/<company-slug>/<model-slug>/<generation-slug>` — generation page
- `/configurations/<configuration-slug-or-id>` — configuration detail
- `/engines/<engine-slug>` — engine detail + list of configurations using it
- `/compare?configurations=a,b,c` — comparison view

## Working Rules

Always:

- Read `PROGRESS.md` before planning next steps or claiming context; update it
  at the end of every working session.
- Treat the Architecture Invariants above as settled — revisiting one is a
  flagged, explicit decision.
- Write an ADR in `docs/decisions/` for any significant new decision before
  implementation.
- Respect rate limits and identify the scraper bot honestly in user-agent
  strings.
- Index foreign key columns. Always.
- Include `source_id`, `scraped_at`, `confidence_score` columns on any new
  *fact-bearing* table (not identity/entity tables — see the provenance
  invariant and ADR 0002).

Never:

- Add a spec as a column on `configurations` when it belongs in EAV (rule of
  thumb: if <80% of configurations would have a value, it's EAV).
- Drop or restructure EAV in favor of new columns without an explicit decision.
- Hard-code source URLs in business logic — sources go in the `sources` table.
- Scrape commercial sites without rate limiting, identification, and respect
  for robots.txt.
- Throw away Tier 3/4 raw scrape data — it may be unrepeatable (ADR 0004).
  Tier 1/2 pruning is allowed but deliberate, recorded, and followed by a
  re-land.
- Delete raw records because a source turned out to be unreliable. Demote it in
  reconciliation instead.
- Narrow the "all cars" scope — the global scope is the explicit point of the
  project.

## Out-of-Scope (current phase)

- Commercial vehicles (trucks >class 3, buses) — passenger only for now.
- Motorcycles.
- Pricing data beyond original MSRP at launch.
- User accounts / saved lists / any social features.
