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

**The database is the product; every surface is a view of it.** It is built
rock-solid from the ground up — explicit structure, natural keys, field-level
provenance — so that search tools and LLM agents can navigate it fast and
mechanically (stable canonical addresses, machine-readable relationships,
honest NULLs that mean "no source has said") while the human-facing
experience stays simple. Anything that would make the data easier to render
but harder to reason over is the wrong trade.

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

**Phase 1: three sources landed, six reconciliation passes live, first cars
materialized.** All 35 tables are applied and reviewed. Wikidata, NHTSA vPIC
and EPA fetch-and-land end to end into `raw_scrape.raw_records`, and six
deterministic passes turn those records into companies, models, lines,
generations, catalogue periods and configurations — every fact carrying
field-level provenance back to the raw record that asserted it. See
`PROGRESS.md` for live counts and the open review queues.

The generation-placement pass is live (ADR 0016 restructured generations;
ADR 0017 owns the evidence): generations re-anchored to companies,
Wikipedia infoboxes supply generation time via sitelinks, and
configurations place by unique dated overlap — everything ambiguous or
under-evidenced waits with a logged reason. ADR 0017 §4 broke the
existence bottleneck: nameplate articles' per-generation sections mint
generations (Wikidata demoted from gatekeeper to contributor), and body
evidence vetoes candidates before the year test — the AMG GT's coupes and
4-doors place into their own generations without ever being each other's
candidates. Next: the review queues §4 opened (unreconciled section
articles, boundary-year overlaps), then the source depth already
inventoried on vPIC and EPA.

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
- **Slugs are display addresses, not identity** (ADR 0019). No registry, key,
  lookup or join may identify a row by its slug; identity is the natural key
  and `external_ids`. A row must be able to exist with no slug at all, and an
  address is a **projection** — recomputed from current data on every run,
  free to change until pages are published. The rule exists because it was
  broken once: curated judgments keyed on slug pairs, so renaming a page
  silently disarmed a human's recorded decision.

## Schema Overview

Core tables (Phase 1 target):

- `companies` — manufacturers, coachbuilders, restomodders, tuners. Top-level
  entity, has its own page. Roles via `company_roles` +
  `company_role_assignments`.
- `models` — nameplates under a company. FK → `companies`.
- `model_lines`, `model_line_members` — "3 Series" as an aggregation over
  as-filed models, not a level in the spine (ADR 0011 §2, ADR 0012 §4).
  Membership is a per-source assertion; lines hold no external ids.
- `generations` — a generation (E46, G80, etc.). FK → `companies`
  (ADR 0016): one E46 covers 325i/330i/M3, so a generation is a
  company-anchored index/display entity, not a child of one model. Holds
  chassis codes. The **goal** level, not a mandatory one: placement is a
  nullable, evidence-gated fact on `configurations` (ADR 0014), and which
  models a generation spans is derived from placements plus
  `generation_model_links`.
- `generation_model_links` — per-source assertions that a generation belongs
  to a model's history (the `model_line_members` shape). The placement
  pass's candidate gate: links are evidence, never inference.
- `catalogue_periods` (formerly `model_years`; ADR 0009, re-parented by
  ADR 0014) — pure time under a **model**: a US model year, a production
  period, or a facelift phase, with `period_kinds` as the closed set.
  FK → `models`, *not* generations — one model year can hold configurations
  of two generations at once.
- `configurations` — atomic unit (period + trim + market + drivetrain
  combo). FK → `catalogue_periods` + `market_regions`, plus the nullable
  `generation_id` that records placement when a source supplies it.
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
  QIDs (ADR 0003). Written only on one-to-one correspondence (ADR 0011 §4).
- `reconciled_records`, `reconciliation_flags`, `match_decisions` — the
  reconciler's own bookkeeping: which raw records a pass has processed at
  which version, the open review questions with their candidates, and the
  rung/method/outcome of every match attempt. The decision log is the
  labeled set a measured matcher will be evaluated against.
- `period_kinds` — the closed set of catalogue-period conventions
  (`model_year`, `production_period`, `phase`).
- `raw_scrape.raw_records` — permanent untransformed scrape landing zone
  (ADR 0003).

**The SQLAlchemy models in `carmanac/db/models/` are the source of schema
truth** (current Alembic head `ea565b7ea489`). `docs/schema_phase1.sql` and
`docs/schema.md` predate ADR 0002-0006 and are **stale** — read them for
history only; a rewrite or a generated-from-models DDL is owed (see
PROGRESS.md Open Questions). Leaf entity renamed `variants` →
`configurations` (ADR 0001).

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
- **Pipeline steps are runnable modules**: each ingest module and reconcile
  pass carries its own entry point (`python -m carmanac.reconcile.matching`),
  sharing `carmanac/runner.py`. `scripts/` holds only what is not pipeline —
  status/backup and the standing judgment tools in `scripts/decisions/`
  (registry-driven, dry-run gated). One-shot correction scripts do not
  accumulate: a correction either becomes pass/registry behaviour or is not
  worth keeping after it runs (git history is the record).
- **Migrations** via Alembic only. Auto-generated, then hand-reviewed.
- **Commits**: Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`).
- **Decisions**: Significant decisions get an ADR in `docs/decisions/` before
  implementation.

## URL / Page Structure

The route map **is** the entity hierarchy — each segment adds only what the
ones before it do not already say (ADR 0019). Truncating any address gives
the index above it: `/bmw/m3/2004` is that year's M3s, `/bmw/m3` is every
M3, `/bmw` is the company.

- `/<company>` — company page (a make is a company holding the `manufacturer`
  role). Company slugs live at the root, so root literals are reserved.
- `/<company>/<model>` — model page, every car under the nameplate
- `/<company>/<model>/<year>` — the model year, an index over its cars
- `/<company>/<model>/<year>/<car>` — configuration detail (**the focal
  page**). The address is only the tail — trim, then drivetrain when the trim
  does not already say it — so it need only be unique inside that year, and
  a car with nothing to distinguish it is `base`.
- `/<company>/generations/<generation>` — generation page, company-anchored
  per ADR 0016. The address is the bare chassis code (`/porsche/generations/964`)
  when exactly one generation under that company carries it; shared codes
  (Celica and Supra are both A60) fall back to `<nameplate>-<code>`.
- `/<company>/lines/<line>` — line browse view
- `/<company>/codes/<code>` — chassis-code view (a query, not an entity)
- `/engines/<engine>` — engine detail + list of configurations using it
- `/compare?cars=a,b,c` — comparison view

Models own the bare second segment; every other kind under a company lives
under a reserved literal, and under a model the third segment is always a
period. Non-model-year periods (production periods, facelift phases) have no
segment grammar yet — none exist, and the rule is owed when the first lands.

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
