# Progress

Working head of the project log. Update at the end of every working session —
even one-line entries. When stopping mid-task, write down where you are and
what's next.

**Session start**: read this file, run `scripts/status.py`, and diff the two —
the script prints the live numbers (Alembic head, entity/queue counts, matched
makes), so this file carries intent and decisions, not counters that go stale.

**Layering rule** (2026-07-29): this head stays small. Session Log keeps only
the last few entries; older entries, finished checklists, and completed review
findings roll into `docs/progress-archive/` verbatim and unedited. Decisions'
durable homes are the ADRs — log entries should be a claim plus a PR/ADR link,
not a restatement.

---

## Current Focus

Phase 1: **the first cars are landed.** 2,018 vPIC passenger models in the landing zone (`model:<id>`), across all 247 makes; **134/247 makes matched** to 7,215 companies; ADR 0009 implemented (`catalogue_periods` live, migration `76cb287dd71c`, deep-reviewed at Gaurav's request — one real find, fixed); **ADR 0010 (the models pass) proposed, awaiting Gaurav**. 113 `match_review` open (parked no-match pool), admission batch-triage proposals parked for Gaurav's "barely cars" pass. Charter: **the focal point is the individual car's page.** Next: ADR 0010 review → the first `models` rows; year-level vPIC + EPA after PR #17 merges.

## In Flight

PR #17 (`catalogue_periods` migration + review fixes) and PR #18 (models fetch-and-land + ADR 0010, stacked on #17) open. Awaiting Gaurav: ADR 0010 review, the admission batch-triage decisions ("barely cars" pass), the parked no-match pool (113 flags).

## Next (immediate)

1. Gaurav reviews ADR 0010 (the models pass) → implement → the first `models` rows (~134 makes' nameplates).
2. Year-level vPIC + EPA unblock after PR #17 merges (US rows land as `model_year` periods).
3. Thin read surface decision (F2): models rows make /makes + /models pages demoable — revisit sequencing then.
4. Gaurav's "barely cars" pass: admission batch decisions (moto/truck/bus/racing/subsidiary signatures), the out-of-scope vPIC makes (Blue Bird, Freightliner, Winnebago...), BLUECAR's company question (no company-shaped Wikidata entity; Bolloré is a holding group), and the parked no-match pool.

## Next (Phase 1 horizon)

- Year-level vPIC (`model_year` catalogue periods) + EPA fueleconomy.gov bulk CSV → the first configurations.
- Wikidata models fetch → cross-source model matching (needs its own ladder; ADR 0010 §4 deliberately excludes it).
- Basic admin UI / review-queue interface (deferred until corroboration has shrunk the queues — Gaurav 2026-07-29).

Deferred review items (important, not blocking): confidence-score methodology (#6); `power_hp` rating standards (F8's second half, sibling ADR to 0009); CI running ruff + `alembic check`.

## Open Questions

These need decisions before they become blockers. Each should resolve to an ADR in `docs/decisions/` when settled.

- **Defunct/acquired makes**: Pontiac, Plymouth, Saab, etc. Are they top-level `makes` or do we model corporate parent relationships? (Leaning toward: makes stay top-level, add an optional `parent_company_id` self-reference.)
- ~~**Coachbuilders**~~ — RESOLVED by ADR 0005 (proposed). They are `builders`, not makes, and attach to vehicles via `vehicle_derivations` keyed on the *base generation*. The gray area dissolves under the WMI rule: a body house only raises the "is it a make?" question when it builds its own car, and then it earns a WMI and passes the normal test. Historical coachbuilding (Duesenberg/Murphy) is the common case — `derived_generation_id` NULL, so the car stays a Duesenberg.
- ~~**Builder product lines**~~ — RESOLVED at ADR 0005 acceptance (2026-07-27): a named product line (Singer DLS) *is* a model/generation under the builder company, linked to its donor via `vehicle_derivations.derived_generation_id`. No new entity kind.
- **Platforms** (NEW, from ADR 0005 §5): a future `platforms` entity — generations point at a platform, platforms carry `evolved_from` lineage (Urus → MLB Evo → MLB → VW Group; matches industry usage). Replaces the dropped `platform_shared` derivation type. Which platform a generation belongs to is a sourced claim and will conflict ("basically a Q5 underneath" vs. `platform: MLB Evo`) — normal reconciliation machinery applies. Wikidata P4243 is the obvious first source. Needs its own ADR when that ingestion is planned.
- ~~**Model-year spine vs. production periods** (F8)~~ — RESOLVED by ADR 0009 (accepted 2026-07-29): catalogue periods. Migration pending; configuration-level ingestion unblocks when it lands. The rating-standards half of F8 still needs its sibling ADR.
- ~~**vPIC external-id namespacing**~~ — RESOLVED 2026-07-29 (Gaurav): kind-prefix both (`make:440` now, `model:<id>` from the models fetch's first run). Re-keyed live via `scripts/rekey_vpic_external_ids.py`; landers write prefixed ids.
- **Company/brand duplicates beyond the match queue**: the 2026-07-29 merges resolved the pairs vPIC exposed, but the earlier review counted ~121 duplicated names overall (Mazda ×3 shape). The rest surface as sources touch them; a systematic sweep of exact-name duplicate companies is possible any time via one query + the same merge machinery.
- **Spec rating standards** (F8): `power_hp` is standardless (SAE net / DIN / JIS gross indistinguishable) and only EPA cycles get first-class columns. Rating-standard/test-cycle lookups, with non-EPA figures in EAV per the 80% rule. Same ADR as above, or its sibling.
- **Concept cars and prototypes**: in scope or out? (Leaning toward: separate boolean flag on `configurations`, default to production-only in queries.)
- **Race-only configurations** (GT3, Group B, etc.): in scope? (Leaning toward: yes, with a flag.)
- **Slug strategy**: stable slugs vs. ID-based URLs. Stable slugs are nicer but historical renames are painful. (Leaning toward: slug + ID, accept slug at any historical value and 301 to canonical.) Gaurav 2026-07-29: collision disambiguators must be **source-neutral** — `tesla-q124981765` leaks Wikidata's identifier scheme into public identity; had ingestion started from vPIC the same company would have slugged differently, which is exactly the inconsistency to avoid. Low urgency (no pages exist), decide in the slug ADR.
- **Corroboration-driven queue reduction** (Gaurav 2026-07-29, design requirement for the vPIC pass): presence in an authoritative source is inherent authentication — a vPIC WMI/make match should auto-resolve that entity's `admission_review` flag rather than wait for a human. The review queue shrinks from evidence first, manual review second. The **review-queue interface is deliberately deferred** until multi-source corroboration exists, so reviews aren't bare is-this-a-car-company judgments.
- **Agent-assisted review** (Gaurav 2026-07-29, future): once resolved flags accumulate as a labeled set, an agent trained/grounded on the confirmed data could pre-screen the review queue. Sequencing: after vPIC corroboration and real queue-working experience.
- **Company eras and revival** (from the Bugatti merge + Gaurav 2026-07-29): a single `defunct_year` cannot represent multi-era companies — Bugatti (Molsheim d.1963 → EB110 era → VW/Rimac era, alive), Scout Motors (revived 2022), MG (Morris → BL → MG Rover → SAIC). Company pages should be able to show eras; "defunct" must not mean permanently dead. Needs an ADR (era/active-period modeling vs. prose); until then the Bugatti canonical-record projection (`defunct 1963`) stands as a known-wrong tentative value.
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
- [0007](docs/decisions/0007-reconciler-policy-and-first-pass.md) — Reconciler: deterministic raw→assertions→projection pipeline; **one engine + one thin mapper per source**; QID-exact identity only in v1 (no fuzzy auto-merge until a labeled set exists); **strict admission, branching outwards** — vetted classes admit, deny-listed exclude, unknowns *quarantine* with `admission_review` flags (under-admission is the cheap error); `manufacturer` role asserted from both Wikidata classes (Pontiac counts; vPIC arbitrates later); tier → affinity → recency → flag; new `reconciled_records` + `reconciliation_flags` tables and `companies.website`. Accepted 2026-07-27; implemented 2026-07-28.
- [0008](docs/decisions/0008-vpic-matching-and-corroboration.md) — vPIC matching + corroboration: match ladder (curated registry → unique exact-normalized-name → `match_review` flag with candidates, never fuzzy-auto); a confirmed match writes `external_ids`, asserts the `manufacturer` role from vPIC, resolves admission flags as `corroborated_by_vpic`, and admits matched quarantined entities (the fourth way in). Corroboration is US-scoped until more sources land; the corroboration trail is the future confidence methodology's raw material. Accepted 2026-07-29; implemented same day.
- [0009](docs/decisions/0009-catalogue-period-spine.md) — The 4th level is a **catalogue period**: `model_year` (start = end; what vPIC/EPA assert), `production_period` (Euro/JDM "built 1998–2005"), or `phase` (zenki/kouki, Phase 1/2), in one table renamed `catalogue_periods`. Five-level hierarchy and one-page-per-configuration unchanged; US per-year pages unchanged; fabrication in either direction rejected on provenance grounds; same-kind overlap flags, never constrains; mixed granularity resolves by containment. Review notes: aggregation above stays (the generation page is the shared page); C190/R190-style code families are one generation's `chassis_codes`. Accepted 2026-07-29; migration pending.

## Known Risks / Things to Watch

- **Scraping ToS exposure**: avoid commercial sites without clearly public data. Lead with Wikidata + government APIs to minimize risk while volume is small.
- **Wikidata coverage gaps**: strong for mainstream Western and Japanese makes, weaker for Soviet-era, Chinese pre-2010, Indian, and Brazilian domestic-market vehicles. Tier 3 sources will be required earlier than expected for those.
- **Wikidata class modelling is not a clean taxonomy.** `automobile manufacturer` and `car brand` overlap inconsistently — Pontiac is a brand but not a manufacturer; Saab is both. Assume any single class misses real marques, and re-check coverage against a known list whenever the query changes. The landed 7,223 also include plants and subsidiaries ("KINTO Europe"), so **what counts as a `make` is an unresolved reconciliation question**, not a solved one.
- **EAV query performance** at scale (>500k configurations × N attributes). Plan to benchmark with synthetic data before declaring schema final.
- **Entity resolution debt**: every source added without a solid matcher compounds the reconciliation problem. Do not add Tier 2/3 sources until matcher precision is measured on a labeled set.
- **Schema intent duplicated** between `docs/schema_phase1.sql` and the SQLAlchemy models. Mitigated 2026-07-22: models declared the source of truth, DDL/schema.md flagged stale. Full rewrite still owed.
- **`confidence` has no methodology yet** (review #6). It is written on facts but means nothing until defined (e.g. tier weight × match confidence). Do not let downstream logic weight it before then.

## Session Log

End-of-session notes, newest at top. Last few entries only — older ones live
in [docs/progress-archive/](docs/progress-archive/2026-06--07.md), along with
the completed F1-F9 fix queue and the 2026-07 review findings.

### 2026-07-29 (part 10 — PROGRESS restructured: hot head + archive + status.py)

- This file split per Gaurav's growth concern (PR #19): head keeps intent (focus/in-flight/next/open questions/ADR index/recent log, ~110 lines), history rolls to `docs/progress-archive/` verbatim, and `scripts/status.py` prints the live numbers so session-start verification is a diff, not an archaeology dig. PROGRESS **stays tracked**: git is the multi-device sync, and the log is portfolio evidence like the ADRs.

### 2026-07-29 (part 9 — the first cars land: 2,018 vPIC passenger models)

- **vPIC models fetch-and-land built and run** (`carmanac/ingest/vpic/models.py` + script, same plumbing as makes): `GetModelsForMakeIdYear/makeId/<id>/vehicletype/{car,mpv}` per landed make, union per ModelId (the Accord appears under both types), sorted type lists, `model:<ModelId>` external ids (the re-key paying off on day one). **2,192 model/type rows across all 247 makes → 2,018 distinct models landed**; ~9 minutes at the polite 1 req/s. Endpoint shape probed live before building (models arrive mixed-case — "Accord", "FCX Clarity" — unlike the SHOUTING makes). 3 landing tests; 90 total green.
- **All 247 makes fetched, not just the 134 matched** — landing is generous (Tier 1 re-fetchable cache) and models under unmatched makes are match-queue evidence. Immediate payoff: **BBC's only "model" is literally named "Passenger Car"**, a vPIC placeholder — evidence *against* it being the Italian BBC brand; the flag stays open but better informed. Sanity: BMW 146 models, Ford 140, Ferrari 66; Accord/Viper/Roadster resolve by ModelId.
- **ADR 0010 proposed** (the models pass): matched-make models only (unmatched makes' models wait — one open make question must not fan out into fifty model-shaped copies); upsert by identity ladder then (company, slug); slug collisions under one company FLAG rather than auto-suffix (two same-slug models under one make are usually the same nameplate — auto-suffixing would mint exactly the duplicate-identity problem the merges just cleaned); `models.name` projects via `field_provenance` so Wikidata can arbitrate later; nameplate level only. Needs Gaurav's review before implementation.

### 2026-07-29 (part 8 — migration review, Gaurav-requested; one real find)

- **Deep review of the hand-written migration** (Gaurav: "everything transferred right and functionality remains"). Full catalog sweep for `model_year` across pg_class/pg_constraint/pg_trigger/pg_attribute/pg_indexes/pg_sequences/pg_proc **found 8 stale names**: a column rename rewrites index *definitions* but not the attribute names stored inside existing index relations — `uq_configurations_natural_key`, `uq_field_provenance_live`, `uq_media_attachments_asset_entity_role`, and the five renamed arc/FK indexes all still said `model_year_id` in pg_attribute. Functionally inert (planner and pg_dump use definitions — verified none carried the old name), but the catalog shouldn't lie. Index relations accept `ALTER TABLE ... RENAME COLUMN`; migration amended (both directions), live DB brought to the amended end-state, downgrade/upgrade round-tripped through the amended code.
- **Everything else verified clean**: catalog sweep now empty; partial arc-index predicates carry the new column name; sequence owned by `catalogue_periods.id`; trigger semantics re-proven across separate transactions (first probe was flawed — `now()` is transaction-fixed, so same-transaction INSERT+UPDATE can never show a bump: no-op update doesn't bump, real update does); five-level seed join resolves (`bmw > 3-series > e46 > 2002-2002 (model_year) > 330i-us-sedan`); table counts intact (7,215 companies / 10,139 raw / 26,902 assertions / 2,850 flags); both reconciler passes re-run as no-ops; seed idempotent; 87 tests green. Code sweep: only the kind code and the already-stale-flagged legacy docs mention model years.

### 2026-07-29 (part 7 — the catalogue_periods migration; ADR 0009 implemented)

- **Migration `76cb287dd71c`** (PR #17), hand-written — a table rename is autogenerate's DROP+CREATE blind spot, and the trigger is invisible to it (both documented since ADR 0006's migration). `model_years` → `catalogue_periods` with every embedded name following (PK, indexes, FK-constraint names, trigger, **and the id sequence** — the ADR 0006 rename left `makes_id_seq` behind on `companies`, noted, not repeated); `year` → (`start_year`, `end_year`, `period_kind_id`) with the seed row backfilled as a model_year period; seeded `period_kinds` lookup; four-column natural key NULLS NOT DISTINCT; CHECK end ≥ start. The five referencing tables' `model_year_id` arc/FK columns renamed to `catalogue_period_id` — column renames rewrite constraint/index *expressions* automatically (verified live on the exclusive-arc CHECKs); only *names* need explicit renames.
- **Downgrade refuses instead of flattening**: real period/phase rows cannot round-trip into a single `year`, so `downgrade()` counts non-model_year rows and raises (the same refuse-don't-discard posture as f3c645b9cb6f's). Round trip verified on the pre-period dataset.
- Verified: `alembic check` clean, downgrade/upgrade round trip, pre-migration seed data survived the rename intact, seed script idempotent on the new shape, arc CHECK expressions rewritten, trigger fires on the renamed table. **87 tests green** (3 new: open-ended-period NULLS NOT DISTINCT collision, end-before-start CHECK, both kinds coexisting on one generation — the ADR's mixed-granularity claim as a permanent test).
- Test-fixture lesson re-learned from conftest's own docstring: migration-seeded lookups (now incl. `period_kinds`) are deliberately NOT truncated between tests — fixtures select them like production code does, never re-insert.
- **Configuration-level ingestion is now unblocked** (year-level vPIC, EPA). Next: vPIC models fetch-and-land.

