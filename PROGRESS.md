# Progress

Living log of project state. Update at the end of every working session — even one-line entries. When stopping mid-task, write down where you are and what's next.

---

## Current Focus

Phase 1: schema implementation. Models and migrations are in place; next is standing the schema up in a live database and landing the first real data.

## Done

- [2026-06-11] GitHub repo created with README and LICENSE.
- [2026-06-11] Initial architecture decisions made (see `CLAUDE.md` Architecture Invariants).
- [2026-06-11] Tech stack selected: Postgres + pgvector, Scrapy/Playwright, Dagster, FastAPI, Next.js.
- [2026-06-11] Source tiering defined (Tiers 1–4).
- [2026-06-11] Five-level entity hierarchy locked: makes → models → generations → model_years → configurations.
- [2026-06-15] `docs/schema.md` written — rationale for every table, the hybrid column-vs-EAV rule, provenance model.
- [2026-06-15] `docs/schema_phase1.sql` written — reference DDL for all 17 Phase 1 tables.
- [2026-06-15] ADR 0001 accepted: leaf entity renamed `variants` → `configurations`.
- [2026-06-15] `infra/docker-compose.yml` written — pinned Postgres 17 + pgvector local dev database.
- [2026-07-22] Fixed `infra/` initdb mount: `00_extensions.sql` moved into `infra/initdb/` so the extension bootstrap actually runs on first boot (the compose file mounted `./initdb`, which did not exist — extensions and the `raw_scrape` schema would have silently never been created).
- [2026-07-22] Applied the ADR 0001 rename in `docs/schema_phase1.sql`, which had been missed — the DDL still said `variants` while the ADR and prose said `configurations`.
- [2026-07-22] Python project scaffolded: `pyproject.toml`, `.venv`, ruff config. SQLAlchemy 2.0.51 / Alembic 1.18.5 / psycopg 3.3.4 on Python 3.14.
- [2026-07-22] SQLAlchemy models written for all 17 Phase 1 tables (`carmanac/db/models/`). Verified to match the reference DDL table-for-table; rendered DDL matches on index names, trigram indexes, and named check constraints.
- [2026-07-22] Alembic initialized and configured — URL injected from `carmanac.config.settings`, `target_metadata` bound to the models, `compare_type` and `compare_server_default` enabled.
- [2026-07-22] `scripts/seed_demo.py` written — seeds a 2002 BMW 330i (E46, US) through all five levels plus engine, transmission, and EAV attributes.
- [2026-07-22] Renamed `gvd` → `carmanac` throughout (Python package, Postgres user/database/container/volume, settings env prefix `CARMANAC_`) so the project carries one name matching the repo. Done before the container's first boot, so it cost nothing.
- [2026-07-22] Renamed top-level `db/` → `infra/`. It held only Docker/Postgres infrastructure and collided confusingly with `carmanac/db/` (the Python package that connects to it). `infra/` also leaves room for the Dagster and API services planned for the same compose file.
- [2026-07-22] Postgres container live; `vector` 0.8.2 and `pg_trgm` 1.6 confirmed loaded, proving the `initdb/` mount fix worked.
- [2026-07-22] **Hand-review of the generated migration caught a real defect**: CHECK constraints were attached inline to columns, where `metadata.create_all()` renders them but Alembic autogenerate silently drops them — the migration had 0 of the 12 the reference DDL requires. Moved to table-level via `provenance_table_args()` in `carmanac/db/base.py`. This is the concrete justification for the "auto-generated, then hand-reviewed" convention.
- [2026-07-22] Baseline migration `06582eecf0b2` generated, reviewed, and applied. Database now has 17 tables, 75 indexes, 12 check constraints.
- [2026-07-22] Demo vehicle seeded — 37 rows, verified idempotent (second run inserts 0). A 2002 BMW 330i reassembles correctly across all five levels plus engine, transmission, and EAV attributes.

## In Flight

Nothing blocking. The schema is live in Postgres and holds one verified end-to-end vehicle.

## Next (immediate)

1. Commit this session's work — split as `fix:` (initdb mount, ADR 0001 rename) and `feat:` (schema implementation), per Conventional Commits.
2. Connect a GUI client (DBeaver) and browse the seeded vehicle through the hierarchy.
3. Decide whether the reference DDL stays a hand-maintained doc or becomes generated from the models — two sources of schema intent already caused one drift (the missed rename).
4. Begin Wikidata SPARQL ingestion into `makes` / `models`, the first real Tier 1 source.
5. Add `raw_scrape` tables before that ingestion lands — raw records are never discarded, so the landing zone must exist first.

## Next (Phase 1 — target: ~6–8 weeks)

- Wikidata SPARQL ingestion → populate `makes` and `models` with QIDs as the universal join key.
- NHTSA vPIC API client → fill US-market configurations (1981+).
- EPA fueleconomy.gov bulk CSV → fuel economy + emissions attributes.
- `raw_scrape` schema tables — raw source records are never discarded.
- Basic admin UI for inspecting ingested data.
- First version of the entity resolution review queue.

## Open Questions

These need decisions before they become blockers. Each should resolve to an ADR in `docs/decisions/` when settled.

- **Defunct/acquired makes**: Pontiac, Plymouth, Saab, etc. Are they top-level `makes` or do we model corporate parent relationships? (Leaning toward: makes stay top-level, add an optional `parent_company_id` self-reference.)
- **Coachbuilders**: Pininfarina, Zagato, Bertone — are they makes, or attached to base vehicles as a separate `coachbuilder` entity? (Leaning toward: separate entity, many-to-many with configurations.)
- **Concept cars and prototypes**: in scope or out? (Leaning toward: separate boolean flag on `configurations`, default to production-only in queries.)
- **Race-only configurations** (GT3, Group B, etc.): in scope? (Leaning toward: yes, with a flag.)
- **Slug strategy**: stable slugs vs. ID-based URLs. Stable slugs are nicer but historical renames are painful. (Leaning toward: slug + ID, accept slug at any historical value and 301 to canonical.)
- **Multi-language attribute names**: do we store one canonical English attribute key and translate at the frontend, or store localized labels in `attribute_definitions`? (Leaning toward: canonical English keys, localized labels as a separate concern later.)
- **Reference DDL vs. models as source of schema intent** — see Next (immediate) #4.

## Resolved Decisions

(ADRs go in `docs/decisions/` — this is a quick index.)

- [0001](docs/decisions/0001-leaf-entity-naming.md) — Leaf entity named `configurations`, not `variants`. Accepted 2026-06-15.

## Known Risks / Things to Watch

- **Scraping ToS exposure**: avoid commercial sites without clearly public data. Lead with Wikidata + government APIs to minimize risk while volume is small.
- **Wikidata coverage gaps**: strong for mainstream Western and Japanese makes, weaker for Soviet-era, Chinese pre-2010, Indian, and Brazilian domestic-market vehicles. Tier 3 sources will be required earlier than expected for those.
- **EAV query performance** at scale (>500k configurations × N attributes). Plan to benchmark with synthetic data before declaring schema final.
- **Entity resolution debt**: every source added without a solid matcher compounds the reconciliation problem. Do not add Tier 2/3 sources until matcher precision is measured on a labeled set.
- **Schema intent duplicated** between `docs/schema_phase1.sql` and the SQLAlchemy models. The rename drift found on 2026-07-22 is exactly the failure mode; resolve per Open Questions.

## Session Log

End-of-session notes go here. Newest at top. Be brief.

### 2026-07-22
- Returned to the project after a gap. Reconciled repo state against this file, which was stale (it listed already-completed schema work as "next"). Fixed three latent bugs: the `infra/initdb/` mount path, the un-applied ADR 0001 rename in the reference DDL, and CHECK constraints being invisible to Alembic. Renamed `gvd` → `carmanac` and `db/` → `infra/` for consistency. Built the Python side end to end: models for all 17 tables, Alembic wired up, baseline migration applied, demo vehicle seeded and verified. **Phase 1 schema is now live in a real database.** Next session: commit, then start Wikidata ingestion (add `raw_scrape` tables first).

### 2026-06-11
- Initial planning conversation. Locked architecture invariants, source tiering, tech stack, and entity hierarchy. Created `CLAUDE.md` and `PROGRESS.md` v0.1. Repo exists with README + LICENSE; no code yet. Next session: schema DDL.
