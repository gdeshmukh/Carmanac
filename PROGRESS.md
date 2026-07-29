# Progress

Living log of project state. Update at the end of every working session — even one-line entries. When stopping mid-task, write down where you are and what's next.

---

## Current Focus

Phase 1: schema live and twice-reviewed (R1-R12; foundation review F1-F9), first source landed via the three-axis Wikidata fetch (9,883 entities, 218-marque coverage fixture enforced), reconciler fully designed (ADR 0007 accepted + amended). Fix queue items 1-6 done — **the F9 backup gate is cleared**; next: reconciler schema + build (#7-8).

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

Nothing mid-task. PR #7 merged 2026-07-28. Every reconciler-blocking decision is made; `carmanac/reconcile/` remains the substance of what's ahead.

## Next (immediate) — the F1-F9 fix queue, then the reconciler

1. ~~**Landing fixes (F4+F3)**~~ — DONE 2026-07-28 (PR #5): `MIN()` aggregates, derived canonicalization vars, `last_seen_at` + `DO UPDATE`, duplicate deleted.
2. ~~**pytest + GitHub Actions (F1)**~~ — DONE 2026-07-28 (PR #5): 35 tests, mutation-verified, CI green on the same pinned Postgres image as dev.
3. ~~**ADR 0007 amendments (F5) + association-provenance decision (F7)**~~ — DONE 2026-07-28: reconciliation unit = current record per (source, external_id) by `max(last_seen_at)`, ascending-QID processing order, tombstone retraction, `source_dropped` flags (QID merges flagged, never auto-merged), the three-step supersede dance; **all four association tables are now per-source assertion stores** (migration `d212a042caa7` — autogenerate produced a broken migration twice over, hand-rewritten; see its docstring).
4. ~~**Query widening (F6)**~~ — DONE 2026-07-28 (PR #7, merged after Gaurav's marque-list approval): three-axis fetch (classes + automotive industry + pinned QIDs), full P31 class sets, ISO codes, label fallback chain; 9,883 entities landed idempotently; 218-marque coverage fixture enforced by the ingest script — 218/218.
5. ~~**De-Claude the outward-facing repo**~~ — DONE 2026-07-28 (PR #8): PR-body footers stripped from #3–#6; CLAUDE.md content moved to `docs/charter.md` as a neutral project charter, thin CLAUDE.md untracked (machine-global git ignore, so `.gitignore` stays clean); all references repointed. **History rewrite considered and declined by Gaurav** — existing commit trailers stay; no attribution markers going forward. ADRs and PROGRESS untouched, as decided.
6. ~~**`scripts/backup.sh` (F9)**~~ — DONE 2026-07-28 (PR #9): pg_dump (custom format, zstd) → readability check → optional scratch-database restore verification → local rotation → rclone upload to Google Drive (`drive.file` scope) with size confirmation + age-based remote pruning. First run verified end to end: 988 KB dump, full restore reproduced 30 tables / 9,886 raw records, upload confirmed on Drive. `infra/` docs no longer present `down -v` as safe. **Tier 3/4 ingest is no longer gated.**
7. ~~Migration: `reconciled_records`, `reconciliation_flags`, `companies.website` (ADR 0007 §8)~~ — DONE 2026-07-28 (PR #10, migration `111a7cd329b8`): sidecar state table, review-queue flags with the two-shape CHECK (entity-scoped kinds take exactly one arc column; `admission_review` is record-scoped and requires `raw_record_id`), partial arc indexes + partial open-queue index, `companies.website`. Hand-review + live probes clean (sequence present, all CHECK shapes fire); 4 new constraint tests; downgrade/upgrade round trip verified.
8. Build `carmanac/reconcile/` (engine + wikidata mapper per ADR 0007 §2); run the companies pass; verify the hand-checked sample.
9. After the companies pass (F2): decide whether a thin read surface (FastAPI companies list/detail) jumps ahead of models ingestion.

## Next (Phase 1 — target: ~6–8 weeks)

- Wikidata SPARQL ingestion → populate `makes` and `models`, QIDs into `external_ids`. First real data lands here.
- NHTSA vPIC API client → fill US-market configurations (1981+).
- EPA fueleconomy.gov bulk CSV → fuel economy + emissions attributes.
- Reconciler: write source assertions to `field_provenance`, project the winner onto entity columns. This logic does not exist yet and is the substance of ingestion.
- `updated_at` DB trigger before any bulk load (review #7 — ORM `onupdate` won't fire on COPY / bulk upsert).
- Basic admin UI for inspecting ingested data.
- First version of the entity resolution review queue (review #9).

Deferred review items (important, not blocking scraping): confidence-score methodology (#6); natural key on `configurations` for cross-source dedup (#5); `power_hp` rating-standard ambiguity + mpg gallon units; CI running ruff + `alembic check`.

## Foundation Review Findings (2026-07-27, F1-F9)

Multi-lens review (potential user, experienced data engineer, recruiter, structural skeptic — 4 independent reviewers, findings deduped and adversarially verified against the repo and live DB). Verdict: **foundation sound, nothing requires core rework**; all confirmed findings are process gaps, amendable ADR text, or refinements to planned work. Strengths independently confirmed: the derivation/companies modeling, the enforced entity/fact split, ADR discipline, migration hygiene.

**F1 — CONFIRMED: zero tests, zero CI; hand-verification failed twice on the same property.** The "verified idempotent" claim (2026-07-24) was falsified twice — by the GROUP_CONCAT bug (caught) and by F4 (uncaught until this review). Fix queue #2.

**F2 — PARTIAL: nothing user-facing demoable; URL decisions accumulate without contact.** Softened by verification (the seeded 3-source provenance demo is real, DB-level). Standing advice: thin read surface after the companies pass, before models. Queue #8.

**F3 — CONFIRMED: landing zone cannot represent A-B-A reverts.** Global `(source_id, content_hash)` + `DO NOTHING` silently drops a reverted payload; the newest landed row stays the intermediate value. Fix: `last_seen_at` + `DO UPDATE`; also yields the disappearance signal retraction needs. Queue #1.

**F4 — CONFIRMED: `SAMPLE()` nondeterminism produced a live spurious duplicate.** Q112162285 landed twice (inception 2020-05-10 vs 2021-11-05; Wikidata holds both claims, entity unedited between fetches — verified against revision history). 84 landed entities carry ≥2 inception values. Same bug class as the fixed GROUP_CONCAT instability, one aggregation over. Queue #1.

**F5 — CONFIRMED: ADR 0007 determinism holes** — per-entity processing order unspecified; retraction/disappearance undefined (a deleted wrong value projects indefinitely); Wikidata QID merges unhandled (a second, open route to duplicate identity — ADR 0006 closed only the two-table route); slug-collision assignment order-dependent. Amend before implementation. Queue #3.

**F6 — CONFIRMED: the fetch is silently lossy.** Label service is `"en"`-only → **3,305 of 7,225 entities (46%) have a bare QID as their name**; ADR 0007 §7 as written would mint companies slugged `q288696`. TVR absent entirely — `historical car manufacturer` class invisible to the two-class query (51 of 56 entities lost). No coverage fixture exists. Queue #4.

**F7 — CONFIRMED: association fact tables reproduce the row-level provenance defect ADR 0002 fixed for entity columns.** One row per fact, single `source_id`, no supersession — two sources cannot both assert "BMW is a manufacturer," which structurally blocks ADR 0007 §4's vPIC arbitration. Also: `uq_configuration_attribute_live` omits `source_id` (the "mirrors" comment in provenance.py is wrong in exactly that dimension). Decide at queue #3.

**F8 — PARTIAL: US catalog conventions at the leaf.** Mandatory model-year spine (Euro/JDM production-period records must fabricate per-year rows — discussed nowhere) and EPA-only economy columns + standardless `power_hp`. Overstated half: EAV *is* the designed home for WLTP/JC08 figures. Now in Open Questions; ADR before configuration-level ingestion.

**F9 — CONFIRMED: archival-forever retention on a single unbacked-up Docker volume** (`infra/README.md` still recommends `down -v` as safe). Currently harmless — everything landed is re-fetchable Tier 1. Backup script at queue #5; first Tier 3/4 ingest gated on it.

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
- ~~**Builder product lines**~~ — RESOLVED at ADR 0005 acceptance (2026-07-27): a named product line (Singer DLS) *is* a model/generation under the builder company, linked to its donor via `vehicle_derivations.derived_generation_id`. No new entity kind.
- **Platforms** (NEW, from ADR 0005 §5): a future `platforms` entity — generations point at a platform, platforms carry `evolved_from` lineage (Urus → MLB Evo → MLB → VW Group; matches industry usage). Replaces the dropped `platform_shared` derivation type. Which platform a generation belongs to is a sourced claim and will conflict ("basically a Q5 underneath" vs. `platform: MLB Evo`) — normal reconciliation machinery applies. Wikidata P4243 is the obvious first source. Needs its own ADR when that ingestion is planned.
- **Model-year spine vs. production periods** (F8): every configuration requires a `model_years` row, but Euro/JDM records are often "built 1998–2005, specs unchanged" — fabricating per-year rows vs. generalizing the spine to catalogue periods (US model year as one kind, facelift/zenki-kouki as another). Needs an ADR before configuration-level ingestion.
- **Spec rating standards** (F8): `power_hp` is standardless (SAE net / DIN / JIS gross indistinguishable) and only EPA cycles get first-class columns. Rating-standard/test-cycle lookups, with non-EPA figures in EAV per the 80% rule. Same ADR as above, or its sibling.
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
- [0005](docs/decisions/0005-what-counts-as-a-make.md) — What counts as a make: manufacturer responsibility (issues its own VINs), **no exceptions**; under ADR 0006 the test classifies rather than admits. Derivation is one `vehicle_derivations` fact table keyed on the base generation; the nullable derived side records **catalogue placement** (own entry under the builder vs. stays under the base make), decoupled from the VIN test — legal status lives only in `company_role_assignments`. `platform_shared` dropped in favour of a future `platforms` entity. Accepted 2026-07-27 as amended; implemented in `05e766a04a5f`.
- [0006](docs/decisions/0006-companies-not-makes.md) — One `companies` table; "make" becomes a role. Alpina is both a manufacturer and a builder, so two tables would give one company two rows and two pages. Accepted 2026-07-24, implemented in `5cbf6be81036`.
- [0007](docs/decisions/0007-reconciler-policy-and-first-pass.md) — Reconciler: deterministic raw→assertions→projection pipeline; **one engine + one thin mapper per source**; QID-exact identity only in v1 (no fuzzy auto-merge until a labeled set exists); **strict admission, branching outwards** — vetted classes admit, deny-listed exclude, unknowns *quarantine* with `admission_review` flags (under-admission is the cheap error); `manufacturer` role asserted from both Wikidata classes (Pontiac counts; vPIC arbitrates later); tier → affinity → recency → flag; new `reconciled_records` + `reconciliation_flags` tables and `companies.website`. Accepted 2026-07-27; implementation pending the foundation review.

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

### 2026-07-28 (part 5 — queue #7: reconciler state schema)

- **Migration `111a7cd329b8`** (PR #10): `reconciled_records` (raw_record_id PK/FK sidecar + `reconciler_version`, so re-reconciliation can target stale records mechanically), `reconciliation_flags` (exclusive-arc review queue; `flag_shape_matches_kind` CHECK encodes both shapes from ADR 0007 §8 — entity kinds take exactly one arc column, `admission_review` takes none and requires `raw_record_id`), `companies.website`.
- Autogenerate was clean this time — but the hand-review checklist (PKs, CHECKs, triggers, cross-schema FKs) was run anyway and the `reconciliation_flags.id` sequence verified live, since PK handling is a proven autogenerate blind spot. All three CHECKs probed live from both directions before trusting them; 4 permanent tests added (45 total, green). Downgrade/upgrade round trip + `alembic check` clean.
- Next: queue #8 — build `carmanac/reconcile/` (engine + policy + wikidata mapper) and run the companies pass.

### 2026-07-28 (part 4 — fix queue #6: backups; F9 gate cleared)

- **`scripts/backup.sh` built and verified end to end** (PR #9): dump from the container (`pg_dump -Fc --compress=zstd`, filename stamped with UTC time + Alembic revision), `pg_restore --list` readability check on every run, `--verify-restore` flag that restores into a scratch database and counts tables (run on both test runs: 30 tables, 9,886 raw records reproduced), local keep-7 rotation, rclone upload to Google Drive with post-upload size confirmation and 90-day remote pruning. All knobs are env-overridable per the charter's no-hard-coded-operational-details rule.
- **Off-machine = Google Drive via rclone**, Gaurav's choice from the options laid out. Deliberately minimal-privilege: `drive.file` OAuth scope, so the stored token can only touch files rclone created — a leaked `rclone.conf` exposes the backup folder, not the Drive. One-time browser OAuth done this session.
- **Maintenance flag**: rclone's shared Google client_id is being retired during 2026 — create a personal client_id (free, rclone.org/drive guide) before it stops working.
- `infra/README.md` and the compose header no longer present `down -v` as harmless (the F9 complaint): both now route through a backup first, and the README gained a Backups section.
- Also this session, at Gaurav's direction: the **no-AI-attribution rule went global** to all his projects via user-level `~/.claude/CLAUDE.md` (machine-global git ignore already covered `CLAUDE.md`/`.claude/`).

### 2026-07-28 (part 3 — PR #7 merged; fix queue #5: de-Claude the public repo shape)

- Reconciled this file against reality on session start: only staleness was "In Flight" saying step 4 was uncommitted — it was up as PR #7, CI green. DB verified to match (Alembic head `d212a042caa7`; 9,883 distinct Wikidata entities). **PR #7 merged.**
- **Fix queue #5 executed, scoped by Gaurav mid-session**: audit first, then act — found co-author trailers on 23/34 commits, "Generated with" footers on PRs #3–#6, and tracked `CLAUDE.md` as the outward-facing markers (code/docs only *reference* CLAUDE.md as the conventions doc; author on every commit is Gaurav).
- Actions: footers edited out of PR bodies #3–#6; CLAUDE.md's content moved to **`docs/charter.md`** with the AI-addressed sections reframed as neutral Working Rules (content unchanged in substance — it was always project documentation); CLAUDE.md untracked and reduced to a thin local pointer that imports the charter, ignored via the machine-global git ignore (`~/.config/git/ignore`) so the repo's `.gitignore` never mentions it; references repointed in README, pyproject, `docs/schema.md`, and four code comments. ADR/PROGRESS history untouched.
- **History rewrite declined** (Gaurav): the trailers stay in pre-merge history; the posture is clean-going-forward. Revisitable any time it stays a solo no-fork repo — `git filter-repo` strips trailers in ~an hour, force-push, stale branches deleted.

### 2026-07-28 (part 2 — step 4: three-axis fetch + coverage fixture; uncommitted pending review)

- **Blazegraph killed the MIN() fix before it ever ran**: the widened query 500'd with `java.lang.StackOverflowError`, bisected to `MIN()` over the date literals (SAMPLE→200, MIN→500, same query otherwise). Resolution is better than MIN anyway: **GROUP_CONCAT the dates too** — every founding-date claim lands sorted (Rising Auto's both dates now visible to the reconciler's multi_value flag), and "earliest" becomes mapper policy where transformations belong. Query-contract test now bans SAMPLE *and* MIN/MAX.
- **Class-based fetching hit its ceiling, measured**: the coverage diff exposed that **Tesla Inc, Peugeot, Li Auto, Automobili Pininfarina, Prince, Hispano-Suiza, Praga, Auburn, GMA** carry only generic corporate classes (Peugeot: literally just `organization`). Response: **three fetch axes** — (1) the three marque classes, (2) `P452 = automotive industry` (catches all but two), (3) **pinned QIDs** for entities both axes miss (Peugeot, Singer Vehicle Design), maintained by fixture triage. Suppliers pulled in by axis 2 land-and-quarantine, per the strict-admission polarity.
- **Coverage fixture built and enforced**: 218 marques across all risk-register axes, each QID resolved via the Wikidata API and hand-reviewed; 8 resolver picks corrected to the *marque-side* entity where Wikidata splits company from brand (Tesla, Eagle — the first resolver hit was literally the bird, Venturi, Tatra, Cord, DeLorean, Fisker, Willys); Gunther Werks (no entity) and Trabant (model-series-only) documented in `NOT_IN_WIKIDATA`. The ingest script now exits nonzero on any fixture miss. Currently **218/218**.
- Pruned (ADR 0004 Tier 1 cache, approved) and re-landed twice during iteration; final state **9,883 entities, idempotent re-run, bare-QID labels 3,305 → 67** (the survivors have no label in any of six languages — quarantine's job). 41 tests green.
- **Marque list approved by Gaurav**; his review added two decisions: (1) **Bugatti is ONE company** — Wikidata's three corporate-era entities (Molsheim / EB110-era / VW-era) reconcile to a single `companies` row via a curated identity-merge registry in the reconciler's `policy.py` (ADR 0007 §5 amended; fixture now lists all three era entities, since each must land); (2) the coverage fixture is understood as Wikidata-fetch-specific — future sources bring their own verification.
- Also queued at Gaurav's direction: **de-Claude the outward-facing repo** (Next #5) — no AI-attribution commit trailers/PR footers from here on; CLAUDE.md's public shape and history rewrite to be decided. ADRs/PROGRESS stay — they're the evidence of decision discipline worth showing.

### 2026-07-28 (fix queue steps 1-3: landing determinism, tests+CI, ADR amendments)

- **Steps 1+2 shipped as PR #5** (merged, CI green): `SAMPLE()`→`MIN()` with canonicalization vars derived from the query text; `last_seen_at` + `ON CONFLICT DO UPDATE` (A-B-A representable; F3); the Q112162285 duplicate deleted after zero-reference verification; **35-test suite** whose fixtures are the real regressions, **mutation-verified** — all four re-introduced bugs caught by their guarding tests, none survived; GitHub Actions running ruff + suite on the dev-pinned Postgres image. CI's first run immediately caught an unformatted file, which is the job description.
- **The test suite discovered reconciler mechanics before the reconciler exists**: the naive supersession order (insert new → repoint old) is impossible under `uq_field_provenance_live`; the working three-step dance (retire-to-self → insert → repoint) is documented in the test and now specified in ADR 0007 §1.
- **Step 3: ADR 0007 amended (F5) and F7 decided** (Gaurav: per-source assertion rows). Amendment pins: reconciliation unit = current record per (source, external_id) by `max(last_seen_at)`; ascending-QID processing order (doubles as the slug-collision tiebreak); retraction = tombstone assertion (NULL observed value); disappearance = `source_dropped` flag, never auto-retirement (vanished QIDs are often Wikidata merges — flagged, auto-merge deferred). **All four association tables became per-source assertion stores** (surrogate PK, `superseded_by`, live-unique over fact+source NULLS NOT DISTINCT); EAV stays winner-shaped with assertions in `field_provenance`. Migration `d212a042caa7` — **autogenerate produced a broken migration twice over** (non-serial NOT NULL id on populated tables; PKs never touched, silently defeating the whole change) — hand-rewritten; Alembic does not compare primary keys, same blind-spot class as inline CHECKs and triggers.
- Prune + re-land approved for step 4 (the data is scaffolding-proof, not treasure).

### 2026-07-27 (part 3 — foundation review F1-F9)

- **Full multi-perspective foundation review run before building the reconciler**: 4 independent reviewer lenses (user / data engineer / recruiter / structural skeptic) over the whole repo + live DB, findings deduped, then each surviving finding adversarially verified by an agent instructed to refute it. 9 findings survived (7 confirmed, 2 partial) — recorded above as F1-F9 with a fix queue in Next. Two were live-data discoveries: **F4** (a spurious duplicate raw record from `SAMPLE()` nondeterminism, proven against Wikidata's revision history) and **F6** (46% of landed labels are bare QIDs; TVR's whole class invisible).
- Verdict: **foundation sound** — no core rework anywhere; the confirmed items are process (tests/CI/backups), amendable ADR text, and refinements to already-planned work. Strengths were independently confirmed across lenses, with the derivation/companies modeling and the enforced entity/fact split called out repeatedly.
- ADR 0007 accepted earlier in the session (before the review); its F5 amendment lands with fix-queue #3, before implementation.
- Gaurav input points in the queue: F7 design choice (#3), ADR 0007 amendment review (#3), coverage-fixture skim (#4), read-surface sequencing (#8).

### 2026-07-27 (part 2 — ADR 0007 drafted)

- **ADR 0007 proposed: the reconciler contract.** Formalizes the 2026-07-24 policies and adds the decisions made today with two grounding discoveries from the live payloads: (1) **payloads carry no P31 class** — the query filters on the two classes but never selects them, so class-based admission/roles need a query widening + re-land first; (2) **Singer/Gunther Werks are not in the landed set** — Wikidata doesn't class restomodders as manufacturers or brands, so this pass produces no builders and the builder-class fetch is future work.
- Decisions: admission = everything except a maintained P31 **class exclusion list** (mechanical, reversible — excluded entities wait in raw); `manufacturer` role asserted from **both** classes (Wikidata's company/marque split is its own artifact — Pontiac is brand-only there yet held WMI `1G2` and is on ADR 0005's own pass list), vPIC arbitrating later; identity resolution is **QID-exact only** in v1, no fuzzy auto-merge until matcher precision is measured on a labeled set; flag resolutions become that labeled set.
- New schema it mandates: `reconciled_records` (sidecar state — landing zone stays untransformed), `reconciliation_flags` (review queue v1, exclusive-arc idiom), `companies.website` (R12 logic: Wikidata returns it, pages want it).
- Next: Gaurav reviews/accepts 0007, then implement in the ADR's stated order (widen query → migration → `carmanac/reconcile/` → companies pass → hand-checked ~50-marque verification).

### 2026-07-27 (ADR 0005 accepted as amended; derivation schema live)

- **ADR 0005 accepted, with the derived side redefined.** ADR 0006's merge had quietly broken §3: it moved legal status into `company_role_assignments` while promising builder product lines full catalogue depth — but §3 still coupled `derived_generation_id` to the VIN test, which would have orphaned a Singer DLS generation from its 964 donor. Now decoupled: **the derived side records catalogue placement** (set = own model/generation under the builder; NULL = stays under the base make), and whose name is in the VIN lives *only* in the role table. Proven per-build, not per-company, by Ruf: own VINs on the CTR, yet customer conversions keep their Porsche VIN — so no company-level test can encode it. Role/catalogue disagreement is a reconciler flag, never a constraint.
- **`platform_shared` dropped; platforms get their own entity later.** Platform siblings (Urus/SQ8/Cayenne on MLB Evo) have no builder, no donor, no direction — forcing them through a directional table means electing a fake "base". Resolved to a future `platforms` entity with `evolved_from` lineage (industry's own term), which also beats pairwise edges on volume: MLB Evo's ~6 generations are 6 FKs, not 15 edges. Both of 0005's open questions thereby closed; decision aid artifact with the 2×2 matrix (VIN × catalogue entry — all four quadrants occupied by real cars) published during the session.
- **Migration `05e766a04a5f`**: `derivation_types` (seeded: coachbuilt/restomod/tuned/rebadged) + `vehicle_derivations` with natural key `(base, company, type, derived)` **UNIQUE NULLS NOT DISTINCT** — same trap as R3/R4: the common case (derived NULL) would otherwise never collide and reconciler re-runs would append endlessly. CHECK `derived <> base`. Partial index on `derived_generation_id` serves the child→parent read ("what is the DLS built on?"), which is the direction users will actually ask. Verified live: duplicate NULL-derived claim rejected, self-derivation rejected, two product lines from one base+company+type coexist (DLS + Classic Study shape), downgrade/upgrade round trip clean, `alembic check` clean. 29 public tables, 130 indexes.
- Also: fixed `alembic/script.py.mako` to emit modern annotations (`collections.abc`, PEP 604) and autofixed the old migration headers — ruff now clean over `carmanac/`, `alembic/`, and `scripts/`.

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
