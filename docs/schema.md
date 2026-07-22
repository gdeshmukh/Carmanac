# Schema — Phase 1

> **⚠ Partially superseded (2026-07-22).** ADR 0002 and ADR 0003 changed the
> provenance model after this was written: provenance moved from row-level
> columns on every table to `field_provenance` (field-level) + fact tables;
> `wikidata_qid` columns were replaced by `external_ids`; `raw_scrape.raw_records`
> was added. The **SQLAlchemy models in `carmanac/db/models/` are now the source
> of truth.** Sections below describing per-table `source_id`/`superseded_by`
> and `wikidata_qid` columns are historical. Full rewrite is a tracked follow-up.

Status: **Phase 1 (lean).** This document is the rationale companion to
`docs/schema_phase1.sql` (the reference DDL). The DDL is the source of intent;
the actual database is built and migrated **via Alembic only** — the reference
DDL exists so the Alembic baseline can be diffed against an intended target and
so reviewers can read the whole schema in one place.

Scope decision (2026-06-15): Phase 1 is deliberately lean. The Open Questions in
`PROGRESS.md` (defunct-make parent companies, coachbuilders, concept/race-only
flags, slug strategy, localized attribute labels) are **deferred to their own
ADRs** and are not implemented here. Where the schema would have to change to
absorb those decisions, the change is additive (a nullable column or a new
table), so deferring them costs nothing now.

---

## Design principles carried from `CLAUDE.md`

- **Five-level hierarchy is the spine.** `makes → models → generations →
  model_years → configurations`. Every spec-bearing row foreign-keys back toward
  `configurations`.
- **Provenance on every fact.** Every fact-bearing row carries `source_id`,
  `scraped_at`, `confidence_score`, and a `superseded_by` self-reference so a
  fact is never destructively overwritten — a better fact supersedes the old
  one and the old one stays for audit.
- **Hybrid storage.** ~20 universal core specs are columns on `configurations`;
  everything sparse is EAV (`configuration_attributes` + `attribute_definitions`).
- **Engines/transmissions are first-class** because of cross-make reuse (BMW
  B58 in the Supra, GM LS swaps, ZF 8HP everywhere).
- **Wikidata QID is the universal join key** — present on every entity table.
- **FK columns are always indexed.**

---

## Lookup / dimension tables

`market_regions`, `body_styles`, `drivetrains`, `fuel_types`,
`transmission_types`. Modeled as tables rather than native Postgres `ENUM`s for
two reasons: new values (a new market, a new body style) can be added by
inserting a row instead of running a migration, and each value can later carry
its own aliases/provenance if a source names things differently. Each has a
stable `code` (used in logic and slugs) and a human `name`.

## `sources`

Referenced by every fact-bearing row. Carries `tier` (1–4, matching the source
tiering in `CLAUDE.md`). This is why URLs never get hard-coded in business
logic — a fact points at a `sources` row, and the row holds the URL and tier.

## Entity hierarchy

- **`makes`** — top-level, has its own page. `wikidata_qid` unique. Carries
  `founded_year` / `defunct_year` (nullable; defunct handling beyond this is an
  open ADR). No `parent_company_id` yet — deferred.
- **`models`** — nameplate under a make. `slug` unique *within* a make
  (`UNIQUE (make_id, slug)`), so `bmw/3-series` and a hypothetical other
  make's `3-series` don't collide.
- **`generations`** — holds `chassis_codes` as a `TEXT[]` because one
  generation routinely spans several codes. `start_year`/`end_year`
  (null end = still in production).
- **`model_years`** — a single year inside a generation; thin by design,
  `UNIQUE (generation_id, year)`.
- **`configurations`** — the atomic unit: a `model_year` × trim × market ×
  drivetrain combination. Holds the core spec columns (below). This is the
  only level at which a single spec value is unambiguous, which is why every
  spec column lives here rather than higher up. (Named `configurations`, not
  `variants` — see `docs/decisions/0001-leaf-entity-naming.md`.)

## Core spec columns on `configurations`

The hybrid-storage rule from `CLAUDE.md`: a spec earns a column only if **≥80%
of configurations would plausibly have a value**. The selected ~20 are grounded in
what the three Phase 1 Tier 1 sources actually populate:

| Column group | Columns | Primary Tier 1 source |
|---|---|---|
| Identity/classification | `trim_name`, `body_style_id`, `drivetrain_id`, `doors`, `seating_capacity` | NHTSA vPIC |
| Powertrain summary | `fuel_type_id`, `engine_displacement_cc`, `cylinders`, `transmission_type_id` | EPA + vPIC |
| Performance/economy | `power_hp`, `torque_nm`, `mpg_city`, `mpg_highway`, `mpg_combined`, `mpge_combined`, `electric_range_km` | EPA fueleconomy.gov |
| Physical | `curb_weight_kg`, `length_mm`, `width_mm`, `height_mm`, `wheelbase_mm` | Wikidata |
| Launch price | `msrp_launch_amount`, `msrp_launch_currency` | mixed (in scope: MSRP-at-launch only) |

The powertrain summary columns are a **denormalized convenience** for fast
list/compare queries. The authoritative powertrain detail lives in the
`engines`/`transmissions` entities via the join tables; the summary columns are
expected to be reconciled against those, not treated as the primary record. All
units are metric and explicit in the column name (`_cc`, `_kg`, `_mm`, `_nm`,
`_km`) to remove ambiguity at ingest; mpg/mpge stay imperial because they are
EPA-defined metrics, not raw measurements.

Anything below the 80% bar — turbo count, valve count per cylinder, brake
specs, market-specific equipment, etc. — goes to EAV.

## EAV: `attribute_definitions` + `configuration_attributes`

`attribute_definitions` is the registry of legal keys; **no attribute lands in
`configuration_attributes` until its key is registered here** with a `data_type` and
optional `unit`/`validation_regex`. Keys are canonical English (localized labels
are a deferred concern). `configuration_attributes` has one typed value column per
data type, and a **partial unique index** enforcing a single live
(non-superseded) value per `(configuration, attribute)` — superseded history is
retained.

## Engines & transmissions

First-class entities with their own `wikidata_qid` and provenance.
`manufacturer_make_id` points at the *engine's* maker, which deliberately may
differ from the car's make (the cross-make-reuse requirement). `configuration_engines`
and `configuration_transmissions` are the many-to-many joins (a configuration may offer
several engines across its trims).

---

## Deferred (each needs an ADR before implementation)

- `parent_company_id` self-reference on `makes` (defunct/acquired marques).
- `coachbuilder` entity + M2M with configurations.
- `is_concept` / `is_race_only` boolean flags on `configurations`.
- Slug strategy (stable slug + ID with 301-to-canonical).
- Localized attribute labels in `attribute_definitions`.

## Validation

`docs/schema_phase1.sql` parses cleanly against the Postgres dialect (58/58
statements, validated with `sqlglot`). Full runtime validation against a live
Postgres + pgvector instance happens at the next step (local docker-compose +
Alembic baseline), per `PROGRESS.md` → Next.
