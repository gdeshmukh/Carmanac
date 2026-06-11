# Progress

Living log of project state. Update at the end of every working session — even one-line entries. When stopping mid-task, write down where you are and what's next.

---

## Current Focus

Phase 0: Foundation setup. Repo scaffolding and schema design before any code lands.

## Done

- [2026-06-11] GitHub repo created with README and LICENSE.
- [2026-06-11] Initial architecture decisions made (see `CLAUDE.md` Architecture Invariants).
- [2026-06-11] Tech stack selected: Postgres + pgvector, Scrapy/Playwright, Dagster, FastAPI, Next.js.
- [2026-06-11] Source tiering defined (Tiers 1–4).
- [2026-06-11] Five-level entity hierarchy locked: makes → models → generations → model_years → variants.

## In Flight

- Drafting `CLAUDE.md` and `PROGRESS.md` (v0.1 — this document).
- Claude Project setup for chat-based design work.

## Next (immediate)

1. Write full `docs/schema.md` with rationale for every table, also changing schema overview in `CLAUDE.md`.
2. Write `CREATE TABLE` DDL for Phase 1 tables (`makes`, `models`, `generations`, `model_years`, `variants`, `market_regions`, `body_styles`, `sources`, `attribute_definitions`, `variant_attributes`).
3. Stand up local Postgres + pgvector via docker-compose.
4. Initialize Alembic and commit baseline migration.
5. Add `engines` and `transmissions` tables + join tables.

## Next (Phase 1 — target: ~6–8 weeks)

- Wikidata SPARQL ingestion → populate `makes` and `models` with QIDs as the universal join key.
- NHTSA vPIC API client → fill US-market variants (1981+).
- EPA fueleconomy.gov bulk CSV → fuel economy + emissions attributes.
- Basic admin UI for inspecting ingested data.
- First version of the entity resolution review queue.

## Open Questions

These need decisions before they become blockers. Each should resolve to an ADR in `docs/decisions/` when settled.

- **Defunct/acquired makes**: Pontiac, Plymouth, Saab, etc. Are they top-level `makes` or do we model corporate parent relationships? (Leaning toward: makes stay top-level, add an optional `parent_company_id` self-reference.)
- **Coachbuilders**: Pininfarina, Zagato, Bertone — are they makes, or attached to base vehicles as a separate `coachbuilder` entity? (Leaning toward: separate entity, many-to-many with variants.)
- **Concept cars and prototypes**: in scope or out? (Leaning toward: separate boolean flag on `variants`, default to production-only in queries.)
- **Race-only variants** (GT3, Group B, etc.): in scope? (Leaning toward: yes, with a flag.)
- **Slug strategy**: stable slugs vs. ID-based URLs. Stable slugs are nicer but historical renames are painful. (Leaning toward: slug + ID, accept slug at any historical value and 301 to canonical.)
- **Multi-language attribute names**: do we store one canonical English attribute key and translate at the frontend, or store localized labels in `attribute_definitions`? (Leaning toward: canonical English keys, localized labels as a separate concern later.)

## Resolved Decisions

(ADRs go in `docs/decisions/` — this is a quick index.)

_None yet._

## Known Risks / Things to Watch

- **Scraping ToS exposure**: avoid commercial sites without clearly public data. Lead with Wikidata + government APIs to minimize risk while volume is small.
- **Wikidata coverage gaps**: strong for mainstream Western and Japanese makes, weaker for Soviet-era, Chinese pre-2010, Indian, and Brazilian domestic-market vehicles. Tier 3 sources will be required earlier than expected for those.
- **EAV query performance** at scale (>500k variants × N attributes). Plan to benchmark with synthetic data before declaring schema final.
- **Entity resolution debt**: every source added without a solid matcher compounds the reconciliation problem. Do not add Tier 2/3 sources until matcher precision is measured on a labeled set.

## Session Log

End-of-session notes go here. Newest at top. Be brief.

### 2026-06-11
- Initial planning conversation. Locked architecture invariants, source tiering, tech stack, and entity hierarchy. Created `CLAUDE.md` and `PROGRESS.md` v0.1. Repo exists with README + LICENSE; no code yet. Next session: schema DDL.
