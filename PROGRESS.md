# Progress

Living log of project state. Update at the end of every working session — even one-line entries. When stopping mid-task, write down where you are and what's next.

---

## Current Focus

Phase 1: schema is live, reviewed, and reconciliation-ready; the first real source is landing. Wikidata fetch-and-land runs end to end — 7,223 manufacturers/brands in `raw_scrape.raw_records`. A full pre-reconciler review (R1-R12) is implemented. Next is the reconciler, which turns raw records into `companies` + `field_provenance`.

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
- [2026-07-22] **Pre-ingestion schema review** (as an experienced data engineer would). Found four critical modeling gaps that would compound once real multi-source data lands, and fixed all four before scraping:
  - **#1 Supersession was structurally impossible on entity tables** — `superseded_by` existed on every table but the unique keys blocked a superseding row from coexisting (proven live: a 2nd BMW row failed `uq_makes_slug`). Root cause: entity rows are identity, not facts. **Fixed** (ADR 0002): entity tables are now identity-only; provenance/supersession moved off them.
  - **#2 Provenance was row-level, but facts are field-level** — one `source_id` per configuration can't express NHTSA→body, EPA→mpg, Wikidata→dims. **Fixed** (ADR 0002): new `field_provenance` sidecar records each source's assertion per `(entity, field)`; the column holds the reconciled winner.
  - **#3 No raw landing zone, no traceability** — `raw_scrape` was empty and no fact linked to its scrape. **Fixed** (ADR 0003): `raw_scrape.raw_records` added; fact tables gained `raw_record_id`.
  - **#4 `wikidata_qid` columns didn't generalize** — **Fixed** (ADR 0003): `external_ids` table maps `(source, external_id)` → entity for any source; the seven `wikidata_qid` columns were dropped.
- [2026-07-22] Migration `00531f09d08f` generated, hand-reviewed (CHECK constraints, partial indexes, cross-schema FK, and drop-ordering all verified), and applied. DB now has 19 public tables + `raw_scrape.raw_records`.
- [2026-07-22] **`alembic check` caught a latent env.py bug**: without `include_schemas=True`, autogenerate could not see the `raw_scrape` schema and would have tried to re-create `raw_records` on every run. Fixed in `alembic/env.py`.
- [2026-07-22] Seed rewritten to the new shape — 63 rows, idempotent. Demonstrates three sources (Wikidata + NHTSA + EPA) contributing different fields to one configuration, each field tracing to its raw scrape.
- [2026-07-24] **First real data landed.** Wikidata SPARQL ingestion built (`carmanac/ingest/wikidata/`) and run against the live endpoint: 7,223 manufacturers and car brands in `raw_scrape.raw_records`, 7.1 MB JSONB, ~5.5s, verified idempotent on re-run. Migration `24e7d0f5602c` makes that idempotency structural via `UNIQUE (source_id, content_hash)`. Three defects found and fixed by running it for real — SPARQL Cartesian fan-out, unstable `GROUP_CONCAT` ordering, and a company-vs-brand coverage gap that was dropping Pontiac/Plymouth/Datsun. See Session Log.
- [2026-07-24] `updated_at` DB trigger added (review #7, migration `a1c4e7b93f20`) — hand-written, since Alembic cannot see triggers (same blind spot that dropped the inline CHECK constraints). `onupdate` is SQLAlchemy-side and never fires on `INSERT ... ON CONFLICT` or COPY, which is exactly how ingestion writes; the wrong value could not have been backfilled afterwards, hence doing it before the first scraper. Verified against the live DB: a raw non-ORM upsert now bumps the timestamp, an idempotent no-op re-scrape does not (`NEW IS DISTINCT FROM OLD`), and downgrade drops all 7 triggers plus the function. `alembic check` clean.

## In Flight

Nothing blocking. The pre-reconciler review is done and **R1-R12 are implemented and verified** (migration `5cbf6be81036`). The end targets are now shaped for the reconciler to sort raw records into.

## Next (immediate)

1. Implement ADR 0005's remaining schema — `vehicle_derivations` + `derivation_types` lookup. ADR 0006 settled the entity question it depended on; the derivation tables themselves are still unbuilt.
2. Write the reconciler ADR — tier precedence, field affinity, tie-breaking, review-queue triggers, and `confidence` (policy decided 2026-07-24, see Session Log; still needs writing up).
3. Build the reconciler: read raw records → write source assertions to `field_provenance` → project the winner onto entity columns. Does not exist yet; it is the substance of ingestion.
4. Reconcile `companies` only for the first pass, applying ADR 0005's admission rule and verifying against a hand-checked sample before scaling to models.
5. Optional: connect a GUI client (DBeaver) and browse the seeded vehicle through the hierarchy.

## Next (Phase 1 — target: ~6–8 weeks)

- Wikidata SPARQL ingestion → populate `makes` and `models`, QIDs into `external_ids`. First real data lands here.
- NHTSA vPIC API client → fill US-market configurations (1981+).
- EPA fueleconomy.gov bulk CSV → fuel economy + emissions attributes.
- Reconciler: write source assertions to `field_provenance`, project the winner onto entity columns. This logic does not exist yet and is the substance of ingestion.
- `updated_at` DB trigger before any bulk load (review #7 — ORM `onupdate` won't fire on COPY / bulk upsert).
- Basic admin UI for inspecting ingested data.
- First version of the entity resolution review queue (review #9).

Deferred review items (important, not blocking scraping): confidence-score methodology (#6); natural key on `configurations` for cross-source dedup (#5); `power_hp` rating-standard ambiguity + mpg gallon units; CI running ruff + `alembic check`.

## Review Findings (2026-07-24 pre-reconciler audit)

Verified against the live database. Ordered by what blocks what.

**R1 — DONE (ADR 0006, migration `5cbf6be81036`): `makes` and `builders` cannot be two tables.** A coachbuilder page needs everything a make page needs (slug, name, country, founded/defunct, description, trigram search), and a builder catalogue needs to parent models — so `builders` becomes a near-copy of `makes`. **Alpina breaks it**: it holds its own WMI (a make) *and* builds on BMW hardware (a builder), so two tables means two rows and two pages for one company, plus an ambiguous match target for the reconciler. Pininfarina shows the same thing over time. Recommendation: one company table, with manufacturer status *derived* from "has a WMI in `external_ids`" rather than a flag someone must set. This revises ADR 0005's §2, which proposed a separate `builders` table.

**R2 — DONE: `field_provenance` accepts unlimited contradictory live assertions.** Its only constraint is the PK. Proven live: three rows (`makes.name` = 'BMW' / 'Bayerische Motoren Werke' / 'BMW AG'), same source, all `superseded_by IS NULL`, all accepted. `configuration_attributes` already does this correctly via `uq_configuration_attribute_live`. Fix: mirror that partial unique index on `(entity, field_name, source_id) WHERE superseded_by IS NULL`. **This is the table the reconciler writes to** — without it, re-runs append instead of supersede.

**R3 — DONE: no natural key on `configurations`.** Uniqueness is `(model_year_id, slug)` and slug is derived from a name, so two sources wording the same car differently both insert. Fix: unique on real identity — `(model_year_id, trim_name, market_region_id, drivetrain_id, body_style_id)` — with slug demoted to display. Previously deferred as review #5; stops being deferrable at the second source.

**R4 — DONE: `market_region_id` is nullable but defines the atomic unit.** CLAUDE.md and the docstring both define a configuration as year × trim × market × drivetrain. NULLs don't collide in Postgres, so two "unknown market" rows never conflict — which defeats R3's key. Fix: NOT NULL with an explicit `UNKNOWN`/`GLOBAL` lookup row (preferred, matches how the other lookups work), or a coalesced unique index.

**R5 — DONE: `chassis_codes` had no index** despite the model docstring calling it the level "enthusiasts actually search by". It is `text[]`, so it needs GIN.

**R6 — DONE: the configuration URL and its index disagreed.** Route map says `/configurations/<slug-or-id>` (flat lookup); uniqueness is `(model_year_id, slug)`, so a bare slug scans. This is the slug-strategy open question surfacing as a concrete index gap.

**R7 — DONE: fuzzy matching stopped at `models`.** Trigram indexes exist on `makes.name` and `models.name` but not `generations.name` or `configurations.trim_name` — which is exactly where matching gets hard ("E46 330Ci", "330i Sport"). Needed before the matcher, not after.

**R8 — DONE: closed sets were stored as free text**, against the schema's own lookup-table principle: `msrp_launch_currency` (ISO 4217), `makes.country_code` (ISO 3166), `engines.aspiration`, `engines.configuration`. Nothing prevents `USD` / `usd` / `$` coexisting.

**R9 — DONE: `engines.configuration` collided with the `configurations` entity.** Two meanings of the most load-bearing word in the schema. Rename to `cylinder_layout` while it is cheap.

**R10 — denormalized `configurations.engine_displacement_cc` / `.cylinders`** duplicate `engines` deliberately for fast list queries, but nothing keeps them in sync. Handle as a reconciler consistency check rather than a schema change.

**R11 — DONE: no media/image tables.** Every page in the route map is one users expect to have pictures. Licensing is genuinely hard here (most car photography is not freely reusable), which argues for deciding early.

**R12 — DONE: no prose fields on `makes`.** A Zagato or Singer homepage is mostly narrative, and there is no column for it — while Wikidata already returns a description we currently discard at reconcile time.

**Confirmed sound:** entity/fact split, engines as first-class entities, EAV gated by `attribute_definitions`, exclusive-arc provenance with partial indexes, and the raw landing zone (proven idempotent over three live runs).

## Open Questions

These need decisions before they become blockers. Each should resolve to an ADR in `docs/decisions/` when settled.

- **Defunct/acquired makes**: Pontiac, Plymouth, Saab, etc. Are they top-level `makes` or do we model corporate parent relationships? (Leaning toward: makes stay top-level, add an optional `parent_company_id` self-reference.)
- ~~**Coachbuilders**~~ — RESOLVED by ADR 0005 (proposed). They are `builders`, not makes, and attach to vehicles via `vehicle_derivations` keyed on the *base generation*. The gray area dissolves under the WMI rule: a body house only raises the "is it a make?" question when it builds its own car, and then it earns a WMI and passes the normal test. Historical coachbuilding (Duesenberg/Murphy) is the common case — `derived_generation_id` NULL, so the car stays a Duesenberg.
- **Builder product lines** (NEW, from ADR 0005): when `derived_generation_id` is NULL, does a named product like "Singer DLS" need its own catalogue entity, or is a configuration under the base make enough? Deferred until real data shows how often it bites.
- **Concept cars and prototypes**: in scope or out? (Leaning toward: separate boolean flag on `configurations`, default to production-only in queries.)
- **Race-only configurations** (GT3, Group B, etc.): in scope? (Leaning toward: yes, with a flag.)
- **Slug strategy**: stable slugs vs. ID-based URLs. Stable slugs are nicer but historical renames are painful. (Leaning toward: slug + ID, accept slug at any historical value and 301 to canonical.)
- **Multi-language attribute names**: do we store one canonical English attribute key and translate at the frontend, or store localized labels in `attribute_definitions`? (Leaning toward: canonical English keys, localized labels as a separate concern later.)
- **Reference DDL vs. models as source of schema intent** — RESOLVED in practice: the SQLAlchemy models are now the source of truth. `docs/schema_phase1.sql` and `docs/schema.md` are flagged partially-superseded by ADR 0002/0003; full rewrite (or generating the DDL from models) is a tracked follow-up.

## Resolved Decisions

(ADRs go in `docs/decisions/` — this is a quick index.)

- [0001](docs/decisions/0001-leaf-entity-naming.md) — Leaf entity named `configurations`, not `variants`. Accepted 2026-06-15.
- [0002](docs/decisions/0002-entity-fact-split-and-field-provenance.md) — Entity/fact split; field-level provenance in `field_provenance`. Accepted 2026-07-22.
- [0003](docs/decisions/0003-raw-landing-zone-and-external-ids.md) — `raw_scrape.raw_records` landing zone; `external_ids` mapping replaces `wikidata_qid` columns. Accepted 2026-07-22.
- [0004](docs/decisions/0004-raw-record-retention.md) — Raw record retention tiered by re-fetchability: Tier 3/4 archival, Tier 1/2 prunable cache, bug artifacts always deletable, distrust never justifies deletion. Amends the CLAUDE.md invariant. Accepted 2026-07-24.
- [0005](docs/decisions/0005-what-counts-as-a-make.md) — What counts as a make: manufacturer responsibility (own WMI), **no exceptions**. Derivation is one `vehicle_derivations` fact table keyed on the base generation, with a nullable derived side. **Proposed** — derivation tables not yet built. Its separate `builders` table is withdrawn by ADR 0006.
- [0006](docs/decisions/0006-companies-not-makes.md) — One `companies` table; "make" becomes a role. Alpina is both a manufacturer and a builder, so two tables would give one company two rows and two pages. Accepted 2026-07-24, implemented in `5cbf6be81036`.

## Known Risks / Things to Watch

- **Scraping ToS exposure**: avoid commercial sites without clearly public data. Lead with Wikidata + government APIs to minimize risk while volume is small.
- **Wikidata coverage gaps**: strong for mainstream Western and Japanese makes, weaker for Soviet-era, Chinese pre-2010, Indian, and Brazilian domestic-market vehicles. Tier 3 sources will be required earlier than expected for those.
- **Wikidata class modelling is not a clean taxonomy.** `automobile manufacturer` and `car brand` overlap inconsistently — Pontiac is a brand but not a manufacturer; Saab is both. Assume any single class misses real marques, and re-check coverage against a known list whenever the query changes. The landed 7,223 also include plants and subsidiaries ("KINTO Europe"), so **what counts as a `make` is an unresolved reconciliation question**, not a solved one.
- **EAV query performance** at scale (>500k configurations × N attributes). Plan to benchmark with synthetic data before declaring schema final.
- **Entity resolution debt**: every source added without a solid matcher compounds the reconciliation problem. Do not add Tier 2/3 sources until matcher precision is measured on a labeled set.
- **Schema intent duplicated** between `docs/schema_phase1.sql` and the SQLAlchemy models. Mitigated 2026-07-22: models declared the source of truth, DDL/schema.md flagged stale. Full rewrite still owed.
- **`confidence` has no methodology yet** (review #6). It is written on facts but means nothing until defined (e.g. tier weight × match confidence). Do not let downstream logic weight it before then.

## Session Log

End-of-session notes go here. Newest at top. Be brief.

### 2026-07-24 (part 4 — pre-reconciler review implemented)
- **All 12 review findings implemented** in one hand-written migration, `5cbf6be81036`. Hand-written because autogenerate renders a rename as DROP + CREATE, which would discard every row and dependent FK, and because it cannot see triggers (the `updated_at` trigger had to be re-pointed by hand after the table rename).
- **ADR 0006 accepted: one `companies` table, "make" becomes a role.** The trigger was wanting coachbuilder homepages: a Zagato page needs everything a make page needs, and a builder catalogue needs to parent models, so `builders` would be a near-copy. **Alpina settles it** — own WMI *and* builds on BMWs, so two tables give one company two rows, two pages, and an ambiguous match target for the reconciler. Roles are a fact-bearing M2M (`company_roles` + `company_role_assignments`) since companies hold several and change over time. Withdraws ADR 0005 §2.
- **R2 verified fixed against the live DB**: the same three contradictory assertions that were accepted before are now rejected by `uq_field_provenance_live`, while supersession still works (history retained, one live row).
- **R3/R4 use `UNIQUE NULLS NOT DISTINCT`** (Postgres 15+). Without it the natural key would have silently done nothing for sparse records — NULL trim, drivetrain or body style never collide by default, so exactly the rows most needing dedup would both insert. Verified: a duplicate config with all-NULL dimensions is now rejected.
- Also landed: chassis-code GIN index (R5), flat configuration slug (R6), trigram indexes on generations/trims/engines (R7), `currencies`/`countries`/`aspirations` lookups (R8), `engines.configuration` → `cylinder_layout` (R9), `media_assets` + `media_attachments` with licence and attribution (R11), and `summary`/`description` prose on every entity (R12).
- Verified end to end: `alembic check` clean, ruff clean, downgrade/upgrade round trip clean, seed rebuilt and idempotent, Wikidata ingest unaffected. 27 tables, 121 indexes.

### 2026-07-24 (part 3 — retention rule + make definition)
- **ADR 0004 accepted: raw retention is tiered by re-fetchability**, amending the blunt "never discarded" invariant. Tier 3/4 stays archival (may be unrepeatable); Tier 1/2 from stable APIs is a prunable cache; artifacts of our own bugs are always deletable. Distrust explicitly does *not* justify deletion — an unreliable source is demoted in reconciliation, since the retained evidence is what justifies the demotion. `CLAUDE.md` invariant and Never-Do list updated to match.
- **Acted on it once**: pruned the 7,223 pre-canonicalization Wikidata records and re-landed clean. Verified first that zero facts referenced them (all 13 belonged to the seed record, which was left intact), so nothing was orphaned. Re-land is now fully idempotent — 7,222 landed, immediate re-run inserts 0.
- **ADR 0005 proposed: what counts as a make.** Test is manufacturer responsibility for the finished vehicle (own WMI + type approval), chosen because NHTSA vPIC makes it *verifiable from data we already plan to ingest* rather than a per-marque judgment. Alpina and Ruf pass; Singer, Gunther Werks, plants, holding companies and KINTO-style subsidiaries fail. **No exceptions** — a first draft admitted Singer on findability grounds and was rejected: an exception granted to whichever marque feels prominent enough is not a rule.
- Findability is instead solved structurally. **`builders` becomes a first-class table** (restomodders, tuners, historical coachbuilders), and one `vehicle_derivations` table is keyed on the *base generation* with a **nullable `derived_generation_id`** — NULL when the car stays under the base make (Singer 911 is a Porsche; Duesenberg/Murphy is a Duesenberg), set when the builder holds manufacturer status (Alpina B3 is an Alpina). Because everything keys on the base, "show me modified 911s" is one query returning Singer, Gunther Werks *and* Ruf together.
- **The coachbuilder gray area dissolved rather than needing a clause.** A body house only raises the make question when it builds its own car — and then it earns a WMI and passes the normal test (Automobili Pininfarina, Zagato Mostro). Historical contract coachbuilding is just the NULL case. One mechanism covers coachbuilt, restomod, tuned, and **rebadged** — the last a large real category (GR86/BRZ, Stellantis) that previously had no home in the schema.

### 2026-07-24 (part 2 — first real data)
- **Wikidata fetch-and-land built and run.** `carmanac/ingest/wikidata/` (client / queries / land) + `scripts/ingest_wikidata_makes.py`. 7,223 distinct QIDs, 7.1 MB of JSONB, ~5.5s end to end. Lands raw records only — no `makes`, no reconciliation.
- Migration `24e7d0f5602c`: `UNIQUE (source_id, content_hash)` on `raw_scrape.raw_records`, making re-scrape idempotency structural instead of an application convention (same lesson as the `updated_at` trigger — bulk paths bypass Python-side rules). Enables `ON CONFLICT DO NOTHING`, which is also race-safe.
- **Two real defects found only by running it against the live endpoint**, both fixed:
  - *SPARQL OPTIONAL clauses form a Cartesian product.* "KINTO Europe" returned **360 rows** — 24 countries × 15 websites. 7,074 rows for 6,514 entities overall. Fixed with server-side `GROUP_CONCAT` + `GROUP BY`: exactly one row per QID, and faster (9.6s vs 14.3s) for less data on the wire.
  - *`GROUP_CONCAT` order is not stable.* The same 18 countries came back rotated after the query was widened, hashing differently and re-landing as a spurious change. Left alone, every multi-valued entity would re-land whenever the query plan shifted. Fixed by canonicalizing (sorting) the aggregated lists before hashing — verified against the two divergent payloads already stored, which now hash identically.
- **Coverage gap found: Wikidata separates company from marque.** Querying only `automobile manufacturer` (Q786820) silently missed **Pontiac, Plymouth, and Datsun**, which are recorded as `car brand` (Q10429667). Saab and Oldsmobile survived only because they carry both classes — luck, not a rule. Query now unions both: 6,514 + 777 → 7,222, recovering 709 entities. Since `makes` means the marque, this was a correctness bug, not a scope preference.
- Scope stays deliberately loose: the result set includes plants and subsidiaries. Filtering at fetch time discards data unrecoverably; filtering at reconcile time is reversible because the raw record persists.
- `httpx` added as the first ingestion dependency. Ingest code lives in `carmanac/ingest/<source>/` rather than top-level `scrapers/` — the latter is for Scrapy spiders, which run under Scrapy's own runner; API sources are importable Python needing a DB session. Noted so the CLAUDE.md convention reads as refined, not ignored.

### 2026-07-24 (part 1)
- Added the `updated_at` DB trigger (see Done), clearing the last ordering constraint before ingestion.
- **Reconciler policy decisions made** (to be formalized in the reconciler ADR before implementation):
  - **Same-tier conflicts resolve by field affinity** — each field names its authoritative source domain (EPA owns fuel economy, NHTSA owns body/safety, Wikidata owns identity/historical facts). Registered per-field, not hard-coded.
  - **Flagged conflicts still project a tentative winner** onto the entity column (higher tier / more recent), with the flag kept — pages always show data; review can overturn later.
  - **`confidence` stays NULL until a real methodology exists** (review #6 stays open) — no tier-restated-as-decimal placeholder numbers that downstream logic might mistake for information.
- **Settled how `source` is allowed to be used** (worth an explicit note, since it is easy to violate silently). Source is recorded so every fact traces to where it came from — it is *not* a query dimension, and nothing user-facing filters by it. Two deliberate exceptions where it is genuinely load-bearing: (1) `external_ids` namespacing, since an identifier is only meaningful scoped to its source (`Q26678` means BMW in Wikidata's namespace; NHTSA and EPA both use bare integers that would otherwise collide) — and the `(source_id, external_id)` lookup is the idempotency key that stops re-scrapes creating duplicate entities; (2) tier-based conflict resolution in the reconciler. In `field_provenance`, source stays pure audit trail: display it, debug with it, never branch on it beyond tier.
- Next: reconciler ADR, then Wikidata fetch-and-land.

### 2026-07-22 (part 2 — pre-ingestion schema review)
- Reviewed the schema as an experienced data engineer would, before scraping. Found and fixed four critical modeling gaps that would have compounded under multi-source ingestion (ADR 0002/0003): entity/fact split with field-level `field_provenance`; `raw_scrape.raw_records` landing zone + `raw_record_id` traceability; `external_ids` replacing `wikidata_qid` columns. Migration `00531f09d08f` applied; seed rewritten to demonstrate three sources on one configuration; `alembic check` clean after fixing an `include_schemas` gap in env.py. CLAUDE.md invariants updated to match. Next: Wikidata ingestion + the reconciler that writes `field_provenance` and projects winners onto columns.

### 2026-07-22 (part 1)
- Returned to the project after a gap. Reconciled repo state against this file, which was stale (it listed already-completed schema work as "next"). Fixed three latent bugs: the `infra/initdb/` mount path, the un-applied ADR 0001 rename in the reference DDL, and CHECK constraints being invisible to Alembic. Renamed `gvd` → `carmanac` and `db/` → `infra/` for consistency. Built the Python side end to end: models for all 17 tables, Alembic wired up, baseline migration applied, demo vehicle seeded and verified. **Phase 1 schema is now live in a real database.**

### 2026-06-11
- Initial planning conversation. Locked architecture invariants, source tiering, tech stack, and entity hierarchy. Created `CLAUDE.md` and `PROGRESS.md` v0.1. Repo exists with README + LICENSE; no code yet. Next session: schema DDL.
